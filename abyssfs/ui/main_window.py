"""
FTP 服务器管理主窗口

提供：
  - MainWindow: FTP 服务器管理主窗口（通过 FileService 控制）
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
    QTextEdit, QDialog, QSizePolicy, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QPixmap, QColor, QPainter

from abyssfs.model.entities import UserProfile
from abyssfs.protocol.base import FileServerProtocol, TlsConfig, SshConfig
from abyssfs.paths import CONFIG_PATH, icon_path
from abyssfs.service import ServiceEvent, ServiceFactory
from abyssfs.service.constants import PerformanceValue
from abyssfs.ui.constants import UISize, vfs_logger, hide_console, show_console, is_console_visible
from abyssfs.ui.firewall import (
    request_firewall_access,
    should_offer_firewall_access,
    should_request_firewall_access,
)
from abyssfs.ui.user_dialog import UserDialog
from abyssfs.ui.settings_dialog import SettingsDialog
from abyssfs.ui.protocol_options import MAIN_PROTO_ICONS, MAIN_PROTO_LABELS

_logger = logging.getLogger(__name__)

_PROTO_LABELS = MAIN_PROTO_LABELS


class ServiceSignalBridge(QObject):
    """把 Service 事件转投递到 Qt 主线程。"""

    status = pyqtSignal(str)
    error = pyqtSignal(str)
    started = pyqtSignal()
    stopped = pyqtSignal()
    user_changed = pyqtSignal()
    client_access = pyqtSignal(str)


class MainWindow(QMainWindow):
    """
    FTP 服务器管理主窗口。

    通过 FileService 管理服务器，不直接依赖控制器和后端实现。
    支持多协议切换，以及证书/密钥的图形化配置。
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
            self._service_runtime = ServiceFactory.open(
                config_path=str(CONFIG_PATH),
                protocol=FileServerProtocol.FTP,
            )
            _logger.info(
                f"FileService 初始化完成, "
                f"协议={self.controller.get_protocol().value}, "
                f"端口={self.controller.get_server_port()}"
            )
        except Exception as exc:
            _logger.critical(f"FileService 初始化失败: {exc}\n{traceback.format_exc()}")
            QMessageBox.critical(None, "启动失败", f"初始化服务失败：\n{exc}")
            raise

        self._setup_service_callbacks()

        self._is_closing  = False
        self._force_quit  = False
        self._restore_console = False
        self._firewall_access_pending = False
        self._firewall_access_requested = False
        self._firewall_access_port: Optional[int] = None

        self.setup_ui()
        self._setup_performance_timer()
        self.setup_tray_icon()
        self.refresh_table()

        hide_console()
        self.update_console_button()
        self._refresh_proto_label()

        atexit.register(self._cleanup_on_exit)
        _logger.info("MainWindow 初始化完成")

    # ------------------------------------------------------------------
    # Service 回调
    # ------------------------------------------------------------------

    @property
    def controller(self):
        """兼容现有 UI 调用；每次取得当前版本的 Service 门面。"""
        return self._service_runtime.api()

    def _setup_service_callbacks(self) -> None:
        _logger.debug("注册 Service 回调")
        self._service_bridge = ServiceSignalBridge(self)
        self._service_bridge.status.connect(self.on_server_status)
        self._service_bridge.error.connect(self.on_server_error)
        self._service_bridge.started.connect(self.on_server_started)
        self._service_bridge.stopped.connect(self.on_server_stopped)
        self._service_bridge.user_changed.connect(self.refresh_table)
        self._service_bridge.client_access.connect(self.on_client_access)

        self.controller.sub(ServiceEvent.STATUS, self._service_bridge.status.emit)
        self.controller.sub(ServiceEvent.ERROR, self._service_bridge.error.emit)
        self.controller.sub(ServiceEvent.STARTED, self._service_bridge.started.emit)
        self.controller.sub(ServiceEvent.STOPPED, self._service_bridge.stopped.emit)
        self.controller.sub(ServiceEvent.USER_CHANGED, self._service_bridge.user_changed.emit)
        self.controller.sub(ServiceEvent.CLIENT_ACCESS, self._service_bridge.client_access.emit)

    # ------------------------------------------------------------------
    # 系统托盘
    # ------------------------------------------------------------------

    def setup_tray_icon(self) -> None:
        """设置系统托盘图标。"""
        _logger.debug("初始化系统托盘图标")
        self.tray_icon = QSystemTrayIcon(self)

        # 尝试加载 ico，失败则绘制占位
        ico_path = icon_path("ftp.ico")
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
        if self._restore_console:
            show_console()
            self._restore_console = False
            self.update_console_button()
        self.showNormal()
        self.activateWindow()
        self.raise_()
        _logger.debug("从系统托盘恢复窗口")

    def minimize_to_tray(self) -> None:
        self._restore_console = is_console_visible()
        if self._restore_console:
            hide_console()
            self.update_console_button()
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
        self._set_status_text(f"状态: {message}")
        _logger.info(f"[服务器状态] {message}")
        self._append_log(message)

    def on_server_error(self, message: str) -> None:
        self._set_status_text(f"错误: {message}")
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
        self._set_status_text("状态: 已停止")
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
        self.lbl_status.setMinimumWidth(UISize.STATUS_LABEL_MIN_WIDTH)
        self.lbl_status.setMaximumWidth(UISize.STATUS_LABEL_MAX_WIDTH)
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.lbl_status.setToolTip("状态: 已停止")
        
        line1.addWidget(QLabel("端口:"))
        line1.addWidget(self.input_port)
        line1.addWidget(self.btn_start)
        line1.addWidget(self.lbl_status)
        line1.addStretch()
        
        # 第二行：协议与工具
        line2 = QHBoxLayout()
        self.lbl_proto = QLabel()
        self.lbl_proto.setMinimumWidth(UISize.STATUS_LABEL_MIN_WIDTH)
        self.lbl_proto.setMaximumWidth(UISize.PROTO_LABEL_MAX_WIDTH)
        self.lbl_proto.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.btn_settings = QPushButton("⚙ 全局设置...")
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        self.btn_console = QPushButton("控制台")
        self.btn_console.clicked.connect(self.toggle_console)
        self.btn_tray = QPushButton("后台运行")
        self.btn_tray.clicked.connect(self.minimize_to_tray)
        
        line2.addWidget(self.lbl_proto)
        line2.addStretch()
        line2.addWidget(self.btn_settings)
        line2.addWidget(self.btn_console)
        line2.addWidget(self.btn_tray)
        
        ctrl_main_layout.addLayout(line1)
        ctrl_main_layout.addLayout(line2)
        ctrl_group.setLayout(ctrl_main_layout)
        main_layout.addWidget(ctrl_group)

        # ---- 运行时性能区 ----
        perf_group = QGroupBox("运行时性能")
        perf_layout = QGridLayout(perf_group)
        self.lbl_cpu = QLabel()
        self.lbl_memory = QLabel()
        self.lbl_threads = QLabel()
        self.lbl_uptime = QLabel()
        for label in (
            self.lbl_cpu,
            self.lbl_memory,
            self.lbl_threads,
            self.lbl_uptime,
        ):
            label.setMinimumWidth(UISize.PERFORMANCE_LABEL_MIN_WIDTH)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        perf_layout.addWidget(self.lbl_cpu, 0, 0, 1, 2)
        perf_layout.addWidget(self.lbl_memory, 1, 0, 1, 2)
        perf_layout.addWidget(self.lbl_threads, 2, 0, 1, 2)
        perf_layout.addWidget(self.lbl_uptime, 3, 0, 1, 2)
        main_layout.addWidget(perf_group)

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

    def _setup_performance_timer(self) -> None:
        self._perf_timer = QTimer(self)
        self._perf_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._perf_timer.setInterval(UISize.PERFORMANCE_FOREGROUND_MS)
        self._perf_timer.timeout.connect(self._refresh_performance)
        self._refresh_performance()
        self._perf_timer.start()

    def _set_performance_mode(self, foreground: bool) -> None:
        interval = (
            UISize.PERFORMANCE_FOREGROUND_MS
            if foreground
            else UISize.PERFORMANCE_BACKGROUND_MS
        )
        if self._perf_timer.interval() == interval:
            return
        self._perf_timer.setInterval(interval)
        if foreground:
            self._refresh_performance()

    def _refresh_performance(self) -> None:
        metrics = self._service_runtime.metrics()
        memory = metrics.memory / PerformanceValue.BYTES_PER_MIB
        memory_peak = metrics.memory_peak / PerformanceValue.BYTES_PER_MIB
        hours, remainder = divmod(int(metrics.uptime), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.lbl_cpu.setText(
            f"CPU {metrics.cpu:.1f}% · 启动平均 {metrics.cpu_avg:.1f}% · 峰值 {metrics.cpu_peak:.1f}%"
        )
        self.lbl_memory.setText(
            f"内存 {memory:.1f} MiB · 峰值 {memory_peak:.1f} MiB"
        )
        self.lbl_threads.setText(
            f"Python 线程 {metrics.threads} · 峰值 {metrics.threads_peak}"
        )
        self.lbl_uptime.setText(
            f"运行 {hours:02d}:{minutes:02d}:{seconds:02d} · 采样 {metrics.samples}"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_performance_mode(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._set_performance_mode(False)

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
                tls_config=self.controller.get_tls_config(),
                ssh_config=self.controller.get_ssh_config(),
                port=current_port,
                host=self.controller.get_server_host(),
                max_cons=self.controller.get_max_cons(),
                max_cons_per_ip=self.controller.get_max_cons_per_ip(),
                loaded_dir_marker_enabled=self.controller.is_loaded_dir_marker_enabled(),
                parent=self,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                (
                    proto,
                    tls_cfg,
                    ssh_cfg,
                    n_port,
                    n_host,
                    n_max,
                    n_max_ip,
                    loaded_dir_marker_enabled,
                ) = dlg.get_result()
                _logger.info(
                    f"[SettingsDialog] 用户确认更改: {proto.value} | "
                    f"certfile={tls_cfg.certfile!r} | "
                    f"host_key={ssh_cfg.host_key_path!r}"
                )
                ok, err = self.controller.update_global_settings(
                    protocol=proto,
                    tls_config=tls_cfg,
                    ssh_config=ssh_cfg,
                    port=n_port,
                    host=n_host,
                    max_cons=n_max,
                    max_cons_per_ip=n_max_ip,
                    loaded_dir_marker_enabled=loaded_dir_marker_enabled,
                )

                if ok:
                    self._refresh_proto_label()
                    self._set_status_text(f"状态: 全局配置已更新 ({_PROTO_LABELS[proto]})")
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
        self.lbl_proto.setText(f"{MAIN_PROTO_ICONS[proto]} {_PROTO_LABELS[proto]}")
        self.lbl_proto.setToolTip(_PROTO_LABELS[proto])
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
                    f"/{vd.get_display_name()} → {vd.real_path} "
                    f"[{vd.perm}, {'开启' if vd.enabled else '关闭'}, "
                    f"{'必须' if vd.is_required() else '可选'}]"
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
            "T": "修改时间",
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
                result = self.controller.save(
                    new_user,
                    self.dialog.edit_user.username,
                )
                action = "更新"
            else:
                result = self.controller.save(new_user)
                action = "添加"

            success = result.ok
            error_msg = result.message

            if success:
                self.dialog.close()
                detail = error_msg or f"用户已{action}"
                self._set_status_text(
                    f"状态: {detail}（配置版本 {result.version}）"
                )
                self._append_log(
                    f"👤 {detail}: {new_user.username}（v{result.version}）"
                )
                _logger.info(
                    f"用户{action}成功: {new_user.username}, "
                    f"version={result.version}, detail={detail}"
                )
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
                    self._set_status_text(f"状态: 用户 {username} 已删除")
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

    def _offer_firewall_access_if_needed(self, port: int) -> None:
        host = self.controller.get_server_host()
        self._firewall_access_pending = False
        self._firewall_access_requested = False
        self._firewall_access_port = None
        if not should_offer_firewall_access(host):
            return

        reply = QMessageBox.question(
            self,
            "允许局域网访问",
            "检测到服务绑定的网络接口为 Public，防火墙可能会阻止局域网设备连接。\n\n"
            f"是否在收到客户端访问时申请允许 TCP {port} 入站访问？\n"
            "选择“是”后不会立刻弹出管理员确认窗口。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._firewall_access_pending = True
        self._firewall_access_port = port
        _logger.info("防火墙授权已设置为延迟触发: port=%s", port)

    def on_client_access(self, remote_ip: str) -> None:
        if not self._firewall_access_pending or self._firewall_access_requested:
            return

        port = self._firewall_access_port
        if port is None:
            return

        host = self.controller.get_server_host()
        if not should_request_firewall_access(host, remote_ip):
            return

        self._firewall_access_requested = True
        ok, msg = request_firewall_access(port, f"AbyssFS TCP {port}")
        if ok:
            QMessageBox.information(
                self,
                "已请求防火墙授权",
                "检测到客户端访问，已打开 Windows 管理员确认窗口。\n"
                "确认后，局域网设备应可访问当前端口。",
            )
            _logger.info("防火墙授权请求已延迟触发: remote_ip=%s, %s", remote_ip, msg)
        else:
            QMessageBox.warning(self, "防火墙授权失败", msg)
            _logger.warning("防火墙授权请求失败: remote_ip=%s, %s", remote_ip, msg)

    def on_toggle_server(self) -> None:
        try:
            if self.controller.is_server_running():
                _logger.info("用户请求停止服务器")
                self._firewall_access_pending = False
                self._firewall_access_requested = False
                self._firewall_access_port = None
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

                self._offer_firewall_access_if_needed(port)

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

    def _set_status_text(self, text: str) -> None:
        self.lbl_status.setToolTip(text)
        self.lbl_status.setText(
            self.lbl_status.fontMetrics().elidedText(
                text,
                Qt.TextElideMode.ElideRight,
                UISize.STATUS_LABEL_MAX_WIDTH,
            )
        )

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
                self._set_status_text("状态: 正在停止服务器...")
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
