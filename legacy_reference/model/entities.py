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
import logging
from typing import Dict, List, Optional, Any

from model.permission import (
    FilePermission,
    to_ftp_perm_str,
    from_ftp_perm_str,
    sanitize_ftp_perm_str,
    FTP_VALID_CHARS,
)

_logger = logging.getLogger(__name__)


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
        invalid = self.INVALID_NAME_CHARS.intersection(self.display_name)
        if invalid:
            errors.append(f"虚拟目录名称包含非法字符: {''.join(sorted(invalid))}")
        if not self.real_path:
            errors.append("真实路径不能为空")
        elif not os.path.isabs(self.real_path):
            _logger.warning(f"虚拟目录使用相对路径: {self.real_path}")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "virtual_name": self.virtual_name,
            "real_path":    self.real_path,
            "perm":         self.perm,                       # FTP 字符串（向后兼容）
            "permission":   int(self._permission.value),     # 新格式
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VirtualDirectory":
        if not isinstance(data, dict):
            _logger.warning(f"无效的虚拟目录数据: {data}")
            return cls("", "", FilePermission.READONLY)

        # 新格式优先；回退到 FTP 字符串格式
        if "permission" in data:
            permission = FilePermission(data["permission"])
        elif "perm" in data:
            permission = from_ftp_perm_str(data["perm"]) or FilePermission.READONLY
        else:
            permission = FilePermission.READONLY

        return cls(
            virtual_name=data.get("virtual_name", ""),
            real_path=data.get("real_path", ""),
            permission=permission,
        )

    def __repr__(self) -> str:
        return (
            f"VirtualDirectory(virtual_name={self.virtual_name!r}, "
            f"real_path={self.real_path!r}, permission={self._permission!r})"
        )


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
        *,
        root_permission: Optional[FilePermission] = None,
    ):
        """
        Args:
            username:        用户名
            password:        密码（明文，建议外部加密后存储）
            root_dir:        根目录路径（为空时使用程序目录下的 root 文件夹）
            root_perm:       根目录 FTP 权限字符串（向后兼容）
            virtual_dirs:    虚拟目录列表
            root_permission: 根目录权限（FilePermission 枚举，优先于 root_perm）
        """
        self.username  = (username or "").strip()
        self.password  = password or ""
        self.root_dir  = (root_dir or "").strip()
        self.virtual_dirs: List[VirtualDirectory] = virtual_dirs or []

        if root_permission is not None:
            self._root_permission = root_permission
        else:
            cleaned = sanitize_ftp_perm_str(root_perm)
            self._root_permission = from_ftp_perm_str(cleaned) or FilePermission.READONLY

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
        seen: set = set()
        for i, vdir in enumerate(self.virtual_dirs):
            name = vdir.get_display_name()
            if name in seen:
                errors.append(f"虚拟目录名称重复: {name}")
            seen.add(name)
            for err in vdir.validate():
                errors.append(f"虚拟目录[{i + 1}] {name}: {err}")
        return errors

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
            "virtual_dirs":    [vd.to_dict() for vd in self.virtual_dirs],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        if not isinstance(data, dict):
            raise ValueError("无效的用户配置数据")

        root_dir = data.get("root_dir") or data.get("homedir", "")

        # 新格式优先；回退到 FTP 字符串格式
        if "root_permission" in data:
            root_permission = FilePermission(data["root_permission"])
            root_perm_arg   = to_ftp_perm_str(root_permission)
        else:
            root_perm_arg   = data.get("root_perm") or data.get("perm", "elr")
            root_permission = None

        virtual_dirs: List[VirtualDirectory] = []
        for vd_data in data.get("virtual_dirs", []):
            try:
                vdir = VirtualDirectory.from_dict(vd_data)
                if vdir.real_path:
                    virtual_dirs.append(vdir)
            except Exception as e:
                _logger.warning(f"解析虚拟目录失败: {vd_data}, 错误: {e}")

        return cls(
            username=data.get("username", ""),
            password=data.get("password", ""),
            root_dir=root_dir,
            root_perm=root_perm_arg,
            virtual_dirs=virtual_dirs,
            root_permission=root_permission,
        )

    def __repr__(self) -> str:
        return (
            f"UserProfile(username={self.username!r}, "
            f"root_dir={self.root_dir!r}, "
            f"root_permission={self._root_permission!r}, "
            f"virtual_dirs={len(self.virtual_dirs)})"
        )
