"""
协议层公共导出及后端工厂函数。
"""

import logging
import traceback
from typing import Optional

from protocol.base import (  # noqa: F401
    FileServerProtocol,
    TlsConfig,
    SshConfig,
    AbstractFileServerBackend,
)
from protocol.ftp import (  # noqa: F401
    FtpBackend,
    FtpsBackend,
    VirtualFTPHandler,
    _make_ftp_handler_class,
)
from protocol.sftp import (  # noqa: F401
    SftpBackend,
    _SftpVirtualServerInterface,
    _SftpVirtualSFTPHandle,
    _SftpVirtualServerInterface2,
)

_logger = logging.getLogger(__name__)


def create_backend(
    protocol: FileServerProtocol,
    *,
    tls_config: Optional[TlsConfig] = None,
    ssh_config: Optional[SshConfig] = None,
) -> AbstractFileServerBackend:
    """
    后端工厂函数。

    Args:
        protocol:   目标协议
        tls_config: FTPS 所需的 TLS 配置（仅 FTPS 时有效）
        ssh_config: SFTP 所需的 SSH 配置（仅 SFTP 时有效）

    Returns:
        对应协议的 AbstractFileServerBackend 实例

    Example::
        backend = create_backend(
            FileServerProtocol.FTPS,
            tls_config=TlsConfig(certfile="cert.pem", keyfile="key.pem"),
        )
    """
    _logger.info(
        f"[create_backend] 创建后端: protocol={protocol.value}, "
        f"tls_certfile={tls_config.certfile if tls_config else None!r}, "
        f"ssh_host_key={ssh_config.host_key_path if ssh_config else None!r}"
    )
    try:
        if protocol == FileServerProtocol.FTP:
            backend = FtpBackend()
        elif protocol == FileServerProtocol.FTPS:
            backend = FtpsBackend(tls_config=tls_config)
        elif protocol == FileServerProtocol.SFTP:
            backend = SftpBackend(ssh_config=ssh_config)
        else:
            raise ValueError(f"不支持的协议: {protocol}")

        _logger.info(
            f"[create_backend] 后端创建成功: {backend.__class__.__name__}"
        )
        return backend

    except Exception as exc:
        _logger.error(
            f"[create_backend] 创建后端失败: protocol={protocol.value}, "
            f"{exc}\n{traceback.format_exc()}"
        )
        raise
