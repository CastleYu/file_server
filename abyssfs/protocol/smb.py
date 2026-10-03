"""
SMB 后端实现（基于 impacket SimpleSMBServer）。

该后端把单个 AbyssFS 用户的根目录与虚拟目录发布为 SMB shares。
当前基于 impacket 的高层服务器 API，安全地保留项目现有用户隔离语义时仅支持
一个 SMB 用户；多用户/每 share ACL 需要更深层的 SMB 服务器定制。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import traceback
from dataclasses import dataclass
from typing import List, Optional

from abyssfs import ospath
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.model.permission import FilePermission
from abyssfs.protocol.base import AbstractFileServerBackend, FileServerProtocol

_logger = logging.getLogger(__name__)

_EMPTY_LM_HASH = "AAD3B435B51404EEAAD3B435B51404EE"
_SHARE_INVALID_RE = re.compile(r'[\\/\[\]:|<>+=;,?*" ]+')


@dataclass
class _SmbShare:
    name: str
    path: str
    comment: str
    read_only: bool


def _has_impacket_smbserver() -> bool:
    try:
        return importlib.util.find_spec("impacket.smbserver") is not None
    except ModuleNotFoundError:
        return False


def _sanitize_share_name(raw: str, fallback: str) -> str:
    name = _SHARE_INVALID_RE.sub("_", (raw or "").strip()).strip("._")
    if not name:
        name = fallback
    return name[:80]


def _share_read_only(permission: FilePermission) -> bool:
    writable = (
        FilePermission.WRITE
        | FilePermission.APPEND
        | FilePermission.DELETE
        | FilePermission.RENAME
        | FilePermission.MKDIR
        | FilePermission.CHMOD
        | FilePermission.MODIFY_TIME
    )
    return not bool(permission & writable)


class SmbBackend(AbstractFileServerBackend):
    """
    SMB 文件共享后端。

    说明
    ----
    - 运行时依赖 impacket: pip install impacket
    - 默认端口为 445；非管理员环境可改用 1445 等高端口。
    - NTLM 认证需要 UserProfile.smb_nt_hash；旧配置用户需要重新设置密码生成。
    - 当前实现为单用户安全模式：该用户根目录作为一个 share，虚拟目录各自作为 share。
    """

    def __init__(self) -> None:
        self._server = None
        self._host: str = "0.0.0.0"
        self._port: int = 445
        self._users: List[UserProfile] = []
        self._shares: List[_SmbShare] = []
        _logger.info("[SmbBackend] 初始化")

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.SMB

    def configure(
        self,
        host: str,
        port: int,
        users: List[UserProfile],
        *,
        max_cons: int = 256,
        max_cons_per_ip: int = 5,
        banner: str = "",
        loaded_dir_marker_enabled: bool = False,
    ) -> None:
        _logger.info(
            f"[SmbBackend] configure(): host={host!r}, port={port}, users={len(users)}"
        )
        self._host = host
        self._port = port
        self._users = list(users)
        self._shares = self._build_shares(self._users)

    def validate_config(self) -> tuple:
        if not _has_impacket_smbserver():
            return False, "SMB 后端需要 impacket 库，请执行: pip install impacket"

        if not self._users:
            return True, None

        smb_users = [user for user in self._users if user.smb_nt_hash]
        if not smb_users:
            return False, "没有可用于 SMB/NTLM 的用户凭据；请重新设置用户密码以生成 smb_nt_hash"
        if len(smb_users) != len(self._users):
            missing = ", ".join(user.username for user in self._users if not user.smb_nt_hash)
            return False, f"以下用户缺少 smb_nt_hash，不能用于 SMB: {missing}"
        if len(smb_users) > 1:
            return (
                False,
                "当前 SMB 后端处于单用户安全模式；impacket SimpleSMBServer "
                "无法可靠复用 AbyssFS 的多用户目录隔离，请仅配置一个用户后启动 SMB",
            )
        if not self._shares:
            return False, "没有可发布的 SMB 共享目录"
        return True, None

    def start(self) -> None:
        _logger.info(f"[SmbBackend] start(): host={self._host!r}, port={self._port}")
        try:
            from impacket import smbserver
        except ImportError as exc:
            raise RuntimeError("SMB 后端需要 impacket 库，请执行: pip install impacket") from exc

        ok, err = self.validate_config()
        if not ok:
            raise RuntimeError(err or "SMB 配置无效")

        user = self._users[0]
        try:
            server = smbserver.SimpleSMBServer(
                listenAddress=self._host,
                listenPort=self._port,
            )
            if hasattr(server, "setSMB2Support"):
                server.setSMB2Support(True)
            if hasattr(server, "addCredential"):
                server.addCredential(user.username, 0, _EMPTY_LM_HASH, user.smb_nt_hash)

            for share in self._shares:
                self._add_share(server, share)

            self._server = server
            _logger.info(
                f"[SmbBackend] SMB 服务器已配置: user={user.username!r}, "
                f"shares={[share.name for share in self._shares]}"
            )
            server.start()
        except Exception as exc:
            _logger.error(f"[SmbBackend] start() 异常: {exc}\n{traceback.format_exc()}")
            raise
        finally:
            self._server = None

    def stop(self) -> None:
        _logger.info("[SmbBackend] stop() 调用")
        if self._server is not None:
            try:
                self._server.stop()
            except Exception as exc:
                _logger.error(f"[SmbBackend] stop() 异常: {exc}\n{traceback.format_exc()}")
            finally:
                self._server = None

    def _build_shares(self, users: List[UserProfile]) -> List[_SmbShare]:
        shares: List[_SmbShare] = []
        used_names: set = set()

        for user in users:
            if user.root_dir and ospath.isdir(user.root_dir):
                root_name = self._unique_share_name(
                    _sanitize_share_name(user.username, "ROOT"),
                    used_names,
                )
                shares.append(
                    _SmbShare(
                        name=root_name,
                        path=os.path.abspath(user.root_dir),
                        comment=f"AbyssFS root for {user.username}",
                        read_only=_share_read_only(user.root_permission),
                    )
                )

            for vdir in user.get_mountable_virtual_dirs(strict_required=True):
                share = self._share_from_vdir(user, vdir, used_names)
                shares.append(share)

        return shares

    def _share_from_vdir(
        self,
        user: UserProfile,
        vdir: VirtualDirectory,
        used_names: set,
    ) -> _SmbShare:
        raw_name = f"{user.username}_{vdir.get_display_name()}"
        share_name = self._unique_share_name(
            _sanitize_share_name(raw_name, "SHARE"),
            used_names,
        )
        return _SmbShare(
            name=share_name,
            path=os.path.abspath(vdir.real_path),
            comment=f"AbyssFS virtual directory {vdir.get_display_name()} for {user.username}",
            read_only=_share_read_only(vdir.permission),
        )

    @staticmethod
    def _unique_share_name(name: str, used_names: set) -> str:
        candidate = name
        suffix = 2
        while candidate.upper() in used_names:
            suffix_text = f"_{suffix}"
            candidate = f"{name[:80 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used_names.add(candidate.upper())
        return candidate

    @staticmethod
    def _add_share(server, share: _SmbShare) -> None:
        read_only = "yes" if share.read_only else "no"
        try:
            server.addShare(
                share.name,
                share.path,
                share.comment,
                readOnly=read_only,
            )
        except TypeError:
            try:
                server.addShare(share.name, share.path, share.comment, 0, read_only)
            except TypeError:
                server.addShare(share.name, share.path, share.comment)
                _logger.warning(
                    f"[SmbBackend] 当前 impacket addShare() 不支持 readOnly 参数: "
                    f"share={share.name!r}"
                )
