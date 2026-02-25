"""
全局设置对话框

提供：
  - SettingsDialog: 统一管理协议切换、端口、网络绑定配置、连接数限制等全局设置
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QButtonGroup, QRadioButton,
    QMessageBox, QFrame, QTabWidget, QWidget, QLineEdit
)
from PyQt6.QtCore import Qt

from protocol.base import FileServerProtocol, TlsConfig, SshConfig
from ui.crypto_dialog import CryptoConfigDialog

_logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    全局设置对话框。
    """

    def __init__(
        self,
        current_protocol: FileServerProtocol = FileServerProtocol.FTP,
        tls_config: Optional[TlsConfig] = None,
        ssh_config: Optional[SshConfig] = None,
        port: int = 2121,
        host: str = "0.0.0.0",
        max_cons: int = 256,
        max_cons_per_ip: int = 5,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("全局设置")
        self.setMinimumWidth(500)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._tls_config: TlsConfig = tls_config or TlsConfig()
        self._ssh_config: SshConfig = ssh_config or SshConfig()

        self._init_ui(current_protocol, port, host, max_cons, max_cons_per_ip)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self, current_protocol: FileServerProtocol, port: int, host: str, max_cons: int, max_cons_per_ip: int) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._init_network_tab(port, host, max_cons, max_cons_per_ip)
        self._init_protocol_tab(current_protocol)

        # --- 按钮栏 ---
        btn_row = QHBoxLayout()
        self._btn_ok = QPushButton("确定保存")
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _init_network_tab(self, port: int, host: str, max_cons: int, max_cons_per_ip: int):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # 基础网络配置
        net_group = QGroupBox("网络与端口")
        net_lay = QVBoxLayout(net_group)
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("服务端口 (Port):"))
        self.input_port = QLineEdit(str(port))
        row1.addWidget(self.input_port)
        net_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("监听地址 (Host):"))
        self.input_host = QLineEdit(host)
        self.input_host.setToolTip("监听地址，如 0.0.0.0（监听所有网卡）或 127.0.0.1（只监听本机提升安全性）")
        row2.addWidget(self.input_host)
        net_lay.addLayout(row2)

        lay.addWidget(net_group)

        # 高级安全连接配置
        sec_group = QGroupBox("并发与安全")
        sec_lay = QVBoxLayout(sec_group)
        
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("总并发阈值 (Max Connections):"))
        self.input_max_cons = QLineEdit(str(max_cons))
        self.input_max_cons.setToolTip("服务器允许的最大总并发连接数量，防止资源耗尽攻击 (DoS)")
        row3.addWidget(self.input_max_cons)
        sec_lay.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("单IP容忍阀值 (Max Connections per IP):"))
        self.input_max_cons_per_ip = QLineEdit(str(max_cons_per_ip))
        self.input_max_cons_per_ip.setToolTip("同一个 IP 被允许建立的最大连接数，防止单 IP 发起各类慢速耗尽攻击 (如 Slow-Loris)")
        row4.addWidget(self.input_max_cons_per_ip)
        sec_lay.addLayout(row4)

        lay.addWidget(sec_group)
        lay.addStretch()
        
        self.tabs.addTab(tab, "网络与安全")

    def _init_protocol_tab(self, current_protocol: FileServerProtocol):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # --- 协议选择 ---
        proto_group = QGroupBox("传输协议")
        proto_lay   = QVBoxLayout(proto_group)

        self._btn_group = QButtonGroup(self)

        def _make_radio(proto: FileServerProtocol, title: str, desc: str) -> QRadioButton:
            rb = QRadioButton(title)
            rb.setChecked(proto == current_protocol)
            rb.setProperty("proto", proto.value)
            self._btn_group.addButton(rb)
            rb.toggled.connect(self._on_proto_changed)

            row = QHBoxLayout()
            row.addWidget(rb)
            note = QLabel(desc)
            note.setStyleSheet("color: #888; font-size: 11px;")
            note.setWordWrap(True)
            row.addWidget(note, stretch=1)
            container = QFrame()
            container.setLayout(row)
            proto_lay.addWidget(container)
            return rb

        self._rb_ftp  = _make_radio(
            FileServerProtocol.FTP,
            "FTP（明文，无加密）",
            "最兼容，适合受信任的局域网环境。"
            "密码和数据均以明文传输，不建议公网使用。",
        )
        self._rb_ftps = _make_radio(
            FileServerProtocol.FTPS,
            "FTPS（FTP over TLS，推荐）",
            "在 FTP 基础上加 TLS 加密通道（STARTTLS）。"
            "需要 X.509 证书 + 私钥，可自动生成自签名版。",
        )
        self._rb_sftp = _make_radio(
            FileServerProtocol.SFTP,
            "SFTP（SSH 文件传输）",
            "基于 SSH 协议，天然全链路加密。"
            "需要 RSA/ECDSA 服务端主机密钥，可自动生成。"
            "需安装 paramiko（pip install paramiko）。",
        )

        lay.addWidget(proto_group)

        # --- 证书/密钥快速状态 ---
        status_group = QGroupBox("加密配置状态")
        status_lay   = QVBoxLayout(status_group)

        self._lbl_ftps_status = QLabel()
        self._lbl_sftp_status = QLabel()
        status_lay.addWidget(self._lbl_ftps_status)
        status_lay.addWidget(self._lbl_sftp_status)

        self._btn_manage_cert = QPushButton("🔐 管理证书/密钥...")
        self._btn_manage_cert.clicked.connect(self._open_crypto_dialog)
        status_lay.addWidget(self._btn_manage_cert)

        lay.addWidget(status_group)
        lay.addStretch()

        self._refresh_status()
        self.tabs.addTab(tab, "协议与加密")

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _current_protocol(self) -> FileServerProtocol:
        for rb in self._btn_group.buttons():
            if rb.isChecked():
                val = rb.property("proto")
                try:
                    return FileServerProtocol(val)
                except Exception as exc:
                    _logger.error(f"[SettingsDialog] 无法解析协议值 {val!r}: {exc}")
        return FileServerProtocol.FTP

    def _on_proto_changed(self, checked: bool) -> None:
        if checked:
            proto = self._current_protocol()
            _logger.debug(f"[SettingsDialog] 协议选择变更为: {proto.value}")
            self._refresh_status()

    def _refresh_status(self) -> None:
        """刷新加密配置状态标签颜色与文字。"""
        # FTPS
        tls_ok, tls_err = self._tls_config.is_valid()
        if tls_ok:
            self._lbl_ftps_status.setText(
                f"✅ FTPS 证书已配置: {self._tls_config.certfile}"
            )
            self._lbl_ftps_status.setStyleSheet("color: green;")
        else:
            self._lbl_ftps_status.setText(f"⚠ FTPS 证书未配置: {tls_err}")
            self._lbl_ftps_status.setStyleSheet("color: #cc8800;")

        # SFTP
        ssh_ok, ssh_err = self._ssh_config.is_valid()
        if ssh_ok:
            self._lbl_sftp_status.setText(
                f"✅ SFTP 密钥已配置: {self._ssh_config.host_key_path}"
            )
            self._lbl_sftp_status.setStyleSheet("color: green;")
        else:
            self._lbl_sftp_status.setText(f"⚠ SFTP 主机密钥未配置: {ssh_err}")
            self._lbl_sftp_status.setStyleSheet("color: #cc8800;")

        _logger.debug(
            f"[SettingsDialog] 刷新状态: FTPS ok={tls_ok}, SFTP ok={ssh_ok}"
        )

    def _open_crypto_dialog(self) -> None:
        _logger.info("[SettingsDialog] 打开证书管理子对话框")
        try:
            dlg = CryptoConfigDialog(self._tls_config, self._ssh_config, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._tls_config = dlg.get_tls_config()
                self._ssh_config = dlg.get_ssh_config()
                _logger.info(
                    f"[SettingsDialog] 证书管理返回: "
                    f"FTPS certfile={self._tls_config.certfile!r}, "
                    f"SFTP host_key={self._ssh_config.host_key_path!r}"
                )
                self._refresh_status()
        except Exception as exc:
            _logger.error(
                f"[SettingsDialog] 打开证书管理对话框异常: {exc}\n{traceback.format_exc()}"
            )
            QMessageBox.critical(self, "错误", f"打开证书管理失败：\n{exc}")

    def _on_accept(self) -> None:
        try:
            int(self.input_port.text())
            int(self.input_max_cons.text())
            int(self.input_max_cons_per_ip.text())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "端口与连接数阈值必须是数字类型！")
            return

        proto = self._current_protocol()
        _logger.info(f"[SettingsDialog] 用户点击确定，目标协议={proto.value}")

        # 加密协议必须校验配置
        if proto == FileServerProtocol.FTPS:
            ok, err = self._tls_config.is_valid()
            if not ok:
                _logger.warning(f"[SettingsDialog] FTPS 配置不完整: {err}")
                reply = QMessageBox.question(
                    self,
                    "FTPS 配置不完整",
                    f"FTPS 证书尚未配置：{err}\n\n"
                    "是否打开证书管理来配置？\n（选「否」将以当前配置继续，启动时可能失败）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._open_crypto_dialog()
                    return

        if proto == FileServerProtocol.SFTP:
            ok, err = self._ssh_config.is_valid()
            if not ok:
                _logger.warning(f"[SettingsDialog] SFTP 配置不完整: {err}")
                reply = QMessageBox.question(
                    self,
                    "SFTP 配置不完整",
                    f"SFTP 主机密钥尚未配置：{err}\n\n"
                    "是否打开密钥管理来配置？\n（选「否」将以当前配置继续，启动时可能失败）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._open_crypto_dialog()
                    return

        self.accept()

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------

    def get_result(self) -> Tuple[FileServerProtocol, TlsConfig, SshConfig, int, str, int, int]:
        """返回 (protocol, tls_config, ssh_config, port, host, max_cons, max_cons_per_ip)。"""
        return (
            self._current_protocol(), 
            self._tls_config, 
            self._ssh_config,
            int(self.input_port.text()),
            self.input_host.text().strip(),
            int(self.input_max_cons.text()),
            int(self.input_max_cons_per_ip.text())
        )
