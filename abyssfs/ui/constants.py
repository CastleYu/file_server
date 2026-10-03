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
from ctypes import wintypes
from typing import Dict, List, Tuple

from abyssfs.paths import LOGS_DIR

# =============================================================================
# 日志目录准备
# =============================================================================

LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_DIR = str(LOGS_DIR)


def _calc_startup_index(date_str: str) -> int:
    """计算今天已有多少个同日志文件（以确定本次启动序号）。"""
    pattern = str(LOGS_DIR / f"server_{date_str}_*.log")
    existing = glob.glob(pattern)
    return len(existing) + 1


# 启动时确定日志文件名
_today = datetime.date.today().strftime("%Y-%m-%d")
_startup_idx = _calc_startup_index(_today)
LOG_FILE_PATH = str(LOGS_DIR / f"server_{_today}_{_startup_idx:03d}.log")


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
    logging.getLogger("wsgidav").setLevel(logging.INFO)
    logging.getLogger("cheroot").setLevel(logging.INFO)

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
# GetConsoleWindow() 在 Windows Terminal 下是 0 尺寸的 PseudoConsoleWindow。
# 对它 SW_HIDE 会被改写成最小化。先聚焦该 HWND，再用 GetForegroundWindow()
# 拿到真正的 CASCADIA 窗口，对那个窗口 SW_HIDE 才会从任务栏消失。

SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
TH32CS_SNAPPROCESS = 0x00000002
CLSCTX_INPROC_SERVER = 1

_HOST_WINDOW_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
}
_TERMINAL_PROCESS_NAMES = {
    "conhost.exe",
    "openconsole.exe",
    "windowsterminal.exe",
}

_console_visible = True
_host_hwnd = 0
_saved_exstyle = None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(d1: int, d2: int, d3: int, d4) -> _GUID:
    value = _GUID()
    value.Data1 = d1
    value.Data2 = d2
    value.Data3 = d3
    value.Data4[:] = d4
    return value


_CLSID_TaskbarList = _guid(0x56FDF344, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
_IID_ITaskbarList = _guid(0x56FDF342, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))


class _ITaskbarListVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.c_void_p),
        ("AddRef", ctypes.c_void_p),
        ("Release", ctypes.c_void_p),
        ("HrInit", ctypes.c_void_p),
        ("AddTab", ctypes.c_void_p),
        ("DeleteTab", ctypes.c_void_p),
        ("ActivateTab", ctypes.c_void_p),
        ("SetActiveAlt", ctypes.c_void_p),
    ]


def _hwnd_int(hwnd) -> int:
    if not hwnd:
        return 0
    if isinstance(hwnd, int):
        return 0 if hwnd in (0, -1) else hwnd
    value = getattr(hwnd, "value", None)
    if value is None:
        try:
            value = int(hwnd)
        except (TypeError, ValueError):
            return 0
    return 0 if not value or value == -1 else int(value)


def _get_console_window() -> int:
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    except (AttributeError, TypeError):
        pass
    return _hwnd_int(kernel32.GetConsoleWindow())


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_host_window(hwnd: int) -> bool:
    return _window_class(hwnd) in _HOST_WINDOW_CLASSES


def _title_looks_like_our_console(hwnd: int) -> bool:
    title = _window_title(hwnd).lower()
    markers = (
        os.path.basename(sys.executable).lower(),
        "python.exe",
        "pythonw.exe",
        "ftp_app",
        "abyssfs",
    )
    return any(marker in title for marker in markers)


def _process_parents() -> Dict[int, Tuple[int, str]]:
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if _hwnd_int(snapshot) in (0, -1):
        return {}
    mapping: Dict[int, Tuple[int, str]] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            mapping[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                (entry.szExeFile or "").lower(),
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return mapping
    finally:
        kernel32.CloseHandle(snapshot)


def _find_titled_host_window_for_pid(pid: int) -> int:
    user32 = ctypes.windll.user32
    found = 0
    fallback = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        nonlocal found, fallback
        handle = _hwnd_int(hwnd)
        if not handle or _window_pid(handle) != pid:
            return True
        if user32.GetWindow(handle, 4):
            return True
        if not _is_host_window(handle):
            return True
        if not (user32.IsWindowVisible(handle) or user32.IsIconic(handle)):
            if not fallback:
                fallback = handle
            return True
        if _title_looks_like_our_console(handle):
            found = handle
            return False
        if not fallback:
            fallback = handle
        return True

    user32.EnumWindows(callback, 0)
    return found or fallback


def _foreground_host_hwnd(console_hwnd: int) -> int:
    """SO 74681745: 聚焦伪控制台后再取前台窗口，才能拿到真正的 Terminal。"""
    user32 = ctypes.windll.user32
    if not console_hwnd:
        return 0
    user32.SetForegroundWindow(console_hwnd)
    fg = _hwnd_int(user32.GetForegroundWindow())
    if fg and _is_host_window(fg):
        return fg
    return 0


def _find_host_terminal_hwnd(console_hwnd: int) -> int:
    parents = _process_parents()
    starts = []
    if console_hwnd:
        starts.append(_window_pid(console_hwnd))
    starts.append(os.getpid())
    seen = set()
    for start in starts:
        current = start
        for _ in range(12):
            if not current or current in seen:
                break
            seen.add(current)
            name = parents.get(current, (0, ""))[1]
            if name in _TERMINAL_PROCESS_NAMES:
                found = _find_titled_host_window_for_pid(current)
                if found:
                    return found
            current = parents.get(current, (0, ""))[0]
    return 0


def _locate_console_hwnd() -> int:
    hwnd = _get_console_window()
    user32 = ctypes.windll.user32
    if hwnd and _window_class(hwnd) == "ConsoleWindowClass":
        if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return hwnd
    fg = _foreground_host_hwnd(hwnd)
    if fg:
        return fg
    return _find_host_terminal_hwnd(hwnd) or hwnd


def _resolve_host_hwnd() -> int:
    """显示后再隐藏时 GUI 已在前台，不能再靠 GetForegroundWindow。记住首次找到的宿主窗口。"""
    global _host_hwnd
    if _host_hwnd and ctypes.windll.user32.IsWindow(_host_hwnd) and _is_host_window(_host_hwnd):
        return _host_hwnd
    hwnd = _locate_console_hwnd()
    if hwnd:
        _host_hwnd = hwnd
    return hwnd


def _taskbar_tab(hwnd: int, present: bool) -> None:
    try:
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        ole32.CoInitialize.restype = ctypes.HRESULT
        ole32.CoCreateInstance.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.HRESULT
        ole32.CoInitialize(None)
        obj = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_TaskbarList),
            None,
            CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_ITaskbarList),
            ctypes.byref(obj),
        )
        if hr != 0 or not obj.value:
            return
        vtbl = ctypes.cast(
            ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p)).contents,
            ctypes.POINTER(_ITaskbarListVtbl),
        ).contents
        ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtbl.HrInit)(obj)
        method = vtbl.AddTab if present else vtbl.DeleteTab
        ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND)(method)(
            obj, hwnd
        )
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl.Release)(obj)
    except Exception:
        pass


def _set_console_hidden(hwnd: int, hidden: bool) -> None:
    global _saved_exstyle
    user32 = ctypes.windll.user32
    if hidden:
        if _saved_exstyle is None:
            _saved_exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            (_saved_exstyle & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW,
        )
        _taskbar_tab(hwnd, False)
        user32.ShowWindow(hwnd, SW_HIDE)
        return
    if _saved_exstyle is not None:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, _saved_exstyle)
        _saved_exstyle = None
    _taskbar_tab(hwnd, True)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)


def hide_console():
    """隐藏控制台窗口（仅 Windows）。"""
    global _console_visible
    if os.name != "nt":
        return
    try:
        hwnd = _resolve_host_hwnd()
        if not hwnd:
            return
        _set_console_hidden(hwnd, True)
        _console_visible = False
    except Exception:
        pass


def show_console():
    """显示控制台窗口（仅 Windows）。"""
    global _console_visible
    if os.name != "nt":
        return
    try:
        hwnd = _resolve_host_hwnd()
        if not hwnd:
            return
        _set_console_hidden(hwnd, False)
        _console_visible = True
    except Exception:
        pass


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
    MAIN_WINDOW_HEIGHT = 540

    # 性能分析
    PERFORMANCE_FOREGROUND_MS = 1000
    PERFORMANCE_BACKGROUND_MS = 10000
    PERFORMANCE_LABEL_MIN_WIDTH = 180

    # 协议/证书对话框
    PROTO_DIALOG_WIDTH  = 560
    PROTO_DIALOG_HEIGHT = 640
    PROTO_LABEL_MAX_WIDTH = 168
    NETWORK_NUMBER_INPUT_WIDTH = 88

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


class NetHost:
    """网络主机常量。"""

    LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
    WILDCARD = frozenset({"", "0.0.0.0", "::", "*"})


class NetCategory:
    """Windows 网络配置类别。"""

    PUBLIC = "Public"
    PRIVATE = "Private"
    DOMAIN = "Domain"


class FirewallProfile:
    """Windows 防火墙 Profile 输出标记。"""

    PUBLIC_HEADER = "Public Profile"
    PRIVATE_HEADER = "Private Profile"
    DOMAIN_HEADER = "Domain Profile"
    HEADER_SUFFIX = ":"
    SEPARATOR_PREFIX = "-"
    COMMAND_OK = "Ok."


class FirewallCommand:
    """Windows 防火墙与网络查询命令。"""

    CURRENT_PROFILE = (
        "netsh",
        "advfirewall",
        "monitor",
        "show",
        "currentprofile",
    )
    CONNECTION_PROFILE_PS = (
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-NetConnectionProfile | ForEach-Object { "
        "[string]::Join([char]9, @($_.InterfaceAlias, $_.NetworkCategory)) "
        "}",
    )
    CONNECTION_DETAIL_PS = (
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-NetConnectionProfile | ForEach-Object { "
        "[string]::Join([char]9, @($_.InterfaceAlias, $_.NetworkCategory, $_.IPv4Connectivity)) "
        "}",
    )
    HOST_ALIAS_PS = (
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-NetIPAddress -IPAddress $args[0] | Select-Object -ExpandProperty InterfaceAlias",
    )
    DEFAULT_ALIAS_PS = (
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        "Sort-Object RouteMetric,InterfaceMetric | "
        "Select-Object -First 1 -ExpandProperty InterfaceAlias",
    )
