"""
虚拟文件系统层

VirtualFS 是 pyftpdlib AbstractedFS 适配器，文件操作委托给协议无关的 VfsSession。
路径安全工具与映射构建从 abyssfs.fs.session 再导出，保持既有 import 路径。
"""

from __future__ import annotations

import logging
import traceback
from typing import List, Optional

from abyssfs import ospath
from abyssfs.fs.session import (  # noqa: F401
    VfsSession,
    _DANGEROUS_PATH_RE,
    _build_virtual_mappings,
    _is_safe_raw_path,
    is_safe_raw_path,
)
from abyssfs.model.entities import UserProfile
from abyssfs.model.permission import FilePermission
from abyssfs.service.registry import DirectoryRegistry

_logger = logging.getLogger(__name__)

try:
    from pyftpdlib.filesystems import AbstractedFS
except ImportError:
    _logger.warning("pyftpdlib.filesystems.AbstractedFS 导入失败，VirtualFS 将失去基类支持")
    class AbstractedFS:  # type: ignore
        def __init__(self, *args, **kwargs): pass


class VirtualFS(AbstractedFS):
    """pyftpdlib AbstractedFS wrapper around VfsSession."""

    loaded_dir_marker_enabled = False

    def __init__(self, root: str, cmd_channel: object) -> None:
        super().__init__(root, cmd_channel)
        self._session = VfsSession(
            root,
            loaded_dir_marker_enabled=self.loaded_dir_marker_enabled,
        )
        self._pull_session()
        _logger.debug(f"[VirtualFS] 初始化: root={root!r}")

    def _push_session(self) -> None:
        self._session.cwd = getattr(self, "cwd", self._session.cwd)

    def _pull_session(self) -> None:
        self.root = self._session.root
        self.cwd = self._session.cwd

    @property
    def _real_root(self) -> str:
        return self._session.real_root

    @property
    def _mappings(self):
        return self._session.mappings

    @property
    def _profile(self) -> Optional[UserProfile]:
        return self._session.profile

    def set_user_profile(self, profile: UserProfile) -> None:
        self._session.set_user_profile(profile)
        self._pull_session()

    def set_user_registry(
        self,
        registry: DirectoryRegistry,
        username: str,
    ) -> None:
        self._session.set_user_registry(registry, username)
        self._pull_session()

    def _sync_profile(self) -> None:
        self._session.sync_profile()
        self._pull_session()

    def _ftp_to_real(self, ftp_path: str) -> Optional[str]:
        self._push_session()
        return self._session.resolve_virtual(ftp_path)

    def _coerce_to_real(self, path: str) -> Optional[str]:
        self._push_session()
        return self._session.coerce(path)

    def _get_vdir(self, ftp_path: str):
        self._push_session()
        return self._session.get_vdir(ftp_path)

    def _has_perm(self, ftp_path: str, perm: FilePermission) -> bool:
        self._push_session()
        return self._session.has_perm(ftp_path, perm)

    def ftp2fs(self, ftppath: str) -> str:
        _logger.debug(f"[VirtualFS] ftp2fs: {ftppath!r}")
        self._push_session()
        return self._session.ftp2fs(ftppath)

    def fs2ftp(self, fspath: str) -> str:
        _logger.debug(f"[VirtualFS] fs2ftp: {fspath!r}")
        self._push_session()
        result = self._session.fs2ftp(fspath)
        self._pull_session()
        return result

    def validpath(self, path: str) -> bool:
        try:
            self._push_session()
            result = self._session.validpath(path)
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
        self._push_session()
        return self._session.listdir(path)

    def isfile(self, path: str) -> bool:
        self._push_session()
        return self._session.isfile(path)

    def islink(self, path: str) -> bool:
        self._push_session()
        return self._session.islink(path)

    def isdir(self, path: str) -> bool:
        self._push_session()
        return self._session.isdir(path)

    def getsize(self, path: str) -> int:
        self._push_session()
        return self._session.getsize(path)

    def getmtime(self, path: str) -> float:
        self._push_session()
        return self._session.getmtime(path)

    def realpath(self, path: str) -> str:
        self._push_session()
        return self._session.realpath(path)

    def lexists(self, path: str) -> bool:
        self._push_session()
        return self._session.lexists(path)

    def open(self, ftp_path: str, mode: str):
        _logger.debug(f"[VirtualFS] open: {ftp_path!r}, mode={mode!r}")
        self._push_session()
        fobj = self._session.open(ftp_path, mode)
        _logger.info(f"[VirtualFS] open 成功: mode={mode!r}")
        return fobj

    def chdir(self, path: str) -> None:
        _logger.debug(f"[VirtualFS] chdir: {path!r}")
        self._push_session()
        real = self._session.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        if not ospath.isdir(real):
            raise FileNotFoundError(2, "No such directory", path)
        ospath.chdir(real)
        self._session.cwd = self._session.fs2ftp(real)
        self._pull_session()

    def stat(self, path: str):
        self._push_session()
        return self._session.stat(path)

    def lstat(self, path: str):
        self._push_session()
        return self._session.lstat(path)

    def utime(self, path: str, timeval) -> None:
        self._push_session()
        self._session.utime(path, timeval)

    def mkdir(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] mkdir: {ftp_path!r}")
        self._push_session()
        self._session.mkdir(ftp_path)
        _logger.info(f"[VirtualFS] mkdir 成功: {ftp_path!r}")

    def rmdir(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] rmdir: {ftp_path!r}")
        self._push_session()
        staged = self._session.rmdir(ftp_path)
        _logger.info(f"[VirtualFS] rmdir 已隔离: {ftp_path!r} -> {staged!r}")

    def remove(self, ftp_path: str) -> None:
        _logger.debug(f"[VirtualFS] remove: {ftp_path!r}")
        self._push_session()
        staged = self._session.remove(ftp_path)
        _logger.info(f"[VirtualFS] remove 已隔离: {ftp_path!r} -> {staged!r}")

    def rename(self, src: str, dst: str) -> None:
        _logger.debug(f"[VirtualFS] rename: {src!r} → {dst!r}")
        self._push_session()
        self._session.rename(src, dst)
        _logger.info(f"[VirtualFS] rename 成功: {src!r} → {dst!r}")

    def chmod(self, ftp_path: str, mode: int) -> None:
        _logger.debug(f"[VirtualFS] chmod: {ftp_path!r}, mode={oct(mode)}")
        self._push_session()
        self._session.chmod(ftp_path, mode)
        _logger.info(f"[VirtualFS] chmod 成功: {ftp_path!r}, mode={oct(mode)}")

    def listdirinfo(self, path: str) -> List[str]:
        return self.listdir(path)

    def mkstemp(self, suffix="", prefix="", dir=None, mode="wb"):
        self._push_session()
        if dir:
            dir = self._session.to_os_dir(dir)
        return super().mkstemp(suffix=suffix, prefix=prefix, dir=dir, mode=mode)

    def readlink(self, path: str) -> str:
        self._push_session()
        return self._session.readlink(path)
