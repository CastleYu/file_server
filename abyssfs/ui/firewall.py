"""Windows firewall helpers for LAN access prompts."""

from __future__ import annotations

import ctypes
import logging
import socket
import subprocess
import sys
from typing import Dict, Optional, Set, Tuple

from abyssfs.ui.constants import (
    FirewallCommand,
    FirewallProfile,
    NetCategory,
    NetHost,
)

_logger = logging.getLogger(__name__)


def is_windows() -> bool:
    return sys.platform == "win32"


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in NetHost.LOOPBACK


def is_wildcard_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in NetHost.WILDCARD


def _run_output(command: Tuple[str, ...], *args: str) -> str:
    result = subprocess.run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        check=False,
    )
    return f"{result.stdout}\n{result.stderr}"


def _parse_profile_aliases(output: str) -> Dict[str, str]:
    profile_by_alias: Dict[str, str] = {}
    header_map = {
        FirewallProfile.PUBLIC_HEADER: NetCategory.PUBLIC,
        FirewallProfile.PRIVATE_HEADER: NetCategory.PRIVATE,
        FirewallProfile.DOMAIN_HEADER: NetCategory.DOMAIN,
    }
    current: Optional[str] = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = line.rstrip(FirewallProfile.HEADER_SUFFIX)
        if header in header_map:
            current = header_map[header]
            continue
        if line.startswith(FirewallProfile.SEPARATOR_PREFIX):
            continue
        if line == FirewallProfile.COMMAND_OK:
            continue
        if current:
            profile_by_alias[line] = current
    return profile_by_alias


def _parse_connection_profiles(output: str) -> Dict[str, str]:
    profiles: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        alias, category = parts
        if alias and category:
            profiles[alias] = category
    return profiles


def _non_public_internet_aliases() -> Set[str]:
    try:
        output = _run_output(FirewallCommand.CONNECTION_DETAIL_PS)
    except Exception as exc:
        _logger.warning("Unable to query connection profile details: %s", exc)
        return set()

    aliases: Set[str] = set()
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("\t")
        if len(parts) != 3:
            continue
        alias, category, ipv4 = parts
        if category != NetCategory.PUBLIC and ipv4 == "Internet":
            aliases.add(alias)
    return aliases


def _active_profiles() -> Dict[str, str]:
    try:
        profiles = _parse_connection_profiles(
            _run_output(FirewallCommand.CONNECTION_PROFILE_PS)
        )
        if profiles:
            return profiles
    except Exception as exc:
        _logger.warning("Unable to query connection profiles: %s", exc)

    try:
        return _parse_profile_aliases(_run_output(FirewallCommand.CURRENT_PROFILE))
    except Exception as exc:
        _logger.warning("Unable to query firewall profile: %s", exc)
        return {}


def _aliases_for_host(host: str) -> Set[str]:
    if is_loopback_host(host):
        return set()
    try:
        output = _run_output(FirewallCommand.HOST_ALIAS_PS, host)
    except Exception as exc:
        _logger.warning("Unable to query host interface alias for %s: %s", host, exc)
        return set()
    return {line.strip() for line in output.splitlines() if line.strip()}


def _default_aliases() -> Set[str]:
    aliases = _non_public_internet_aliases()
    if aliases:
        return aliases

    try:
        output = _run_output(FirewallCommand.DEFAULT_ALIAS_PS)
    except Exception as exc:
        _logger.warning("Unable to query default route interface alias: %s", exc)
        return set()
    return {line.strip() for line in output.splitlines() if line.strip()}


def _local_host_for_remote(remote_ip: str) -> str:
    family = socket.AF_INET6 if ":" in remote_ip else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.connect((remote_ip, 9))
        return str(sock.getsockname()[0])


def _target_aliases(host: str) -> Set[str]:
    if is_loopback_host(host):
        return set()
    if is_wildcard_host(host):
        return _default_aliases()
    return _aliases_for_host(host)


def _has_public_alias(aliases: Set[str]) -> bool:
    if not aliases:
        return False
    profiles = _active_profiles()
    return any(profiles.get(alias) == NetCategory.PUBLIC for alias in aliases)


def is_public_profile_active(host: str = "") -> bool:
    """Return True when the target interface is using a Public profile."""
    if not is_windows():
        return False
    return _has_public_alias(_target_aliases(host))


def is_public_access(host: str, remote_ip: str) -> bool:
    """Return True when a client access is routed through a Public interface."""
    if not is_windows() or is_loopback_host(remote_ip):
        return False
    try:
        local_host = _local_host_for_remote(remote_ip)
    except Exception as exc:
        _logger.warning("Unable to resolve local host for remote %s: %s", remote_ip, exc)
        return False
    if not is_wildcard_host(host) and local_host != host:
        return False
    return _has_public_alias(_aliases_for_host(local_host))


def should_offer_firewall_access(host: str) -> bool:
    """Whether the UI should offer to request inbound firewall access."""
    return is_windows() and not is_loopback_host(host) and is_public_profile_active(host)


def should_request_firewall_access(host: str, remote_ip: str) -> bool:
    """Whether a real client access should trigger the deferred UAC request."""
    return is_windows() and is_public_access(host, remote_ip)


def request_firewall_access(port: int, rule_name: Optional[str] = None) -> Tuple[bool, str]:
    """
    Ask Windows UAC to add an inbound TCP allow rule for the configured port.

    Existing Python Public block rules for the same executable are removed first,
    because Windows block rules can take precedence over later allow rules.
    """
    if not is_windows():
        return False, "仅 Windows 支持自动配置防火墙规则"
    if port < 1 or port > 65535:
        return False, f"端口号无效: {port}"

    exe_path = sys.executable
    rule_name = rule_name or f"AbyssFS TCP {port}"
    commands = [
        [
            "netsh",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            "name=Python",
            "dir=in",
            f"program={exe_path}",
        ],
        [
            "netsh",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            "name=python.exe",
            "dir=in",
            f"program={exe_path}",
        ],
        [
            "netsh",
            "advfirewall",
            "firewall",
            "add",
            "rule",
            f"name={rule_name}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={port}",
            "profile=private,public",
            "enable=yes",
        ],
    ]
    cmd = "/c " + " & ".join(subprocess.list2cmdline(command) for command in commands)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "cmd.exe",
            cmd,
            None,
            1,
        )
    except Exception as exc:
        _logger.error("Failed to request firewall rule elevation: %s", exc)
        return False, f"无法打开管理员确认窗口: {exc}"
    if rc <= 32:
        return False, f"管理员确认窗口启动失败，ShellExecute 返回 {rc}"
    return True, "已请求管理员确认以添加防火墙入站规则"
