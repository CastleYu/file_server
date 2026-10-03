"""Protocol-neutral virtual filesystem session.

All file-protocol backends share this mapper: a user root plus sibling
virtual-directory mounts, path-traversal guards, permission checks, and
the async delete queue. Protocol adapters (FTP AbstractedFS, SFTP, WebDAV,
NFS) should call VfsSession instead of reimplementing path routing.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import traceback
from typing import Dict, List, Optional, Tuple

from abyssfs import ospath
from abyssfs.fs.delete_queue import AsyncDeleteService
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.model.permission import FilePermission
from abyssfs.plugins.loaded_dir_marker import LoadedDirMarker
from abyssfs.service.registry import DirectoryRegistry

_logger = logging.getLogger(__name__)

_DANGEROUS_PATH_RE = re.compile(
    r"(?:^|[/\\])"
    r"(?:"
    r"\.{2,}"
    r"|%2e{2}|%252e{2}"
    r"|\.{2,}%2[fF]|%2[eE]%2[fF]"
    r")"
    r"(?:[/\\]|$)",
    re.IGNORECASE,
)


def _is_safe_raw_path(raw_path: str) -> bool:
    result = not bool(_DANGEROUS_PATH_RE.search(raw_path))
    if not result:
        _logger.warning(f"[path_safety] 危险路径检测: {raw_path!r}")
    return result


def is_safe_raw_path(raw_path: str) -> bool:
    return _is_safe_raw_path(raw_path)


def _build_virtual_mappings(
    profile: UserProfile,
) -> Dict[str, VirtualDirectory]:
    mappings: Dict[str, VirtualDirectory] = {}
    for vd in profile.get_mountable_virtual_dirs(strict_required=True):
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


class VfsSession:
    """Per-user virtual filesystem independent of any wire protocol."""

    def __init__(
        self,
        root: str,
        *,
        loaded_dir_marker_enabled: bool = False,
    ) -> None:
        self._profile: Optional[UserProfile] = None
        self._registry: Optional[DirectoryRegistry] = None
        self._username: Optional[str] = None
        self._profile_version = -1
        self._mappings: Dict[str, VirtualDirectory] = {}
        self._real_root: str = os.path.normpath(ospath.strip_extended(root))
        self.root: str = root
        self.cwd: str = "/"
        self._dir_marker = LoadedDirMarker(loaded_dir_marker_enabled)
        _logger.debug(f"[VfsSession] 初始化: root={root!r}")

    @property
    def real_root(self) -> str:
        return self._real_root

    @property
    def mappings(self) -> Dict[str, VirtualDirectory]:
        return self._mappings

    @property
    def profile(self) -> Optional[UserProfile]:
        return self._profile

    def set_user_profile(self, profile: UserProfile) -> None:
        self._registry = None
        self._username = profile.username
        self._profile_version = -1
        self._profile = profile
        self._mappings = _build_virtual_mappings(profile)
        self._real_root = os.path.normpath(ospath.strip_extended(profile.root_dir))
        self.root = profile.root_dir
        _logger.info(
            f"[VfsSession] set_user_profile: "
            f"user={profile.username!r}, root={self._real_root!r}, "
            f"vdirs={list(self._mappings.keys())}"
        )

    def set_user_registry(
        self,
        registry: DirectoryRegistry,
        username: str,
    ) -> None:
        self._registry = registry
        self._username = username
        self._profile_version = -1
        self.sync_profile()

    def sync_profile(self) -> None:
        if self._registry is None or self._username is None:
            return
        entry = self._registry.user(self._username)
        if entry is None:
            self._profile = None
            self._mappings = {}
            return
        if entry.version == self._profile_version:
            return

        previous = self._profile_version
        self._profile = entry.profile
        self._mappings = _build_virtual_mappings(entry.profile)
        self._real_root = os.path.normpath(ospath.strip_extended(entry.profile.root_dir))
        self.root = entry.profile.root_dir
        self._profile_version = entry.version
        if previous >= 0:
            self.cwd = "/"
        _logger.info(
            f"[VfsSession] 目录配置已同步: user={self._username!r}, "
            f"version={entry.version}, vdirs={list(self._mappings.keys())}"
        )

    @staticmethod
    def _isabs(path: str, _windows: bool = os.name == "nt") -> bool:
        if _windows and path.startswith("/"):
            return True
        return os.path.isabs(path)

    def ftpnorm(self, ftppath: str) -> str:
        if self._isabs(ftppath):
            p = os.path.normpath(ftppath)
        else:
            p = os.path.normpath(os.path.join(self.cwd, ftppath))
        if os.sep == "\\":
            p = p.replace("\\", "/")
        while p[:2] == "//":
            p = p[1:]
        if not self._isabs(p):
            p = "/"
        return p

    def resolve_virtual(self, ftp_path: str) -> Optional[str]:
        """Map a virtual path to a storage path; None if unsafe."""
        self.sync_profile()
        raw_path = ftp_path or ""
        if re.search(r"%2e|%252e|%2f|%5c", raw_path, re.IGNORECASE) and not _is_safe_raw_path(raw_path):
            _logger.warning(f"[VfsSession] resolve_virtual 路径不安全: {ftp_path!r}")
            return None

        ftp_path = self.ftpnorm(raw_path.replace("\\", "/"))
        if not _is_safe_raw_path(ftp_path):
            _logger.warning(
                f"[VfsSession] resolve_virtual 规范化后路径不安全: {ftp_path!r}"
            )
            return None
        parts = self._normalize_marked_parts([p for p in ftp_path.split("/") if p])

        if not parts:
            result = self._real_root
        elif parts[0] in self._mappings:
            vd = self._mappings[parts[0]]
            sub = os.sep.join(parts[1:])
            result = os.path.normpath(
                os.path.join(vd.real_path, sub) if sub else vd.real_path
            )
        else:
            sub = os.sep.join(parts)
            result = os.path.normpath(os.path.join(self._real_root, sub))

        try:
            real_resolved = ospath.realpath(result)
            root_resolved = ospath.realpath(self._real_root)
            in_root = real_resolved.startswith(root_resolved + os.sep) or \
                      real_resolved == root_resolved
            if not in_root:
                in_vdir = any(
                    real_resolved.startswith(
                        ospath.realpath(vd.real_path) + os.sep
                    ) or real_resolved == ospath.realpath(vd.real_path)
                    for vd in self._mappings.values()
                )
                if not in_vdir:
                    _logger.error(
                        f"[VfsSession] 符号链接逃逸检测失败! "
                        f"ftp={ftp_path!r}, real={result!r}, "
                        f"resolved={real_resolved!r}"
                    )
                    return None
        except Exception as exc:
            _logger.error(
                f"[VfsSession] realpath 检查异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return None

        _logger.debug(f"[VfsSession] resolve_virtual: {ftp_path!r} → {result!r}")
        return result

    @staticmethod
    def _norm_real(path: str) -> str:
        return os.path.normcase(ospath.realpath(os.path.normpath(path)))

    @classmethod
    def _is_within(cls, path: str, root: str) -> bool:
        try:
            npath = cls._norm_real(path)
            nroot = cls._norm_real(root)
            return npath == nroot or npath.startswith(nroot + os.sep)
        except Exception:
            return False

    def _is_managed_real_path(self, path: str) -> bool:
        if not path or not os.path.isabs(path):
            return False
        if self._is_within(path, self._real_root):
            return True
        return any(
            self._is_within(path, vd.real_path)
            for vd in self._mappings.values()
        )

    @staticmethod
    def _looks_like_local_absolute_path(path: str) -> bool:
        if not path or not os.path.isabs(path):
            return False
        normalized = path.replace("\\", "/")
        return bool(os.path.splitdrive(path)[0]) or normalized.startswith("//")

    def _resolve_root_virtual_alias(self, path: str) -> Optional[str]:
        if not path or not os.path.isabs(path):
            return None
        try:
            real_root = os.path.normpath(self._real_root)
            if not self._is_within(path, real_root):
                return None
            norm_path = os.path.normpath(path)
            if os.path.normcase(norm_path) == os.path.normcase(real_root):
                return None
            rel = os.path.relpath(norm_path, real_root)
            parts = [p for p in rel.split(os.sep) if p and p != "."]
            if not parts:
                return None
            parts = self._normalize_marked_parts(parts)
            first = parts[0]
            if first not in self._mappings:
                return None
            sub = os.sep.join(parts[1:])
            target = self._mappings[first].real_path
            return os.path.normpath(os.path.join(target, sub) if sub else target)
        except Exception as exc:
            _logger.error(
                f"[VfsSession] _resolve_root_virtual_alias 异常: "
                f"{path!r}, {exc}\n{traceback.format_exc()}"
            )
            return None

    def coerce(self, path: str) -> Optional[str]:
        """Accept a virtual path or a managed real path; reject unmanaged abs paths."""
        self.sync_profile()
        alias = self._resolve_root_virtual_alias(path)
        if alias is not None:
            return alias
        if self._is_managed_real_path(path):
            return os.path.normpath(path)
        if self._looks_like_local_absolute_path(path):
            return None
        return self.resolve_virtual(path)

    def _normalize_marked_parts(self, parts: List[str]) -> List[str]:
        if not self._dir_marker.enabled or not parts:
            return parts

        normalized: List[str] = []
        current_real = self._real_root
        for idx, part in enumerate(parts):
            storage_name = part
            if part.startswith("#"):
                candidate = part[1:]
                if idx == 0 and candidate in self._mappings:
                    candidate_real = self._mappings[candidate].real_path
                else:
                    candidate_real = os.path.join(current_real, candidate)
                if self._dir_marker.is_loaded(candidate_real):
                    storage_name = candidate

            normalized.append(storage_name)
            if idx == 0 and storage_name in self._mappings:
                current_real = self._mappings[storage_name].real_path
            else:
                current_real = os.path.join(current_real, storage_name)
        return normalized

    def _display_entry_name(self, parent_real: str, name: str) -> str:
        parent_is_root = (
            os.path.normcase(os.path.normpath(parent_real))
            == os.path.normcase(os.path.normpath(self._real_root))
        )
        if parent_is_root and name in self._mappings:
            real = self._mappings[name].real_path
        else:
            real = os.path.join(parent_real, name)
        return self._dir_marker.display_name(name, real) if ospath.isdir(real) else name

    def _get_vdir_for_real(self, real_path: str) -> Optional[VirtualDirectory]:
        for vd in self._mappings.values():
            if self._is_within(real_path, vd.real_path):
                return vd
        return None

    def get_vdir(self, ftp_path: str) -> Optional[VirtualDirectory]:
        self.sync_profile()
        alias = self._resolve_root_virtual_alias(ftp_path)
        if alias is not None:
            return self._get_vdir_for_real(alias)
        if self._is_managed_real_path(ftp_path):
            return self._get_vdir_for_real(ftp_path)

        ftp_path = self.ftpnorm((ftp_path or "").replace("\\", "/"))
        parts = self._normalize_marked_parts(
            [p for p in ftp_path.lstrip("/").split("/") if p]
        )
        if parts and parts[0] in self._mappings:
            return self._mappings[parts[0]]
        return None

    def has_perm(self, ftp_path: str, perm: FilePermission) -> bool:
        if not self._profile:
            _logger.warning("[VfsSession] has_perm: 用户配置为空，默认拒绝")
            return False
        vd = self.get_vdir(ftp_path)
        if vd:
            result = bool(vd.permission & perm)
        else:
            result = bool(self._profile.root_permission & perm)
        _logger.debug(
            f"[VfsSession] has_perm: path={ftp_path!r}, perm={perm!r} → {result}"
        )
        return result

    def ftp2fs(self, ftppath: str) -> str:
        result = self.resolve_virtual(ftppath)
        if result is None:
            _logger.warning(
                f"[VfsSession] ftp2fs 安全检查拒绝: {ftppath!r}, 返回根目录"
            )
            return self._real_root
        return result

    def fs2ftp(self, fspath: str) -> str:
        self.sync_profile()
        try:
            real = os.path.normpath(fspath)
            nroot = self._real_root
            real_cmp = os.path.normcase(real)
            nroot_cmp = os.path.normcase(os.path.normpath(nroot))
            if real_cmp == nroot_cmp:
                return "/"
            if real_cmp.startswith(nroot_cmp + os.sep):
                rel = real[len(nroot):].replace(os.sep, "/")
                return rel if rel.startswith("/") else "/" + rel
            for vname, vd in self._mappings.items():
                vreal = os.path.normpath(vd.real_path)
                vreal_cmp = os.path.normcase(vreal)
                if real_cmp == vreal_cmp:
                    return f"/{vname}"
                if real_cmp.startswith(vreal_cmp + os.sep):
                    sub = real[len(vreal):].replace(os.sep, "/")
                    return f"/{vname}{sub}"
            return "/"
        except Exception as exc:
            _logger.error(
                f"[VfsSession] fs2ftp 异常: {fspath!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return "/"

    def validpath(self, path: str) -> bool:
        try:
            return self.coerce(path) is not None
        except Exception as exc:
            _logger.error(
                f"[VfsSession] validpath 异常: {path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False

    def listdir(self, path: str) -> List[str]:
        try:
            real = self.coerce(path)
            if real is None:
                return []
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                names: set = set()
                if ospath.isdir(real):
                    names.update(
                        name for name in ospath.listdir(real)
                        if not AsyncDeleteService.is_internal_name(name)
                    )
                names.update(self._mappings.keys())
                result = sorted(self._display_entry_name(real, name) for name in names)
                self._dir_marker.mark_loaded(real)
                return result
            entries = [
                self._display_entry_name(real, name)
                for name in ospath.listdir(real)
                if not AsyncDeleteService.is_internal_name(name)
            ]
            self._dir_marker.mark_loaded(real)
            return entries
        except PermissionError as exc:
            _logger.warning(f"[VfsSession] listdir 权限错误: {path!r}, {exc}")
            return []
        except Exception as exc:
            _logger.error(
                f"[VfsSession] listdir 异常: {path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            return []

    def isfile(self, path: str) -> bool:
        try:
            real = self.coerce(path)
            return ospath.isfile(real) if real else False
        except Exception as exc:
            _logger.error(f"[VfsSession] isfile 异常: {path!r}, {exc}")
            return False

    def islink(self, path: str) -> bool:
        try:
            real = self.coerce(path)
            return ospath.islink(real) if real else False
        except Exception:
            return False

    def isdir(self, path: str) -> bool:
        try:
            real = self.coerce(path)
            if real is None:
                return False
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                return True
            if any(norm == os.path.normpath(vd.real_path) for vd in self._mappings.values()):
                return True
            return ospath.isdir(real)
        except Exception as exc:
            _logger.error(f"[VfsSession] isdir 异常: {path!r}, {exc}")
            return False

    def getsize(self, path: str) -> int:
        try:
            real = self.coerce(path)
            return ospath.getsize(real) if real else 0
        except Exception as exc:
            _logger.error(f"[VfsSession] getsize 异常: {path!r}, {exc}")
            return 0

    def getmtime(self, path: str) -> float:
        try:
            real = self.coerce(path)
            return ospath.getmtime(real) if real else 0.0
        except Exception as exc:
            _logger.error(f"[VfsSession] getmtime 异常: {path!r}, {exc}")
            return 0.0

    def realpath(self, path: str) -> str:
        real = self.coerce(path)
        return real if real else self._real_root

    def lexists(self, path: str) -> bool:
        try:
            real = self.coerce(path)
            if real is None:
                return False
            norm = os.path.normpath(real)
            if norm == os.path.normpath(self._real_root):
                return True
            if any(norm == os.path.normpath(vd.real_path) for vd in self._mappings.values()):
                return True
            return ospath.lexists(real)
        except Exception as exc:
            _logger.error(f"[VfsSession] lexists 异常: {path!r}, {exc}")
            return False

    def open(self, ftp_path: str, mode: str):
        if "a" in mode:
            need_perm = FilePermission.APPEND
        elif "w" in mode or "+" in mode:
            need_perm = FilePermission.WRITE
        else:
            need_perm = FilePermission.READ
        if not self.has_perm(ftp_path, need_perm):
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        try:
            real = self.coerce(ftp_path)
            if real is None:
                raise PermissionError("路径无效或不安全")
            return ospath.open_file(real, mode)
        except PermissionError:
            raise
        except Exception as exc:
            _logger.error(
                f"[VfsSession] open 异常: {ftp_path!r}, "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def chdir(self, path: str) -> str:
        """Update logical cwd. Returns the virtual path. Does not os.chdir."""
        real = self.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        if not ospath.isdir(real):
            raise FileNotFoundError(2, "No such directory", path)
        self.cwd = self.fs2ftp(real)
        return self.cwd

    def stat(self, path: str):
        real = self.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        return ospath.stat(real)

    def lstat(self, path: str):
        real = self.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        return ospath.lstat(real)

    def utime(self, path: str, timeval) -> None:
        if not self.has_perm(path, FilePermission.MODIFY_TIME):
            raise PermissionError(errno.EACCES, "Permission denied", path)
        real = self.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        ospath.utime(real, (timeval, timeval))

    def mkdir(self, ftp_path: str) -> None:
        if not self.has_perm(ftp_path, FilePermission.MKDIR):
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        real = self.coerce(ftp_path)
        if real is None:
            raise PermissionError("路径无效")
        ospath.mkdir(real)

    def rmdir(self, ftp_path: str) -> str:
        if not self.has_perm(ftp_path, FilePermission.DELETE):
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        real = self.coerce(ftp_path)
        if real is None:
            raise PermissionError("路径无效")
        if any(
            os.path.normcase(os.path.normpath(real))
            == os.path.normcase(os.path.normpath(vd.real_path))
            for vd in self._mappings.values()
        ):
            raise PermissionError(
                errno.EACCES, "Cannot remove virtual root", ftp_path
            )
        return AsyncDeleteService.default().dir(real)

    def remove(self, ftp_path: str) -> str:
        if not self.has_perm(ftp_path, FilePermission.DELETE):
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        real = self.coerce(ftp_path)
        if real is None:
            raise PermissionError("路径无效")
        return AsyncDeleteService.default().file(real)

    def rename(self, src: str, dst: str) -> None:
        if not self.has_perm(src, FilePermission.RENAME):
            raise PermissionError(errno.EACCES, "Permission denied", src)
        real_src = self.coerce(src)
        real_dst = self.coerce(dst)
        if real_src is None or real_dst is None:
            raise PermissionError("路径无效")
        virtual_roots = [
            os.path.normcase(os.path.normpath(vd.real_path))
            for vd in self._mappings.values()
        ]
        if (
            os.path.normcase(os.path.normpath(real_src)) in virtual_roots
            or os.path.normcase(os.path.normpath(real_dst)) in virtual_roots
        ):
            raise PermissionError(
                errno.EACCES, "Cannot rename virtual root", src
            )
        ospath.rename(real_src, real_dst)

    def chmod(self, ftp_path: str, mode: int) -> None:
        if not self.has_perm(ftp_path, FilePermission.CHMOD):
            raise PermissionError(errno.EACCES, "Permission denied", ftp_path)
        real = self.coerce(ftp_path)
        if real is None:
            raise PermissionError("路径无效")
        ospath.chmod(real, mode)

    def readlink(self, path: str) -> str:
        real = self.coerce(path)
        if real is None:
            raise PermissionError("路径无效或不安全")
        return ospath.readlink(real)

    def to_os_dir(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return path
        real = self.coerce(path)
        return ospath.to_os_path(real if real is not None else path)
