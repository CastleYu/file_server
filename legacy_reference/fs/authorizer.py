"""
FTP 用户认证器

提供 _VirtualDirAuthorizer（适配 pyftpdlib DummyAuthorizer），
支持按虚拟目录独立检查权限，并缓存 UserProfile 供 VirtualFS 登录后读取。
"""

from __future__ import annotations

import os
import logging
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
        self._auth    = DummyAuthorizer()
        self._profiles: Dict[str, UserProfile] = {}

    def add_user_profile(self, profile: UserProfile) -> None:
        if not os.path.exists(profile.root_dir):
            os.makedirs(profile.root_dir, exist_ok=True)
        for vd in profile.virtual_dirs:
            if vd.real_path and not os.path.exists(vd.real_path):
                os.makedirs(vd.real_path, exist_ok=True)
        self._auth.add_user(
            profile.username,
            profile.password,
            profile.root_dir,
            perm=profile.root_perm,
        )
        self._profiles[profile.username] = profile

    def get_user_profile(self, username: str) -> Optional[UserProfile]:
        return self._profiles.get(username)

    # --- pyftpdlib authorizer 协议 ---

    def validate_authentication(self, username: str, password: str, handler: Any) -> None:
        self._auth.validate_authentication(username, password, handler)

    def get_home_dir(self, username: str) -> str:
        return self._auth.get_home_dir(username)

    def has_user(self, username: str) -> bool:
        return self._auth.has_user(username)

    def has_perm(self, username: str, perm: str, path: Optional[str] = None) -> bool:
        profile = self._profiles.get(username)
        if not profile:
            return self._auth.has_perm(username, perm, path)
        if path is None:
            return perm in profile.root_perm
        ftp_path = path.replace("\\", "/")
        if not ftp_path.startswith("/"):
            ftp_path = "/" + ftp_path
        parts = [p for p in ftp_path.split("/") if p]
        if not parts:
            return perm in profile.root_perm
        first = parts[0]
        for vd in profile.virtual_dirs:
            if vd.get_display_name() == first:
                return perm in vd.perm
        return perm in profile.root_perm

    def get_perms(self, username: str) -> str:
        profile = self._profiles.get(username)
        if not profile:
            return self._auth.get_perms(username)
        all_perms: set = set(profile.root_perm)
        for vd in profile.virtual_dirs:
            all_perms.update(vd.perm)
        return "".join(sorted(all_perms))

    def get_msg_login(self, username: str) -> str:
        return self._auth.get_msg_login(username)

    def get_msg_quit(self, username: str) -> str:
        return self._auth.get_msg_quit(username)

    def impersonate_user(self, username: str, password: str) -> None:
        self._auth.impersonate_user(username, password)

    def terminate_impersonation(self, username: str) -> None:
        self._auth.terminate_impersonation(username)


# 公开别名
VirtualDirAuthorizer = _VirtualDirAuthorizer
