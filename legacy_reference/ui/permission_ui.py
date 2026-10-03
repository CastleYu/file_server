"""
权限相关 UI 组件

提供：
  - PERMISSION_PRESETS: GUI 用的 FTP 字符串权限预设（从 model 派生）
  - PERMISSION_CATEGORIES: GUI 用的权限分类（从 model 派生）
  - CustomPermissionDialog: 自定义权限选择对话框（3列布局）
  - PermissionWidget: 可复用的权限选择组件（预设单选 + 自定义配置按钮）
"""

from __future__ import annotations

from typing import Dict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QGroupBox, QDialogButtonBox, QWidget, QRadioButton, QPushButton,
    QButtonGroup,
)

from model.permission import (
    FilePermission,
    to_ftp_perm_str,
    PERMISSION_PRESETS as MODEL_PERMISSION_PRESETS,
    PERMISSION_DETAILS as MODEL_PERMISSION_DETAILS,
)
from ui.constants import UISize


# =============================================================================
# 权限预设常量（由 model 派生，单一数据源）
# =============================================================================

# 从 model 的 PERMISSION_PRESETS 派生 FTP 字符串版
PERMISSION_PRESETS = {
    name: (to_ftp_perm_str(fp), display, desc)
    for name, (fp, display, desc) in MODEL_PERMISSION_PRESETS.items()
}
PERMISSION_PRESETS["custom"] = ("", "自定义", "自定义权限组合")

# 从 model 的 PERMISSION_DETAILS 派生 GUI 用的分类（读/写/其他）
_READ_PERMS  = (FilePermission.NAVIGATE, FilePermission.LIST, FilePermission.READ)
_WRITE_PERMS = (FilePermission.WRITE, FilePermission.APPEND, FilePermission.DELETE,
                FilePermission.RENAME, FilePermission.MKDIR)
_OTHER_PERMS = (FilePermission.CHMOD,)


def _perms_entry(flags):
    return [
        (to_ftp_perm_str(p), MODEL_PERMISSION_DETAILS[p][0], MODEL_PERMISSION_DETAILS[p][1])
        for p in flags
    ]


PERMISSION_CATEGORIES = {
    "read":  {"title": "读权限", "perms": _perms_entry(_READ_PERMS)},
    "write": {"title": "写权限", "perms": _perms_entry(_WRITE_PERMS)},
    "other": {"title": "其他",   "perms": _perms_entry(_OTHER_PERMS)},
}


# =============================================================================
# 自定义权限选择对话框
# =============================================================================

class CustomPermissionDialog(QDialog):
    """
    自定义权限选择对话框。
    
    以 3 列布局显示所有权限选项：读权限、写权限、其他。
    """
    
    def __init__(self, parent=None, current_perm: str = ""):
        super().__init__(parent)
        self.setWindowTitle("自定义权限设置")
        self.resize(UISize.PERM_DIALOG_WIDTH, UISize.PERM_DIALOG_HEIGHT)
        self.current_perm = current_perm
        self.perm_checkboxes: Dict[str, QCheckBox] = {}
        self.setup_ui()
        self.load_permissions(current_perm)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        hint_label = QLabel("选择需要的权限组合：")
        layout.addWidget(hint_label)
        
        columns_layout = QHBoxLayout()
        
        for category_key, category_data in PERMISSION_CATEGORIES.items():
            group = QGroupBox(category_data["title"])
            group_layout = QVBoxLayout()
            
            for perm_char, perm_name, perm_desc in category_data["perms"]:
                checkbox = QCheckBox(f"{perm_char.upper()} - {perm_name}")
                checkbox.setToolTip(perm_desc)
                self.perm_checkboxes[perm_char] = checkbox
                group_layout.addWidget(checkbox)
            
            group_layout.addStretch()
            group.setLayout(group_layout)
            columns_layout.addWidget(group)
        
        layout.addLayout(columns_layout)
        
        self.lbl_preview = QLabel("当前权限: ")
        self.lbl_preview.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.lbl_preview)
        
        for checkbox in self.perm_checkboxes.values():
            checkbox.stateChanged.connect(self.update_preview)
        
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def load_permissions(self, perm: str):
        """加载权限到复选框。"""
        for perm_char, checkbox in self.perm_checkboxes.items():
            checkbox.setChecked(perm_char in perm)
        self.update_preview()
    
    def update_preview(self):
        """更新权限预览。"""
        perm = self.get_permission()
        self.lbl_preview.setText(f"当前权限: {perm if perm else '(无)'}")
    
    def get_permission(self) -> str:
        """获取选中的权限字符串。"""
        return ''.join([
            perm_char for perm_char, checkbox in self.perm_checkboxes.items()
            if checkbox.isChecked()
        ])


# =============================================================================
# 可复用权限选择组件
# =============================================================================

class PermissionWidget(QWidget):
    """
    可复用的权限选择组件。
    
    包含：
    - 预设权限单选框（只读、整理、上传、读写、完全、自定义）
    - 自定义权限按钮（打开详细权限对话框）
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_custom_perm = ""
        self.setup_ui()
        self.set_preset("readonly")
    
    def setup_ui(self):
        from PyQt6.QtWidgets import QSizePolicy
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        self.preset_group = QButtonGroup(self)
        self.preset_radios: Dict[str, QRadioButton] = {}
        preset_order = ["readonly", "organize", "upload", "readwrite", "full", "custom"]
        
        for preset_key in preset_order:
            perm_str, name, desc = PERMISSION_PRESETS[preset_key]
            
            radio = QRadioButton(name)
            if preset_key != "custom":
                radio.setToolTip(f"{desc}\n权限: {perm_str}")
            else:
                radio.setToolTip(desc)
            
            self.preset_radios[preset_key] = radio
            self.preset_group.addButton(radio)
            layout.addWidget(radio)
        
        self.btn_custom = QPushButton("配置...")
        self.btn_custom.setToolTip("点击打开详细权限设置对话框")
        self.btn_custom.setFixedWidth(UISize.CUSTOM_PERM_BUTTON_WIDTH)
        self.btn_custom.clicked.connect(self.on_custom_button_clicked)
        layout.addWidget(self.btn_custom)
        
        layout.addStretch()
        
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    
    def on_custom_button_clicked(self):
        self.open_custom_dialog(auto_select=True)
    
    def open_custom_dialog(self, auto_select: bool = False):
        """打开自定义权限对话框。"""
        from PyQt6.QtWidgets import QDialog
        dialog = CustomPermissionDialog(self, self.current_custom_perm)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.current_custom_perm = dialog.get_permission()
            if auto_select:
                self.preset_radios["custom"].setChecked(True)
            if self.current_custom_perm:
                self.btn_custom.setText(f"({self.current_custom_perm})")
            else:
                self.btn_custom.setText("配置...")
    
    def set_preset(self, preset_key: str):
        """设置预设权限。"""
        if preset_key in self.preset_radios:
            self.preset_radios[preset_key].setChecked(True)
    
    def set_permission(self, perm: str):
        """根据权限字符串设置组件状态（自动匹配预设）。"""
        for preset_key, (preset_perm, _, _) in PERMISSION_PRESETS.items():
            if preset_key == "custom":
                continue
            if set(perm) == set(preset_perm):
                self.set_preset(preset_key)
                return
        
        self.current_custom_perm = perm
        self.set_preset("custom")
        if perm:
            self.btn_custom.setText(f"({perm})")
    
    def get_permission(self) -> str:
        """获取当前选择的权限字符串。"""
        for preset_key, radio in self.preset_radios.items():
            if radio.isChecked():
                if preset_key == "custom":
                    return self.current_custom_perm
                return PERMISSION_PRESETS[preset_key][0]
        return "elr"
