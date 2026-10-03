"""
文件服务器实体模型

提供与传输协议无关的领域对象：
  - VirtualDirectory: 虚拟目录配置（虚拟名 → 真实路径 + 权限）
  - UserProfile: 用户配置（认证信息 + 根目录 + 虚拟目录列表）

向后兼容说明
----------
VirtualDirectory 和 UserProfile 保留了以下 FTP 字符串风格的兼容接口：
  - VirtualDirectory.perm          → FTP 权限字符串（如 "elr"）
  - VirtualDirectory.get_display_name() → 可调用方法
  - UserProfile.root_perm          → FTP 权限字符串
  - UserProfile.homedir / .perm    → 旧版兼容属性
"""

from __future__ import annotations

import os
import re
import struct
import logging
from typing import Dict, List, Optional, Tuple, Any, Union

from abyssfs import ospath
from abyssfs.model.permission import (
    FilePermission,
    to_ftp_perm_str,
    from_ftp_perm_str,
    from_permission_value,
    sanitize_ftp_perm_str,
    FTP_VALID_CHARS,
)

_logger = logging.getLogger(__name__)


from enum import Enum


def _rol32(value: int, count: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _md4_digest(data: bytes) -> bytes:
    """Return the MD4 digest used by NTLM password hashes."""
    msg = bytearray(data)
    bit_len = (8 * len(msg)) & 0xFFFFFFFFFFFFFFFF
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += struct.pack("<Q", bit_len)

    a = 0x67452301
    b = 0xEFCDAB89
    c = 0x98BADCFE
    d = 0x10325476

    def f(x: int, y: int, z: int) -> int:
        return ((x & y) | (~x & z)) & 0xFFFFFFFF

    def g(x: int, y: int, z: int) -> int:
        return ((x & y) | (x & z) | (y & z)) & 0xFFFFFFFF

    def h(x: int, y: int, z: int) -> int:
        return (x ^ y ^ z) & 0xFFFFFFFF

    for offset in range(0, len(msg), 64):
        x = list(struct.unpack("<16I", msg[offset:offset + 64]))
        aa, bb, cc, dd = a, b, c, d

        for i in range(16):
            s = (3, 7, 11, 19)[i % 4]
            if i % 4 == 0:
                a = _rol32((a + f(b, c, d) + x[i]) & 0xFFFFFFFF, s)
            elif i % 4 == 1:
                d = _rol32((d + f(a, b, c) + x[i]) & 0xFFFFFFFF, s)
            elif i % 4 == 2:
                c = _rol32((c + f(d, a, b) + x[i]) & 0xFFFFFFFF, s)
            else:
                b = _rol32((b + f(c, d, a) + x[i]) & 0xFFFFFFFF, s)

        for i, k in enumerate((0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15)):
            s = (3, 5, 9, 13)[i % 4]
            if i % 4 == 0:
                a = _rol32((a + g(b, c, d) + x[k] + 0x5A827999) & 0xFFFFFFFF, s)
            elif i % 4 == 1:
                d = _rol32((d + g(a, b, c) + x[k] + 0x5A827999) & 0xFFFFFFFF, s)
            elif i % 4 == 2:
                c = _rol32((c + g(d, a, b) + x[k] + 0x5A827999) & 0xFFFFFFFF, s)
            else:
                b = _rol32((b + g(c, d, a) + x[k] + 0x5A827999) & 0xFFFFFFFF, s)

        for i, k in enumerate((0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)):
            s = (3, 9, 11, 15)[i % 4]
            if i % 4 == 0:
                a = _rol32((a + h(b, c, d) + x[k] + 0x6ED9EBA1) & 0xFFFFFFFF, s)
            elif i % 4 == 1:
                d = _rol32((d + h(a, b, c) + x[k] + 0x6ED9EBA1) & 0xFFFFFFFF, s)
            elif i % 4 == 2:
                c = _rol32((c + h(d, a, b) + x[k] + 0x6ED9EBA1) & 0xFFFFFFFF, s)
            else:
                b = _rol32((b + h(c, d, a) + x[k] + 0x6ED9EBA1) & 0xFFFFFFFF, s)

        a = (a + aa) & 0xFFFFFFFF
        b = (b + bb) & 0xFFFFFFFF
        c = (c + cc) & 0xFFFFFFFF
        d = (d + dd) & 0xFFFFFFFF

    return struct.pack("<4I", a, b, c, d)


def nt_hash_password(password: str) -> str:
    """Return the NT hash hex string required by SMB/NTLM authentication."""
    return _md4_digest((password or "").encode("utf-16le")).hex().upper()


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled", "开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled", "关闭"}:
            return False
    return bool(value)

class IpRuleType(Enum):
    WILDCARD = "wildcard"
    CIDR     = "cidr"


class VirtualDirectoryValidation(Enum):
    OPTIONAL = "optional"
    REQUIRED = "required"

    @classmethod
    def from_value(cls, value: Any) -> "VirtualDirectoryValidation":
        try:
            return cls(value)
        except Exception:
            return cls.OPTIONAL

class IpRule:
    """IP 过滤规则实体。"""
    def __init__(self, pattern: str, rule_type: IpRuleType = IpRuleType.WILDCARD, enabled: bool = True):
        self.pattern   = pattern.strip()
        self.rule_type = rule_type
        self.enabled   = enabled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern":   self.pattern,
            "rule_type": self.rule_type.value,
            "enabled":   self.enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IpRule":
        return cls(
            pattern=data.get("pattern", ""),
            rule_type=IpRuleType(data.get("rule_type", "wildcard")),
            enabled=_coerce_bool(data.get("enabled", True), True)
        )

# =============================================================================
# VirtualDirectory（虚拟目录配置）
# =============================================================================

class VirtualDirectory:
    """
    虚拟目录配置对象。

    将一个虚拟名称映射到真实文件系统路径，并绑定独立的访问权限。

    权限接受两种风格：
      - 新式：FilePermission 枚举（第三个位置参数）
      - 兼容：perm= 关键字参数，传入 FTP 权限字符串（如 "elrw"）
    """

    # 有效的 FTP 权限字符集合（向后兼容：供外部代码使用）
    VALID_PERMS: frozenset = FTP_VALID_CHARS

    # 虚拟目录名称的非法字符（Windows 文件系统保留字符）
    INVALID_NAME_CHARS: frozenset = frozenset('/\\:*?"<>|')

    def __init__(
        self,
        virtual_name: str,
        real_path: str,
        permission: FilePermission = FilePermission.READONLY,
        *,
        perm: Optional[str] = None,
        enabled: bool = True,
        validation_mode: Union[VirtualDirectoryValidation, str] = VirtualDirectoryValidation.OPTIONAL,
        source_type: str = "local",
    ):
        """
        Args:
            virtual_name: FTP 中显示的目录名（可为空，则自动取路径末段）
            real_path:    真实文件系统路径
            permission:   权限（FilePermission 枚举）
            perm:         【兼容参数】FTP 权限字符串，存在时优先于 permission
        """
        self.virtual_name = (virtual_name or "").strip()
        self.real_path    = (real_path or "").strip()
        self.enabled      = _coerce_bool(enabled, True)
        self.validation_mode = VirtualDirectoryValidation.from_value(validation_mode)
        self.source_type = (source_type or "local").strip().lower() or "local"

        if perm is not None:
            self._permission = from_ftp_perm_str(perm) or FilePermission.READONLY
        else:
            self._permission = permission

    # ------------------------------------------------------------------
    # 权限访问
    # ------------------------------------------------------------------

    @property
    def permission(self) -> FilePermission:
        """以 FilePermission 枚举形式返回权限。"""
        return self._permission

    @permission.setter
    def permission(self, value: FilePermission) -> None:
        self._permission = value

    @property
    def perm(self) -> str:
        """
        以 FTP 权限字符串形式返回权限（向后兼容属性）。
        供 pyftpdlib 的 DummyAuthorizer 及相关代码使用。
        """
        return to_ftp_perm_str(self._permission)

    @perm.setter
    def perm(self, value: str) -> None:
        """接受 FTP 权限字符串并转换为内部 FilePermission（向后兼容）。"""
        self._permission = from_ftp_perm_str(value) or FilePermission.READONLY

    # ------------------------------------------------------------------
    # 显示名称
    # ------------------------------------------------------------------

    @property
    def display_name(self) -> str:
        """获取显示名称（虚拟名优先；未设置则使用路径末段文件夹名）。"""
        if self.virtual_name:
            return self.virtual_name
        return os.path.basename(self.real_path.rstrip('/\\')) or "root"

    def get_display_name(self) -> str:
        """获取显示名称（可调用方法，向后兼容旧版接口）。"""
        return self.display_name

    # ------------------------------------------------------------------
    # 静态工具（向后兼容）
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_perm(perm: str) -> str:
        """清理 FTP 权限字符串（向后兼容静态方法）。"""
        return sanitize_ftp_perm_str(perm)

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.enabled:
            return errors
        invalid = self.INVALID_NAME_CHARS.intersection(self.display_name)
        if invalid:
            errors.append(f"虚拟目录名称包含非法字符: {''.join(sorted(invalid))}")
        if not self.real_path:
            if self.validation_mode == VirtualDirectoryValidation.REQUIRED:
                errors.append("真实路径不能为空")
        elif not os.path.isabs(self.real_path):
            _logger.warning(f"虚拟目录使用相对路径: {self.real_path}")
        if self.validation_mode == VirtualDirectoryValidation.REQUIRED:
            if not self.has_valid_path():
                errors.append(f"真实路径不存在或不是目录: {self.real_path}")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    def is_required(self) -> bool:
        return self.validation_mode == VirtualDirectoryValidation.REQUIRED

    def has_valid_path(self) -> bool:
        if self.source_type != "local":
            return False
        return bool(self.real_path) and ospath.isdir(self.real_path)

    def is_mountable(self) -> bool:
        return self.enabled and self.has_valid_path()

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "virtual_name": self.virtual_name,
            "real_path":    self.real_path,
            "perm":         self.perm,                       # FTP 字符串（向后兼容）
            "permission":   int(self._permission.value),     # 新格式
            "enabled":      self.enabled,
            "validation_mode": self.validation_mode.value,
            "source_type":  self.source_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VirtualDirectory":
        if not isinstance(data, dict):
            _logger.warning(f"无效的虚拟目录数据: {data}")
            return cls("", "", FilePermission.READONLY)

        # 新格式优先；回退到 FTP 字符串格式
        try:
            if "permission" in data:
                permission = from_permission_value(data["permission"])
            elif "perm" in data:
                permission = from_ftp_perm_str(data["perm"]) or FilePermission.READONLY
            else:
                permission = FilePermission.READONLY
        except Exception:
            _logger.warning(f"虚拟目录权限配置无法识别，回退为只读: {data}")
            permission = FilePermission.READONLY

        validation_mode = data.get(
            "validation_mode",
            data.get("validation", data.get("path_validation", VirtualDirectoryValidation.OPTIONAL.value)),
        )
        if "required" in data and _coerce_bool(data.get("required"), False):
            validation_mode = VirtualDirectoryValidation.REQUIRED.value

        return cls(
            virtual_name=data.get("virtual_name", ""),
            real_path=data.get("real_path", ""),
            permission=permission,
            enabled=_coerce_bool(data.get("enabled", data.get("is_enabled", True)), True),
            validation_mode=validation_mode,
            source_type=data.get("source_type", "local"),
        )

    def __repr__(self) -> str:
        return (
            f"VirtualDirectory(virtual_name={self.virtual_name!r}, "
            f"real_path={self.real_path!r}, permission={self._permission!r}, "
            f"enabled={self.enabled!r}, validation_mode={self.validation_mode.value!r})"
        )


class VirtualDirectorySnapshot:
    """一次配置加载周期内的虚拟目录挂载结果。"""

    def __init__(
        self,
        active: List[VirtualDirectory],
        disabled: List[VirtualDirectory],
        optional_invalid: List[VirtualDirectory],
        required_invalid: List[VirtualDirectory],
    ) -> None:
        self.active: Tuple[VirtualDirectory, ...] = tuple(active)
        self.disabled: Tuple[VirtualDirectory, ...] = tuple(disabled)
        self.optional_invalid: Tuple[VirtualDirectory, ...] = tuple(optional_invalid)
        self.required_invalid: Tuple[VirtualDirectory, ...] = tuple(required_invalid)


# =============================================================================
# UserProfile（用户配置）
# =============================================================================

class UserProfile:
    """
    用户配置对象，封装单个用户的认证信息与目录访问配置。

    根目录权限接受两种风格：
      - 新式：root_permission= 关键字参数，传入 FilePermission
      - 兼容：root_perm= 位置/关键字参数，传入 FTP 权限字符串（如 "elr"）
    """

    USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')

    def __init__(
        self,
        username: str,
        password: str,
        root_dir: str = "",
        root_perm: str = "elr",
        virtual_dirs: Optional[List[VirtualDirectory]] = None,
        ip_rules: Optional[List[IpRule]] = None,
        allow_all_ips: bool = True,
        *,
        root_permission: Optional[FilePermission] = None,
        smb_nt_hash: Optional[str] = None,
    ):
        """
        Args:
            username:        用户名
            password:        密码
            root_dir:        根目录路径
            root_perm:       根目录 FTP 权限字符串
            virtual_dirs:    虚拟目录列表
            ip_rules:        IP 过滤规则列表
            root_permission: 根目录权限（FilePermission 枚举）
        """
        raw_password = password or ""
        self.username  = (username or "").strip()
        self.password  = self.hash_password(raw_password)
        self.root_dir  = (root_dir or "").strip()
        self.virtual_dirs: List[VirtualDirectory] = virtual_dirs or []
        self._virtual_dir_snapshot: Optional[VirtualDirectorySnapshot] = None
        self.ip_rules:     List[IpRule]           = ip_rules or []
        self.allow_all_ips = allow_all_ips
        self.smb_nt_hash = (smb_nt_hash or "").strip().upper()
        if raw_password and not raw_password.startswith("sha256$"):
            self.smb_nt_hash = nt_hash_password(raw_password)

        if root_permission is not None:
            self._root_permission = root_permission
        else:
            cleaned = sanitize_ftp_perm_str(root_perm)
            self._root_permission = from_ftp_perm_str(cleaned) or FilePermission.READONLY

    # ------------------------------------------------------------------
    # 密码与安全逻辑
    # ------------------------------------------------------------------

    @staticmethod
    def hash_password(password: str) -> str:
        if not password or password.startswith("sha256$"):
            return password
        import hashlib, secrets
        salt = secrets.token_hex(8)
        h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        return f"sha256${salt}${h}"

    def verify_password(self, password: str) -> bool:
        if self.password.startswith("sha256$"):
            import hashlib
            parts = self.password.split("$", 2)
            if len(parts) == 3:
                _, salt, h = parts
                return hashlib.sha256((salt + password).encode('utf-8')).hexdigest() == h
        return self.password == password

    # ------------------------------------------------------------------
    # IP 检查逻辑
    # ------------------------------------------------------------------

    def check_ip(self, remote_ip: str) -> bool:
        """
        检查远程 IP 是否符合规则。
        允许所有 IP 时直接放行；否则必须命中至少一条启用规则。
        """
        if self.allow_all_ips:
            return True
            
        enabled_rules = [r for r in self.ip_rules if r.enabled]
        if not enabled_rules:
            return False

        import fnmatch
        import ipaddress

        for rule in enabled_rules:
            try:
                if rule.rule_type == IpRuleType.WILDCARD:
                    if fnmatch.fnmatch(remote_ip, rule.pattern):
                        return True
                elif rule.rule_type == IpRuleType.CIDR:
                    if ipaddress.ip_address(remote_ip) in ipaddress.ip_network(rule.pattern):
                        return True
            except Exception as e:
                _logger.warning(f"IP 规则匹配异常 ({rule.pattern}): {e}")
        
        return False

    # ------------------------------------------------------------------
    # 权限访问
    # ------------------------------------------------------------------

    @property
    def root_permission(self) -> FilePermission:
        """根目录权限（FilePermission 枚举）。"""
        return self._root_permission

    @root_permission.setter
    def root_permission(self, value: FilePermission) -> None:
        self._root_permission = value

    @property
    def root_perm(self) -> str:
        """根目录 FTP 权限字符串（向后兼容属性）。"""
        return to_ftp_perm_str(self._root_permission)

    @root_perm.setter
    def root_perm(self, value: str) -> None:
        self._root_permission = from_ftp_perm_str(value) or FilePermission.READONLY

    # ------------------------------------------------------------------
    # 旧版兼容属性
    # ------------------------------------------------------------------

    @property
    def homedir(self) -> str:
        """兼容旧版 homedir 属性。"""
        return self.root_dir

    @property
    def perm(self) -> str:
        """兼容旧版 perm 属性（等同于 root_perm）。"""
        return self.root_perm

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.username:
            errors.append("用户名不能为空")
        elif not self.USERNAME_PATTERN.match(self.username):
            errors.append("用户名只能包含字母、数字、下划线、连字符和点")
        if not self.password:
            errors.append("密码不能为空")
        if not self.root_dir:
            errors.append("根目录不能为空")
        
        import ipaddress
        for rule in self.ip_rules:
            if rule.rule_type == IpRuleType.CIDR:
                try:
                    ipaddress.ip_network(rule.pattern)
                except Exception:
                    errors.append(f"无效的 CIDR 格式: {rule.pattern}")

        seen: set = set()
        for i, vdir in enumerate(self.virtual_dirs):
            if not vdir.enabled:
                continue
            if not vdir.has_valid_path() and not vdir.is_required():
                continue
            name = vdir.get_display_name()
            if name in seen:
                errors.append(f"虚拟目录名称重复: {name}")
            seen.add(name)
            for err in vdir.validate():
                errors.append(f"虚拟目录[{i + 1}] {name}: {err}")
        return errors

    def resolve_virtual_dirs(self, *, refresh: bool = False) -> VirtualDirectorySnapshot:
        if self._virtual_dir_snapshot is not None and not refresh:
            return self._virtual_dir_snapshot

        active: List[VirtualDirectory] = []
        disabled: List[VirtualDirectory] = []
        optional_invalid: List[VirtualDirectory] = []
        required_invalid: List[VirtualDirectory] = []
        for vdir in self.virtual_dirs:
            if not vdir.enabled:
                disabled.append(vdir)
                continue
            if vdir.has_valid_path():
                active.append(vdir)
                continue
            target = required_invalid if vdir.is_required() else optional_invalid
            target.append(vdir)

        snapshot = VirtualDirectorySnapshot(
            active,
            disabled,
            optional_invalid,
            required_invalid,
        )
        self._virtual_dir_snapshot = snapshot

        for vdir in snapshot.disabled:
            _logger.info(
                f"虚拟目录已关闭，配置加载时跳过: user={self.username!r}, "
                f"name={vdir.get_display_name()!r}"
            )
        for vdir in snapshot.optional_invalid:
            _logger.warning(
                f"可选虚拟目录路径无效，配置加载时跳过: user={self.username!r}, "
                f"name={vdir.get_display_name()!r}, path={vdir.real_path!r}"
            )
        return snapshot

    def get_mountable_virtual_dirs(
        self,
        *,
        strict_required: bool = True,
        refresh: bool = False,
    ) -> List[VirtualDirectory]:
        snapshot = self.resolve_virtual_dirs(refresh=refresh)
        if strict_required and snapshot.required_invalid:
            vdir = snapshot.required_invalid[0]
            raise FileNotFoundError(
                f"必需虚拟目录路径无效: {vdir.get_display_name()} -> {vdir.real_path}"
            )
        return list(snapshot.active)

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username":        self.username,
            "password":        self.password,
            "root_dir":        self.root_dir,
            "root_perm":       self.root_perm,               # FTP 字符串（向后兼容）
            "root_permission": int(self._root_permission.value),  # 新格式
            "smb_nt_hash":     self.smb_nt_hash,
            "ip_rules":        [r.to_dict() for r in self.ip_rules],
            "allow_all_ips":   self.allow_all_ips,
            "virtual_dirs":    [vd.to_dict() for vd in self.virtual_dirs],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        if not isinstance(data, dict):
            raise ValueError("无效的用户配置数据")

        root_dir = data.get("root_dir") or data.get("homedir", "")

        # 新格式优先；回退到 FTP 字符串格式
        try:
            if "root_permission" in data:
                root_permission = from_permission_value(data["root_permission"])
            else:
                root_perm_arg   = data.get("root_perm") or data.get("perm", "elr")
                root_permission = from_ftp_perm_str(root_perm_arg) or FilePermission.READONLY
        except Exception:
            _logger.warning(f"用户根目录权限配置无法识别，回退为只读: {data}")
            root_permission = FilePermission.READONLY

        virtual_dirs: List[VirtualDirectory] = []
        for vd_data in data.get("virtual_dirs", []):
            try:
                vdir = VirtualDirectory.from_dict(vd_data)
                virtual_dirs.append(vdir)
            except Exception as e:
                _logger.warning(f"解析虚拟目录失败: {vd_data}, 错误: {e}")

        ip_rules: List[IpRule] = []
        if "ip_rules" in data:
            for r_data in data["ip_rules"]:
                ip_rules.append(IpRule.from_dict(r_data))
        elif "allowed_ips" in data:
            # 兼容旧格式
            old_ips = data["allowed_ips"]
            if isinstance(old_ips, list):
                for ip in old_ips:
                    ip_rules.append(IpRule(pattern=ip, rule_type=IpRuleType.WILDCARD))
            elif isinstance(old_ips, str):
                for ip in old_ips.split(","):
                    if ip.strip():
                        ip_rules.append(IpRule(pattern=ip.strip(), rule_type=IpRuleType.WILDCARD))

        return cls(
            username=data.get("username", ""),
            password=data.get("password", ""),
            root_dir=root_dir,
            virtual_dirs=virtual_dirs,
            root_permission=root_permission,
            ip_rules=ip_rules,
            allow_all_ips=_coerce_bool(data.get("allow_all_ips", True), True),
            smb_nt_hash=data.get("smb_nt_hash", ""),
        )

    def __repr__(self) -> str:
        return (
            f"UserProfile(username={self.username!r}, "
            f"root_dir={self.root_dir!r}, "
            f"root_permission={self._root_permission!r}, "
            f"virtual_dirs={len(self.virtual_dirs)})"
        )
