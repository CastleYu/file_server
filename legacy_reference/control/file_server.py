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
from typing import Dict, List, Optional, Any, Tuple

from control.base import BaseServerContext, BaseServerThread, BaseServerController
from model.entities import UserProfile
from protocol.base import AbstractFileServerBackend, FileServerProtocol, TlsConfig, SshConfig
from protocol import create_backend

_logger = logging.getLogger(__name__)


# =============================================================================
# FileServerContext（协议无关的服务器上下文）
# =============================================================================

class FileServerContext(BaseServerContext):
    """
    协议无关的文件服务器上下文。

    在 BaseServerContext 基础上扩展：
      - protocol:    目标协议（FTP / FTPS / SFTP）
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
            FileServerProtocol.FTP:  2121,
            FileServerProtocol.FTPS: 2121,
            FileServerProtocol.SFTP: 2222,
        }
        super().__init__(config_path, default_port=default_ports[protocol])
        self.protocol:     FileServerProtocol = protocol
        self.users:        List[UserProfile]  = []
        self.banner:       str  = ""
        self.max_cons:     int  = 256
        self.max_cons_per_ip: int = 5
        self.default_root: str = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "root"
        )
        self.tls_config: TlsConfig = TlsConfig()
        self.ssh_config: SshConfig = SshConfig()

    # --- 用户管理 ---

    def add_user(self, user: UserProfile) -> None:
        self.users = [u for u in self.users if u.username != user.username]
        self.users.append(user)

    def remove_user(self, username: str) -> None:
        self.users = [u for u in self.users if u.username != username]

    def get_user(self, username: str) -> Optional[UserProfile]:
        return next((u for u in self.users if u.username == username), None)

    # --- 序列化 ---

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol":       self.protocol.value,
            "host":           self.host,
            "port":           self.port,
            "banner":         self.banner,
            "max_cons":       self.max_cons,
            "max_cons_per_ip": self.max_cons_per_ip,
            "default_root":   self.default_root,
            "tls_config":     self.tls_config.to_dict(),
            "ssh_config":     self.ssh_config.to_dict(),
            "users":          [u.to_dict() for u in self.users],
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        proto_str = data.get("protocol", "ftp")
        try:
            self.protocol = FileServerProtocol(proto_str)
        except ValueError:
            _logger.warning(f"未知协议 '{proto_str}'，回退到 FTP")
            self.protocol = FileServerProtocol.FTP

        self.host             = data.get("host", "0.0.0.0")
        self.port             = data.get("port", 2121)
        self.banner           = data.get("banner", "")
        self.max_cons         = data.get("max_cons", 256)
        self.max_cons_per_ip  = data.get("max_cons_per_ip", 5)
        self.default_root     = data.get("default_root", self.default_root)
        self.tls_config       = TlsConfig.from_dict(data.get("tls_config", {}))
        self.ssh_config       = SshConfig.from_dict(data.get("ssh_config", {}))
        self.users            = [UserProfile.from_dict(u) for u in data.get("users", [])]

    def build_backend(self) -> AbstractFileServerBackend:
        """根据当前协议和配置构建对应后端实例。"""
        return create_backend(
            self.protocol,
            tls_config=self.tls_config,
            ssh_config=self.ssh_config,
        )


# =============================================================================
# FileServerThread（协议无关的服务器工作线程）
# =============================================================================

class FileServerThread(BaseServerThread):
    """
    协议无关的文件服务器工作线程。

    使用 AbstractFileServerBackend 接口驱动底层服务器，
    业务层通过 Qt 信号接收状态通知。
    """

    def __init__(
        self,
        context: FileServerContext,
        backend: AbstractFileServerBackend,
        parent=None,
    ) -> None:
        super().__init__(context, parent)
        self.context: FileServerContext         = context
        self._backend: AbstractFileServerBackend = backend

    def setup_server(self) -> None:
        if not os.path.exists(self.context.default_root):
            os.makedirs(self.context.default_root, exist_ok=True)

        users = []
        for user in self.context.users:
            if not user.root_dir:
                user.root_dir = self.context.default_root
            users.append(user)
            self.status_signal.emit(f"已加载用户: {user.username} -> {user.root_dir}")

        self._backend.configure(
            host=self.context.host,
            port=self.context.port,
            users=users,
            max_cons=self.context.max_cons,
            max_cons_per_ip=self.context.max_cons_per_ip,
            banner=self.context.banner,
        )
        self.status_signal.emit(
            f"后端就绪 [{self._backend.protocol_name}]: "
            f"{self.context.host}:{self.context.port}"
        )

    def serve(self) -> None:
        self._backend.start()

    def stop_server(self) -> None:
        self._backend.stop()
        self.status_signal.emit(
            f"[{self._backend.protocol_name}] 服务器已停止"
        )


# =============================================================================
# FileServerController（协议无关的业务控制器）
# =============================================================================

class FileServerController(BaseServerController):
    """
    协议无关的文件服务器业务控制器。

    提供用户管理、目录配置、服务器启停等业务操作，
    底层协议（FTP / FTPS / SFTP）对调用者完全透明。

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
        if self.is_server_running():
            return False, "服务器运行中，请先停止后再切换协议"

        self.context.protocol = protocol
        if tls_config is not None:
            self.context.tls_config = tls_config
        if ssh_config is not None:
            self.context.ssh_config = ssh_config

        self.context.save_to_disk()
        _logger.info(f"协议已切换为: {protocol.value.upper()}")
        return True, None

    def get_protocol(self) -> FileServerProtocol:
        """获取当前配置的协议。"""
        return self.context.protocol

    # ------------------------------------------------------------------
    # 用户管理
    # ------------------------------------------------------------------

    def get_users(self) -> List[UserProfile]:
        return list(self.context.users)

    def get_user(self, username: str) -> Optional[UserProfile]:
        return self.context.get_user(username)

    def add_user(self, user: UserProfile) -> Tuple[bool, Optional[str]]:
        errors = user.validate()
        if errors:
            return False, "; ".join(errors)
        if self.context.get_user(user.username) is not None:
            return False, f"用户名已存在: {user.username}"
        self.context.add_user(user)
        self.context.save_to_disk()
        self._emit_callback("on_user_changed")
        return True, None

    def update_user(
        self, original_username: str, updated_user: UserProfile
    ) -> Tuple[bool, Optional[str]]:
        errors = updated_user.validate()
        if errors:
            return False, "; ".join(errors)
        self.context.remove_user(original_username)
        self.context.add_user(updated_user)
        self.context.save_to_disk()
        self._emit_callback("on_user_changed")
        return True, None

    def remove_user(self, username: str) -> Tuple[bool, Optional[str]]:
        if self.context.get_user(username) is None:
            return False, f"用户不存在: {username}"
        self.context.remove_user(username)
        self.context.save_to_disk()
        self._emit_callback("on_user_changed")
        return True, None

    def get_default_root(self) -> str:
        return self.context.default_root

    # ------------------------------------------------------------------
    # 服务器线程工厂
    # ------------------------------------------------------------------

    def create_server_thread(self) -> FileServerThread:
        backend = self.context.build_backend()
        ok, err = backend.validate_config()
        if not ok:
            raise RuntimeError(f"后端配置验证失败: {err}")
        return FileServerThread(self.context, backend)
