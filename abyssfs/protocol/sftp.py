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
import traceback
from typing import Dict, List, Optional, Any

from abyssfs.model.permission import FilePermission
from abyssfs.model.entities import UserProfile
from abyssfs import ospath
from abyssfs.fs.delete_queue import AsyncDeleteService
from abyssfs.fs.session import VfsSession
from abyssfs.service.registry import DirectoryRegistry
from abyssfs.protocol.base import AbstractFileServerBackend, FileServerProtocol, SshConfig

_logger = logging.getLogger(__name__)

# paramiko 内部的 transport logger 会将 SSH banner 读取失败（客户端误连/扫描）
# 以 ERROR 级别输出完整 traceback，信息量大但无实际价值。
# 将其级别调为 WARNING，仅在真正的传输层错误时输出。
logging.getLogger("paramiko.transport").setLevel(logging.WARNING)
logging.getLogger("paramiko.transport.sftp").setLevel(logging.WARNING)


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
        _logger.debug(f"[_SftpVirtualServerInterface] 初始化: users={list(users.keys())}")

    def _make_server_interface_cls(self) -> type:
        import paramiko

        users_ref = self._users
        logger    = _logger

        class _Impl(paramiko.ServerInterface):
            _authenticated_user: Optional[str] = None

            def check_channel_request(self, kind, chanid):
                logger.debug(f"[SSH] check_channel_request: kind={kind!r}, chanid={chanid}")
                if kind == "session":
                    return paramiko.OPEN_SUCCEEDED
                return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_auth_password(self, username, password):
                profile = users_ref.get(username)
                if profile and profile.password == password:
                    _Impl._authenticated_user = username
                    logger.info(f"[SSH] 密码认证成功: {username!r}")
                    return paramiko.AUTH_SUCCESSFUL
                logger.warning(f"[SSH] 密码认证失败: {username!r}")
                return paramiko.AUTH_FAILED

            def check_auth_none(self, username):
                logger.debug(f"[SSH] check_auth_none: {username!r} → AUTH_FAILED")
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
        self._base     = paramiko.SFTPHandle(flags)
        self.readfile  = None
        self.writefile = None

    def __getattr__(self, name):
        return getattr(self._base, name)


class _SftpVirtualServerInterface2:
    """
    SFTP 子系统接口（实现 paramiko.SFTPServerInterface）。
    将 SFTP 路径操作路由到对应真实路径，并执行权限检查。
    """

    def __init__(
        self,
        profile: UserProfile,
        loaded_dir_marker_enabled: bool = False,
        *,
        registry: Optional[DirectoryRegistry] = None,
        username: Optional[str] = None,
    ) -> None:
        self._session = VfsSession(
            profile.root_dir,
            loaded_dir_marker_enabled=loaded_dir_marker_enabled,
        )
        self._session.set_user_profile(profile)
        if registry is not None:
            self._session.set_user_registry(registry, username or profile.username)
        _logger.debug(
            f"[_SftpVirtualServerInterface2] 初始化: "
            f"user={profile.username!r}, root={self._root!r}, "
            f"mappings={list(self._mappings.keys())}"
        )

    @property
    def _profile(self) -> Optional[UserProfile]:
        return self._session.profile

    @property
    def _root(self) -> str:
        return self._session.real_root

    @property
    def _mappings(self):
        return self._session.mappings

    @property
    def _dir_marker(self):
        return self._session._dir_marker

    def _sync_profile(self) -> None:
        self._session.sync_profile()

    def _ftp_to_real(self, sftp_path: str) -> str:
        """将 SFTP 路径映射到真实文件系统路径。"""
        result = self._session.ftp2fs(sftp_path)
        _logger.debug(f"[SFTP] _ftp_to_real: {sftp_path!r} → {result!r}")
        return result

    def _has_permission(self, sftp_path: str, perm: FilePermission) -> bool:
        return self._session.has_perm(sftp_path, perm)

    # --- SFTPServerInterface 方法 ---

    def _make_sftp_server_interface_cls(self) -> type:
        import paramiko
        import stat as stat_mod

        owner        = self
        dir_marker   = self._dir_marker
        ftp2real     = self._ftp_to_real
        has_perm     = self._has_permission
        logger       = _logger
        delete_svc   = AsyncDeleteService.default()

        class _Impl(paramiko.SFTPServerInterface):

            def __init__(self_inner, server, *largs, **kwargs):
                super().__init__(server, *largs, **kwargs)

            def _attr_from_path(self_inner, path: str):
                try:
                    attr          = paramiko.SFTPAttributes.from_stat(ospath.stat(path))
                    attr.filename = os.path.basename(path)
                    return attr
                except OSError as exc:
                    logger.error(
                        f"[SFTP] _attr_from_path() 失败: path={path!r}, {exc}"
                    )
                    raise

            def _attr_for_listing(self_inner, path: str, display_name: str):
                attr = self_inner._attr_from_path(path)
                if ospath.isdir(path):
                    attr.filename = dir_marker.display_name(display_name, path)
                else:
                    attr.filename = display_name
                return attr

            def list_folder(self_inner, path: str):
                logger.debug(f"[SFTP] list_folder: {path!r}")
                if not has_perm(path, FilePermission.LIST):
                    logger.warning(f"[SFTP] list_folder 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    entries = []
                    norm = os.path.normpath(real)
                    root_ref = owner._root
                    mappings_ref = owner._mappings
                    if norm == root_ref:
                        names = (
                            {
                                name for name in ospath.listdir(real)
                                if not AsyncDeleteService.is_internal_name(name)
                            }
                            if ospath.isdir(real)
                            else set()
                        )
                        for vname in mappings_ref:
                            names.add(vname)
                        for name in names:
                            p = os.path.join(real, name)
                            if name in mappings_ref:
                                p = mappings_ref[name].real_path
                            try:
                                entries.append(self_inner._attr_for_listing(p, name))
                            except OSError as exc:
                                logger.warning(
                                    f"[SFTP] list_folder 跳过条目 {name!r}: {exc}"
                                )
                    else:
                        for name in ospath.listdir(real):
                            if AsyncDeleteService.is_internal_name(name):
                                continue
                            try:
                                entries.append(
                                    self_inner._attr_for_listing(os.path.join(real, name), name)
                                )
                            except OSError as exc:
                                logger.warning(
                                    f"[SFTP] list_folder 跳过条目 {name!r}: {exc}"
                                )
                    logger.debug(
                        f"[SFTP] list_folder 返回 {len(entries)} 项: {path!r}"
                    )
                    dir_marker.mark_loaded(real)
                    return entries
                except OSError as exc:
                    logger.error(
                        f"[SFTP] list_folder 失败: path={path!r}, "
                        f"real={real!r}, {exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTP_FAILURE

            def stat(self_inner, path: str):
                logger.debug(f"[SFTP] stat: {path!r}")
                if not has_perm(path, FilePermission.READ):
                    logger.warning(f"[SFTP] stat 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    return self_inner._attr_from_path(real)
                except OSError as exc:
                    logger.error(
                        f"[SFTP] stat 失败: path={path!r}, real={real!r}, {exc}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            lstat = stat

            def open(self_inner, path: str, flags: int, attr):
                write_flag = bool(
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                )
                need_perm = FilePermission.WRITE if write_flag else FilePermission.READ
                logger.debug(
                    f"[SFTP] open: {path!r}, flags={flags}, "
                    f"write={write_flag}, need_perm={need_perm!r}"
                )
                if not has_perm(path, need_perm):
                    logger.warning(f"[SFTP] open 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                real = ftp2real(path)
                try:
                    ospath.makedirs(os.path.dirname(real), exist_ok=True)
                    fd = ospath.os_open(
                        real,
                        flags | os.O_BINARY if hasattr(os, "O_BINARY") else flags,
                        0o666,
                    )
                    fobj   = os.fdopen(fd, "r+b" if not write_flag else "w+b")
                    handle = paramiko.SFTPHandle(flags)
                    handle.filename  = real
                    handle.readfile  = fobj
                    handle.writefile = fobj
                    logger.info(f"[SFTP] open 成功: {real!r}, write={write_flag}")
                    return handle
                except OSError as exc:
                    logger.error(
                        f"[SFTP] open 失败: path={path!r}, real={real!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def remove(self_inner, path: str):
                logger.debug(f"[SFTP] remove: {path!r}")
                if not has_perm(path, FilePermission.DELETE):
                    logger.warning(f"[SFTP] remove 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    real = ftp2real(path)
                    staged = delete_svc.file(real)
                    logger.info(f"[SFTP] remove 已隔离: {real!r} -> {staged!r}")
                    return paramiko.SFTP_OK
                except OSError as exc:
                    logger.error(
                        f"[SFTP] remove 失败: {path!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def rename(self_inner, oldpath: str, newpath: str):
                logger.debug(f"[SFTP] rename: {oldpath!r} → {newpath!r}")
                if not has_perm(oldpath, FilePermission.RENAME):
                    logger.warning(f"[SFTP] rename 权限拒绝: {oldpath!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    old_real = ftp2real(oldpath)
                    new_real = ftp2real(newpath)
                    ospath.rename(old_real, new_real)
                    logger.info(f"[SFTP] rename 成功: {old_real!r} → {new_real!r}")
                    return paramiko.SFTP_OK
                except OSError as exc:
                    logger.error(
                        f"[SFTP] rename 失败: {oldpath!r} → {newpath!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def mkdir(self_inner, path: str, attr):
                logger.debug(f"[SFTP] mkdir: {path!r}")
                if not has_perm(path, FilePermission.MKDIR):
                    logger.warning(f"[SFTP] mkdir 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    real = ftp2real(path)
                    ospath.mkdir(real)
                    logger.info(f"[SFTP] mkdir 成功: {real!r}")
                    return paramiko.SFTP_OK
                except OSError as exc:
                    logger.error(
                        f"[SFTP] mkdir 失败: {path!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def rmdir(self_inner, path: str):
                logger.debug(f"[SFTP] rmdir: {path!r}")
                if not has_perm(path, FilePermission.DELETE):
                    logger.warning(f"[SFTP] rmdir 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    real = ftp2real(path)
                    virtual_roots = {
                        os.path.normcase(os.path.normpath(vd.real_path))
                        for vd in owner._mappings.values()
                    }
                    if os.path.normcase(os.path.normpath(real)) in virtual_roots:
                        return paramiko.SFTP_PERMISSION_DENIED
                    staged = delete_svc.dir(real)
                    logger.info(f"[SFTP] rmdir 已隔离: {real!r} -> {staged!r}")
                    return paramiko.SFTP_OK
                except OSError as exc:
                    logger.error(
                        f"[SFTP] rmdir 失败: {path!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def chattr(self_inner, path: str, attr):
                logger.debug(f"[SFTP] chattr: {path!r}, st_mode={attr.st_mode}")
                if not has_perm(path, FilePermission.CHMOD):
                    logger.warning(f"[SFTP] chattr 权限拒绝: {path!r}")
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    real = ftp2real(path)
                    if attr.st_mode is not None:
                        ospath.chmod(real, attr.st_mode)
                        logger.info(
                            f"[SFTP] chattr 成功: {real!r}, "
                            f"mode={oct(attr.st_mode)}"
                        )
                    return paramiko.SFTP_OK
                except OSError as exc:
                    logger.error(
                        f"[SFTP] chattr 失败: {path!r}, "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def canonicalize(self_inner, path: str) -> str:
                if not path or path == ".":
                    return "/"
                result = ("/" + path.replace("\\", "/").lstrip("/")).rstrip("/") or "/"
                logger.debug(f"[SFTP] canonicalize: {path!r} → {result!r}")
                return result

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
        self._loaded_dir_marker_enabled = False
        self._registry = DirectoryRegistry()
        _logger.info(
            f"[SftpBackend] 初始化: "
            f"host_key_path={self._ssh_config.host_key_path!r}"
        )

    supports_directory_hot_apply = True

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.SFTP

    def validate_config(self) -> tuple:
        ok, msg = self._ssh_config.is_valid()
        if not ok:
            _logger.warning(f"[SftpBackend] validate_config() 失败: {msg}")
            return False, f"SFTP SSH 配置无效: {msg}"
        _logger.info("[SftpBackend] validate_config() 通过")
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
        loaded_dir_marker_enabled: bool = False,
    ) -> None:
        _logger.info(
            f"[SftpBackend] configure(): host={host!r}, port={port}, "
            f"users={len(users)}"
        )
        self._host  = host
        self._port  = port
        snapshot = self._registry.replace(users, refresh=True)
        self._users = {
            username: entry.profile
            for username, entry in snapshot.users.items()
        }
        self._loaded_dir_marker_enabled = loaded_dir_marker_enabled
        _logger.info(
            f"[SftpBackend] configured: host={host!r}, port={port}, "
            f"usernames={list(self._users.keys())}"
        )

    def start(self) -> None:
        _logger.info(
            f"[SftpBackend] start(): host={self._host!r}, port={self._port}, "
            f"host_key={self._ssh_config.host_key_path!r}"
        )
        try:
            import paramiko
        except ImportError as exc:
            _logger.error(f"[SftpBackend] paramiko 未安装: {exc}")
            raise RuntimeError(
                "SFTP 后端需要 paramiko 库，请执行: pip install paramiko"
            )

        self._stop_event.clear()

        try:
            host_key = paramiko.RSAKey(filename=self._ssh_config.host_key_path)
            _logger.info(
                f"[SftpBackend] 主机密钥已加载: "
                f"{self._ssh_config.host_key_path!r}"
            )
        except Exception as exc:
            _logger.error(
                f"[SftpBackend] 加载主机密钥失败: "
                f"{self._ssh_config.host_key_path!r}, {exc}\n{traceback.format_exc()}"
            )
            raise

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
            self._sock.bind((self._host, self._port))
            self._sock.listen(10)
            self._sock.settimeout(1.0)
            _logger.info(
                f"[SftpBackend] 监听端口已绑定: "
                f"{self._host}:{self._port}"
            )
        except OSError as exc:
            _logger.error(
                f"[SftpBackend] 绑定监听端口失败: "
                f"{self._host}:{self._port}, {exc}\n{traceback.format_exc()}"
            )
            raise

        _logger.info(f"[SftpBackend] 进入主循环, 等待客户端连接...")
        while not self._stop_event.is_set():
            try:
                client_sock, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop_event.is_set():
                    _logger.error(
                        f"[SftpBackend] accept() OSError: "
                        f"{exc}\n{traceback.format_exc()}"
                    )
                break

            _logger.info(f"[SftpBackend] 接受连接: {addr}")
            self._emit_access(addr[0])
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock, host_key, addr),
                daemon=True,
            )
            t.start()

        _logger.info("[SftpBackend] 主循环退出")

    def _handle_client(
        self,
        client_sock: socket.socket,
        host_key: Any,
        addr: Any,
    ) -> None:
        """在独立线程中处理单个 SSH/SFTP 客户端连接。"""
        _logger.info(f"[SftpBackend] 处理客户端连接: {addr}")
        try:
            import paramiko

            transport = paramiko.Transport(client_sock)
            transport.add_server_key(host_key)

            _authenticated: List[Optional[str]] = [None]
            registry_ref = self._registry
            logger    = _logger

            class _ServerInterface(paramiko.ServerInterface):
                def check_channel_request(self, kind, chanid):
                    logger.debug(
                        f"[SSH][{addr}] check_channel_request: {kind!r}"
                    )
                    if kind == "session":
                        return paramiko.OPEN_SUCCEEDED
                    return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

                def check_auth_password(self, username, password):
                    entry = registry_ref.user(username)
                    if not entry:
                        return paramiko.AUTH_FAILED
                    profile = entry.profile
                    
                    # IP 过滤
                    if not profile.check_ip(addr[0]):
                        logger.warning(
                            f"[SSH][{addr}] IP 过滤拒绝: user={username!r}, ip={addr[0]}"
                        )
                        return paramiko.AUTH_FAILED

                    if profile.verify_password(password):
                        _authenticated[0] = username
                        logger.info(
                            f"[SSH][{addr}] 密码认证成功: {username!r}"
                        )
                        return paramiko.AUTH_SUCCESSFUL
                    
                    logger.warning(
                        f"[SSH][{addr}] 密码认证失败: {username!r}"
                    )
                    return paramiko.AUTH_FAILED

                def check_auth_none(self, username):
                    return paramiko.AUTH_FAILED

                def get_allowed_auths(self, username):
                    return "password"

            def make_sftp_interface(server, *args, **kwargs):
                username = _authenticated[0]
                entry = registry_ref.user(username) if username is not None else None
                if entry is None:
                    raise RuntimeError("SFTP 认证用户配置不存在")
                iface = _SftpVirtualServerInterface2(
                    entry.profile,
                    loaded_dir_marker_enabled=self._loaded_dir_marker_enabled,
                    registry=self._registry,
                    username=username,
                )
                interface_cls = iface._make_sftp_server_interface_cls()
                return interface_cls(server, *args, **kwargs)

            transport.set_subsystem_handler(
                "sftp",
                paramiko.SFTPServer,
                make_sftp_interface,
            )

            try:
                transport.start_server(server=_ServerInterface())
                _logger.debug(f"[SftpBackend][{addr}] SSH 握手完成，等待通道")
                chan = transport.accept(timeout=30)
                if chan is None:
                    _logger.warning(
                        f"[SftpBackend][{addr}] transport.accept() 超时，无通道"
                    )
                    return

                username = _authenticated[0]
                entry = registry_ref.user(username) if username is not None else None
                if entry is None:
                    _logger.warning(
                        f"[SftpBackend][{addr}] 认证用户不在用户列表: {username!r}"
                    )
                    chan.close()
                    return

                profile = entry.profile
                _logger.info(
                    f"[SftpBackend][{addr}] SFTP 会话开始: "
                    f"user={username!r}, root={profile.root_dir!r}"
                )
                transport.join()
                _logger.info(
                    f"[SftpBackend][{addr}] SFTP 会话结束: {username!r}"
                )

            except Exception as exc:
                exc_name = type(exc).__name__
                exc_msg  = str(exc)
                # SSH banner 读取失败通常是客户端误连或端口扫描，降级为 WARNING
                if (
                    "banner" in exc_msg.lower()
                    or "SSHException" in exc_name
                    or isinstance(exc, (TimeoutError, ConnectionResetError))
                ):
                    _logger.warning(
                        f"[SftpBackend][{addr}] SSH 握手失败（可能是非 SSH 客户端扫描）: "
                        f"{exc_name}: {exc_msg}"
                    )
                else:
                    _logger.error(
                        f"[SftpBackend][{addr}] SSH/SFTP 会话异常: "
                        f"{exc}\n{traceback.format_exc()}"
                    )
            finally:
                try:
                    transport.close()
                except Exception:
                    pass
                try:
                    client_sock.close()
                except Exception:
                    pass
                _logger.debug(f"[SftpBackend][{addr}] 连接已清理")

        except Exception as exc:
            _logger.error(
                f"[SftpBackend][{addr}] _handle_client() 顶层异常: "
                f"{exc}\n{traceback.format_exc()}"
            )

    def stop(self) -> None:
        _logger.info("[SftpBackend] stop() 调用")
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
                _logger.info("[SftpBackend] 监听 socket 已关闭")
            except OSError as exc:
                _logger.error(
                    f"[SftpBackend] 关闭 socket 异常: "
                    f"{exc}\n{traceback.format_exc()}"
                )
            finally:
                self._sock = None
        _logger.info("[SftpBackend] stop() 完成")

    def apply_directories(self, users: List[UserProfile]) -> int:
        previous = self._registry.snapshot()
        old_users = self._users
        try:
            snapshot = self._registry.replace(users, refresh=True)
            self._users = {
                username: entry.profile
                for username, entry in snapshot.users.items()
            }
            _logger.info(
                f"[SftpBackend] 目录配置热更新完成: "
                f"version={snapshot.version}, users={len(users)}"
            )
            return snapshot.version
        except Exception:
            self._registry.restore(previous)
            self._users = old_users
            raise
