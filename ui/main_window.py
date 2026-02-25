"""
FTP 服务器管理主窗口

提供：
  - MainWindow: FTP 服务器管理主窗口（通过 FileServerController 控制）
  - setup_signal_handlers(): 设置系统信号处理器
  - main(): 程序主入口
"""

from __future__ import annotations

import sys
import signal
import atexit
import traceback
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QMessageBox, QSystemTrayIcon, QMenu,
    QTextEdit, QDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QAction, QPixmap, QColor, QPainter

from model.entities import UserProfile
from control.file_server import FileServerController
from protocol.base import FileServerProtocol, TlsConfig, SshConfig
from ui.constants import UISize, vfs_logger, hide_console, show_console, is_console_visible
from ui.user_dialog import UserDialog
from ui.settings_dialog import SettingsDialog

_logger = logging.getLogger(__name__)

# 协议显示名称映射
_PROTO_LABELS = {
    FileServerProtocol.FTP:  "FTP（明文）",
    FileServerProtocol.FTPS: "FTPS（TLS加密）",
    FileServerProtocol.SFTP: "SFTP（SSH加密）",
}


class MainWindow(QMainWindow):
    """
    FTP 服务器管理主窗口。

    通过 FileServerController 控制器管理服务器，实现 UI 与业务逻辑分离。
    支持 FTP / FTPS / SFTP 协议切换，以及证书/密钥的图形化配置。
    """

    _instance: Optional["MainWindow"] = None

    def __init__(self):
        super().__init__()
        MainWindow._instance = self
        _logger.info("=" * 60)
        _logger.info("MainWindow 初始化开始")

        self.setWindowTitle("AbyssFS - 多协议文件服务器管理器")
        self.resize(UISize.MAIN_WINDOW_WIDTH, UISize.MAIN_WINDOW_HEIGHT)

        try:
            self.controller = FileServerController(
                config_path="ftp_users.json",
                protocol=FileServerProtocol.FTP,
            )
            _logger.info(
                f"FileServerController 初始化完成, "
                f"协议={self.controller.get_protocol().value}, "
                f"端口={self.controller.get_server_port()}"
            )
        except Exception as exc:
            _logger.critical(f"FileServerController 初始化失败: {exc}\n{traceback.format_exc()}")
            QMessageBox.critical(None, "启动失败", f"初始化控制器失败：\n{exc}")
            raise

        self._setup_controller_callbacks()

        self._is_closing  = False
        self._force_quit  = False

        self.setup_ui()
        self.setup_tray_icon()
        self.refresh_table()

        hide_console()
        self.update_console_button()
        self._refresh_proto_label()

        atexit.register(self._cleanup_on_exit)
        _logger.info("MainWindow 初始化完成")

    # ------------------------------------------------------------------
    # 控制器回调
    # ------------------------------------------------------------------

    def _setup_controller_callbacks(self) -> None:
        _logger.debug("注册控制器回调")
        self.controller.register_callback("on_status",       self.on_server_status)
        self.controller.register_callback("on_error",        self.on_server_error)
        self.controller.register_callback("on_started",      self.on_server_started)
        self.controller.register_callback("on_stopped",      self.on_server_stopped)
        self.controller.register_callback("on_user_changed", self.refresh_table)

    # ------------------------------------------------------------------
    # 系统托盘
    # ------------------------------------------------------------------

    def setup_tray_icon(self) -> None:
        """设置系统托盘图标。"""
        _logger.debug("初始化系统托盘图标")
        self.tray_icon = QSystemTrayIcon(self)

        # 尝试加载 ico，失败则绘制占位
        ico_path = "ftp.ico"
        if not __import__("os").path.isfile(ico_path):
            pixmap = QPixmap(32, 32)
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pixmap)
            painter.setBrush(QColor(76, 175, 80))
            painter.setPen(QColor(56, 142, 60))
            painter.drawEllipse(2, 2, 28, 28)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "FS")
            painter.end()
            self.tray_icon.setIcon(QIcon(pixmap))
            _logger.debug("使用绘制占位图标（ftp.ico 不存在）")
        else:
            self.tray_icon.setIcon(QIcon(ico_path))
            _logger.debug(f"加载图标: {ico_path}")

        self.tray_icon.setToolTip("文件服务器管理器")

        tray_menu = QMenu()

        action_show = QAction("显示主窗口", self)
        action_show.triggered.connect(self.show_from_tray)
        tray_menu.addAction(action_show)

        tray_menu.addSeparator()

        self.action_toggle_server = QAction("启动服务器", self)
        self.action_toggle_server.triggered.connect(self.on_toggle_server)
        tray_menu.addAction(self.action_toggle_server)

        tray_menu.addSeparator()

        action_quit = QAction("退出", self)
        action_quit.triggered.connect(self.force_quit)
        tray_menu.addAction(action_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)

    def on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()
        _logger.debug("从系统托盘恢复窗口")

    def minimize_to_tray(self) -> None:
        self.hide()
        self.tray_icon.show()
        self.tray_icon.showMessage(
            "文件服务器",
            "程序已最小化到系统托盘，服务器继续运行中。",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
        _logger.info("程序已最小化到系统托盘")

    def force_quit(self) -> None:
        _logger.info("用户请求强制退出")
        self._force_quit = True
        self.close()

    def update_tray_menu(self) -> None:
        running = self.controller.is_server_running()
        self.action_toggle_server.setText("停止服务器" if running else "启动服务器")
        _logger.debug(f"更新托盘菜单: 服务器运行={running}")

    # ------------------------------------------------------------------
    # 服务器状态回调
    # ------------------------------------------------------------------

    def on_server_status(self, message: str) -> None:
        self.lbl_status.setText(f"状态: {message}")
        _logger.info(f"[服务器状态] {message}")
        self._append_log(message)

    def on_server_error(self, message: str) -> None:
        self.lbl_status.setText(f"错误: {message}")
        _logger.error(f"[服务器错误] {message}")
        self._append_log(f"❌ {message}")
        QMessageBox.critical(self, "服务器错误", message)
        self._reset_start_button()

    def on_server_started(self) -> None:
        _logger.info("[服务器事件] started_signal 收到，100ms 后检查状态")
        QTimer.singleShot(100, self._check_server_started)

    def _check_server_started(self) -> None:
        if self.controller.is_server_running():
            proto  = self.controller.get_protocol()
            port   = self.controller.get_server_port()
            _logger.info(f"[服务器启动确认] 协议={proto.value}, 端口={port}")
            self.btn_start.setText("停止服务器")
            self.btn_start.setStyleSheet(
                "background-color: #F44336; color: white; font-weight: bold;"
            )
            self.input_port.setEnabled(False)
            self._append_log(f"✅ 服务器已启动 [{_PROTO_LABELS[proto]}] 端口={port}")
            self.update_tray_menu()
        else:
            _logger.warning("[服务器启动确认] 检查时服务器已不在运行")
            self._reset_start_button()

    def on_server_stopped(self) -> None:
        proto = self.controller.get_protocol()
        _logger.info(f"[服务器事件] stopped_signal 收到, 协议={proto.value}")
        self.btn_start.setText("启动服务器")
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        self.input_port.setEnabled(True)
        self.lbl_status.setText("状态: 已停止")
        self._append_log("⏹ 服务器已停止")
        self.update_tray_menu()

    def _reset_start_button(self) -> None:
        self.btn_start.setText("启动服务器")
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        self.input_port.setEnabled(True)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ---- 服务器控制区 ----
        ctrl_group  = QGroupBox("服务器控制")
        ctrl_main_layout = QVBoxLayout()
        
        # 第一行：基础控制
        line1 = QHBoxLayout()
        self.input_port = QLineEdit(str(self.controller.get_server_port()))
        self.input_port.setFixedWidth(UISize.PORT_INPUT_WIDTH)
        self.btn_start = QPushButton("启动服务器")
        self.btn_start.clicked.connect(self.on_toggle_server)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.lbl_status = QLabel("状态: 已停止")
        
        line1.addWidget(QLabel("端口:"))
        line1.addWidget(self.input_port)
        line1.addWidget(self.btn_start)
        line1.addWidget(self.lbl_status)
        line1.addStretch()
        
        # 第二行：协议与工具
        line2 = QHBoxLayout()
        self.lbl_proto = QLabel()
        self.btn_settings = QPushButton("⚙ 全局设置...")
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        self.btn_console = QPushButton("控制台")
        self.btn_console.clicked.connect(self.toggle_console)
        self.btn_tray = QPushButton("后台运行")
        self.btn_tray.clicked.connect(self.minimize_to_tray)
        
        line2.addWidget(self.lbl_proto)
        line2.addWidget(self.btn_settings)
        line2.addWidget(self.btn_console)
        line2.addWidget(self.btn_tray)
        
        ctrl_main_layout.addLayout(line1)
        ctrl_main_layout.addLayout(line2)
        ctrl_group.setLayout(ctrl_main_layout)
        main_layout.addWidget(ctrl_group)

        # ---- 用户管理区 ----
        user_group  = QGroupBox("用户管理")
        user_layout = QVBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["用户名", "根目录", "权限", "虚拟数", "操作"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, UISize.USER_TABLE_ACTION_COLUMN_WIDTH)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.on_table_double_click)

        user_layout.addWidget(self.table)

        btn_add_user = QPushButton("添加新用户")
        btn_add_user.clicked.connect(self.open_add_user_dialog)
        user_layout.addWidget(btn_add_user)

        user_group.setLayout(user_layout)
        main_layout.addWidget(user_group)

        user_group.setLayout(user_layout)
        main_layout.addWidget(user_group)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 协议切换与全局设置
    # ------------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        """打开全局设置对话框。"""
        if self.controller.is_server_running():
            _logger.warning("[SettingsDialog] 服务器运行时尝试改变全局设置，已拦截")
            QMessageBox.warning(
                self, "无法配置", "服务器运行中，请先停止服务器再修改配置。"
            )
            return

        _logger.info("[SettingsDialog] 打开全局设置对话框")
        try:
            try:
                current_port = int(self.input_port.text())
            except ValueError:
                current_port = self.controller.get_server_port()

            dlg = SettingsDialog(
                current_protocol=self.controller.get_protocol(),
                tls_config=self.controller.context.tls_config,
                ssh_config=self.controller.context.ssh_config,
                port=current_port,
                host=self.controller.get_server_host(),
                max_cons=self.controller.get_max_cons(),
                max_cons_per_ip=self.controller.get_max_cons_per_ip(),
                parent=self,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                proto, tls_cfg, ssh_cfg, n_port, n_host, n_max, n_max_ip = dlg.get_result()
                _logger.info(
                    f"[SettingsDialog] 用户确认更改: {proto.value} | "
                    f"certfile={tls_cfg.certfile!r} | "
                    f"host_key={ssh_cfg.host_key_path!r}"
                )
                ok, err = self.controller.switch_protocol(
                    proto, tls_config=tls_cfg, ssh_config=ssh_cfg
                )
                
                self.controller.set_server_port(n_port)
                self.controller.set_server_host(n_host)
                self.controller.set_max_cons(n_max)
                self.controller.set_max_cons_per_ip(n_max_ip)
                
                if ok:
                    self._refresh_proto_label()
                    self.lbl_status.setText(f"状态: 全局配置已更新 ({_PROTO_LABELS[proto]})")
                    self._append_log(f"🔄 配置更新完成")
                    _logger.info(f"配置更新成功")
                else:
                    _logger.error(f"协议切换失败: {err}")
                    QMessageBox.warning(self, "切换失败", err or "未知错误")
        except Exception as exc:
            _logger.error(f"[SettingsDialog] 异常: {exc}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "错误", f"打开设置面板失败：\n{exc}")

    def _refresh_proto_label(self) -> None:
        proto = self.controller.get_protocol()
        icons = {
            FileServerProtocol.FTP:  "🔓",
            FileServerProtocol.FTPS: "🔒",
            FileServerProtocol.SFTP: "🛡",
        }
        self.lbl_proto.setText(f"{icons[proto]} {_PROTO_LABELS[proto]}")
        self.btn_settings.setEnabled(not self.controller.is_server_running())
        # 同步端口默认值
        current_port = self.controller.get_server_port()
        self.input_port.setText(str(current_port))
        _logger.debug(f"_refresh_proto_label: proto={proto.value}, port={current_port}")

    # ------------------------------------------------------------------
    # 用户表格
    # ------------------------------------------------------------------

    def refresh_table(self) -> None:
        users = self.controller.get_users()
        _logger.debug(f"刷新用户表格: {len(users)} 个用户")
        self.table.setRowCount(0)
        for row, user in enumerate(users):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(user.username))
            self.table.setItem(row, 1, QTableWidgetItem(user.root_dir or "(默认)"))

            perm_item = QTableWidgetItem(user.root_perm or "(无)")
            perm_item.setToolTip(self.get_permission_tooltip(user.root_perm))
            self.table.setItem(row, 2, perm_item)

            vdir_count = len(user.virtual_dirs)
            vdir_item  = QTableWidgetItem(str(vdir_count))
            if vdir_count > 0:
                vdir_item.setToolTip("\n".join(
                    f"/{vd.get_display_name()} → {vd.real_path} [{vd.perm}]"
                    for vd in user.virtual_dirs
                ))
            self.table.setItem(row, 3, vdir_item)

            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(4, 2, 4, 2)

            btn_edit = QPushButton("编辑")
            btn_edit.clicked.connect(lambda _, u=user: self.edit_user(u))
            btn_del  = QPushButton("删除")
            btn_del.clicked.connect(
                lambda _, username=user.username: self.delete_user(username)
            )

            action_layout.addWidget(btn_edit)
            action_layout.addWidget(btn_del)
            action_widget.setLayout(action_layout)
            self.table.setCellWidget(row, 4, action_widget)

    def get_permission_tooltip(self, perm: str) -> str:
        if not perm:
            return "无权限"
        names = {
            "e": "切换目录", "l": "列出文件", "r": "下载文件",
            "w": "上传/写入", "a": "追加数据", "d": "删除",
            "f": "重命名",   "m": "创建目录", "M": "修改权限",
        }
        descs = [f"{c.upper()}: {names[c]}" for c in perm if c in names]
        return "\n".join(descs) if descs else f"自定义权限: {perm}"

    def on_table_double_click(self, row: int, column: int) -> None:
        users = self.controller.get_users()
        if 0 <= row < len(users):
            self.edit_user(users[row])

    # ------------------------------------------------------------------
    # 用户对话框
    # ------------------------------------------------------------------

    def open_add_user_dialog(self) -> None:
        _logger.info("打开添加用户对话框")
        self.dialog = UserDialog(
            self, default_root=self.controller.get_default_root()
        )
        self.dialog.btn_save.clicked.connect(self.save_user_from_dialog)
        self.dialog.show()

    def edit_user(self, user: UserProfile) -> None:
        _logger.info(f"打开编辑用户对话框: {user.username}")
        self.dialog = UserDialog(
            self,
            edit_user=user,
            default_root=self.controller.get_default_root(),
        )
        self.dialog.btn_save.clicked.connect(self.save_user_from_dialog)
        self.dialog.show()

    def save_user_from_dialog(self) -> None:
        try:
            new_user = self.dialog.get_data()
            _logger.info(
                f"保存用户: username={new_user.username!r}, "
                f"root_dir={new_user.root_dir!r}, "
                f"vdirs={len(new_user.virtual_dirs)}"
            )
            if not new_user.username:
                QMessageBox.warning(self, "输入错误", "用户名不能为空！")
                return

            if not self.dialog.edit_user and not new_user.password:
                QMessageBox.warning(self, "输入错误", "新用户必须设置密码！")
                return

            if self.dialog.edit_user:
                success, error_msg = self.controller.update_user(
                    self.dialog.edit_user.username, new_user
                )
                action = "更新"
            else:
                success, error_msg = self.controller.add_user(new_user)
                action = "添加"

            if success:
                self.dialog.close()
                self.lbl_status.setText(f"状态: 用户 {new_user.username} 已{action}")
                self._append_log(f"👤 用户已{action}: {new_user.username}")
                _logger.info(f"用户{action}成功: {new_user.username}")
            else:
                _logger.warning(f"用户{action}失败: {error_msg}")
                QMessageBox.warning(self, "操作失败", error_msg or "未知错误")
        except Exception as exc:
            _logger.error(f"save_user_from_dialog 异常: {exc}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "错误", f"保存用户时出错：\n{exc}")

    def delete_user(self, username: str) -> None:
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除用户 '{username}' 吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            _logger.info(f"删除用户: {username}")
            try:
                success, error_msg = self.controller.remove_user(username)
                if success:
                    self.lbl_status.setText(f"状态: 用户 {username} 已删除")
                    self._append_log(f"🗑 用户已删除: {username}")
                    _logger.info(f"用户删除成功: {username}")
                else:
                    _logger.warning(f"用户删除失败: {error_msg}")
                    QMessageBox.warning(self, "删除失败", error_msg or "未知错误")
            except Exception as exc:
                _logger.error(f"delete_user 异常: {exc}\n{traceback.format_exc()}")
                QMessageBox.critical(self, "错误", f"删除用户时出错：\n{exc}")

    # ------------------------------------------------------------------
    # 服务器启停
    # ------------------------------------------------------------------

    def on_toggle_server(self) -> None:
        try:
            if self.controller.is_server_running():
                _logger.info("用户请求停止服务器")
                self.controller.stop_server()
                self.btn_settings.setEnabled(True)
            else:
                try:
                    port = int(self.input_port.text())
                except ValueError:
                    QMessageBox.warning(self, "端口错误", "请输入有效的端口号(数字)")
                    return

                _logger.info(f"用户请求启动服务器: port={port}")
                
                ok, err = self.controller.set_server_port(port)
                if not ok:
                    _logger.warning(f"设置端口失败: {err}")
                    QMessageBox.warning(self, "端口错误", err or "无效的端口号")
                    return

                ok, err = self.controller.start_server()
                if not ok:
                    _logger.error(f"启动服务器失败: {err}")
                    QMessageBox.warning(self, "启动失败", err or "服务器启动失败")
                    return

                self.btn_settings.setEnabled(False)
        except Exception as exc:
            _logger.error(f"on_toggle_server 异常: {exc}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "错误", f"操作服务器时出错：\n{exc}")

    # ------------------------------------------------------------------
    # 控制台切换
    # ------------------------------------------------------------------

    def toggle_console(self) -> None:
        if is_console_visible():
            hide_console()
        else:
            show_console()
        self.update_console_button()

    def update_console_button(self) -> None:
        self.btn_console.setText(
            "隐藏控制台" if is_console_visible() else "显示控制台"
        )

    # ------------------------------------------------------------------
    # 日志框辅助
    # ------------------------------------------------------------------

    def _append_log(self, msg: str) -> None:
        """不再向 UI 输出日志。"""
        pass

    # ------------------------------------------------------------------
    # 关闭与清理
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._is_closing:
            event.accept()
            return

        if self._force_quit:
            _logger.info("强制退出：关闭服务器并退出")
            self._shutdown_server()
            self.tray_icon.hide()
            event.accept()
            return

        if self.controller.is_server_running():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("关闭确认")
            msg_box.setText("文件服务器正在运行中")
            msg_box.setInformativeText(
                "您可以选择最小化到系统托盘继续后台运行，或者彻底退出程序。"
            )
            msg_box.setIcon(QMessageBox.Icon.Question)
            
            btn_bg     = msg_box.addButton("后台运行", QMessageBox.ButtonRole.AcceptRole)
            btn_quit   = msg_box.addButton("仍然退出", QMessageBox.ButtonRole.RejectRole)
            btn_cancel = msg_box.addButton("取消关闭", QMessageBox.ButtonRole.RejectRole)
            
            # 默认聚焦在“取消关闭”以防误操作
            msg_box.setDefaultButton(btn_cancel)
            # 明确 Esc 键和关闭图标的行为导向
            msg_box.setEscapeButton(btn_cancel)
            msg_box.exec()

            clicked = msg_box.clickedButton()
            if clicked == btn_bg:
                _logger.info("用户选择后台运行")
                self.minimize_to_tray()
                event.ignore()
                return
            elif clicked == btn_quit:
                _logger.info("用户选择在服务器运行时退出")
                self._shutdown_server()
                self.tray_icon.hide()
                event.accept()
                return
            else:
                # 包含点击“取消关闭”或直接关闭对话框的情况
                _logger.info("用户撤销关闭操作")
                event.ignore()
                return

        _logger.info("窗口关闭（服务器未运行）")
        self.tray_icon.hide()
        event.accept()

    def _shutdown_server(self) -> None:
        if self._is_closing:
            return
        self._is_closing = True
        vfs_logger.info("正在关闭服务器...")
        _logger.info("_shutdown_server 开始")
        try:
            if self.controller.is_server_running():
                self.lbl_status.setText("状态: 正在停止服务器...")
                self.controller.stop_server()
                vfs_logger.info("服务器已安全停止")
                _logger.info("_shutdown_server 完成（服务器已停止）")
            else:
                _logger.info("_shutdown_server: 服务器未在运行，无需停止")
        except Exception as exc:
            _logger.error(f"_shutdown_server 异常: {exc}\n{traceback.format_exc()}")
            vfs_logger.error(f"关闭服务器时出错: {exc}")

    def _cleanup_on_exit(self) -> None:
        _logger.info("atexit 清理开始")
        try:
            if self.controller.is_server_running():
                vfs_logger.info("atexit: 正在清理服务器...")
                self.controller.cleanup()
                _logger.info("atexit 清理完成")
        except Exception as exc:
            _logger.error(f"atexit 清理异常: {exc}\n{traceback.format_exc()}")
            vfs_logger.error(f"atexit 清理时出错: {exc}")

    @classmethod
    def handle_signal(cls, signum, frame) -> None:
        try:
            signal_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        except Exception:
            signal_name = str(signum)
        vfs_logger.info(f"收到系统信号: {signal_name}")
        _logger.info(f"handle_signal: {signal_name}")
        if cls._instance:
            try:
                cls._instance._shutdown_server()
            except Exception as exc:
                _logger.error(f"handle_signal 关闭服务器异常: {exc}")
        QApplication.quit()


# =============================================================================
# 程序入口
# =============================================================================

def setup_signal_handlers() -> None:
    """设置系统信号处理器。"""
    _logger.info("设置信号处理器")
    try:
        signal.signal(signal.SIGINT, MainWindow.handle_signal)
    except Exception as exc:
        _logger.warning(f"注册 SIGINT 失败: {exc}")

    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, MainWindow.handle_signal)
        except Exception as exc:
            _logger.warning(f"注册 SIGTERM 失败: {exc}")

    if sys.platform == "win32":
        try:
            import ctypes

            CTRL_C_EVENT     = 0
            CTRL_BREAK_EVENT = 1
            CTRL_CLOSE_EVENT = 2

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
            def console_handler(event):
                if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
                    vfs_logger.info(f"Windows 控制台事件: {event}")
                    _logger.info(f"Windows console event: {event}")
                    if MainWindow._instance:
                        try:
                            MainWindow._instance._shutdown_server()
                        except Exception as exc:
                            _logger.error(f"console_handler 关闭异常: {exc}")
                    return True
                return False

            ctypes.windll.kernel32.SetConsoleCtrlHandler(console_handler, True)
            _logger.info("Windows 控制台事件处理器已注册")
        except Exception as exc:
            _logger.warning(f"设置 Windows 控制台处理器失败: {exc}")


def main() -> None:
    """程序主入口。"""

    def exception_hook(exc_type, exc_value, exc_traceback):
        _logger.critical(
            "未捕获的全局异常",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        vfs_logger.error(f"未捕获异常: {exc_type.__name__}: {exc_value}")
        if MainWindow._instance:
            try:
                MainWindow._instance._shutdown_server()
            except Exception:
                pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_hook

    app = QApplication(sys.argv)

    setup_signal_handlers()

    try:
        window = MainWindow()
    except Exception as exc:
        _logger.critical(f"主窗口创建失败: {exc}\n{traceback.format_exc()}")
        QMessageBox.critical(None, "启动失败", f"程序无法启动：\n{exc}")
        sys.exit(1)

    window.show()
    _logger.info("进入 Qt 事件循环")

    exit_code = app.exec()
    _logger.info(f"Qt 事件循环退出, exit_code={exit_code}")

    if window.controller.is_server_running():
        vfs_logger.info("程序退出前最终清理...")
        _logger.info("程序退出前最终清理服务器")
        try:
            window.controller.cleanup()
        except Exception as exc:
            _logger.error(f"最终清理异常: {exc}")

    _logger.info(f"程序退出, exit_code={exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
