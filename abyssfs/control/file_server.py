"""
文件服务器业务调度层

提供协议无关的文件服务器上下文、线程和控制器：
  - FileServerContext: 协议无关的服务器配置（含用户列表、TLS/SSH 配置）
  - FileServerThread: 协议无关的服务器工作线程（通过 AbstractFileServerBackend 驱动）
  - FileServerController: 协议无关的业务控制器（用户管理、服务器启停、协议切换）
"""

from __future__ import annotations

import os
import logging
import traceback
from typing import Dict, List, Optional, Any, Tuple

from abyssfs.control.base import BaseServerContext, BaseServerThread, BaseServerController
from abyssfs.model.entities import UserProfile
from abyssfs.protocol.base import (
    AbstractFileServerBackend,
    FileServerProtocol,
    TlsConfig,
    SshConfig,
    WebdavConfig,
    NfsConfig,
)
from abyssfs.protocol import create_backend
from abyssfs.paths import ROOT_DIR

_logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled", "开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled", "关闭"}:
            return False
    return bool(value)


# =============================================================================
# FileServerContext（协议无关的服务器上下文）
# =============================================================================

class FileServerContext(BaseServerContext):
    """
    协议无关的文件服务器上下文。

    在 BaseServerContext 基础上扩展：
      - protocol:    目标协议（FTP / FTPS / SFTP / SMB）
      - tls_config:  FTPS 配置
      - ssh_config:  SFTP 配置
      - users:       用户列表
      - banner, max_cons, max_cons_per_ip
    """

    def __init__(
        self,
        config_path: str = "file_server.json",
        protocol: FileServerProtocol = FileServerProtocol.FTP,
    ) -> None:
        default_ports = {
            FileServerProtocol.FTP:    2121,
            FileServerProtocol.FTPS:   2121,
            FileServerProtocol.SFTP:   2222,
            FileServerProtocol.SMB:    445,
            FileServerProtocol.WEBDAV: 8080,
            FileServerProtocol.NFS:    2049,
        }
        super().__init__(config_path, default_port=default_ports.get(protocol, 2121))
        self.protocol:        FileServerProtocol = protocol
        self.users:           List[UserProfile]  = []
        self.banner:          str  = ""
        self.max_cons:        int  = 256
        self.max_cons_per_ip: int  = 5
        self.loaded_dir_marker_enabled: bool = False
        self.default_root:    str  = str(ROOT_DIR)
        self.tls_config: TlsConfig = TlsConfig()
        self.ssh_config: SshConfig = SshConfig()
        self.webdav_config: WebdavConfig = WebdavConfig()
        self.nfs_config: NfsConfig = NfsConfig()
        _logger.info(
            f"[FileServerContext] 初始化完成: protocol={protocol.value}, "
            f"port={self.port}, config_path={config_path!r}"
        )

    # --- 用户管理 ---

    def add_user(self, user: UserProfile) -> None:
        _logger.debug(f"[FileServerContext] add_user: {user.username!r}")
        self.users = [u for u in self.users if u.username != user.username]
        self.users.append(user)

    def remove_user(self, username: str) -> None:
        before = len(self.users)
        self.users = [u for u in self.users if u.username != username]
        after = len(self.users)
        _logger.debug(
            f"[FileServerContext] remove_user: {username!r}, "
            f"用户数量 {before} → {after}"
        )

    def get_user(self, username: str) -> Optional[UserProfile]:
        result = next((u for u in self.users if u.username == username), None)
        _logger.debug(
            f"[FileServerContext] get_user: {username!r} → "
            f"{'found' if result else 'not found'}"
        )
        return result

    # --- 序列化 ---

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "protocol":        self.protocol.value,
            "host":            self.host,
            "port":            self.port,
            "banner":          self.banner,
            "max_cons":        self.max_cons,
            "max_cons_per_ip": self.max_cons_per_ip,
            "plugins": {
                "loaded_dir_marker": {
                    "enabled": self.loaded_dir_marker_enabled,
                },
            },
            "default_root":    self.default_root,
            "tls_config":      self.tls_config.to_dict(),
            "ssh_config":      self.ssh_config.to_dict(),
            "webdav_config":   self.webdav_config.to_dict(),
            "nfs_config":      self.nfs_config.to_dict(),
            "users":           [u.to_dict() for u in self.users],
        }
        _logger.debug(
            f"[FileServerContext] to_dict: protocol={data['protocol']}, "
            f"port={data['port']}, users={len(data['users'])}"
        )
        return data

    def from_dict(self, data: Dict[str, Any]) -> None:
        _logger.info(f"[FileServerContext] from_dict 开始: keys={list(data.keys())}")
        proto_str = data.get("protocol", "ftp")
        try:
            self.protocol = FileServerProtocol(proto_str)
        except ValueError:
            _logger.warning(
                f"[FileServerContext] 未知协议 {proto_str!r}，回退到 FTP"
            )
            self.protocol = FileServerProtocol.FTP

        self.host            = data.get("host", "0.0.0.0")
        self.port            = data.get("port", 2121)
        self.banner          = data.get("banner", "")
        self.max_cons        = data.get("max_cons", 256)
        self.max_cons_per_ip = data.get("max_cons_per_ip", 5)
        plugins = data.get("plugins", {})
        loaded_dir_marker = plugins.get("loaded_dir_marker", {}) if isinstance(plugins, dict) else {}
        self.loaded_dir_marker_enabled = _coerce_bool(
            data.get(
                "loaded_dir_marker_enabled",
                loaded_dir_marker.get("enabled", False)
            ),
            False,
        )
        self.default_root    = data.get("default_root", self.default_root)

        try:
            self.tls_config = TlsConfig.from_dict(data.get("tls_config", {}))
        except Exception as exc:
            _logger.error(
                f"[FileServerContext] 解析 tls_config 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self.tls_config = TlsConfig()

        try:
            self.ssh_config = SshConfig.from_dict(data.get("ssh_config", {}))
        except Exception as exc:
            _logger.error(
                f"[FileServerContext] 解析 ssh_config 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self.ssh_config = SshConfig()

        try:
            self.webdav_config = WebdavConfig.from_dict(data.get("webdav_config", {}))
        except Exception as exc:
            _logger.error(
                f"[FileServerContext] 解析 webdav_config 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self.webdav_config = WebdavConfig()

        try:
            self.nfs_config = NfsConfig.from_dict(data.get("nfs_config", {}))
        except Exception as exc:
            _logger.error(
                f"[FileServerContext] 解析 nfs_config 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self.nfs_config = NfsConfig()

        users = []
        for i, u_data in enumerate(data.get("users", [])):
            try:
                users.append(UserProfile.from_dict(u_data))
            except Exception as exc:
                _logger.error(
                    f"[FileServerContext] 解析用户[{i}]失败: "
                    f"{exc}\n{traceback.format_exc()}"
                )
        self.users = users
        _logger.info(
            f"[FileServerContext] from_dict 完成: protocol={self.protocol.value}, "
            f"port={self.port}, users={len(self.users)}, "
            f"tls_certfile={self.tls_config.certfile!r}, "
            f"ssh_host_key={self.ssh_config.host_key_path!r}"
        )

    def build_backend(self) -> AbstractFileServerBackend:
        """根据当前协议和配置构建对应后端实例。"""
        _logger.info(
            f"[FileServerContext] build_backend: 协议={self.protocol.value}, "
            f"tls_certfile={self.tls_config.certfile!r}, "
            f"ssh_host_key={self.ssh_config.host_key_path!r}"
        )
        try:
            backend = create_backend(
                self.protocol,
                tls_config=self.tls_config,
                ssh_config=self.ssh_config,
                webdav_config=self.webdav_config,
                nfs_config=self.nfs_config,
            )
            _logger.info(
                f"[FileServerContext] backend 创建成功: "
                f"{backend.__class__.__name__}"
            )
            return backend
        except Exception as exc:
            _logger.error(
                f"[FileServerContext] build_backend() 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise


# =============================================================================
# FileServerThread（协议无关的服务器工作线程）
# =============================================================================

class FileServerThread(BaseServerThread):
    """
    协议无关的文件服务器工作线程。

    使用 AbstractFileServerBackend 接口驱动底层服务器，
    业务层通过回调信号接收状态通知。
    """

    def __init__(
        self,
        context: FileServerContext,
        backend: AbstractFileServerBackend,
        parent=None,
    ) -> None:
        super().__init__(context, parent)
        self.context:  FileServerContext          = context
        self._backend: AbstractFileServerBackend  = backend
        _logger.info(
            f"[FileServerThread] 创建: backend={backend.__class__.__name__}, "
            f"protocol={backend.protocol_name}"
        )

    def setup_server(self) -> None:
        _logger.info(
            f"[FileServerThread] setup_server() 开始: "
            f"protocol={self._backend.protocol_name}, "
            f"host={self.context.host!r}, port={self.context.port}"
        )
        try:
            if not os.path.exists(self.context.default_root):
                os.makedirs(self.context.default_root, exist_ok=True)
                _logger.info(
                    f"[FileServerThread] 默认根目录已创建: "
                    f"{self.context.default_root!r}"
                )

            users = []
            for user in self.context.users:
                if not user.root_dir:
                    user.root_dir = self.context.default_root
                    _logger.debug(
                        f"[FileServerThread] 用户 {user.username!r} "
                        f"使用默认根目录: {user.root_dir!r}"
                    )
                active_vdirs = user.get_mountable_virtual_dirs(
                    strict_required=True,
                    refresh=True,
                )
                users.append(user)
                self.status_signal.emit(
                    f"已加载用户: {user.username} → {user.root_dir} "
                    f"[权限: {user.root_perm}, 虚拟目录: {len(active_vdirs)}/{len(user.virtual_dirs)}]"
                )
                _logger.info(
                    f"[FileServerThread] 用户已加载: username={user.username!r}, "
                    f"root_dir={user.root_dir!r}, root_perm={user.root_perm!r}, "
                    f"active_vdirs={len(active_vdirs)}, total_vdirs={len(user.virtual_dirs)}"
                )

            _logger.info(
                f"[FileServerThread] 调用 backend.configure(): "
                f"users={len(users)}, max_cons={self.context.max_cons}, "
                f"max_cons_per_ip={self.context.max_cons_per_ip}"
            )
            self._backend.configure(
                host=self.context.host,
                port=self.context.port,
                users=users,
                max_cons=self.context.max_cons,
                max_cons_per_ip=self.context.max_cons_per_ip,
                banner=self.context.banner,
                loaded_dir_marker_enabled=self.context.loaded_dir_marker_enabled,
            )
            self._backend.set_access_callback(self.client_access_signal.emit)
            msg = (
                f"后端就绪 [{self._backend.protocol_name}]: "
                f"{self.context.host}:{self.context.port}"
            )
            self.status_signal.emit(msg)
            _logger.info(f"[FileServerThread] setup_server() 完成: {msg}")

        except Exception as exc:
            _logger.error(
                f"[FileServerThread] setup_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def serve(self) -> None:
        _logger.info(
            f"[FileServerThread] serve() 开始: "
            f"protocol={self._backend.protocol_name}"
        )
        try:
            self._backend.start()
            _logger.info(
                f"[FileServerThread] serve() 正常返回: "
                f"protocol={self._backend.protocol_name}"
            )
        except Exception as exc:
            _logger.error(
                f"[FileServerThread] serve() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def stop_server(self) -> None:
        _logger.info(
            f"[FileServerThread] stop_server() 调用: "
            f"protocol={self._backend.protocol_name}"
        )
        try:
            self._backend.stop()
            msg = f"[{self._backend.protocol_name}] 服务器已停止"
            self.status_signal.emit(msg)
            _logger.info(f"[FileServerThread] {msg}")
        except Exception as exc:
            _logger.error(
                f"[FileServerThread] stop_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def apply_directories(self, users: List[UserProfile]) -> int:
        """在线应用目录配置，不重启监听服务。"""
        return self._backend.apply_directories(users)


# =============================================================================
# FileServerController（协议无关的业务控制器）
# =============================================================================

class FileServerController(BaseServerController):
    """
    协议无关的文件服务器业务控制器。

    提供用户管理、目录配置、服务器启停等业务操作，
    底层协议（FTP / FTPS / SFTP / SMB）对调用者完全透明。

    切换协议示例::

        controller = FileServerController(protocol=FileServerProtocol.FTP)
        controller.switch_protocol(
            FileServerProtocol.FTPS,
            tls_config=TlsConfig(certfile="cert.pem"),
        )
    """

    def __init__(
        self,
        config_path: str = "file_server.json",
        protocol: FileServerProtocol = FileServerProtocol.FTP,
    ) -> None:
        context = FileServerContext(config_path, protocol)
        super().__init__(context)
        self.context: FileServerContext = context
        self.callbacks["on_user_changed"] = []
        _logger.info(
            f"[FileServerController] 初始化完成: "
            f"protocol={self.context.protocol.value}, "
            f"port={self.context.port}, "
            f"users={len(self.context.users)}"
        )

    # ------------------------------------------------------------------
    # 协议切换
    # ------------------------------------------------------------------

    def switch_protocol(
        self,
        protocol: FileServerProtocol,
        *,
        tls_config: Optional[TlsConfig] = None,
        ssh_config: Optional[SshConfig] = None,
    ) -> Tuple[bool, Optional[str]]:
        """切换服务协议。服务器运行时无法切换。"""
        _logger.info(
            f"[FileServerController] switch_protocol(): "
            f"{self.context.protocol.value} → {protocol.value}, "
            f"tls_certfile={tls_config.certfile if tls_config else None!r}, "
            f"ssh_host_key={ssh_config.host_key_path if ssh_config else None!r}"
        )
        if self.is_server_running():
            msg = "服务器运行中，请先停止后再切换协议"
            _logger.warning(f"[FileServerController] switch_protocol() 被拒绝: {msg}")
            return False, msg

        try:
            old_protocol = self.context.protocol
            self.context.protocol = protocol

            # 切换协议时同步更新默认端口（若用户未手动改过）
            default_ports = {
                FileServerProtocol.FTP:    2121,
                FileServerProtocol.FTPS:   2121,
                FileServerProtocol.SFTP:   2222,
                FileServerProtocol.SMB:    445,
                FileServerProtocol.WEBDAV: 8080,
                FileServerProtocol.NFS:    2049,
            }
            if tls_config is not None:
                self.context.tls_config = tls_config
                _logger.info(
                    f"[FileServerController] tls_config 已更新: "
                    f"certfile={tls_config.certfile!r}, keyfile={tls_config.keyfile!r}"
                )
            if ssh_config is not None:
                self.context.ssh_config = ssh_config
                _logger.info(
                    f"[FileServerController] ssh_config 已更新: "
                    f"host_key_path={ssh_config.host_key_path!r}"
                )

            self.context.save_to_disk()
            _logger.info(
                f"[FileServerController] 协议已切换: "
                f"{old_protocol.value} → {protocol.value}"
            )
            return True, None

        except Exception as exc:
            _logger.error(
                f"[FileServerController] switch_protocol() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False, f"切换协议失败: {exc}"

    def update_global_settings(
        self,
        *,
        protocol: FileServerProtocol,
        tls_config: TlsConfig,
        ssh_config: SshConfig,
        port: int,
        host: str,
        max_cons: int,
        max_cons_per_ip: int,
        loaded_dir_marker_enabled: bool,
    ) -> Tuple[bool, Optional[str]]:
        """一次性更新全局设置，并只保存一次配置。"""
        _logger.info(
            f"[FileServerController] update_global_settings(): "
            f"protocol={protocol.value}, host={host!r}, port={port}, "
            f"max_cons={max_cons}, max_cons_per_ip={max_cons_per_ip}, "
            f"loaded_dir_marker={loaded_dir_marker_enabled}"
        )
        if self.is_server_running():
            msg = "服务器运行中，请先停止后再修改全局设置"
            _logger.warning(f"[FileServerController] update_global_settings() 被拒绝: {msg}")
            return False, msg
        if port < 1 or port > 65535:
            return False, f"端口号必须在 1-65535 之间: {port}"
        if max_cons < 1:
            return False, "最大连接数必须大于 0"
        if max_cons_per_ip < 0:
            return False, "单 IP 最大连接数不能为负数"

        old_state = (
            self.context.protocol,
            self.context.tls_config,
            self.context.ssh_config,
            self.context.port,
            self.context.host,
            self.context.max_cons,
            self.context.max_cons_per_ip,
            self.context.loaded_dir_marker_enabled,
        )
        try:
            self.context.protocol = protocol
            self.context.tls_config = tls_config
            self.context.ssh_config = ssh_config
            self.context.port = port
            self.context.host = host
            self.context.max_cons = max_cons
            self.context.max_cons_per_ip = max_cons_per_ip
            self.context.loaded_dir_marker_enabled = bool(loaded_dir_marker_enabled)
            self.context.save_to_disk()
            _logger.info("[FileServerController] 全局设置已一次性保存")
            return True, None
        except Exception as exc:
            (
                self.context.protocol,
                self.context.tls_config,
                self.context.ssh_config,
                self.context.port,
                self.context.host,
                self.context.max_cons,
                self.context.max_cons_per_ip,
                self.context.loaded_dir_marker_enabled,
            ) = old_state
            _logger.error(
                f"[FileServerController] update_global_settings() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False, f"更新全局设置失败: {exc}"

    def get_protocol(self) -> FileServerProtocol:
        """获取当前配置的协议。"""
        return self.context.protocol

    def get_max_cons(self) -> int:
        return self.context.max_cons
        
    def set_max_cons(self, max_cons: int) -> Tuple[bool, Optional[str]]:
        if max_cons < 1:
            return (False, "最大连接数必须大于 0")
        self.context.max_cons = max_cons
        self.context.save_to_disk()
        return (True, None)

    def get_max_cons_per_ip(self) -> int:
        return self.context.max_cons_per_ip
        
    def set_max_cons_per_ip(self, max_cons_per_ip: int) -> Tuple[bool, Optional[str]]:
        if max_cons_per_ip < 0:
            return (False, "单 IP 最大连接数不能为负数")
        self.context.max_cons_per_ip = max_cons_per_ip
        self.context.save_to_disk()
        return (True, None)

    def is_loaded_dir_marker_enabled(self) -> bool:
        return self.context.loaded_dir_marker_enabled

    def set_loaded_dir_marker_enabled(self, enabled: bool) -> Tuple[bool, Optional[str]]:
        if self.is_server_running():
            msg = "服务器运行中无法修改插件状态"
            _logger.warning(f"[FileServerController] {msg}")
            return (False, msg)
        self.context.loaded_dir_marker_enabled = bool(enabled)
        self.context.save_to_disk()
        return (True, None)

    # ------------------------------------------------------------------
    # 用户管理
    # ------------------------------------------------------------------

    @staticmethod
    def _auth_signature(user: UserProfile) -> tuple:
        rules = tuple(
            (rule.pattern, rule.rule_type.value, rule.enabled)
            for rule in user.ip_rules
        )
        return (
            user.username,
            user.password,
            user.smb_nt_hash,
            user.allow_all_ips,
            rules,
        )

    def _validate_runtime_users(
        self,
        users: List[UserProfile],
    ) -> Tuple[bool, Optional[str]]:
        backend = self.context.build_backend()
        backend.configure(
            host=self.context.host,
            port=self.context.port,
            users=users,
            max_cons=self.context.max_cons,
            max_cons_per_ip=self.context.max_cons_per_ip,
            banner=self.context.banner,
            loaded_dir_marker_enabled=self.context.loaded_dir_marker_enabled,
        )
        return backend.validate_config()

    def get_users(self) -> List[UserProfile]:
        users = list(self.context.users)
        _logger.debug(f"[FileServerController] get_users(): {len(users)} 个用户")
        return users

    def get_user(self, username: str) -> Optional[UserProfile]:
        result = self.context.get_user(username)
        _logger.debug(
            f"[FileServerController] get_user({username!r}): "
            f"{'found' if result else 'not found'}"
        )
        return result

    def add_user(self, user: UserProfile) -> Tuple[bool, Optional[str]]:
        _logger.info(
            f"[FileServerController] add_user(): username={user.username!r}, "
            f"root_dir={user.root_dir!r}, vdirs={len(user.virtual_dirs)}"
        )
        try:
            if self.is_server_running():
                return False, "服务器运行中只能热更新现有用户的目录配置"
            errors = user.validate()
            if errors:
                msg = "; ".join(errors)
                _logger.warning(f"[FileServerController] add_user() 验证失败: {msg}")
                return False, msg
            if self.context.get_user(user.username) is not None:
                msg = f"用户名已存在: {user.username}"
                _logger.warning(f"[FileServerController] add_user() 冲突: {msg}")
                return False, msg
            self.context.add_user(user)
            self.context.save_to_disk()
            self._emit_callback("on_user_changed")
            _logger.info(
                f"[FileServerController] add_user() 成功: {user.username!r}"
            )
            return True, None
        except Exception as exc:
            _logger.error(
                f"[FileServerController] add_user() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False, f"添加用户失败: {exc}"

    def update_user(
        self, original_username: str, updated_user: UserProfile
    ) -> Tuple[bool, Optional[str]]:
        _logger.info(
            f"[FileServerController] update_user(): "
            f"original={original_username!r} → new={updated_user.username!r}"
        )
        try:
            errors = updated_user.validate()
            if errors:
                msg = "; ".join(errors)
                _logger.warning(f"[FileServerController] update_user() 验证失败: {msg}")
                return False, msg

            current = self.context.get_user(original_username)
            if current is None:
                return False, f"用户不存在: {original_username}"
            duplicate = self.context.get_user(updated_user.username)
            if duplicate is not None and duplicate is not current:
                return False, f"用户名已存在: {updated_user.username}"

            running = self.is_server_running()
            if running and self._auth_signature(current) != self._auth_signature(updated_user):
                return False, "服务器运行中不能修改用户名、密码或 IP 规则，请先停止服务"

            old_users = list(self.context.users)
            candidate = [
                updated_user if user.username == original_username else user
                for user in old_users
            ]
            applied = False
            restarted = False
            try:
                if running and not self.supports_directory_hot_apply():
                    valid, runtime_error = self._validate_runtime_users(candidate)
                    if not valid:
                        return False, runtime_error or "目录配置无效"
                    if not self.stop_server():
                        return False, "无法停止服务以应用目录配置"
                    restarted = True
                elif running:
                    if self.server_thread is None:
                        return False, "服务器线程状态无效，无法热更新目录"
                    self.server_thread.apply_directories(candidate)
                    applied = True
                self.context.users = candidate
                self.context.save_to_disk()
                if restarted:
                    started, start_error = self.start_server()
                    if not started:
                        raise RuntimeError(start_error or "服务重新启动失败")
            except Exception:
                self.context.users = old_users
                if applied and self.server_thread is not None:
                    try:
                        self.server_thread.apply_directories(old_users)
                    except Exception as rollback_exc:
                        _logger.error(
                            f"[FileServerController] 目录热更新回滚失败: "
                            f"{rollback_exc}\n{traceback.format_exc()}"
                        )
                if restarted:
                    try:
                        self.context.save_to_disk()
                        if not self.is_server_running():
                            self.start_server()
                    except Exception as rollback_exc:
                        _logger.error(
                            f"[FileServerController] 受控重启回滚失败: "
                            f"{rollback_exc}\n{traceback.format_exc()}"
                        )
                raise

            self._emit_callback("on_user_changed")
            _logger.info(
                f"[FileServerController] update_user() 成功: "
                f"{updated_user.username!r}, hot={running}"
            )
            return True, None
        except Exception as exc:
            _logger.error(
                f"[FileServerController] update_user() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False, f"更新用户失败: {exc}"

    def remove_user(self, username: str) -> Tuple[bool, Optional[str]]:
        _logger.info(f"[FileServerController] remove_user({username!r})")
        try:
            if self.is_server_running():
                return False, "服务器运行中不能删除用户，请先停止服务"
            if self.context.get_user(username) is None:
                msg = f"用户不存在: {username}"
                _logger.warning(f"[FileServerController] remove_user() 失败: {msg}")
                return False, msg
            self.context.remove_user(username)
            self.context.save_to_disk()
            self._emit_callback("on_user_changed")
            _logger.info(f"[FileServerController] remove_user() 成功: {username!r}")
            return True, None
        except Exception as exc:
            _logger.error(
                f"[FileServerController] remove_user() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False, f"删除用户失败: {exc}"

    def get_default_root(self) -> str:
        return self.context.default_root

    def supports_directory_hot_apply(self) -> bool:
        thread = self.server_thread
        backend = getattr(thread, "_backend", None)
        if backend is not None:
            return bool(getattr(backend, "supports_directory_hot_apply", False))
        try:
            return bool(self.context.build_backend().supports_directory_hot_apply)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 服务器线程工厂
    # ------------------------------------------------------------------

    def create_server_thread(self) -> FileServerThread:
        _logger.info(
            f"[FileServerController] create_server_thread(): "
            f"protocol={self.context.protocol.value}"
        )
        try:
            backend = self.context.build_backend()
            ok, err = backend.validate_config()
            if not ok:
                _logger.error(
                    f"[FileServerController] 后端配置验证失败: {err}"
                )
                raise RuntimeError(f"后端配置验证失败: {err}")
            _logger.info(
                f"[FileServerController] 后端配置验证通过: "
                f"{backend.__class__.__name__}"
            )
            return FileServerThread(self.context, backend)
        except Exception as exc:
            _logger.error(
                f"[FileServerController] create_server_thread() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise
