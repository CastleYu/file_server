"""
协议切换对话框

提供：
  - ProtocolDialog: 选择 FTP / FTPS / SFTP 并配置对应证书/密钥
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QButtonGroup, QRadioButton,
    QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt

from protocol.base import FileServerProtocol, TlsConfig, SshConfig
from ui.crypto_dialog import CryptoConfigDialog

_logger = logging.getLogger(__name__)


class ProtocolDialog(QDialog):
    """
    协议切换对话框。

    用户在此选择 FTP / FTPS / SFTP，并可跳转到证书管理。
    确认后可通过 get_result() 获取 (protocol, tls_config, ssh_config)。
    """

    def __init__(
        self,
        current_protocol: FileServerProtocol = FileServerProtocol.FTP,
        tls_config: Optional[TlsConfig] = None,
        ssh_config: Optional[SshConfig] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("协议与加密设置")
        self.setMinimumWidth(480)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._tls_config: TlsConfig = tls_config or TlsConfig()
        self._ssh_config: SshConfig = ssh_config or SshConfig()

        _logger.info(
            f"[ProtocolDialog] 打开, 当前协议={current_protocol.value}, "
            f"FTPS certfile={self._tls_config.certfile!r}, "
            f"SFTP host_key={self._ssh_config.host_key_path!r}"
        )

        self._init_ui(current_protocol)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self, current_protocol: FileServerProtocol) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

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

        layout.addWidget(proto_group)

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

        layout.addWidget(status_group)

        # --- 按钮栏 ---
        btn_row = QHBoxLayout()
        self._btn_ok = QPushButton("确定切换")
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self._refresh_status()

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
                    _logger.error(f"[ProtocolDialog] 无法解析协议值 {val!r}: {exc}")
        return FileServerProtocol.FTP

    def _on_proto_changed(self, checked: bool) -> None:
        if checked:
            proto = self._current_protocol()
            _logger.debug(f"[ProtocolDialog] 协议选择变更为: {proto.value}")
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
            f"[ProtocolDialog] 刷新状态: FTPS ok={tls_ok}, SFTP ok={ssh_ok}"
        )

    def _open_crypto_dialog(self) -> None:
        _logger.info("[ProtocolDialog] 打开证书管理子对话框")
        try:
            dlg = CryptoConfigDialog(self._tls_config, self._ssh_config, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._tls_config = dlg.get_tls_config()
                self._ssh_config = dlg.get_ssh_config()
                _logger.info(
                    f"[ProtocolDialog] 证书管理返回: "
                    f"FTPS certfile={self._tls_config.certfile!r}, "
                    f"SFTP host_key={self._ssh_config.host_key_path!r}"
                )
                self._refresh_status()
        except Exception as exc:
            _logger.error(
                f"[ProtocolDialog] 打开证书管理对话框异常: {exc}\n{traceback.format_exc()}"
            )
            QMessageBox.critical(self, "错误", f"打开证书管理失败：\n{exc}")

    def _on_accept(self) -> None:
        proto = self._current_protocol()
        _logger.info(f"[ProtocolDialog] 用户点击确定，目标协议={proto.value}")

        # 加密协议必须校验配置
        if proto == FileServerProtocol.FTPS:
            ok, err = self._tls_config.is_valid()
            if not ok:
                _logger.warning(f"[ProtocolDialog] FTPS 配置不完整: {err}")
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
                _logger.warning(f"[ProtocolDialog] SFTP 配置不完整: {err}")
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

        _logger.info(
            f"[ProtocolDialog] 协议切换确认: {proto.value} | "
            f"FTPS certfile={self._tls_config.certfile!r} | "
            f"SFTP host_key={self._ssh_config.host_key_path!r}"
        )
        self.accept()

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------

    def get_result(
        self,
    ) -> Tuple[FileServerProtocol, TlsConfig, SshConfig]:
        """返回 (protocol, tls_config, ssh_config)。"""
        return self._current_protocol(), self._tls_config, self._ssh_config
