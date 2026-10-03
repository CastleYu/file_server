"""
虚拟文件系统层

提供路径安全工具与 VirtualFS（pyftpdlib AbstractedFS 的虚拟目录实现）：
  - _DANGEROUS_PATH_RE / _is_safe_raw_path / is_safe_raw_path: 路径遍历防护
  - _build_virtual_mappings: 将 UserProfile.virtual_dirs 转为名称→VirtualDirectory 字典
  - VirtualFS: 全功能虚拟文件系统（含符号链接逃逸检查）
"""

from __future__ import annotations

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

from model.permission import FilePermission
from model.entities import VirtualDirectory, UserProfile

_logger = logging.getLogger(__name__)

# 危险路径模式（防路径遍历）
_DANGEROUS_PATH_RE = re.compile(r'\.\.[\\/]|[\\/]\.\.[\\/]|[\\/]\.\.$')


def _is_safe_raw_path(path: str) -> bool:
    """快速检查原始路径字符串是否包含路径遍历特征。"""
    return not bool(_DANGEROUS_PATH_RE.search(path or ""))


# 公开别名，供需要路径遍历检查的调用方使用
is_safe_raw_path = _is_safe_raw_path


def _build_virtual_mappings(user: UserProfile) -> Dict[str, VirtualDirectory]:
    """将 UserProfile 的虚拟目录列表转换为名称→VirtualDirectory 字典。"""
    return {vd.get_display_name(): vd for vd in user.virtual_dirs}


# =============================================================================
# VirtualFS（pyftpdlib AbstractedFS 子类）
# =============================================================================

from pyftpdlib.filesystems import AbstractedFS


class VirtualFS(AbstractedFS):
    """
    虚拟文件系统类（完整版，含符号链接逃逸检查）。

    通过继承 pyftpdlib 的 AbstractedFS，实现：
    - 将虚拟路径路由到真实文件系统路径
    - 在根目录下显示所有虚拟目录作为子目录
    - 支持单用户多文件夹的访问控制与路径遍历、符号链接逃逸防护
    """

    def __init__(self, root, cmd_channel):
        super().__init__(root, cmd_channel)
        self.virtual_mappings: Dict[str, VirtualDirectory] = {}
        self.user_profile: Optional[UserProfile] = None
        self._cmd_channel = cmd_channel
        _logger.info("VirtualFS 初始化: root=%s", root)
        self._try_load_user_profile()

    def _try_load_user_profile(self) -> None:
        from fs.authorizer import _VirtualDirAuthorizer
        if not hasattr(self._cmd_channel, "username") or not self._cmd_channel.username:
            _logger.debug("用户尚未登录，跳过加载用户配置")
            return
        username = self._cmd_channel.username
        authorizer = getattr(self._cmd_channel, "authorizer", None)
        if authorizer and isinstance(authorizer, _VirtualDirAuthorizer):
            profile = authorizer.get_user_profile(username)
            if profile:
                self.set_user_profile(profile)
                _logger.info("从 cmd_channel 加载了用户配置: %s", username)
            else:
                _logger.warning("未找到用户配置: %s", username)
        else:
            _logger.warning("authorizer 不是 VirtualDirAuthorizer")

    def set_user_profile(self, profile: UserProfile) -> None:
        self.user_profile = profile
        self.virtual_mappings.clear()
        for vdir in profile.virtual_dirs:
            self.virtual_mappings[vdir.get_display_name()] = vdir
        _logger.info("用户配置已加载: %s, 根目录: %s, 虚拟目录: %s",
                     profile.username, self.root, list(self.virtual_mappings.keys()))

    def _ensure_user_profile_loaded(self) -> None:
        if not self.user_profile and not self.virtual_mappings:
            self._try_load_user_profile()

    def _parse_virtual_path(self, path: str) -> Tuple[bool, Optional[str], str, Optional[VirtualDirectory]]:
        self._ensure_user_profile_loaded()
        path = path.replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        parts = [p for p in path.split("/") if p]
        if not parts:
            return (False, None, "/", None)
        first_part = parts[0]
        if first_part in self.virtual_mappings:
            vdir = self.virtual_mappings[first_part]
            sub_path = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"
            return (True, first_part, sub_path, vdir)
        return (False, None, path, None)

    def ftp2fs(self, ftppath: str) -> str:
        self._ensure_user_profile_loaded()
        if not _is_safe_raw_path(ftppath):
            _logger.warning("ftp2fs: 拒绝危险路径: %r", ftppath)
            raise OSError(22, "Invalid argument")
        ftppath_normalized = ftppath.replace("\\", "/")
        if not ftppath_normalized.startswith("/"):
            cwd = getattr(self, "_cwd", "/").replace("\\", "/")
            ftppath_normalized = (cwd + "/" + ftppath_normalized) if not cwd.endswith("/") else cwd + ftppath_normalized
        parts = []
        for part in ftppath_normalized.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        ftppath_normalized = "/" + "/".join(parts)
        is_virtual, _vdir_name, sub_path, vdir = self._parse_virtual_path(ftppath_normalized)
        if is_virtual and vdir:
            return os.path.normpath(os.path.join(vdir.real_path, sub_path.lstrip("/")))
        return os.path.normpath(os.path.join(self.root, ftppath_normalized.lstrip("/")))

    def fs2ftp(self, fspath: str) -> str:
        fspath = os.path.normpath(fspath)
        for vdir_name, vdir in self.virtual_mappings.items():
            vdir_real = os.path.normpath(vdir.real_path)
            if fspath == vdir_real:
                return "/" + vdir_name
            if fspath.startswith(vdir_real + os.sep):
                return "/" + vdir_name + "/" + os.path.relpath(fspath, vdir_real).replace("\\", "/")
        return super().fs2ftp(fspath)

    def validpath(self, path: str) -> bool:
        self._ensure_user_profile_loaded()
        resolved = self._resolve_virtual_in_root(path)
        norm_path = os.path.normpath(resolved)
        for vdir in self.virtual_mappings.values():
            vr = os.path.normpath(vdir.real_path)
            if norm_path == vr or norm_path.startswith(vr + os.sep):
                return True
        root_norm = os.path.normpath(self.root)
        if norm_path == root_norm or norm_path.startswith(root_norm + os.sep):
            return True
        ftp_path = path.replace("\\", "/").rstrip("/")
        parts = [p for p in ftp_path.split("/") if p]
        if parts and parts[0] in self.virtual_mappings:
            return True
        return False

    def listdir(self, path: str) -> List[str]:
        self._ensure_user_profile_loaded()
        resolved = self._resolve_virtual_in_root(path)
        norm_path = os.path.normpath(resolved)
        root_norm = os.path.normpath(self.root)
        if norm_path == root_norm:
            if not self._is_safe_resolved_path(self.root):
                _logger.warning("listdir: 根目录符号链接逃逸: %s", self.root)
                return []
            real_items = list(os.listdir(self.root)) if os.path.isdir(self.root) else []
            for vname in self.virtual_mappings:
                if vname not in real_items:
                    real_items.append(vname)
            return real_items
        for vdir_name, vdir in self.virtual_mappings.items():
            vdir_real = os.path.normpath(vdir.real_path)
            if norm_path == vdir_real or norm_path.startswith(vdir_real + os.sep):
                if not self._is_safe_resolved_path(norm_path):
                    _logger.warning("listdir: 虚拟目录路径符号链接逃逸: %s", norm_path)
                    return []
                return os.listdir(norm_path)
        if norm_path.startswith(root_norm + os.sep):
            if not self._is_safe_resolved_path(norm_path):
                _logger.warning("listdir: 根目录子路径符号链接逃逸: %s", norm_path)
                return []
            return os.listdir(norm_path)
        return super().listdir(resolved)

    def _resolve_virtual_in_root(self, path: str) -> str:
        if not path:
            return self.root
        try:
            norm_path = os.path.normpath(path)
            root_norm = os.path.normpath(self.root)
            if norm_path.startswith(root_norm + os.sep):
                rel_path = os.path.relpath(norm_path, root_norm)
                first_part = rel_path.split(os.sep)[0]
                if first_part in self.virtual_mappings:
                    vdir = self.virtual_mappings[first_part]
                    remaining = rel_path[len(first_part):].lstrip(os.sep)
                    resolved = os.path.normpath(os.path.join(vdir.real_path, remaining)) if remaining else os.path.normpath(vdir.real_path)
                    vdir_norm = os.path.normpath(vdir.real_path)
                    if resolved == vdir_norm or resolved.startswith(vdir_norm + os.sep):
                        return resolved
                    _logger.warning("路径逃逸检测: %s -> %s (不在 %s 内)", path, resolved, vdir_norm)
                    return path
        except Exception as e:
            _logger.error("解析路径时出错: %s, 错误: %s", path, e)
        return path

    def _is_valid_real_path(self, path: str) -> bool:
        norm_path = os.path.normpath(path)
        root_norm = os.path.normpath(self.root)
        if norm_path == root_norm or norm_path.startswith(root_norm + os.sep):
            return True
        for vdir in self.virtual_mappings.values():
            vr = os.path.normpath(vdir.real_path)
            if norm_path == vr or norm_path.startswith(vr + os.sep):
                return True
        return False

    def _is_safe_resolved_path(self, resolved: str) -> bool:
        if not self._is_valid_real_path(resolved):
            return False
        try:
            return self._is_valid_real_path(os.path.realpath(resolved))
        except OSError:
            return False

    def isdir(self, path: str) -> bool:
        try:
            return os.path.isdir(self._resolve_virtual_in_root(path))
        except Exception:
            return False

    def isfile(self, path: str) -> bool:
        try:
            return os.path.isfile(self._resolve_virtual_in_root(path))
        except Exception:
            return False

    def islink(self, path: str) -> bool:
        try:
            return os.path.islink(self._resolve_virtual_in_root(path))
        except Exception:
            return False

    def getsize(self, path: str) -> int:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝 getsize 符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        return os.path.getsize(resolved)

    def getmtime(self, path: str) -> float:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            raise OSError(13, "Permission denied")
        return os.path.getmtime(resolved)

    def realpath(self, path: str) -> str:
        try:
            resolved = self._resolve_virtual_in_root(path)
            real = os.path.realpath(resolved)
            if not self._is_valid_real_path(real):
                _logger.warning("realpath 逃逸检测: %s -> %s 超出允许范围", resolved, real)
                return os.path.normpath(resolved)
            return real
        except Exception:
            return path

    def lexists(self, path: str) -> bool:
        try:
            return os.path.lexists(self._resolve_virtual_in_root(path))
        except Exception:
            return False

    def stat(self, path: str):
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            raise OSError(13, "Permission denied")
        return os.stat(resolved)

    def lstat(self, path: str):
        return os.lstat(self._resolve_virtual_in_root(path))

    def open(self, filename: str, mode: str):
        resolved = self._resolve_virtual_in_root(filename)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝打开不在允许范围内的文件或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        return open(resolved, mode)

    def chdir(self, path: str) -> None:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝切换到不在允许范围内的目录或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        if not os.path.isdir(resolved):
            raise OSError(2, "No such directory")
        self._cwd = self.fs2ftp(resolved)

    def mkdir(self, path: str) -> None:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝在不允许的位置创建目录或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        os.mkdir(resolved)

    def rmdir(self, path: str) -> None:
        resolved = self._resolve_virtual_in_root(path)
        norm_path = os.path.normpath(resolved)
        for vdir_name, vdir in self.virtual_mappings.items():
            if norm_path == os.path.normpath(vdir.real_path):
                _logger.warning("拒绝删除虚拟目录: %s", vdir_name)
                raise OSError(1, "Operation not permitted on virtual directory")
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝删除不在允许范围内的目录或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        os.rmdir(resolved)

    def remove(self, path: str) -> None:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝删除不在允许范围内的文件或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        os.remove(resolved)

    def rename(self, src: str, dst: str) -> None:
        resolved_src = self._resolve_virtual_in_root(src)
        resolved_dst = self._resolve_virtual_in_root(dst)
        for resolved in (resolved_src, resolved_dst):
            norm_path = os.path.normpath(resolved)
            for vdir_name, vdir in self.virtual_mappings.items():
                if norm_path == os.path.normpath(vdir.real_path):
                    _logger.warning("拒绝重命名虚拟目录: %s", vdir_name)
                    raise OSError(1, "Operation not permitted on virtual directory")
        if not self._is_safe_resolved_path(resolved_src) or not self._is_safe_resolved_path(resolved_dst):
            _logger.warning("拒绝重命名不在允许范围内的文件或符号链接逃逸: %s -> %s", resolved_src, resolved_dst)
            raise OSError(13, "Permission denied")
        os.rename(resolved_src, resolved_dst)

    def chmod(self, path: str, mode: int) -> None:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝修改不在允许范围内的文件权限或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        os.chmod(resolved, mode)

    def utime(self, path: str, timeval) -> None:
        resolved = self._resolve_virtual_in_root(path)
        if not self._is_safe_resolved_path(resolved):
            _logger.warning("拒绝修改不在允许范围内的文件时间或符号链接逃逸: %s", resolved)
            raise OSError(13, "Permission denied")
        os.utime(resolved, timeval)
