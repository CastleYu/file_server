"""
SFTP 后端实现（基于 paramiko）

提供：
  - _SftpVirtualServerInterface: SFTP 服务端接口（SSH 握手认证）
  - _SftpVirtualSFTPHandle: SFTP 文件句柄
  - _SftpVirtualServerInterface2: SFTP 子系统接口（路径路由 + 权限检查）
  - SftpBackend: SFTP 后端

依赖: pip install paramiko
"""

from __future__ import annotations

import os
import socket
import threading
import logging
from typing import Dict, List, Optional, Any

from model.permission import FilePermission
from model.entities import UserProfile
from fs.virtual_fs import _build_virtual_mappings
from protocol.base import AbstractFileServerBackend, FileServerProtocol, SshConfig

_logger = logging.getLogger(__name__)


# =============================================================================
# SFTP 后端内部组件（paramiko）
# =============================================================================

class _SftpVirtualServerInterface:
    """
    SFTP 服务端接口（实现 paramiko.ServerInterface）。

    负责 SSH 握手期间的用户认证。
    """

    def __init__(self, users: Dict[str, UserProfile]) -> None:
        self._users = users

    def _make_server_interface_cls(self) -> type:
        import paramiko

        users_ref = self._users

        class _Impl(paramiko.ServerInterface):
            _authenticated_user: Optional[str] = None

            def check_channel_request(self, kind, chanid):
                if kind == "session":
                    return paramiko.OPEN_SUCCEEDED
                return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_auth_password(self, username, password):
                profile = users_ref.get(username)
                if profile and profile.password == password:
                    _Impl._authenticated_user = username
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def check_auth_none(self, username):
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "password"

        return _Impl


class _SftpVirtualSFTPHandle:
    """
    SFTP 文件句柄（实现 paramiko.SFTPHandle）。
    将读写操作代理到实际文件对象。
    """

    def __init__(self, flags: int) -> None:
        import paramiko
        self._base = paramiko.SFTPHandle(flags)
        self.readfile  = None
        self.writefile = None

    def __getattr__(self, name):
        return getattr(self._base, name)


class _SftpVirtualServerInterface2:
    """
    SFTP 子系统接口（实现 paramiko.SFTPServerInterface）。

    将 SFTP 路径操作路由到对应真实路径，并执行权限检查。
    """

    def __init__(self, profile: UserProfile) -> None:
        self._profile  = profile
        self._root     = os.path.normpath(profile.root_dir)
        self._mappings = _build_virtual_mappings(profile)

    # --- 路径解析 ---

    def _ftp_to_real(self, sftp_path: str) -> str:
        """将 SFTP 路径映射到真实文件系统路径。"""
        sftp_path = sftp_path.replace("\\", "/")
        if not sftp_path.startswith("/"):
            sftp_path = "/" + sftp_path
        parts = [p for p in sftp_path.split("/") if p]
        if parts and parts[0] in self._mappings:
            vdir = self._mappings[parts[0]]
            sub  = os.sep.join(parts[1:])
            return os.path.normpath(
                os.path.join(vdir.real_path, sub) if sub else vdir.real_path
            )
        sub = os.sep.join(parts)
        return os.path.normpath(os.path.join(self._root, sub))

    def _has_permission(self, sftp_path: str, perm: FilePermission) -> bool:
        parts = [p for p in sftp_path.replace("\\", "/").lstrip("/").split("/") if p]
        if not parts:
            return bool(self._profile.root_permission & perm)
        first = parts[0]
        for vd in self._profile.virtual_dirs:
            if vd.get_display_name() == first:
                return bool(vd.permission & perm)
        return bool(self._profile.root_permission & perm)

    # --- SFTPServerInterface 方法 ---

    def _make_sftp_server_interface_cls(self) -> type:
        import paramiko
        import stat as stat_mod

        profile_ref  = self._profile
        mappings_ref = self._mappings
        root_ref     = self._root
        ftp2real     = self._ftp_to_real
        has_perm     = self._has_permission

        class _Impl(paramiko.SFTPServerInterface):

            def __init__(self_inner, server, *largs, **kwargs):
                super().__init__(server, *largs, **kwargs)

            def _attr_from_path(self_inner, path: str):
                attr       = paramiko.SFTPAttributes.from_stat(os.stat(path))
                attr.filename = os.path.basename(path)
                return attr

            def list_folder(self_inner, path: str):
                if not has_perm(path, FilePermission.LIST):
                    return paramiko.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    entries = []
                    norm = os.path.normpath(real)
                    if norm == root_ref:
                        names = set(os.listdir(real)) if os.path.isdir(real) else set()
                        for vname in mappings_ref:
                            names.add(vname)
                        for name in names:
                            p = os.path.join(real, name)
                            if name in mappings_ref:
                                p = mappings_ref[name].real_path
                            try:
                                entries.append(self_inner._attr_from_path(p))
                            except OSError:
                                pass
                    else:
                        for name in os.listdir(real):
                            try:
                                entries.append(
                                    self_inner._attr_from_path(os.path.join(real, name))
                                )
                            except OSError:
                                pass
                    return entries
                except OSError:
                    return paramiko.SFTP_FAILURE

            def stat(self_inner, path: str):
                if not has_perm(path, FilePermission.READ):
                    return paramiko.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    return self_inner._attr_from_path(real)
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            lstat = stat

            def open(self_inner, path: str, flags: int, attr):
                import paramiko as pm
                write_flag = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
                need_perm  = FilePermission.WRITE if write_flag else FilePermission.READ
                if not has_perm(path, need_perm):
                    return pm.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    os.makedirs(os.path.dirname(real), exist_ok=True)
                    fd = os.open(real, flags | os.O_BINARY if hasattr(os, "O_BINARY") else flags, 0o666)
                    fobj = os.fdopen(fd, "r+b" if not write_flag else "w+b")
                    handle = pm.SFTPHandle(flags)
                    handle.filename  = real
                    handle.readfile  = fobj
                    handle.writefile = fobj
                    return handle
                except OSError as e:
                    return pm.SFTPServer.convert_errno(e.errno)

            def remove(self_inner, path: str):
                if not has_perm(path, FilePermission.DELETE):
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    os.remove(ftp2real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def rename(self_inner, oldpath: str, newpath: str):
                if not has_perm(oldpath, FilePermission.RENAME):
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    os.rename(ftp2real(oldpath), ftp2real(newpath))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def mkdir(self_inner, path: str, attr):
                if not has_perm(path, FilePermission.MKDIR):
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    os.mkdir(ftp2real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def rmdir(self_inner, path: str):
                if not has_perm(path, FilePermission.DELETE):
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    os.rmdir(ftp2real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def chattr(self_inner, path: str, attr):
                if not has_perm(path, FilePermission.CHMOD):
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    if attr.st_mode is not None:
                        os.chmod(ftp2real(path), attr.st_mode)
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def canonicalize(self_inner, path: str) -> str:
                if not path or path == ".":
                    return "/"
                return ("/" + path.replace("\\", "/").lstrip("/")).rstrip("/") or "/"

        return _Impl


# =============================================================================
# SFTP 后端实现（paramiko）
# =============================================================================

class SftpBackend(AbstractFileServerBackend):
    """
    SFTP 后端（SSH 文件传输协议，基于 paramiko）。

    依赖: pip install paramiko

    注意
    ----
    - SFTP 使用 SSH 传输层，天然加密，无需额外 TLS 证书。
    - 需要提供服务端 RSA/ECDSA 私钥（SshConfig.host_key_path）。
    - 默认认证方式：密码认证。
    """

    def __init__(self, ssh_config: Optional[SshConfig] = None) -> None:
        self._ssh_config: SshConfig = ssh_config or SshConfig()
        self._host:  str  = "0.0.0.0"
        self._port:  int  = 22
        self._users: Dict[str, UserProfile] = {}
        self._sock:  Optional[socket.socket] = None
        self._stop_event = threading.Event()

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.SFTP

    def validate_config(self) -> tuple:
        ok, msg = self._ssh_config.is_valid()
        if not ok:
            return False, f"SFTP SSH 配置无效: {msg}"
        return True, None

    def configure(
        self,
        host: str,
        port: int,
        users: List[UserProfile],
        *,
        max_cons: int = 256,
        max_cons_per_ip: int = 5,
        banner: str = "",
    ) -> None:
        self._host  = host
        self._port  = port
        self._users = {u.username: u for u in users}

    def start(self) -> None:
        try:
            import paramiko
        except ImportError:
            raise RuntimeError(
                "SFTP 后端需要 paramiko 库，请执行: pip install paramiko"
            )

        self._stop_event.clear()

        host_key = paramiko.RSAKey(filename=self._ssh_config.host_key_path)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self._sock.bind((self._host, self._port))
        self._sock.listen(10)
        self._sock.settimeout(1.0)

        _logger.info(f"[SFTP] 启动服务器，监听 {self._host}:{self._port}")

        while not self._stop_event.is_set():
            try:
                client_sock, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            _logger.debug(f"[SFTP] 接受连接: {addr}")
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock, host_key),
                daemon=True,
            )
            t.start()

    def _handle_client(
        self,
        client_sock: socket.socket,
        host_key: Any,
    ) -> None:
        """在独立线程中处理单个 SSH/SFTP 客户端连接。"""
        import paramiko

        transport = paramiko.Transport(client_sock)
        transport.add_server_key(host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, None)

        _authenticated: List[Optional[str]] = [None]
        users_ref = self._users

        class _ServerInterface(paramiko.ServerInterface):
            def check_channel_request(self, kind, chanid):
                if kind == "session":
                    return paramiko.OPEN_SUCCEEDED
                return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_auth_password(self, username, password):
                profile = users_ref.get(username)
                if profile and profile.password == password:
                    _authenticated[0] = username
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def check_auth_none(self, username):
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "password"

        try:
            transport.start_server(server=_ServerInterface())
            chan = transport.accept(timeout=30)
            if chan is None:
                return
            username = _authenticated[0]
            if username is None or username not in self._users:
                chan.close()
                return

            profile = self._users[username]
            sftp_iface = _SftpVirtualServerInterface2(profile)
            sftp_cls   = sftp_iface._make_sftp_server_interface_cls()
            transport.set_subsystem_handler(
                "sftp", paramiko.SFTPServer, sftp_cls
            )
            transport.join()
        except Exception as e:
            _logger.debug(f"[SFTP] 客户端处理异常: {e}")
        finally:
            transport.close()
            client_sock.close()

    def stop(self) -> None:
        _logger.info("[SFTP] 停止服务器")
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
