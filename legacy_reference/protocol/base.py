"""
协议层基础定义

提供：
  - FileServerProtocol: 受支持的文件传输协议枚举
  - TlsConfig: FTPS（FTP over TLS）配置数据类
  - SshConfig: SFTP 服务端 SSH 配置数据类
  - AbstractFileServerBackend: 文件服务器后端抽象接口
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

from model.entities import UserProfile


# =============================================================================
# 协议枚举
# =============================================================================

class FileServerProtocol(Enum):
    """受支持的文件传输协议。"""
    FTP  = "ftp"
    FTPS = "ftps"
    SFTP = "sftp"


# =============================================================================
# 协议专属配置数据类
# =============================================================================

@dataclass
class TlsConfig:
    """
    FTPS（FTP over TLS/SSL）配置。

    certfile / keyfile 使用 PEM 格式。可通过 OpenSSL 自签：
        openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
    """
    certfile:    str  = ""
    keyfile:     str  = ""
    require_tls: bool = True    # True=强制 TLS，False=允许明文降级

    def is_valid(self) -> Tuple[bool, str]:
        if not self.certfile:
            return False, "certfile 不能为空"
        if not os.path.isfile(self.certfile):
            return False, f"certfile 不存在: {self.certfile}"
        if self.keyfile and not os.path.isfile(self.keyfile):
            return False, f"keyfile 不存在: {self.keyfile}"
        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "certfile":    self.certfile,
            "keyfile":     self.keyfile,
            "require_tls": self.require_tls,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TlsConfig":
        return cls(
            certfile=data.get("certfile", ""),
            keyfile=data.get("keyfile", ""),
            require_tls=data.get("require_tls", True),
        )


@dataclass
class SshConfig:
    """
    SFTP 服务端 SSH 配置。

    host_key_path: RSA/ECDSA 服务端私钥路径（PEM），用于 SSH 握手。
    可通过以下命令生成：
        ssh-keygen -t rsa -b 4096 -f sftp_host_key -N ""
    """
    host_key_path: str       = ""
    auth_methods:  List[str] = field(default_factory=lambda: ["password"])

    def is_valid(self) -> Tuple[bool, str]:
        if not self.host_key_path:
            return False, "host_key_path 不能为空"
        if not os.path.isfile(self.host_key_path):
            return False, f"host_key_path 不存在: {self.host_key_path}"
        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_key_path": self.host_key_path,
            "auth_methods":  self.auth_methods,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SshConfig":
        return cls(
            host_key_path=data.get("host_key_path", ""),
            auth_methods=data.get("auth_methods", ["password"]),
        )


# =============================================================================
# 后端抽象接口（业务层唯一依赖）
# =============================================================================

class AbstractFileServerBackend(ABC):
    """
    文件服务器后端抽象接口。

    职责
    ----
    - 接收业务层传入的配置（主机/端口/用户列表）
    - 隐藏底层协议（pyftpdlib / paramiko 等）所有实现细节
    - 提供统一的 configure() → start()（阻塞）→ stop() 生命周期

    派生类
    ------
      FtpBackend   → 明文 FTP
      FtpsBackend  → FTP over TLS
      SftpBackend  → SSH 文件传输

    典型用法（在 QThread.run() 中）
    --------------------------------
        backend.configure(host, port, users)
        backend.start()          # 阻塞直到 stop() 被调用
    """

    # ------------------------------------------------------------------
    # 协议标识（子类必须实现）
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def protocol(self) -> FileServerProtocol:
        """返回本后端对应的协议类型。"""

    @property
    def protocol_name(self) -> str:
        """返回协议的可读大写名称，如 "FTP"、"FTPS"、"SFTP"。"""
        return self.protocol.value.upper()

    # ------------------------------------------------------------------
    # 生命周期（子类必须实现）
    # ------------------------------------------------------------------

    @abstractmethod
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
        """
        配置后端参数。必须在 start() 之前调用。

        Args:
            host:            监听地址（"0.0.0.0" = 全部接口）
            port:            监听端口
            users:           用户配置列表
            max_cons:        最大并发连接数
            max_cons_per_ip: 每 IP 最大并发连接数
            banner:          欢迎横幅（协议支持时使用）
        """

    @abstractmethod
    def start(self) -> None:
        """
        启动服务器主循环（阻塞直到 stop() 被调用）。
        应在独立线程中运行。
        """

    @abstractmethod
    def stop(self) -> None:
        """
        停止服务器，释放所有连接和资源。
        可从任意线程安全调用。
        """

    # ------------------------------------------------------------------
    # 可选钩子（子类可覆盖）
    # ------------------------------------------------------------------

    def validate_config(self) -> Tuple[bool, Optional[str]]:
        """
        验证后端配置是否完整有效。

        Returns:
            (is_valid, error_message)
        """
        return True, None
