"""
加密证书与密钥管理对话框

提供：
  - CertGeneratorThread: 后台线程，运行 openssl / paramiko 生成证书或密钥
  - FtpsCertWidget:  FTPS 证书配置面板（certfile + keyfile）
  - SftpKeyWidget:   SFTP 主机密钥配置面板（host_key_path）
  - CryptoConfigDialog: 统一的证书/密钥管理总对话框
"""

from __future__ import annotations

import logging
import os
import subprocess
import datetime
import traceback
from typing import Optional

from PyQt6.QtCore  import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
    QMessageBox, QProgressBar, QTabWidget,
)

from protocol.base import TlsConfig, SshConfig

_logger = logging.getLogger(__name__)


# =============================================================================
# 后台生成线程
# =============================================================================

class CertGeneratorThread(QThread):
    """后台执行证书/密钥生成，防止 UI 卡住。"""

    log_signal     = pyqtSignal(str)   # 进度文本
    success_signal = pyqtSignal(str)   # 成功，携带 "certfile|keyfile" 或 keyfile 路径
    error_signal   = pyqtSignal(str)   # 失败，携带错误信息

    MODE_FTPS_CERT = "ftps_cert"
    MODE_SFTP_KEY  = "sftp_key"

    def __init__(
        self,
        mode: str,
        out_dir: str,
        *,
        common_name: str = "localhost",
        days: int = 3650,
        bits: int = 4096,
        parent=None,
    ):
        super().__init__(parent)
        self._mode        = mode
        self._out_dir     = out_dir
        self._common_name = common_name
        self._days        = days
        self._bits        = bits

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            os.makedirs(self._out_dir, exist_ok=True)
            if self._mode == self.MODE_FTPS_CERT:
                self._gen_ftps_cert()
            elif self._mode == self.MODE_SFTP_KEY:
                self._gen_sftp_key()
            else:
                self.error_signal.emit(f"未知生成模式: {self._mode}")
        except Exception as exc:
            detail = traceback.format_exc()
            _logger.error(f"[CertGeneratorThread] 生成失败: {exc}\n{detail}")
            self.error_signal.emit(str(exc))

    # ------------------------------------------------------------------
    # FTPS：生成自签名 X.509 证书 + 私钥
    # ------------------------------------------------------------------
    def _gen_ftps_cert(self) -> None:
        ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        cert_fp = os.path.join(self._out_dir, f"ftps_cert_{ts}.pem")
        key_fp  = os.path.join(self._out_dir, f"ftps_key_{ts}.pem")

        # 优先用 cryptography 库（纯 Python，无需系统 OpenSSL）
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            self.log_signal.emit("▶ 使用 cryptography 库生成 RSA 私钥...")
            _logger.info(f"[FTPS] 开始生成 RSA {self._bits}-bit 私钥")
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=self._bits,
            )

            self.log_signal.emit("▶ 构建自签名证书主体信息...")
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FTP Server"),
                x509.NameAttribute(NameOID.COMMON_NAME, self._common_name),
            ])
            now = datetime.datetime.utcnow()
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + datetime.timedelta(days=self._days))
                .add_extension(
                    x509.SubjectAlternativeName([x509.DNSName(self._common_name)]),
                    critical=False,
                )
                .sign(private_key, hashes.SHA256())
            )

            self.log_signal.emit(f"▶ 写入证书文件: {cert_fp}")
            with open(cert_fp, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            _logger.info(f"[FTPS] 证书已写出: {cert_fp}")

            self.log_signal.emit(f"▶ 写入私钥文件: {key_fp}")
            with open(key_fp, "wb") as f:
                f.write(private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                ))
            _logger.info(f"[FTPS] 私钥已写出: {key_fp}")

        except ImportError:
            # 回退：尝试系统 openssl 命令
            self.log_signal.emit("⚠ 未安装 cryptography，尝试系统 openssl 命令...")
            _logger.warning("[FTPS] cryptography 未安装，回退到 openssl 命令")
            self._gen_ftps_cert_openssl(cert_fp, key_fp)

        self.log_signal.emit("✔ FTPS 证书和私钥生成完毕！")
        _logger.info(f"[FTPS] 证书生成完成: cert={cert_fp}, key={key_fp}")
        self.success_signal.emit(f"{cert_fp}|{key_fp}")

    def _gen_ftps_cert_openssl(self, cert_fp: str, key_fp: str) -> None:
        """用系统 openssl 命令行生成（备用方案）。"""
        cmd = [
            "openssl", "req", "-x509",
            "-newkey", f"rsa:{self._bits}",
            "-keyout", key_fp,
            "-out",    cert_fp,
            "-days",   str(self._days),
            "-nodes",
            "-subj",   f"/CN={self._common_name}/O=FTP Server/C=CN",
        ]
        self.log_signal.emit(f"▶ 执行: {' '.join(cmd)}")
        _logger.info(f"[FTPS][openssl] 执行命令: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            err = result.stderr or result.stdout
            _logger.error(f"[FTPS][openssl] 命令失败: {err}")
            raise RuntimeError(f"openssl 失败: {err}")
        _logger.info(f"[FTPS][openssl] 命令成功: stdout={result.stdout}")

    # ------------------------------------------------------------------
    # SFTP：生成 RSA 主机密钥
    # ------------------------------------------------------------------
    def _gen_sftp_key(self) -> None:
        ts     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        key_fp = os.path.join(self._out_dir, f"sftp_host_key_{ts}")

        try:
            import paramiko

            self.log_signal.emit(f"▶ 使用 paramiko 生成 RSA {self._bits}-bit 主机密钥...")
            _logger.info(f"[SFTP] 开始生成 RSA {self._bits}-bit 主机密钥")
            key = paramiko.RSAKey.generate(bits=self._bits)
            key.write_private_key_file(key_fp)
            pub_fp = key_fp + ".pub"
            with open(pub_fp, "w") as f:
                f.write(f"ssh-rsa {key.get_base64()}\n")
            _logger.info(f"[SFTP] 主机密钥已写出: {key_fp}, 公钥: {pub_fp}")

        except ImportError:
            # 回退：ssh-keygen
            self.log_signal.emit("⚠ 未安装 paramiko，尝试 ssh-keygen 命令...")
            _logger.warning("[SFTP] paramiko 未安装，回退到 ssh-keygen")
            cmd = [
                "ssh-keygen",
                "-t", "rsa",
                "-b", str(self._bits),
                "-f", key_fp,
                "-N", "",
                "-q",
            ]
            self.log_signal.emit(f"▶ 执行: {' '.join(cmd)}")
            _logger.info(f"[SFTP][ssh-keygen] 执行命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                err = result.stderr or result.stdout
                _logger.error(f"[SFTP][ssh-keygen] 命令失败: {err}")
                raise RuntimeError(f"ssh-keygen 失败: {err}")
            _logger.info("[SFTP][ssh-keygen] 命令成功")

        self.log_signal.emit(f"✔ SFTP 主机密钥生成完毕: {key_fp}")
        _logger.info(f"[SFTP] 密钥生成完成: {key_fp}")
        self.success_signal.emit(key_fp)


# =============================================================================
# 文件路径选择行
# =============================================================================

class _FilePickRow(QWidget):
    """单行：标签 + 路径输入框 + 选择按钮。"""

    def __init__(self, label: str, dialog_title: str, filter_str: str = "", parent=None):
        super().__init__(parent)
        self._dialog_title = dialog_title
        self._filter       = filter_str

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel(label))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("点击「选择」或直接填写路径...")
        row.addWidget(self.edit, stretch=1)

        btn = QPushButton("选择...")
        btn.setFixedWidth(72)
        btn.clicked.connect(self._browse)
        row.addWidget(btn)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self._dialog_title, "", self._filter
        )
        if path:
            self.edit.setText(path)
            _logger.debug(f"[FilePick] 用户选择文件: {path}")

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, v: str) -> None:
        self.edit.setText(v)


# =============================================================================
# FTPS 证书面板
# =============================================================================

class FtpsCertWidget(QWidget):
    """FTPS 证书配置面板。"""

    def __init__(self, tls_config: Optional[TlsConfig] = None, parent=None):
        super().__init__(parent)
        self._thread: Optional[CertGeneratorThread] = None
        self._setup_ui()
        if tls_config:
            self.load(tls_config)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # 当前配置
        cfg_group = QGroupBox("当前 FTPS 证书配置")
        cfg_lay   = QVBoxLayout(cfg_group)

        self._cert_row = _FilePickRow(
            "证书文件 (.pem):", "选择证书文件", "PEM 文件 (*.pem *.crt *.cer);;所有文件 (*)"
        )
        self._key_row = _FilePickRow(
            "私钥文件 (.pem):", "选择私钥文件", "PEM 文件 (*.pem *.key);;所有文件 (*)"
        )

        cfg_lay.addWidget(self._cert_row)
        cfg_lay.addWidget(self._key_row)

        root.addWidget(cfg_group)

        # 自动生成
        gen_group = QGroupBox("一键生成自签名证书（推荐用于局域网/测试）")
        gen_lay   = QVBoxLayout(gen_group)

        cn_row = QHBoxLayout()
        cn_row.addWidget(QLabel("Common Name (服务器地址/域名):"))
        self._cn_edit = QLineEdit("localhost")
        cn_row.addWidget(self._cn_edit, stretch=1)
        gen_lay.addLayout(cn_row)

        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("有效期（天）:"))
        self._days_edit = QLineEdit("3650")
        self._days_edit.setFixedWidth(80)
        days_row.addWidget(self._days_edit)
        days_row.addStretch()
        gen_lay.addLayout(days_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出目录:"))
        self._out_edit = QLineEdit()
        default_out = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs"
        )
        self._out_edit.setText(default_out)
        out_row.addWidget(self._out_edit, stretch=1)
        btn_out = QPushButton("选择...")
        btn_out.setFixedWidth(72)
        btn_out.clicked.connect(self._pick_out_dir)
        out_row.addWidget(btn_out)
        gen_lay.addLayout(out_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        gen_lay.addWidget(self._progress)

        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(100)
        self._log_box.setPlaceholderText("生成日志...")
        gen_lay.addWidget(self._log_box)

        self._gen_btn = QPushButton("🔑 生成自签名证书")
        self._gen_btn.clicked.connect(self._start_generate)
        gen_lay.addWidget(self._gen_btn)

        root.addWidget(gen_group)

    def _pick_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self._out_edit.setText(d)

    def _start_generate(self) -> None:
        out_dir = self._out_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "输入错误", "请指定输出目录")
            return

        try:
            days = int(self._days_edit.text())
            assert 1 <= days <= 36500
        except Exception:
            QMessageBox.warning(self, "输入错误", "有效期必须为 1-36500 之间的整数")
            return

        cn = self._cn_edit.text().strip() or "localhost"
        _logger.info(f"[FtpsCertWidget] 开始生成证书 CN={cn}, days={days}, out={out_dir}")

        self._log_box.clear()
        self._progress.setVisible(True)
        self._gen_btn.setEnabled(False)

        self._thread = CertGeneratorThread(
            CertGeneratorThread.MODE_FTPS_CERT,
            out_dir,
            common_name=cn,
            days=days,
        )
        self._thread.log_signal.connect(self._on_log)
        self._thread.success_signal.connect(self._on_cert_success)
        self._thread.error_signal.connect(self._on_error)
        self._thread.start()

    def _on_log(self, msg: str) -> None:
        self._log_box.append(msg)
        _logger.debug(f"[FtpsCertWidget][gen] {msg}")

    def _on_cert_success(self, result: str) -> None:
        self._progress.setVisible(False)
        self._gen_btn.setEnabled(True)
        parts = result.split("|", 1)
        if len(parts) == 2:
            cert_fp, key_fp = parts
            self._cert_row.setText(cert_fp)
            self._key_row.setText(key_fp)
        _logger.info(f"[FtpsCertWidget] 证书生成成功: {result}")
        QMessageBox.information(
            self, "生成成功",
            f"证书和私钥已生成：\n{result.replace('|', chr(10))}"
        )

    def _on_error(self, err: str) -> None:
        self._progress.setVisible(False)
        self._gen_btn.setEnabled(True)
        _logger.error(f"[FtpsCertWidget] 证书生成失败: {err}")
        QMessageBox.critical(self, "生成失败", f"证书生成出错：\n{err}")

    def load(self, tls_config: TlsConfig) -> None:
        self._cert_row.setText(tls_config.certfile)
        self._key_row.setText(tls_config.keyfile)

    def get_config(self) -> TlsConfig:
        cfg = TlsConfig(
            certfile=self._cert_row.text(),
            keyfile=self._key_row.text(),
            require_tls=True,
        )
        _logger.debug(f"[FtpsCertWidget] get_config: certfile={cfg.certfile}, keyfile={cfg.keyfile}")
        return cfg


# =============================================================================
# SFTP 主机密钥面板
# =============================================================================

class SftpKeyWidget(QWidget):
    """SFTP 主机密钥配置面板。"""

    def __init__(self, ssh_config: Optional[SshConfig] = None, parent=None):
        super().__init__(parent)
        self._thread: Optional[CertGeneratorThread] = None
        self._setup_ui()
        if ssh_config:
            self.load(ssh_config)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        cfg_group = QGroupBox("当前 SFTP 主机密钥配置")
        cfg_lay   = QVBoxLayout(cfg_group)

        self._key_row = _FilePickRow(
            "主机密钥文件:", "选择主机密钥", "所有文件 (*)"
        )
        cfg_lay.addWidget(self._key_row)
        root.addWidget(cfg_group)

        gen_group = QGroupBox("一键生成 RSA 主机密钥")
        gen_lay   = QVBoxLayout(gen_group)

        bits_row = QHBoxLayout()
        bits_row.addWidget(QLabel("密钥长度（bits）:"))
        self._bits_edit = QLineEdit("4096")
        self._bits_edit.setFixedWidth(80)
        bits_row.addWidget(self._bits_edit)
        bits_row.addStretch()
        gen_lay.addLayout(bits_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出目录:"))
        self._out_edit = QLineEdit()
        default_out = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs"
        )
        self._out_edit.setText(default_out)
        out_row.addWidget(self._out_edit, stretch=1)
        btn_out = QPushButton("选择...")
        btn_out.setFixedWidth(72)
        btn_out.clicked.connect(self._pick_out_dir)
        out_row.addWidget(btn_out)
        gen_lay.addLayout(out_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        gen_lay.addWidget(self._progress)

        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(100)
        self._log_box.setPlaceholderText("生成日志...")
        gen_lay.addWidget(self._log_box)

        self._gen_btn = QPushButton("🔑 生成 SSH 主机密钥")
        self._gen_btn.clicked.connect(self._start_generate)
        gen_lay.addWidget(self._gen_btn)

        # 说明
        note = QLabel(
            "说明：主机密钥用于身份认证（类似 SSH 服务器的 /etc/ssh/ssh_host_rsa_key）。\n"
            "客户端首次连接会看到指纹提示，无需颁发给用户。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        gen_lay.addWidget(note)

        root.addWidget(gen_group)

    def _pick_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self._out_edit.setText(d)

    def _start_generate(self) -> None:
        out_dir = self._out_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "输入错误", "请指定输出目录")
            return

        try:
            bits = int(self._bits_edit.text())
            assert bits in (1024, 2048, 4096)
        except Exception:
            QMessageBox.warning(self, "输入错误", "密钥长度需为 1024 / 2048 / 4096")
            return

        _logger.info(f"[SftpKeyWidget] 开始生成主机密钥 bits={bits}, out={out_dir}")
        self._log_box.clear()
        self._progress.setVisible(True)
        self._gen_btn.setEnabled(False)

        self._thread = CertGeneratorThread(
            CertGeneratorThread.MODE_SFTP_KEY,
            out_dir,
            bits=bits,
        )
        self._thread.log_signal.connect(self._on_log)
        self._thread.success_signal.connect(self._on_key_success)
        self._thread.error_signal.connect(self._on_error)
        self._thread.start()

    def _on_log(self, msg: str) -> None:
        self._log_box.append(msg)
        _logger.debug(f"[SftpKeyWidget][gen] {msg}")

    def _on_key_success(self, key_fp: str) -> None:
        self._progress.setVisible(False)
        self._gen_btn.setEnabled(True)
        self._key_row.setText(key_fp)
        _logger.info(f"[SftpKeyWidget] 主机密钥生成成功: {key_fp}")
        QMessageBox.information(
            self, "生成成功",
            f"主机密钥已生成：{key_fp}\n公钥：{key_fp}.pub"
        )

    def _on_error(self, err: str) -> None:
        self._progress.setVisible(False)
        self._gen_btn.setEnabled(True)
        _logger.error(f"[SftpKeyWidget] 密钥生成失败: {err}")
        QMessageBox.critical(self, "生成失败", f"主机密钥生成出错：\n{err}")

    def load(self, ssh_config: SshConfig) -> None:
        self._key_row.setText(ssh_config.host_key_path)

    def get_config(self) -> SshConfig:
        cfg = SshConfig(host_key_path=self._key_row.text())
        _logger.debug(f"[SftpKeyWidget] get_config: host_key_path={cfg.host_key_path}")
        return cfg


# =============================================================================
# 统一的证书管理对话框（被 ProtocolDialog 调用）
# =============================================================================

class CryptoConfigDialog(QDialog):
    """证书与密钥管理总对话框（FTPS / SFTP 分 Tab）。"""

    def __init__(
        self,
        tls_config: Optional[TlsConfig] = None,
        ssh_config: Optional[SshConfig] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("加密证书与密钥管理")
        self.resize(580, 600)
        _logger.info("[CryptoConfigDialog] 打开证书管理对话框")

        layout = QVBoxLayout(self)

        tab = QTabWidget()
        self._ftps_widget = FtpsCertWidget(tls_config)
        self._sftp_widget = SftpKeyWidget(ssh_config)
        tab.addTab(self._ftps_widget, "FTPS 证书")
        tab.addTab(self._sftp_widget, "SFTP 主机密钥")
        layout.addWidget(tab)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _on_accept(self) -> None:
        tls = self._ftps_widget.get_config()
        ssh = self._sftp_widget.get_config()
        _logger.info(
            f"[CryptoConfigDialog] 用户确认: FTPS certfile={tls.certfile!r}, "
            f"SFTP host_key={ssh.host_key_path!r}"
        )
        self.accept()

    def get_tls_config(self) -> TlsConfig:
        return self._ftps_widget.get_config()

    def get_ssh_config(self) -> SshConfig:
        return self._sftp_widget.get_config()
