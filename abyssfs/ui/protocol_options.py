"""Shared protocol radio copy, main-window labels, and radio builders."""

from __future__ import annotations

from typing import Callable, List, Sequence, Tuple

from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from abyssfs.protocol.base import FileServerProtocol

ProtocolChoice = Tuple[FileServerProtocol, str, str]

PROTOCOL_CHOICES: Sequence[ProtocolChoice] = (
    (
        FileServerProtocol.FTP,
        "FTP（明文，无加密）",
        "最兼容，适合受信任的局域网环境。"
        "密码和数据均以明文传输，不建议公网使用。",
    ),
    (
        FileServerProtocol.FTPS,
        "FTPS（FTP over TLS，推荐）",
        "在 FTP 基础上加 TLS 加密通道（STARTTLS）。"
        "需要 X.509 证书 + 私钥，可自动生成自签名版。",
    ),
    (
        FileServerProtocol.SFTP,
        "SFTP（SSH 文件传输）",
        "基于 SSH 协议，天然全链路加密。"
        "需要 RSA/ECDSA 服务端主机密钥，可自动生成。"
        "需安装 paramiko（pip install paramiko）。",
    ),
    (
        FileServerProtocol.SMB,
        "SMB（Windows 文件共享）",
        "以 SMB share 形式发布目录，适合局域网文件共享。"
        "需安装 impacket（pip install impacket）；默认 445 端口通常需要管理员权限。",
    ),
    (
        FileServerProtocol.WEBDAV,
        "WebDAV（HTTP 文件访问）",
        "以 HTTP(S) 发布同一套虚拟目录树，可用现有 FTPS 证书启用 HTTPS。"
        "需安装 wsgidav 与 cheroot；默认端口 8080。目录热更新与 FTP 相同。",
    ),
    (
        FileServerProtocol.NFS,
        "NFS（实验级 NFSv3）",
        "用户态 NFSv3，单用户映射，默认端口 2049，不绑定 111。"
        "Linux 客户端：mount -o port=2049,mountport=2049,nolock。"
        "目录变更需受控重启。",
    ),
)

MAIN_PROTO_LABELS = {
    FileServerProtocol.FTP: "FTP（明文）",
    FileServerProtocol.FTPS: "FTPS（TLS加密）",
    FileServerProtocol.SFTP: "SFTP（SSH加密）",
    FileServerProtocol.SMB: "SMB（文件共享）",
    FileServerProtocol.WEBDAV: "WebDAV",
    FileServerProtocol.NFS: "NFS（实验）",
}

MAIN_PROTO_ICONS = {
    FileServerProtocol.FTP: "🔓",
    FileServerProtocol.FTPS: "🔒",
    FileServerProtocol.SFTP: "🛡",
    FileServerProtocol.SMB: "🪟",
    FileServerProtocol.WEBDAV: "🌐",
    FileServerProtocol.NFS: "📦",
}


def add_protocol_radios(
    owner: QWidget,
    proto_lay: QVBoxLayout,
    btn_group: QButtonGroup,
    current_protocol: FileServerProtocol,
    on_toggled: Callable[[bool], None],
) -> List[QRadioButton]:
    radios: List[QRadioButton] = []
    for proto, title, desc in PROTOCOL_CHOICES:
        rb = QRadioButton(title)
        rb.setChecked(proto == current_protocol)
        rb.setProperty("proto", proto.value)
        btn_group.addButton(rb)
        rb.toggled.connect(on_toggled)

        note = QLabel(desc)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px; margin-left: 22px;")

        box = QVBoxLayout()
        box.setSpacing(2)
        box.setContentsMargins(0, 4, 0, 6)
        box.addWidget(rb)
        box.addWidget(note)
        container = QFrame()
        container.setLayout(box)
        proto_lay.addWidget(container)
        radios.append(rb)
    return radios
