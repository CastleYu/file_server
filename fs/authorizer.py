"""
FTP 用户认证器

提供 _VirtualDirAuthorizer（适配 pyftpdlib DummyAuthorizer），
支持按虚拟目录独立检查权限，并缓存 UserProfile 供 VirtualFS 登录后读取。
"""

from __future__ import annotations

import os
import logging
import traceback
from typing import Dict, Optional, Any

from model.entities import UserProfile

_logger = logging.getLogger(__name__)


class _VirtualDirAuthorizer:
    """
    FTP 用户认证器（适配 pyftpdlib DummyAuthorizer）。

    在标准 DummyAuthorizer 之上支持按虚拟目录独立检查权限，
    并缓存 UserProfile 供 VirtualFS 在登录后读取。
    """

    def __init__(self) -> None:
        from pyftpdlib.authorizers import DummyAuthorizer
        self._auth = DummyAuthorizer()
        self._profiles: Dict[str, UserProfile] = {}
        _logger.debug("[_VirtualDirAuthorizer] 初始化完成")

    def add_user_profile(self, profile: UserProfile) -> None:
        _logger.info(
            f"[_VirtualDirAuthorizer] add_user_profile: "
            f"username={profile.username!r}, root_dir={profile.root_dir!r}, "
            f"root_perm={profile.root_perm!r}, vdirs={len(profile.virtual_dirs)}"
        )
        try:
            if not os.path.exists(profile.root_dir):
                os.makedirs(profile.root_dir, exist_ok=True)
                _logger.info(
                    f"[_VirtualDirAuthorizer] 根目录已创建: {profile.root_dir!r}"
                )
            for vd in profile.virtual_dirs:
                if vd.real_path and not os.path.exists(vd.real_path):
                    os.makedirs(vd.real_path, exist_ok=True)
                    _logger.info(
                        f"[_VirtualDirAuthorizer] 虚拟目录已创建: "
                        f"{vd.get_display_name()!r} → {vd.real_path!r}"
                    )

            self._auth.add_user(
                profile.username,
                profile.password,
                profile.root_dir,
                perm=profile.root_perm,
            )
            self._profiles[profile.username] = profile
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
        result = self._profiles.get(username)
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
        result = self._auth.get_home_dir(username)
        _logger.debug(
            f"[_VirtualDirAuthorizer] get_home_dir({username!r}): {result!r}"
        )
        return result

    def has_user(self, username: str) -> bool:
        result = self._auth.has_user(username)
        _logger.debug(
            f"[_VirtualDirAuthorizer] has_user({username!r}): {result}"
        )
        return result

    def has_perm(self, username: str, perm: str, path: Optional[str] = None) -> bool:
        profile = self._profiles.get(username)
        if not profile:
            result = self._auth.has_perm(username, perm, path)
            _logger.debug(
                f"[_VirtualDirAuthorizer] has_perm (no profile) "
                f"{username!r}, perm={perm!r}, path={path!r} → {result}"
            )
            return result

        if path is None:
            result = perm in profile.root_perm
            _logger.debug(
                f"[_VirtualDirAuthorizer] has_perm (root) "
                f"{username!r}, perm={perm!r} → {result}"
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
            result = None
            for vd in profile.virtual_dirs:
                if vd.get_display_name() == first:
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
        profile = self._profiles.get(username)
        if not profile:
            result = self._auth.get_perms(username)
            _logger.debug(
                f"[_VirtualDirAuthorizer] get_perms (no profile) "
                f"{username!r} → {result!r}"
            )
            return result
        all_perms: set = set(profile.root_perm)
        for vd in profile.virtual_dirs:
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
