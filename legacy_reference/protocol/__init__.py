"""
协议层公共导出及后端工厂函数。
"""
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
from typing import Optional


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
    if protocol == FileServerProtocol.FTP:
        return FtpBackend()
    if protocol == FileServerProtocol.FTPS:
        return FtpsBackend(tls_config=tls_config)
    if protocol == FileServerProtocol.SFTP:
        return SftpBackend(ssh_config=ssh_config)
    raise ValueError(f"不支持的协议: {protocol}")
