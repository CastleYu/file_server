"""
IP 规则管理对话框

提供：
  - IpRuleEditDialog: 编辑单个 IP 规则（模式、类型、启用状态）
  - IpRuleDialog: 管理用户的 IP 规则列表
"""

from __future__ import annotations

import ipaddress
from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QCheckBox, QDialogButtonBox, QMessageBox, QWidget
)
from PyQt6.QtCore import Qt

from model.entities import IpRule, IpRuleType
from ui.constants import UISize


class IpRuleEditDialog(QDialog):
    """编辑单个 IP 规则的对话框。"""
    
    def __init__(self, parent=None, rule: Optional[IpRule] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑 IP 规则" if rule else "添加 IP 规则")
        self.setup_ui()
        if rule:
            self.load_data(rule)
            
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # 模式输入
        layout.addWidget(QLabel("匹配模式:"))
        self.input_pattern = QLineEdit()
        self.input_pattern.setPlaceholderText("例如: 192.168.1.* 或 192.168.1.0/24")
        layout.addWidget(self.input_pattern)
        
        # 类型选择
        layout.addWidget(QLabel("匹配类型:"))
        self.combo_type = QComboBox()
        self.combo_type.addItem("通配符 (Wildcard)", IpRuleType.WILDCARD)
        self.combo_type.addItem("子网掩码 (CIDR)", IpRuleType.CIDR)
        layout.addWidget(self.combo_type)
        
        # 启用状态
        self.check_enabled = QCheckBox("启用此规则")
        self.check_enabled.setChecked(True)
        layout.addWidget(self.check_enabled)
        
        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def load_data(self, rule: IpRule):
        self.input_pattern.setText(rule.pattern)
        index = self.combo_type.findData(rule.rule_type)
        if index >= 0:
            self.combo_type.setCurrentIndex(index)
        self.check_enabled.setChecked(rule.enabled)
        
    def validate_and_accept(self):
        pattern = self.input_pattern.text().strip()
        if not pattern:
            QMessageBox.warning(self, "错误", "模式不能为空")
            return
            
        rule_type = self.combo_type.currentData()
        if rule_type == IpRuleType.CIDR:
            try:
                ipaddress.ip_network(pattern, strict=False)
            except Exception as e:
                QMessageBox.warning(self, "无效的 CIDR", f"IP 地址或掩码格式错误:\n{e}")
                return
        
        self.accept()
        
    def get_data(self) -> IpRule:
        return IpRule(
            pattern=self.input_pattern.text().strip(),
            rule_type=self.combo_type.currentData(),
            enabled=self.check_enabled.isChecked()
        )


class IpRuleDialog(QDialog):
    """管理用户的 IP 规则列表。"""
    
    def __init__(self, parent=None, rules: List[IpRule] = None):
        super().__init__(parent)
        self.setWindowTitle("管理允许访问的 IP 规则")
        self.resize(UISize.IP_RULE_DIALOG_WIDTH, UISize.IP_RULE_DIALOG_HEIGHT)
        self.rules = [IpRule(r.pattern, r.rule_type, r.enabled) for r in (rules or [])]
        self.setup_ui()
        self.refresh_table()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        desc = QLabel("说明: 只有启用的规则会被检查。如果没有配置任何启用的规则，则默认允许所有 IP。")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["状态", "匹配模式", "类型", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("添加规则")
        btn_add.clicked.connect(self.add_rule)
        btn_layout.addWidget(btn_add)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def refresh_table(self):
        self.table.setRowCount(0)
        for i, rule in enumerate(self.rules):
            self.table.insertRow(i)
            
            # 状态 (Checkbox)
            chk = QCheckBox()
            chk.setChecked(rule.enabled)
            # 居中显示
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.addWidget(chk)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.setContentsMargins(0,0,0,0)
            chk.stateChanged.connect(lambda state, idx=i: self.toggle_rule(idx, state))
            self.table.setCellWidget(i, 0, cell_widget)
            
            # 模式
            self.table.setItem(i, 1, QTableWidgetItem(rule.pattern))
            
            # 类型
            type_str = "通配符" if rule.rule_type == IpRuleType.WILDCARD else "CIDR"
            self.table.setItem(i, 2, QTableWidgetItem(type_str))
            
            # 操作
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            
            btn_edit = QPushButton("编辑")
            btn_edit.clicked.connect(lambda _, idx=i: self.edit_rule(idx))
            btn_del = QPushButton("删除")
            btn_del.clicked.connect(lambda _, idx=i: self.delete_rule(idx))
            
            action_layout.addWidget(btn_edit)
            action_layout.addWidget(btn_del)
            self.table.setCellWidget(i, 3, action_widget)

    def toggle_rule(self, index: int, state: int):
        self.rules[index].enabled = (state == Qt.CheckState.Checked.value)

    def add_rule(self):
        dlg = IpRuleEditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.rules.append(dlg.get_data())
            self.refresh_table()
            
    def edit_rule(self, index: int):
        dlg = IpRuleEditDialog(self, self.rules[index])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.rules[index] = dlg.get_data()
            self.refresh_table()
            
    def delete_rule(self, index: int):
        if QMessageBox.question(self, "确认", "确定删除此规则吗?") == QMessageBox.StandardButton.Yes:
            self.rules.pop(index)
            self.refresh_table()
            
    def get_rules(self) -> List[IpRule]:
        return self.rules
