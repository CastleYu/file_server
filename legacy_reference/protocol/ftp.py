"""
FTP / FTPS 后端实现（基于 pyftpdlib）

提供：
  - _make_ftp_handler_class(): 动态创建绑定了认证器与虚拟文件系统的 FTPHandler 子类
  - VirtualFTPHandler: 自定义 FTP 处理器（登录时设置虚拟目录映射）
  - FtpBackend: 明文 FTP 后端
  - FtpsBackend: FTPS（FTP over TLS）后端
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from model.entities import UserProfile
from fs.virtual_fs import VirtualFS
from fs.authorizer import _VirtualDirAuthorizer, VirtualDirAuthorizer
from protocol.base import AbstractFileServerBackend, FileServerProtocol, TlsConfig

_logger = logging.getLogger(__name__)


def _make_ftp_handler_class(
    authorizer: _VirtualDirAuthorizer,
    banner: str,
    abstracted_fs_cls: Optional[type] = None,
) -> type:
    """
    动态创建一个 FTPHandler 子类，绑定认证器与虚拟文件系统。
    避免在模块级持有全局处理器状态（多后端实例并存时安全）。
    """
    from pyftpdlib.handlers import FTPHandler

    class _Handler(FTPHandler):
        pass

    _Handler.authorizer = authorizer
    _Handler.banner = banner or "FTP Server Ready."
    _Handler.abstracted_fs = abstracted_fs_cls if abstracted_fs_cls is not None else VirtualFS

    def _on_login(self, username: str) -> None:
        FTPHandler.on_login(self, username)
        if isinstance(self.authorizer, _VirtualDirAuthorizer):
            profile = self.authorizer.get_user_profile(username)
            if profile and hasattr(self, "fs") and hasattr(self.fs, "set_user_profile"):
                self.fs.set_user_profile(profile)

    _Handler.on_login = _on_login
    return _Handler


class VirtualFTPHandler:
    """
    自定义 FTP 处理器（在连接时设置用户的虚拟目录映射）。

    此类作为工厂使用：调用 make() 获得绑定了 authorizer 的 FTPHandler 子类。
    也可直接继承 FTPHandler 使用（见 make_class 方法）。
    """

    @staticmethod
    def make_class(authorizer: _VirtualDirAuthorizer, banner: str = "") -> type:
        """创建绑定了认证器的 FTPHandler 子类。"""
        return _make_ftp_handler_class(authorizer, banner)


# =============================================================================
# FTP 后端实现
# =============================================================================

class FtpBackend(AbstractFileServerBackend):
    """
    明文 FTP 后端（基于 pyftpdlib）。

    特点
    ----
    - 支持每用户虚拟目录映射
    - 路径遍历防护 + 符号链接逃逸检测
    - 细粒度按目录权限控制
    """

    def __init__(self) -> None:
        self._server    = None
        self._host:     str  = "0.0.0.0"
        self._port:     int  = 2121
        self._users:    List[UserProfile] = []
        self._max_cons: int  = 256
        self._max_cons_per_ip: int = 5
        self._banner:   str  = ""

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.FTP

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
        self._host           = host
        self._port           = port
        self._users          = list(users)
        self._max_cons       = max_cons
        self._max_cons_per_ip = max_cons_per_ip
        self._banner         = banner

    def _build_server(self):
        """创建 pyftpdlib FTPServer 实例（子类可覆盖以注入 TLS）。"""
        from pyftpdlib.servers import FTPServer

        authorizer = _VirtualDirAuthorizer()
        for user in self._users:
            authorizer.add_user_profile(user)

        handler_cls = _make_ftp_handler_class(
            authorizer=authorizer,
            banner=self._banner or f"{self.protocol_name} Server Ready.",
        )
        handler_cls = self._configure_handler(handler_cls)

        server = FTPServer((self._host, self._port), handler_cls)
        server.max_cons        = self._max_cons
        server.max_cons_per_ip = self._max_cons_per_ip
        return server

    def _configure_handler(self, handler_cls: type) -> type:
        """钩子方法：子类（FtpsBackend）可在此注入 TLS 上下文。"""
        return handler_cls

    def start(self) -> None:
        self._server = self._build_server()
        _logger.info(
            f"[{self.protocol_name}] 启动服务器，监听 {self._host}:{self._port}"
        )
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server:
            _logger.info(f"[{self.protocol_name}] 停止服务器")
            self._server.close_all()
            self._server = None


# =============================================================================
# FTPS 后端实现（FTP over TLS）
# =============================================================================

class FtpsBackend(FtpBackend):
    """
    FTPS 后端（FTP over TLS/SSL，基于 pyftpdlib TLS_FTPHandler）。

    使用 pyftpdlib 内置的 TLS_FTPHandler，只需提供证书和私钥即可。
    默认开启 FTPS explicit（STARTTLS），客户端连接后显式升级到 TLS。
    """

    def __init__(self, tls_config: Optional[TlsConfig] = None) -> None:
        super().__init__()
        self.tls_config: TlsConfig = tls_config or TlsConfig()

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.FTPS

    def validate_config(self) -> Tuple[bool, Optional[str]]:
        ok, msg = self.tls_config.is_valid()
        if not ok:
            return False, f"FTPS TLS 配置无效: {msg}"
        return True, None

    def _configure_handler(self, handler_cls: type) -> type:
        """注入 TLS 上下文到处理器。"""
        try:
            from pyftpdlib.handlers import TLS_FTPHandler
        except ImportError:
            raise RuntimeError(
                "pyftpdlib 未包含 TLS 支持，请确认版本 >= 1.5 且已安装 ssl 模块。"
            )

        # 创建一个继承 TLS_FTPHandler 的新类，并复制已配置的属性
        class _TLSHandler(TLS_FTPHandler):
            pass

        _TLSHandler.authorizer    = handler_cls.authorizer
        _TLSHandler.banner        = handler_cls.banner
        _TLSHandler.abstracted_fs = handler_cls.abstracted_fs
        _TLSHandler.on_login      = handler_cls.on_login

        _TLSHandler.certfile = self.tls_config.certfile
        if self.tls_config.keyfile:
            _TLSHandler.keyfile = self.tls_config.keyfile

        if self.tls_config.require_tls:
            _TLSHandler.tls_control_required = True
            _TLSHandler.tls_data_required    = True

        return _TLSHandler
