"""服务层枚举和常量。"""

from enum import Enum


class PerformanceValue:
    BYTES_PER_MIB = 1024 * 1024
    PERCENT = 100.0
    MIN_INTERVAL = 1e-9


class PerformancePath:
    PROC_STATM = "/proc/self/statm"


class ServiceState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RELOADING = "reloading"
    STOPPING = "stopping"
    FAILED = "failed"


class ServiceEvent(Enum):
    STATUS = "status"
    ERROR = "error"
    STARTED = "started"
    STOPPED = "stopped"
    USER_CHANGED = "user_changed"
    CLIENT_ACCESS = "client_access"
    CONFIG_APPLIED = "config_applied"


class HostMode(Enum):
    EMBEDDED = "embedded"
    DAEMON = "daemon"


class ResultCode:
    OK = "ok"
    INVALID = "invalid"
    CONFLICT = "conflict"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    RESTART_REQUIRED = "restart_required"


class ControllerEvent:
    STATUS = "on_status"
    ERROR = "on_error"
    STARTED = "on_started"
    STOPPED = "on_stopped"
    USER_CHANGED = "on_user_changed"
    CLIENT_ACCESS = "on_client_access"


class ServiceKey:
    MODULE_PREFIX = "_abyssfs_service_"
    DASH = "-"
    UNDERLINE = "_"
    NAME = "abyssfs_file_service"
    ENTRY = "FileService"


class ServiceText:
    MODULE_SPEC = "无法创建 Service 模块描述"
    LOADER = "Service 模块缺少加载器"
    STARTED = "服务启动请求已提交"
    STOPPED = "服务已停止"
    RELOADED = "配置已重新加载"
