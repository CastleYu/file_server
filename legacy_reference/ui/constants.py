"""
UI 层常量与工具

提供：
  - LimitedMemoryHandler: 内存日志处理器（限制行数）
  - 日志系统初始化 + vfs_logger / _memory_handler
  - hide_console() / show_console() / is_console_visible(): 控制台可见性（Windows）
  - UISize: 界面尺寸配置类
"""

import sys
import os
import logging
import ctypes
from typing import List

# =============================================================================
# 内存日志处理器
# =============================================================================

class LimitedMemoryHandler(logging.Handler):
    """
    内存日志处理器，限制最大行数。
    
    用于 GUI 显示日志，避免内存占用过大。
    """
    MAX_LINES = 10000
    
    def __init__(self):
        super().__init__()
        self.log_records: List[str] = []
    
    def emit(self, record):
        msg = self.format(record)
        self.log_records.append(msg)
        if len(self.log_records) > self.MAX_LINES:
            self.log_records = self.log_records[-self.MAX_LINES:]
    
    def get_logs(self) -> List[str]:
        return self.log_records.copy()
    
    def clear(self):
        self.log_records.clear()


# =============================================================================
# 日志系统初始化
# =============================================================================

_log_level = os.environ.get('FTP_LOG_LEVEL', 'INFO').upper()
_log_format = '[%(asctime)s] [%(levelname)s] %(message)s'
_date_format = '%Y-%m-%d %H:%M:%S'

logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format=_log_format,
    datefmt=_date_format
)

vfs_logger = logging.getLogger('VirtualFS')

_memory_handler = LimitedMemoryHandler()
_memory_handler.setFormatter(logging.Formatter(_log_format, _date_format))
vfs_logger.addHandler(_memory_handler)


# =============================================================================
# 控制台显示/隐藏功能 (Windows)
# =============================================================================

_console_visible = True


def hide_console():
    """隐藏控制台窗口（仅 Windows）。"""
    global _console_visible
    if sys.platform == 'win32':
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                _console_visible = False
        except Exception as e:
            vfs_logger.warning(f"隐藏控制台失败: {e}")


def show_console():
    """显示控制台窗口（仅 Windows）。"""
    global _console_visible
    if sys.platform == 'win32':
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                _console_visible = True
        except Exception as e:
            vfs_logger.warning(f"显示控制台失败: {e}")


def is_console_visible() -> bool:
    """检查控制台是否可见。"""
    return _console_visible


# =============================================================================
# 界面尺寸常量
# =============================================================================

class UISize:
    """界面尺寸配置类。统一管理所有窗口、控件的尺寸常量。"""
    
    # 主窗口
    MAIN_WINDOW_WIDTH = 520
    MAIN_WINDOW_HEIGHT = 350
    
    # 自定义权限对话框
    PERM_DIALOG_WIDTH = 385
    PERM_DIALOG_HEIGHT = 300
    
    # 虚拟目录对话框
    VDIR_DIALOG_WIDTH = 415
    VDIR_DIALOG_HEIGHT = 260
    
    # 用户编辑对话框
    USER_DIALOG_WIDTH = 415
    USER_DIALOG_HEIGHT = 550
    
    # 端口输入框
    PORT_INPUT_WIDTH = 80
    
    # 状态标签
    STATUS_LABEL_MIN_WIDTH = 100
    STATUS_LABEL_MAX_WIDTH = 300
    
    # 自定义权限按钮
    CUSTOM_PERM_BUTTON_WIDTH = 50
    
    # 用户表格操作栏宽度
    USER_TABLE_ACTION_COLUMN_WIDTH = 130
    
    # 虚拟目录表格最小高度
    VDIR_TABLE_MIN_HEIGHT = 150
