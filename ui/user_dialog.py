"""
用户与虚拟目录对话框

提供：
  - DragDropLineEdit: 支持拖放文件夹的路径输入框
  - VirtualDirectoryDialog: 添加/编辑虚拟目录的对话框
  - UserDialog: 添加/编辑用户的弹窗
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QDialogButtonBox, QFileDialog, QMessageBox, QCheckBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QUrl

from model.entities import VirtualDirectory, UserProfile, IpRule
from ui.constants import UISize
from ui.permission_ui import PermissionWidget
from ui.ip_rule_dialog import IpRuleDialog


# =============================================================================
# 可拖放路径输入组件
# =============================================================================

class DragDropLineEdit(QLineEdit):
    """支持拖放文件夹的路径输入框。"""
    
    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.setAcceptDrops(True)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.setToolTip("可以拖放文件夹到此处")
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if os.path.isdir(path):
                        event.acceptProposedAction()
                        self.setStyleSheet("border: 2px solid #4CAF50;")
                        return
        event.ignore()
    
    def dragLeaveEvent(self, event):
        self.setStyleSheet("")
        event.accept()
    
    def dropEvent(self, event):
        self.setStyleSheet("")
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if os.path.isdir(path):
                        self.setText(path)
                        event.acceptProposedAction()
                        return
        event.ignore()


# =============================================================================
# 虚拟目录对话框
# =============================================================================

class VirtualDirectoryDialog(QDialog):
    """添加/编辑虚拟目录的对话框。"""
    
    def __init__(self, parent=None, edit_vdir: VirtualDirectory = None):
        super().__init__(parent)
        self.edit_vdir = edit_vdir
        self.setWindowTitle("编辑虚拟目录" if edit_vdir else "添加虚拟目录")
        self.resize(UISize.VDIR_DIALOG_WIDTH, UISize.VDIR_DIALOG_HEIGHT)
        self.setup_ui()
        
        if edit_vdir:
            self.load_vdir_data(edit_vdir)
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("虚拟目录名称 (在FTP中显示的名称，留空则使用文件夹名):"))
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("例如: downloads, shared, backup")
        layout.addWidget(self.input_name)
        
        layout.addWidget(QLabel("真实路径 (可拖放文件夹):"))
        path_layout = QHBoxLayout()
        self.input_path = DragDropLineEdit(placeholder="拖放文件夹或点击选择...")
        self.input_path.textChanged.connect(self.on_path_changed)
        btn_browse = QPushButton("选择...")
        btn_browse.clicked.connect(self.browse_dir)
        path_layout.addWidget(self.input_path)
        path_layout.addWidget(btn_browse)
        layout.addLayout(path_layout)
        
        perm_group = QGroupBox("目录权限")
        perm_layout = QVBoxLayout()
        self.perm_widget = PermissionWidget()
        perm_layout.addWidget(self.perm_widget)
        perm_group.setLayout(perm_layout)
        layout.addWidget(perm_group)
        
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def on_path_changed(self, path: str):
        if path and not self.input_name.text():
            self.input_name.setText(os.path.basename(path.rstrip('/\\')))
    
    def browse_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if directory:
            self.input_path.setText(directory)
    
    def load_vdir_data(self, vdir: VirtualDirectory):
        self.input_name.setText(vdir.virtual_name)
        self.input_path.setText(vdir.real_path)
        self.perm_widget.set_permission(vdir.perm)
    
    def get_data(self) -> VirtualDirectory:
        return VirtualDirectory(
            virtual_name=self.input_name.text().strip(),
            real_path=self.input_path.text().strip(),
            perm=self.perm_widget.get_permission()
        )


# =============================================================================
# 用户编辑弹窗
# =============================================================================

class UserDialog(QWidget):
    """添加/编辑用户的弹窗。"""
    
    def __init__(self, parent=None, edit_user: UserProfile = None, default_root: str = ""):
        super().__init__(parent, Qt.WindowType.Window)
        self.edit_user = edit_user
        self.default_root = default_root
        self.setWindowTitle("编辑用户" if edit_user else "添加用户")
        self.resize(UISize.USER_DIALOG_WIDTH, UISize.USER_DIALOG_HEIGHT)
        self.setup_ui()
        
        self.virtual_dirs: List[VirtualDirectory] = []
        self.ip_rules:     List[IpRule]           = []
        
        if edit_user:
            self.load_user_data(edit_user)
        else:
            if default_root:
                self.input_root_dir.setText(default_root)
        
    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        basic_group = QGroupBox("基本信息")
        basic_layout = QVBoxLayout()
        
        basic_layout.addWidget(QLabel("用户名:"))
        self.input_user = QLineEdit()
        if self.edit_user:
            self.input_user.setEnabled(False)
            self.input_user.setToolTip("用户名创建后不可修改")
        basic_layout.addWidget(self.input_user)

        basic_layout.addWidget(QLabel("密码:"))
        self.input_pass = QLineEdit()
        self.input_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pass.setPlaceholderText("编辑时留空表示不修改密码" if self.edit_user else "")
        basic_layout.addWidget(self.input_pass)
        
        basic_layout.addWidget(QLabel("访问控制:"))
        ip_ctrl_layout = QHBoxLayout()
        self.check_allow_all = QCheckBox("允许所有 IP 访问")
        self.check_allow_all.setChecked(True)
        self.check_allow_all.toggled.connect(self.on_allow_all_toggled)
        
        self.btn_ip_rules = QPushButton("配置 IP 规则...")
        self.btn_ip_rules.clicked.connect(self.open_ip_rule_dialog)
        
        ip_ctrl_layout.addWidget(self.check_allow_all)
        ip_ctrl_layout.addWidget(self.btn_ip_rules)
        basic_layout.addLayout(ip_ctrl_layout)
        
        basic_group.setLayout(basic_layout)
        layout.addWidget(basic_group)

        root_group = QGroupBox("根目录设置 (用户登录后的默认目录)")
        root_layout = QVBoxLayout()
        
        root_layout.addWidget(QLabel("根目录路径 (可拖放文件夹，留空使用默认):"))
        root_path_layout = QHBoxLayout()
        self.input_root_dir = DragDropLineEdit(placeholder="拖放文件夹或点击选择... (默认: 程序目录/root)")
        btn_browse_root = QPushButton("选择...")
        btn_browse_root.clicked.connect(self.browse_root_dir)
        root_path_layout.addWidget(self.input_root_dir)
        root_path_layout.addWidget(btn_browse_root)
        root_layout.addLayout(root_path_layout)
        
        root_layout.addWidget(QLabel("根目录权限:"))
        self.root_perm_widget = PermissionWidget()
        root_layout.addWidget(self.root_perm_widget)
        
        root_group.setLayout(root_layout)
        layout.addWidget(root_group)

        vdir_group = QGroupBox("虚拟目录 (可添加多个映射目录)")
        vdir_layout = QVBoxLayout()
        
        self.vdir_table = QTableWidget()
        self.vdir_table.setColumnCount(4)
        self.vdir_table.setHorizontalHeaderLabels(["虚拟名称", "真实路径", "权限", "操作"])
        self.vdir_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vdir_table.setMinimumHeight(UISize.VDIR_TABLE_MIN_HEIGHT)
        vdir_layout.addWidget(self.vdir_table)
        
        btn_add_vdir = QPushButton("添加虚拟目录")
        btn_add_vdir.clicked.connect(self.add_virtual_dir)
        vdir_layout.addWidget(btn_add_vdir)
        
        vdir_group.setLayout(vdir_layout)
        layout.addWidget(vdir_group)

        self.btn_save = QPushButton("保存用户")
        layout.addWidget(self.btn_save)

    def browse_root_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择根目录")
        if directory:
            self.input_root_dir.setText(directory)

    def open_ip_rule_dialog(self):
        dlg = IpRuleDialog(self, self.ip_rules)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ip_rules = dlg.get_rules()
            self._update_ip_button_text()

    def _update_ip_button_text(self):
        enabled_count = len([r for r in self.ip_rules if r.enabled])
        self.btn_ip_rules.setText(f"修改规则 (已配: {enabled_count})")

    def on_allow_all_toggled(self, checked: bool):
        self.btn_ip_rules.setEnabled(not checked)
    
    def add_virtual_dir(self):
        dialog = VirtualDirectoryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            vdir = dialog.get_data()
            if vdir.real_path:
                self.virtual_dirs.append(vdir)
                self.refresh_vdir_table()
    
    def edit_virtual_dir(self, index: int):
        if 0 <= index < len(self.virtual_dirs):
            dialog = VirtualDirectoryDialog(self, edit_vdir=self.virtual_dirs[index])
            if dialog.exec() == QDialog.DialogCode.Accepted:
                vdir = dialog.get_data()
                if vdir.real_path:
                    self.virtual_dirs[index] = vdir
                    self.refresh_vdir_table()
    
    def delete_virtual_dir(self, index: int):
        if 0 <= index < len(self.virtual_dirs):
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定要删除虚拟目录 '{self.virtual_dirs[index].get_display_name()}' 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                del self.virtual_dirs[index]
                self.refresh_vdir_table()
    
    def refresh_vdir_table(self):
        self.vdir_table.setRowCount(0)
        for row, vdir in enumerate(self.virtual_dirs):
            self.vdir_table.insertRow(row)
            self.vdir_table.setItem(row, 0, QTableWidgetItem(vdir.get_display_name()))
            self.vdir_table.setItem(row, 1, QTableWidgetItem(vdir.real_path))
            self.vdir_table.setItem(row, 2, QTableWidgetItem(vdir.perm))
            
            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(4, 2, 4, 2)
            
            btn_edit = QPushButton("编辑")
            btn_edit.clicked.connect(lambda _, idx=row: self.edit_virtual_dir(idx))
            btn_del = QPushButton("删除")
            btn_del.clicked.connect(lambda _, idx=row: self.delete_virtual_dir(idx))
            
            action_layout.addWidget(btn_edit)
            action_layout.addWidget(btn_del)
            action_widget.setLayout(action_layout)
            self.vdir_table.setCellWidget(row, 3, action_widget)
    
    def load_user_data(self, user: UserProfile):
        self.input_user.setText(user.username)
        self.input_root_dir.setText(user.root_dir)
        self.root_perm_widget.set_permission(user.root_perm)
        self.virtual_dirs = [VirtualDirectory(
            vd.virtual_name, vd.real_path, perm=vd.perm
        ) for vd in user.virtual_dirs]
        self.ip_rules = [IpRule(r.pattern, r.rule_type, r.enabled) for r in user.ip_rules]
        self.check_allow_all.setChecked(user.allow_all_ips)
        self.on_allow_all_toggled(user.allow_all_ips)
        self._update_ip_button_text()
        self.refresh_vdir_table()

    def get_data(self) -> UserProfile:
        password = self.input_pass.text()
        if self.edit_user and not password:
            password = self.edit_user.password
        
        return UserProfile(
            username=self.input_user.text().strip(),
            password=password,
            root_dir=self.input_root_dir.text().strip(),
            root_perm=self.root_perm_widget.get_permission(),
            virtual_dirs=self.virtual_dirs.copy(),
            ip_rules=self.ip_rules.copy(),
            allow_all_ips=self.check_allow_all.isChecked()
        )
