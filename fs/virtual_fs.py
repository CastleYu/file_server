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
import traceback
from typing import Dict, List, Optional, Tuple

from model.permission import FilePermission
from model.entities import VirtualDirectory, UserProfile

_logger = logging.getLogger(__name__)


# =============================================================================
# 路径安全工具
# =============================================================================

_DANGEROUS_PATH_RE = re.compile(
    r"(?:^|[/\\])"                         # 路径开头或分隔符
    r"(?:"
    r"\.{2,}"                              # .. 或更多点（路径回溯）
    r"|%2e{2}|%252e{2}"                    # URL 编码的 ..
    r"|\.{2,}%2[fF]|%2[eE]%2[fF]"         # ../ URL 变体
    r")"
    r"(?:[/\\]|$)",
    re.IGNORECASE,
)


def _is_safe_raw_path(raw_path: str) -> bool:
    """检测路径是否包含危险的路径遍历字符串（不解析真实路径，仅检测原始字符串）。"""
    result = not bool(_DANGEROUS_PATH_RE.search(raw_path))
    if not result:
        _logger.warning(f"[path_safety] 危险路径检测: {raw_path!r}")
    return result


def is_safe_raw_path(raw_path: str) -> bool:
    """公开接口：检测路径是否包含危险的路径遍历字符串。"""
    return _is_safe_raw_path(raw_path)


# =============================================================================
# 辅助函数：构建虚拟目录映射表
# =============================================================================

def _build_virtual_mappings(
    profile: UserProfile,
) -> Dict[str, VirtualDirectory]:
    """
    将 UserProfile.virtual_dirs 转为 {虚拟名 → VirtualDirectory} 字典。
    虚拟名取 get_display_name() 的值（去除冲突时补加后缀）。
    """
    mappings: Dict[str, VirtualDirectory] = {}
    for vd in profile.virtual_dirs:
        name = vd.get_display_name()
        if name in mappings:
            suffix = 2
            while f"{name}_{suffix}" in mappings:
                suffix += 1
            new_name = f"{name}_{suffix}"
            _logger.warning(
                f"[_build_virtual_mappings] 虚拟名 {name!r} 冲突, "
                f"改为 {new_name!r} (user={profile.username!r})"
            )
            name = new_name
        mappings[name] = vd
    _logger.debug(
        f"[_build_virtual_mappings] user={profile.username!r}, "
        f"mappings={list(mappings.keys())}"
    )
    return mappings


try:
    from pyftpdlib.filesystems import AbstractedFS
except ImportError:
    _logger.warning("pyftpdlib.filesystems.AbstractedFS 导入失败，VirtualFS 将失去基类支持")
    class AbstractedFS:  # type: ignore
        def __init__(self, *args, **kwargs): pass

# =============================================================================
# VirtualFS（pyftpdlib AbstractedFS 子类）
# =============================================================================

class VirtualFS(AbstractedFS):
    """
    全功能虚拟文件系统（pyftpdlib AbstractedFS 的虚拟目录实现）。

    功能
    ----
    - 将用户的多个虚拟目录映射到 FTP 路径空间
    - 路径遍历防护（双重检查：字符串 + realpath）
    - 符号链接逃逸检测
    - 细粒度按目录权限控制
    """

    def __init__(self, root: str, cmd_channel: object) -> None:
        super().__init__(root, cmd_channel)

        self._profile: Optional[UserProfile] = None
        self._mappings: Dict[str, VirtualDirectory] = {}
        self._real_root: str = os.path.normpath(root)
        _logger.debug(f"[VirtualFS] 初始化: root={root!r}")

    def set_user_profile(self, profile: UserProfile) -> None:
        """登录后注入用户配置。"""
        self._profile  = profile
        self._mappings = _build_virtual_mappings(profile)
        self._real_root = os.path.normpath(profile.root_dir)
        _logger.info(
            f"[VirtualFS] set_user_profile: "
            f"user={profile.username!r}, root={self._real_root!r}, "
            f"vdirs={list(self._mappings.keys())}"
        )

    # ------------------------------------------------------------------
    # 路径映射与安全
    # ------------------------------------------------------------------

    def _ftp_to_real(self, ftp_path: str) -> Optional[str]:
        """将 FTP 虚拟路径映射到真实文件系统路径；如路径不安全返回 None。"""
        if not _is_safe_raw_path(ftp_path):
            _logger.warning(
                f"[VirtualFS] _ftp_to_real 路径不安全: {ftp_path!r}"
            )
            return None

        ftp_path = ftp_path.replace("\\", "/")
        if not ftp_path.startswith("/"):
            ftp_path = "/" + ftp_path
        parts = [p for p in ftp_path.split("/") if p]

        if not parts:
            result = self._real_root
        elif parts[0] in self._mappings:
            vd  = self._mappings[parts[0]]
            sub = os.sep.join(parts[1:])
            result = os.path.normpath(
                os.path.join(vd.real_path, sub) if sub else vd.real_path
            )
        else:
            sub = os.sep.join(parts)
            result = os.path.normpath(os.path.join(self._real_root, sub))

        # 符号链接逃逸检测
        try:
            real_resolved = os.path.realpath(result)
            root_resolved = os.path.realpath(self._real_root)
            in_root = real_resolved.startswith(root_resolved + os.sep) or \
                      real_resolved == root_resolved
            if not in_root:
                # 检查是否在某个虚拟目录的真实根下
                in_vdir = any(
                    real_resolved.startswith(
                        os.path.realpath(vd.real_path) + os.sep
                    ) or real_resolved == os.path.realpath(vd.real_path)
                    for vd in self._mappings.values()
                )
                if not in_vdir:
                    _logger.error(
                        f"[VirtualFS] 符号链接逃逸检测失败! "
                        f"ftp={ftp_path!r}, real={result!r}, "
                        f"resolved={real_resolved!r}"
                    )
                    return None
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] realpath 检查异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return None

        _logger.debug(f"[VirtualFS] _ftp_to_real: {ftp_path!r} → {result!r}")
        return result

    def _get_vdir(self, ftp_path: str) -> Optional[VirtualDirectory]:
        """返回 ftp_path 对应的 VirtualDirectory（若路径在虚拟目录下）。"""
        parts = [p for p in ftp_path.replace("\\", "/").lstrip("/").split("/") if p]
        if parts and parts[0] in self._mappings:
            return self._mappings[parts[0]]
        return None

    def _has_perm(self, ftp_path: str, perm: FilePermission) -> bool:
        """检查当前用户对给定 FTP 路径是否有指定权限。"""
        if not self._profile:
            _logger.warning("[VirtualFS] _has_perm: 用户配置为空，默认拒绝")
            return False
        vd = self._get_vdir(ftp_path)
        if vd:
            result = bool(vd.permission & perm)
            _logger.debug(
                f"[VirtualFS] _has_perm (vdir): "
                f"path={ftp_path!r}, perm={perm!r} → {result}"
            )
        else:
            result = bool(self._profile.root_permission & perm)
            _logger.debug(
                f"[VirtualFS] _has_perm (root): "
                f"path={ftp_path!r}, perm={perm!r} → {result}"
            )
        return result

    # ------------------------------------------------------------------
    # AbstractedFS 接口（覆盖操作以添加权限检查和详细日志）
    # ------------------------------------------------------------------

    def ftp2fs(self, ftppath: str) -> str:
        _logger.debug(f"[VirtualFS] ftp2fs: {ftppath!r}")
        result = self._ftp_to_real(ftppath)
        if result is None:
            _logger.warning(
                f"[VirtualFS] ftp2fs 安全检查拒绝: {ftppath!r}, 返回根目录"
            )
            return self._real_root
        return result

    def fs2ftp(self, fspath: str) -> str:
        _logger.debug(f"[VirtualFS] fs2ftp: {fspath!r}")
        try:
            real = os.path.normpath(fspath)
            nroot = self._real_root
            if real == nroot:
                return "/"
            if real.startswith(nroot + os.sep):
                rel = real[len(nroot):].replace(os.sep, "/")
                return rel if rel.startswith("/") else "/" + rel
            for vname, vd in self._mappings.items():
                vreal = os.path.normpath(vd.real_path)
                if real == vreal:
                    return f"/{vname}"
                if real.startswith(vreal + os.sep):
                    sub = real[len(vreal):].replace(os.sep, "/")
                    return f"/{vname}{sub}"
            return "/"
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] fs2ftp 异常: {fspath!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return "/"

    def validpath(self, path: str) -> bool:
        try:
            result = self._ftp_to_real(path) is not None
            _logger.debug(f"[VirtualFS] validpath: {path!r} → {result}")
            return result
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] validpath 异常: {path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False

    def listdir(self, path: str) -> List[str]:
        _logger.debug(f"[VirtualFS] listdir: {path!r}")
        try:
            real = self._ftp_to_real(path)
            if real is None:
                return []
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                names: set = set()
                if os.path.isdir(real):
                    names.update(os.listdir(real))
                names.update(self._mappings.keys())
                result = sorted(names)
                _logger.debug(
                    f"[VirtualFS] listdir 根目录: {len(result)} 项"
                )
                return result
            entries = os.listdir(real)
            _logger.debug(
                f"[VirtualFS] listdir: {real!r}, {len(entries)} 项"
            )
            return entries
        except PermissionError as exc:
            _logger.warning(
                f"[VirtualFS] listdir 权限错误: {path!r}, {exc}"
            )
            return []
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] listdir 异常: {path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return []

    def isfile(self, path: str) -> bool:
        try:
            real   = self._ftp_to_real(path)
            result = os.path.isfile(real) if real else False
            return result
        except Exception as exc:
            _logger.error(f"[VirtualFS] isfile 异常: {path!r}, {exc}")
            return False

    def islink(self, path: str) -> bool:
        try:
            real   = self._ftp_to_real(path)
            result = os.path.islink(real) if real else False
            return result
        except Exception:
            return False

    def isdir(self, path: str) -> bool:
        try:
            real = self._ftp_to_real(path)
            if real is None:
                return False
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                return True
            if any(norm == os.path.normpath(vd.real_path) for vd in self._mappings.values()):
                return True
            return os.path.isdir(real)
        except Exception as exc:
            _logger.error(f"[VirtualFS] isdir 异常: {path!r}, {exc}")
            return False

    def getsize(self, path: str) -> int:
        try:
            real = self._ftp_to_real(path)
            return os.path.getsize(real) if real else 0
        except Exception as exc:
            _logger.error(f"[VirtualFS] getsize 异常: {path!r}, {exc}")
            return 0

    def getmtime(self, path: str) -> float:
        try:
            real = self._ftp_to_real(path)
            return os.path.getmtime(real) if real else 0.0
        except Exception as exc:
            _logger.error(f"[VirtualFS] getmtime 异常: {path!r}, {exc}")
            return 0.0

    def realpath(self, path: str) -> str:
        real = self._ftp_to_real(path)
        return real if real else self._real_root

    def lexists(self, path: str) -> bool:
        try:
            real = self._ftp_to_real(path)
            if real is None:
                return False
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                return True
            if any(norm == os.path.normpath(vd.real_path) for vd in self._mappings.values()):
                return True
            return os.path.lexists(real)
        except Exception as exc:
            _logger.error(f"[VirtualFS] lexists 异常: {path!r}, {exc}")
            return False

    def open(self, ftp_path: str, mode: str):
        _logger.debug(f"[VirtualFS] open: {ftp_path!r}, mode={mode!r}")
        need_write = "w" in mode or "a" in mode
        need_perm  = FilePermission.WRITE if need_write else FilePermission.READ
        if not self._has_perm(ftp_path, need_perm):
            _logger.warning(
                f"[VirtualFS] open 权限拒绝: {ftp_path!r}, mode={mode!r}"
            )
            import errno
            raise PermissionError(
                errno.EACCES, "Permission denied", ftp_path
            )
        try:
            real = self._ftp_to_real(ftp_path)
            if real is None:
                raise PermissionError("路径无效或不安全")
            fobj = open(real, mode)
            _logger.info(
                f"[VirtualFS] open 成功: {real!r}, mode={mode!r}"
            )
            return fobj
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] open 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def mkdir(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] mkdir: {ftp_path!r}")
        if not self._has_perm(ftp_path, FilePermission.MKDIR):
            _logger.warning(f"[VirtualFS] mkdir 权限拒绝: {ftp_path!r}")
            import errno
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        try:
            real = self._ftp_to_real(ftp_path)
            if real is None:
                raise PermissionError("路径无效")
            os.mkdir(real)
            _logger.info(f"[VirtualFS] mkdir 成功: {real!r}")
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] mkdir 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def rmdir(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] rmdir: {ftp_path!r}")
        if not self._has_perm(ftp_path, FilePermission.DELETE):
            _logger.warning(f"[VirtualFS] rmdir 权限拒绝: {ftp_path!r}")
            import errno
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        try:
            real = self._ftp_to_real(ftp_path)
            if real is None:
                raise PermissionError("路径无效")
            if any(os.path.normpath(real) == os.path.normpath(vd.real_path)
                   for vd in self._mappings.values()):
                _logger.warning(
                    f"[VirtualFS] rmdir 试图删除虚拟目录根: {ftp_path!r}"
                )
                import errno
                raise PermissionError(
                    errno.EACCES, "Cannot remove virtual root", ftp_path
                )
            os.rmdir(real)
            _logger.info(f"[VirtualFS] rmdir 成功: {real!r}")
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] rmdir 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def remove(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] remove: {ftp_path!r}")
        if not self._has_perm(ftp_path, FilePermission.DELETE):
            _logger.warning(f"[VirtualFS] remove 权限拒绝: {ftp_path!r}")
            import errno
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        try:
            real = self._ftp_to_real(ftp_path)
            if real is None:
                raise PermissionError("路径无效")
            os.remove(real)
            _logger.info(f"[VirtualFS] remove 成功: {real!r}")
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] remove 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def rename(self, src: str, dst: str) -> None:
        _logger.debug(f"[VirtualFS] rename: {src!r} → {dst!r}")
        if not self._has_perm(src, FilePermission.RENAME):
            _logger.warning(f"[VirtualFS] rename 权限拒绝: {src!r}")
            import errno
            raise PermissionError(errno.EACCES, "Permission denied", src)
        try:
            real_src = self._ftp_to_real(src)
            real_dst = self._ftp_to_real(dst)
            if real_src is None or real_dst is None:
                raise PermissionError("路径无效")
            os.rename(real_src, real_dst)
            _logger.info(f"[VirtualFS] rename 成功: {real_src!r} → {real_dst!r}")
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] rename 异常: {src!r} → {dst!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def chmod(self, ftp_path: str, mode: int) -> None:
        _logger.debug(f"[VirtualFS] chmod: {ftp_path!r}, mode={oct(mode)}")
        if not self._has_perm(ftp_path, FilePermission.CHMOD):
            _logger.warning(f"[VirtualFS] chmod 权限拒绝: {ftp_path!r}")
            import errno
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        try:
            real = self._ftp_to_real(ftp_path)
            if real is None:
                raise PermissionError("路径无效")
            os.chmod(real, mode)
            _logger.info(f"[VirtualFS] chmod 成功: {real!r}, mode={oct(mode)}")
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VirtualFS] chmod 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise
