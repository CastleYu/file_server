"""
UI 层常量与工具

提供：
  - LimitedMemoryHandler: 内存日志处理器（限制行数）
  - PersistentFileHandler: 持久化日志文件处理器（按天+启动次数）
  - setup_logging(): 初始化日志系统（内存 + 文件 + 控制台三路输出）
  - hide_console() / show_console() / is_console_visible(): 控制台可见性（Windows）
  - UISize: 界面尺寸配置类
"""

import sys
import os
import logging
import ctypes
import datetime
import glob
from typing import List

# =============================================================================
# 日志目录准备
# =============================================================================

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)
os.makedirs(_LOG_DIR, exist_ok=True)


def _calc_startup_index(date_str: str) -> int:
    """计算今天已有多少个同日志文件（以确定本次启动序号）。"""
    pattern = os.path.join(_LOG_DIR, f"server_{date_str}_*.log")
    existing = glob.glob(pattern)
    return len(existing) + 1


# 启动时确定日志文件名
_today = datetime.date.today().strftime("%Y-%m-%d")
_startup_idx = _calc_startup_index(_today)
LOG_FILE_PATH = os.path.join(_LOG_DIR, f"server_{_today}_{_startup_idx:03d}.log")


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
        try:
            msg = self.format(record)
            self.log_records.append(msg)
            if len(self.log_records) > self.MAX_LINES:
                self.log_records = self.log_records[-self.MAX_LINES:]
        except Exception:
            self.handleError(record)

    def get_logs(self) -> List[str]:
        return self.log_records.copy()

    def clear(self):
        self.log_records.clear()


# =============================================================================
# 持久化日志文件处理器
# =============================================================================

class PersistentFileHandler(logging.FileHandler):
    """
    按天 + 启动次数划分的持久化日志文件处理器。
    日志位于 logs/server_YYYY-MM-DD_NNN.log。
    """

    def __init__(self, filepath: str):
        super().__init__(filepath, encoding="utf-8", delay=False)

    def emit(self, record):
        try:
            super().emit(record)
        except Exception:
            self.handleError(record)


# =============================================================================
# 日志系统初始化
# =============================================================================

_LOG_FORMAT   = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
_DATE_FORMAT  = "%Y-%m-%d %H:%M:%S"
_formatter    = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)


_logging_initialized = False

def setup_logging() -> None:
    """
    初始化全局日志系统。
    包含：
      1. 控制台输出 (StreamHandler)
      2. 内存缓冲区 (_memory_handler)，供 GUI 显示
      3. 持久化文件输出 (PersistentFileHandler)
    """
    global _logging_initialized
    if _logging_initialized:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清理已有的 handler
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    # 1. 控制台 Handler
    import sys
    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(_formatter)
    console_h.setLevel(logging.INFO)  # 控制台仅输出 INFO 以上
    root_logger.addHandler(console_h)

    # 2. 持久化文件 Handler
    try:
        file_h = PersistentFileHandler(LOG_FILE_PATH)
        file_h.setFormatter(_formatter)
        file_h.setLevel(logging.DEBUG)  # 文件记录完整 DEBUG 信息
        root_logger.addHandler(file_h)
    except Exception as e:
        # 此时控制台已就绪，可以直接使用
        root_logger.warning(f"无法创建日志文件 {LOG_FILE_PATH}: {e}")

    # 3. 内存 Handler
    root_logger.addHandler(_memory_handler)

    # 4. 优化第三方库日志（噪音过滤）
    # 默认只看 INFO 以上信息，防止 pyftpdlib/paramiko 的大量 DEBUG 刷屏
    logging.getLogger("pyftpdlib").setLevel(logging.INFO)
    logging.getLogger("paramiko").setLevel(logging.INFO)
    logging.getLogger("asyncore").setLevel(logging.INFO)

    _logging_initialized = True
    root_logger.info(
        f"日志系统已初始化 | 文件: {LOG_FILE_PATH} "
        f"| 今日第 {_startup_idx} 次启动"
    )


# 内存 handler（setup_logging 前就可安全引用）
_memory_handler = LimitedMemoryHandler()
_memory_handler.setFormatter(_formatter)

# 先调用一次（main_window 导入时自动生效）
setup_logging()

# 便捷访问：与旧代码保持兼容
vfs_logger = logging.getLogger("server")


# =============================================================================
# 控制台显示/隐藏功能 (Windows)
# =============================================================================

_console_visible = True


def hide_console():
    """隐藏控制台窗口（仅 Windows）。"""
    global _console_visible
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                _console_visible = False
                vfs_logger.debug("控制台窗口已隐藏")
        except Exception as e:
            vfs_logger.warning(f"隐藏控制台失败: {e}")


def show_console():
    """显示控制台窗口（仅 Windows）。"""
    global _console_visible
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                _console_visible = True
                vfs_logger.debug("控制台窗口已显示")
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
    MAIN_WINDOW_WIDTH  = 450
    MAIN_WINDOW_HEIGHT = 380

    # 协议/证书对话框
    PROTO_DIALOG_WIDTH  = 560
    PROTO_DIALOG_HEIGHT = 580

    # 自定义权限对话框
    PERM_DIALOG_WIDTH  = 385
    PERM_DIALOG_HEIGHT = 300

    # 虚拟目录对话框
    VDIR_DIALOG_WIDTH  = 415
    VDIR_DIALOG_HEIGHT = 260

    # 用户编辑对话框
    USER_DIALOG_WIDTH  = 415
    USER_DIALOG_HEIGHT = 550

    # 端口输入框
    PORT_INPUT_WIDTH = 80

    # 状态标签
    STATUS_LABEL_MIN_WIDTH = 100
    STATUS_LABEL_MAX_WIDTH = 300

    # 自定义权限按钮
    CUSTOM_PERM_BUTTON_WIDTH = 50

    # 用户表格操作栏宽度
    USER_TABLE_ACTION_COLUMN_WIDTH = 100

    # 虚拟目录表格最小高度
    VDIR_TABLE_MIN_HEIGHT = 150

    # IP 规则对话框
    IP_RULE_DIALOG_WIDTH  = 500
    IP_RULE_DIALOG_HEIGHT = 400
