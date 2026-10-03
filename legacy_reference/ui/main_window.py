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
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QMessageBox, QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QAction, QPixmap, QColor, QPainter

from model.entities import UserProfile
from control.file_server import FileServerController
from protocol.base import FileServerProtocol
from ui.constants import UISize, vfs_logger, hide_console, show_console, is_console_visible
from ui.user_dialog import UserDialog


class MainWindow(QMainWindow):
    """
    FTP 服务器管理主窗口。
    
    通过 FileServerController 控制器管理服务器，实现 UI 与业务逻辑分离。
    """
    
    _instance: Optional["MainWindow"] = None
    
    def __init__(self):
        super().__init__()
        MainWindow._instance = self
        
        self.setWindowTitle("Python FTP 服务器管理器 (PyQt6) - 虚拟目录版")
        self.resize(UISize.MAIN_WINDOW_WIDTH, UISize.MAIN_WINDOW_HEIGHT)

        self.controller = FileServerController(
            config_path="ftp_users.json",
            protocol=FileServerProtocol.FTP,
        )
        self._setup_controller_callbacks()
        
        self._is_closing = False
        self._force_quit = False

        self.setup_ui()
        self.setup_tray_icon()
        self.refresh_table()
        
        hide_console()
        self.update_console_button()
        
        atexit.register(self._cleanup_on_exit)
    
    def _setup_controller_callbacks(self):
        self.controller.register_callback('on_status', self.on_server_status)
        self.controller.register_callback('on_error', self.on_server_error)
        self.controller.register_callback('on_started', self.on_server_started)
        self.controller.register_callback('on_stopped', self.on_server_stopped)
        self.controller.register_callback('on_user_changed', self.refresh_table)
    
    def setup_tray_icon(self):
        """设置系统托盘图标。"""
        self.tray_icon = QSystemTrayIcon(self)
        
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setBrush(QColor(76, 175, 80))
        painter.setPen(QColor(56, 142, 60))
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "FTP")
        painter.end()
        
        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip("FTP 服务器管理器")
        
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
    
    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()
    
    def show_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()
    
    def minimize_to_tray(self):
        self.hide()
        self.tray_icon.show()
        self.tray_icon.showMessage(
            "FTP 服务器",
            "程序已最小化到系统托盘，服务器继续运行中。",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )
    
    def force_quit(self):
        self._force_quit = True
        self.close()
    
    def update_tray_menu(self):
        if self.controller.is_server_running():
            self.action_toggle_server.setText("停止服务器")
        else:
            self.action_toggle_server.setText("启动服务器")

    def on_server_status(self, message: str) -> None:
        self.lbl_status.setText(f"状态: {message}")
    
    def on_server_error(self, message: str) -> None:
        self.lbl_status.setText(f"错误: {message}")
        QMessageBox.critical(self, "服务器错误", message)
        self._reset_start_button()
    
    def on_server_started(self) -> None:
        QTimer.singleShot(100, self._check_server_started)
    
    def _check_server_started(self) -> None:
        if self.controller.is_server_running():
            self.btn_start.setText("停止服务器")
            self.btn_start.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
            self.input_port.setEnabled(False)
            self.update_tray_menu()
        else:
            self._reset_start_button()
    
    def on_server_stopped(self) -> None:
        self.btn_start.setText("启动服务器")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.input_port.setEnabled(True)
        self.lbl_status.setText("状态: 已停止")
        self.update_tray_menu()
    
    def _reset_start_button(self) -> None:
        self.btn_start.setText("启动服务器")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.input_port.setEnabled(True)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        control_group = QGroupBox("服务器控制")
        control_layout = QHBoxLayout()
        
        self.input_port = QLineEdit(str(self.controller.get_server_port()))
        self.input_port.setPlaceholderText("端口")
        self.input_port.setFixedWidth(UISize.PORT_INPUT_WIDTH)
        
        self.btn_start = QPushButton("启动服务器")
        self.btn_start.clicked.connect(self.on_toggle_server)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        
        self.lbl_status = QLabel("状态: 已停止")
        self.lbl_status.setMinimumWidth(UISize.STATUS_LABEL_MIN_WIDTH)
        self.lbl_status.setMaximumWidth(UISize.STATUS_LABEL_MAX_WIDTH)
        
        self.btn_console = QPushButton("显示控制台")
        self.btn_console.clicked.connect(self.toggle_console)
        self.btn_console.setToolTip("显示或隐藏控制台窗口")
        
        self.btn_tray = QPushButton("后台运行")
        self.btn_tray.clicked.connect(self.minimize_to_tray)
        self.btn_tray.setToolTip("最小化到系统托盘后台运行")
        
        control_layout.addWidget(QLabel("端口:"))
        control_layout.addWidget(self.input_port)
        control_layout.addWidget(self.btn_start)
        control_layout.addWidget(self.lbl_status)
        control_layout.addStretch()
        control_layout.addWidget(self.btn_console)
        control_layout.addWidget(self.btn_tray)
        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

        user_group = QGroupBox("用户管理")
        user_layout = QVBoxLayout()
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["用户名", "根目录", "根目录权限", "虚拟目录数", "操作"])
        
        header = self.table.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
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

    def refresh_table(self):
        self.table.setRowCount(0)
        for row, user in enumerate(self.controller.get_users()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(user.username))
            self.table.setItem(row, 1, QTableWidgetItem(user.root_dir or "(默认)"))
            
            root_perm_item = QTableWidgetItem(user.root_perm or "(无)")
            root_perm_item.setToolTip(self.get_permission_tooltip(user.root_perm))
            self.table.setItem(row, 2, root_perm_item)
            
            vdir_count = len(user.virtual_dirs)
            vdir_item = QTableWidgetItem(str(vdir_count))
            if vdir_count > 0:
                vdir_tooltip = "\n".join([
                    f"/{vd.get_display_name()} -> {vd.real_path} [{vd.perm}]"
                    for vd in user.virtual_dirs
                ])
                vdir_item.setToolTip(vdir_tooltip)
            self.table.setItem(row, 3, vdir_item)

            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(4, 2, 4, 2)
            
            btn_edit = QPushButton("编辑")
            btn_edit.clicked.connect(lambda _, u=user: self.edit_user(u))
            btn_del = QPushButton("删除")
            btn_del.clicked.connect(lambda _, username=user.username: self.delete_user(username))
            
            action_layout.addWidget(btn_edit)
            action_layout.addWidget(btn_del)
            action_widget.setLayout(action_layout)
            self.table.setCellWidget(row, 4, action_widget)
    
    def get_permission_tooltip(self, perm: str) -> str:
        """获取权限的中文描述（用于 tooltip）。"""
        if not perm:
            return "无权限"
        
        perm_names = {
            'e': '切换目录', 'l': '列出文件', 'r': '下载文件',
            'w': '上传/写入', 'a': '追加数据', 'd': '删除',
            'f': '重命名',   'm': '创建目录', 'M': '修改权限'
        }
        
        descriptions = []
        for char in perm:
            if char in perm_names:
                descriptions.append(f"{char.upper()}: {perm_names[char]}")
        
        return "\n".join(descriptions) if descriptions else f"自定义权限: {perm}"
    
    def on_table_double_click(self, row: int, column: int):
        users = self.controller.get_users()
        if row < 0 or row >= len(users):
            return
        self.edit_user(users[row])

    def open_add_user_dialog(self):
        self.dialog = UserDialog(self, default_root=self.controller.get_default_root())
        self.dialog.btn_save.clicked.connect(self.save_user_from_dialog)
        self.dialog.show()
    
    def edit_user(self, user: UserProfile):
        self.dialog = UserDialog(self, edit_user=user, default_root=self.controller.get_default_root())
        self.dialog.btn_save.clicked.connect(self.save_user_from_dialog)
        self.dialog.show()

    def save_user_from_dialog(self):
        new_user = self.dialog.get_data()
        
        if not new_user.username:
            QMessageBox.warning(self, "输入错误", "用户名不能为空！")
            return
        
        if not self.dialog.edit_user and not new_user.password:
            QMessageBox.warning(self, "输入错误", "新用户必须设置密码！")
            return

        if self.dialog.edit_user:
            success, error_msg = self.controller.update_user(self.dialog.edit_user.username, new_user)
            action = "更新"
        else:
            success, error_msg = self.controller.add_user(new_user)
            action = "添加"
        
        if success:
            self.dialog.close()
            self.lbl_status.setText(f"状态: 用户 {new_user.username} 已{action}")
        else:
            QMessageBox.warning(self, "操作失败", error_msg or "未知错误")

    def delete_user(self, username: str):
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除用户 '{username}' 吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, error_msg = self.controller.remove_user(username)
            if success:
                self.lbl_status.setText(f"状态: 用户 {username} 已删除")
            else:
                QMessageBox.warning(self, "删除失败", error_msg or "未知错误")

    def on_toggle_server(self):
        if self.controller.is_server_running():
            self.controller.stop_server()
        else:
            try:
                port = int(self.input_port.text())
                success, error_msg = self.controller.set_server_port(port)
                if not success:
                    QMessageBox.warning(self, "端口错误", error_msg or "无效的端口号")
                    return
            except ValueError:
                QMessageBox.warning(self, "端口错误", "请输入有效的端口号")
                return

            success, error_msg = self.controller.start_server()
            if not success:
                QMessageBox.warning(self, "启动失败", error_msg or "服务器启动失败")
    
    def toggle_console(self):
        if is_console_visible():
            hide_console()
        else:
            show_console()
        self.update_console_button()
    
    def update_console_button(self):
        if is_console_visible():
            self.btn_console.setText("隐藏控制台")
        else:
            self.btn_console.setText("显示控制台")
    
    def closeEvent(self, event) -> None:
        if self._is_closing:
            event.accept()
            return
        
        if self._force_quit:
            self._shutdown_server()
            self.tray_icon.hide()
            event.accept()
            return
        
        if self.controller.is_server_running():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("关闭确认")
            msg_box.setText("FTP 服务器正在运行中")
            msg_box.setInformativeText("您可以选择最小化到系统托盘继续后台运行，或者退出程序。")
            msg_box.setIcon(QMessageBox.Icon.Question)
            
            btn_background = msg_box.addButton("后台运行", QMessageBox.ButtonRole.AcceptRole)
            btn_quit = msg_box.addButton("仍然退出", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_background)
            
            msg_box.exec()
            
            clicked_button = msg_box.clickedButton()
            if clicked_button == btn_background:
                self.minimize_to_tray()
                event.ignore()
                return
            elif clicked_button == btn_quit:
                self._shutdown_server()
                self.tray_icon.hide()
                event.accept()
                return
            else:
                event.ignore()
                return
        
        self.tray_icon.hide()
        event.accept()
    
    def _shutdown_server(self) -> None:
        if self._is_closing:
            return
        
        self._is_closing = True
        vfs_logger.info("正在关闭 FTP 服务器...")
        
        try:
            if self.controller.is_server_running():
                self.lbl_status.setText("状态: 正在停止服务器...")
                self.controller.stop_server()
                vfs_logger.info("FTP 服务器已安全停止")
        except Exception as e:
            vfs_logger.error(f"关闭服务器时出错: {e}")
    
    def _cleanup_on_exit(self) -> None:
        try:
            if self.controller.is_server_running():
                vfs_logger.info("atexit: 正在清理 FTP 服务器...")
                self.controller.cleanup()
        except Exception as e:
            vfs_logger.error(f"atexit 清理时出错: {e}")
    
    @classmethod
    def handle_signal(cls, signum, frame) -> None:
        signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        vfs_logger.info(f"收到信号: {signal_name}")
        
        if cls._instance:
            cls._instance._shutdown_server()
        
        QApplication.quit()


# =============================================================================
# 程序入口
# =============================================================================

def setup_signal_handlers() -> None:
    """设置系统信号处理器。"""
    signal.signal(signal.SIGINT, MainWindow.handle_signal)
    
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, MainWindow.handle_signal)
    
    import sys as _sys
    if _sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            
            CTRL_C_EVENT = 0
            CTRL_BREAK_EVENT = 1
            CTRL_CLOSE_EVENT = 2
            
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
            def console_handler(event):
                if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
                    vfs_logger.info(f"Windows 控制台事件: {event}")
                    if MainWindow._instance:
                        MainWindow._instance._shutdown_server()
                    return True
                return False
            
            kernel32.SetConsoleCtrlHandler(console_handler, True)
        except Exception as e:
            vfs_logger.warning(f"设置 Windows 控制台处理器失败: {e}")


def main():
    """程序主入口。"""
    def exception_hook(exc_type, exc_value, exc_traceback):
        vfs_logger.error(f"未捕获的异常: {exc_type.__name__}: {exc_value}")
        
        if MainWindow._instance:
            try:
                MainWindow._instance._shutdown_server()
            except Exception:
                pass
        
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = exception_hook
    
    app = QApplication(sys.argv)
    
    setup_signal_handlers()
    
    window = MainWindow()
    window.show()
    
    exit_code = app.exec()
    
    if window.controller.is_server_running():
        vfs_logger.info("程序退出前最终清理...")
        window.controller.cleanup()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
