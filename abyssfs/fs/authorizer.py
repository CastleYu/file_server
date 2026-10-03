"""
FTP 用户认证器

提供 _VirtualDirAuthorizer（适配 pyftpdlib DummyAuthorizer），
支持按虚拟目录独立检查权限，并缓存 UserProfile 供 VirtualFS 登录后读取。
"""

from __future__ import annotations

import os
import logging
import traceback
from typing import Dict, List, Optional, Any

from abyssfs import ospath
from abyssfs.model.entities import UserProfile
from abyssfs.service.registry import DirectoryRegistry

_logger = logging.getLogger(__name__)


class _VirtualDirAuthorizer:
    """
    FTP 用户认证器（适配 pyftpdlib DummyAuthorizer）。

    在标准 DummyAuthorizer 之上支持按虚拟目录独立检查权限，
    并缓存 UserProfile 供 VirtualFS 在登录后读取。
    """

    def __init__(self, registry: Optional[DirectoryRegistry] = None) -> None:
        from pyftpdlib.authorizers import DummyAuthorizer
        self._auth = DummyAuthorizer()
        self._registry = registry or DirectoryRegistry()
        self._profiles: Dict[str, UserProfile] = {}
        _logger.debug("[_VirtualDirAuthorizer] 初始化完成")

    @property
    def registry(self) -> DirectoryRegistry:
        return self._registry

    def replace_profiles(
        self,
        profiles: List[UserProfile],
        *,
        update_registry: bool = True,
    ) -> int:
        from pyftpdlib.authorizers import DummyAuthorizer

        if update_registry:
            snapshot = self._registry.replace(profiles, refresh=True)
        else:
            snapshot = self._registry.snapshot()

        auth = DummyAuthorizer()
        mapped: Dict[str, UserProfile] = {}
        for entry in snapshot.users.values():
            profile = entry.profile
            if not ospath.exists(profile.root_dir):
                ospath.makedirs(profile.root_dir, exist_ok=True)
            homedir = profile.root_dir
            if not os.path.isdir(homedir) and ospath.isdir(homedir):
                homedir = ospath.to_os_path(homedir)
            auth.add_user(
                profile.username,
                profile.password,
                homedir,
                perm=profile.root_perm,
            )
            mapped[profile.username] = profile

        self._auth = auth
        self._profiles = mapped
        return snapshot.version

    def add_user_profile(self, profile: UserProfile) -> None:
        _logger.info(
            f"[_VirtualDirAuthorizer] add_user_profile: "
            f"username={profile.username!r}, root_dir={profile.root_dir!r}, "
            f"root_perm={profile.root_perm!r}, vdirs={len(profile.virtual_dirs)}"
        )
        try:
            profiles = [
                current for current in self._registry.profiles()
                if current.username != profile.username
            ]
            profiles.append(profile)
            self.replace_profiles(profiles)
            _logger.info(
                f"[_VirtualDirAuthorizer] 用户已添加到认证器: "
                f"{profile.username!r}"
            )
        except Exception as exc:
            _logger.error(
                f"[_VirtualDirAuthorizer] add_user_profile() 异常 "
                f"({profile.username!r}): {exc}\n{traceback.format_exc()}"
            )
            raise

    def get_user_profile(self, username: str) -> Optional[UserProfile]:
        entry = self._registry.user(username)
        result = entry.profile if entry else None
        _logger.debug(
            f"[_VirtualDirAuthorizer] get_user_profile({username!r}): "
            f"{'found' if result else 'not found'}"
        )
        return result

    # --- pyftpdlib authorizer 协议 ---

    def validate_authentication(self, username: str, password: str, handler: Any) -> None:
        from pyftpdlib.authorizers import AuthenticationFailed

        _logger.info(
            f"[_VirtualDirAuthorizer] validate_authentication: "
            f"username={username!r}, remote_ip={handler.remote_ip}"
        )

        profile = self.get_user_profile(username)
        if profile:
            if not profile.check_ip(handler.remote_ip):
                _logger.warning(
                    f"[_VirtualDirAuthorizer] IP 过滤拒绝: "
                    f"user={username!r}, ip={handler.remote_ip}"
                )
                raise AuthenticationFailed("IP address not allowed")
                
            if not profile.verify_password(password):
                _logger.warning(
                    f"[_VirtualDirAuthorizer] 密码验证失败: "
                    f"user={username!r}"
                )
                raise AuthenticationFailed("Authentication failed.")
            
            _logger.info(
                f"[_VirtualDirAuthorizer] 认证成功 (Hash匹配): {username!r}"
            )
            return

        try:
            self._auth.validate_authentication(username, password, handler)
            _logger.info(
                f"[_VirtualDirAuthorizer] 认证成功 (Fallback): {username!r}"
            )
        except Exception as exc:
            _logger.warning(
                f"[_VirtualDirAuthorizer] 认证失败: "
                f"{username!r}, {exc}"
            )
            raise

    def get_home_dir(self, username: str) -> str:
        profile = self.get_user_profile(username)
        result = profile.root_dir if profile else self._auth.get_home_dir(username)
        result = ospath.strip_extended(result)
        _logger.debug(
            f"[_VirtualDirAuthorizer] get_home_dir({username!r}): {result!r}"
        )
        return result

    def has_user(self, username: str) -> bool:
        result = self._registry.user(username) is not None
        _logger.debug(
            f"[_VirtualDirAuthorizer] has_user({username!r}): {result}"
        )
        return result

    def has_perm(self, username: str, perm: str, path: Optional[str] = None) -> bool:
        profile = self.get_user_profile(username)
        if not profile:
            result = self._auth.has_perm(username, perm, path)
            _logger.debug(
                f"[_VirtualDirAuthorizer] has_perm (no profile) "
                f"{username!r}, perm={perm!r}, path={path!r} → {result}"
            )
            return result

        def _norm_real(value: str) -> str:
            return os.path.normcase(ospath.realpath(os.path.normpath(value)))

        def _is_within(value: str, root: str) -> bool:
            try:
                nvalue = _norm_real(value)
                nroot = _norm_real(root)
                return nvalue == nroot or nvalue.startswith(nroot + os.sep)
            except Exception:
                return False

        if path is None:
            result = perm in profile.root_perm
            _logger.debug(
                f"[_VirtualDirAuthorizer] has_perm (root) "
                f"{username!r}, perm={perm!r} → {result}"
            )
            return result

        if os.path.isabs(path):
            for vd in profile.get_mountable_virtual_dirs(strict_required=False):
                if _is_within(path, vd.real_path):
                    result = perm in vd.perm
                    _logger.debug(
                        f"[_VirtualDirAuthorizer] has_perm (vdir real path) "
                        f"{username!r}, perm={perm!r}, path={path!r} → {result}"
                    )
                    return result
            if _is_within(path, profile.root_dir):
                result = perm in profile.root_perm
                _logger.debug(
                    f"[_VirtualDirAuthorizer] has_perm (root real path) "
                    f"{username!r}, perm={perm!r}, path={path!r} → {result}"
                )
                return result

        ftp_path = path.replace("\\", "/")
        if not ftp_path.startswith("/"):
            ftp_path = "/" + ftp_path
        parts = [p for p in ftp_path.split("/") if p]
        if not parts:
            result = perm in profile.root_perm
        else:
            first  = parts[0]
            marked_first = first[1:] if first.startswith("#") else first
            result = None
            for vd in profile.get_mountable_virtual_dirs(strict_required=False):
                if vd.get_display_name() in (first, marked_first):
                    result = perm in vd.perm
                    _logger.debug(
                        f"[_VirtualDirAuthorizer] has_perm (vdir {first!r}) "
                        f"{username!r}, perm={perm!r} → {result}"
                    )
                    break
            if result is None:
                result = perm in profile.root_perm
                _logger.debug(
                    f"[_VirtualDirAuthorizer] has_perm (root fallback) "
                    f"{username!r}, perm={perm!r}, path={path!r} → {result}"
                )

        return result

    def get_perms(self, username: str) -> str:
        profile = self.get_user_profile(username)
        if not profile:
            result = self._auth.get_perms(username)
            _logger.debug(
                f"[_VirtualDirAuthorizer] get_perms (no profile) "
                f"{username!r} → {result!r}"
            )
            return result
        all_perms: set = set(profile.root_perm)
        for vd in profile.get_mountable_virtual_dirs(strict_required=False):
            all_perms.update(vd.perm)
        result = "".join(sorted(all_perms))
        _logger.debug(
            f"[_VirtualDirAuthorizer] get_perms {username!r} → {result!r}"
        )
        return result

    def get_msg_login(self, username: str) -> str:
        return self._auth.get_msg_login(username)

    def get_msg_quit(self, username: str) -> str:
        return self._auth.get_msg_quit(username)

    def impersonate_user(self, username: str, password: str) -> None:
        _logger.debug(f"[_VirtualDirAuthorizer] impersonate_user: {username!r}")
        self._auth.impersonate_user(username, password)

    def terminate_impersonation(self, username: str) -> None:
        _logger.debug(f"[_VirtualDirAuthorizer] terminate_impersonation: {username!r}")
        self._auth.terminate_impersonation(username)


# 公开别名
VirtualDirAuthorizer = _VirtualDirAuthorizer
