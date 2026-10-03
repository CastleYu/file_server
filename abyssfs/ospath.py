"""OS filesystem helpers with Windows long-path support.

Windows MAX_PATH is 260 characters unless both of the following are true:

- ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled = 1``
- the process manifest sets ``longPathAware``

CPython's ``python.exe`` opts in; Nuitka-frozen AbyssFS builds typically
do not.  Without that opt-in, ``os.stat`` / ``open`` / ``os.listdir`` /
``os.chdir`` fail on long folder names even when the files exist, and
``os.path.realpath`` may return a ``\\\\?\\``-prefixed path that then
fails the VirtualFS containment check.

Logical paths used for mapping and comparison never carry the prefix.
Convert at the syscall boundary with :func:`to_os_path`.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, List

_LONG_PREFIX = "\\\\?\\"
_UNC_LONG_PREFIX = "\\\\?\\UNC\\"
_LONG_PREFIX_ALT = "//?/"
_UNC_LONG_PREFIX_ALT = "//?/UNC/"


def strip_extended(path: str) -> str:
    """Remove a Windows ``\\\\?\\`` / ``\\\\?\\UNC\\`` prefix if present."""
    if not path:
        return path
    if path.startswith(_UNC_LONG_PREFIX) or path.startswith(_UNC_LONG_PREFIX_ALT):
        return "\\\\" + path[len(_UNC_LONG_PREFIX):]
    if path.startswith(_LONG_PREFIX) or path.startswith(_LONG_PREFIX_ALT):
        return path[len(_LONG_PREFIX):]
    return path


def to_os_path(path: str) -> str:
    """Absolute path form that Windows APIs accept beyond MAX_PATH.

    Non-Windows, empty, and non-absolute paths are returned unchanged.
    ``.`` / ``..`` are collapsed *before* the prefix is added; the kernel
    treats those as literal names on extended paths.
    """
    if os.name != "nt" or not path:
        return path

    path = os.path.normpath(strip_extended(path).replace("/", "\\"))
    if path.startswith("\\\\"):
        return _UNC_LONG_PREFIX + path[2:]

    drive, tail = os.path.splitdrive(path)
    if drive and tail.startswith("\\"):
        return _LONG_PREFIX + path
    return path


def realpath(path: str) -> str:
    """Canonical path with any extended prefix stripped.

    Used for containment checks so ``\\\\?\\C:\\foo`` and ``C:\\foo``
    compare equal.
    """
    if not path:
        return path
    logical = strip_extended(path)
    try:
        resolved = os.path.realpath(to_os_path(logical))
    except (OSError, ValueError):
        try:
            resolved = os.path.abspath(os.path.normpath(logical))
        except (OSError, ValueError):
            resolved = os.path.normpath(logical)
    return strip_extended(resolved)


def exists(path: str) -> bool:
    return os.path.exists(to_os_path(path))


def lexists(path: str) -> bool:
    return os.path.lexists(to_os_path(path))


def isdir(path: str) -> bool:
    return os.path.isdir(to_os_path(path))


def isfile(path: str) -> bool:
    return os.path.isfile(to_os_path(path))


def islink(path: str) -> bool:
    return os.path.islink(to_os_path(path))


def getsize(path: str) -> int:
    return os.path.getsize(to_os_path(path))


def getmtime(path: str) -> float:
    return os.path.getmtime(to_os_path(path))


def listdir(path: str) -> List[str]:
    return os.listdir(to_os_path(path))


def stat(path: str) -> os.stat_result:
    return os.stat(to_os_path(path))


def lstat(path: str) -> os.stat_result:
    return os.lstat(to_os_path(path))


def mkdir(path: str, mode: int = 0o777) -> None:
    os.mkdir(to_os_path(path), mode)


def makedirs(path: str, mode: int = 0o777, exist_ok: bool = False) -> None:
    os.makedirs(to_os_path(path), mode=mode, exist_ok=exist_ok)


def rename(src: str, dst: str) -> None:
    os.rename(to_os_path(src), to_os_path(dst))


def replace(src: str, dst: str) -> None:
    os.replace(to_os_path(src), to_os_path(dst))


def remove(path: str) -> None:
    os.remove(to_os_path(path))


def rmdir(path: str) -> None:
    os.rmdir(to_os_path(path))


def chmod(path: str, mode: int) -> None:
    os.chmod(to_os_path(path), mode)


def utime(path: str, times: Any) -> None:
    os.utime(to_os_path(path), times)


def readlink(path: str) -> str:
    return os.readlink(to_os_path(path))


def open_file(path: str, mode: str = "r", *args: Any, **kwargs: Any):
    return open(to_os_path(path), mode, *args, **kwargs)


def os_open(path: str, flags: int, mode: int = 0o777) -> int:
    return os.open(to_os_path(path), flags, mode)


def rmtree(path: str) -> None:
    shutil.rmtree(to_os_path(path))


def chdir(path: str) -> None:
    """Change process CWD, ignoring MAX_PATH failures on Windows.

    pyftpdlib uses ``chdir`` only to *validate* the target directory, then
    restores the original process CWD.  ``SetCurrentDirectoryW`` still
    fails past MAX_PATH without the long-path opt-in, even with ``\\\\?\\``.
    Existence is checked first so missing directories still error.
    """
    os_path = to_os_path(path)
    if not os.path.isdir(os_path):
        raise FileNotFoundError(2, "No such directory", path)
    try:
        os.chdir(os_path)
    except OSError as exc:
        # SetCurrentDirectoryW still fails past MAX_PATH without the
        # long-path opt-in (WinError 3 / 206). Access-denied must propagate.
        if os.name != "nt" or getattr(exc, "winerror", None) not in (3, 161, 206):
            raise
