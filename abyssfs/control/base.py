"""
服务器抽象基类模块

提供通用的服务器对象基类和服务器工作线程基类，
支持不同类型服务器的基本共同运行逻辑接口。
"""

import json
import logging
import threading
import traceback
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List, Callable, Tuple

from abyssfs.service.store import ConfigStore

_logger = logging.getLogger(__name__)


class _CallbackSignal:
    """A tiny callback signal used by the control layer without UI dependencies."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._callbacks: List[Callable] = []

    def connect(self, callback: Callable) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def emit(self, *args, **kwargs) -> None:
        for callback in list(self._callbacks):
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                _logger.error(
                    f"[CallbackSignal:{self._name}] 回调执行异常 "
                    f"(fn={getattr(callback, '__name__', repr(callback))}): "
                    f"{exc}\n{traceback.format_exc()}"
                )


# =============================================================================
# 服务器上下文基类
# =============================================================================

class BaseServerContext(ABC):
    """
    服务器上下文抽象基类。

    管理服务器的配置数据和持久化逻辑。
    子类需要实现具体的配置内容。

    Attributes:
        config_path: 配置文件路径
        host: 监听地址
        port: 监听端口
    """

    def __init__(self, config_path: str, default_port: int = 8080):
        """
        初始化服务器上下文。

        Args:
            config_path: 配置文件保存路径
            default_port: 默认监听端口
        """
        self.config_path = config_path
        self._config_store = ConfigStore(config_path)
        self.host = "0.0.0.0"
        self.port = default_port
        _logger.debug(
            f"[{self.__class__.__name__}] 初始化: config_path={config_path!r}, "
            f"default_port={default_port}"
        )

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """将上下文转换为可序列化的字典。"""
        pass

    @abstractmethod
    def from_dict(self, data: Dict[str, Any]) -> None:
        """从字典加载配置数据。"""
        pass

    def save_to_disk(self) -> None:
        """将配置原子保存到磁盘文件。"""
        _logger.info(f"[{self.__class__.__name__}] 保存配置到: {self.config_path!r}")
        try:
            self._config_store.save(self.to_dict())
            _logger.info(f"[{self.__class__.__name__}] 配置保存成功")
        except OSError as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 保存配置文件失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 保存配置时遇到未知错误: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def load_from_disk(self) -> bool:
        """
        从磁盘文件加载配置。

        Returns:
            如果成功加载返回 True，文件不存在返回 False
        """
        try:
            data = self._config_store.load()
        except json.JSONDecodeError as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 配置文件 JSON 解析失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False

        if data is None:
            _logger.info(
                f"[{self.__class__.__name__}] 配置文件不存在，使用默认值: "
                f"{self.config_path!r}"
            )
            return False

        _logger.info(f"[{self.__class__.__name__}] 加载配置文件: {self.config_path!r}")
        try:
            self.from_dict(data)
            _logger.info(
                f"[{self.__class__.__name__}] 配置加载成功: "
                f"host={self.host!r}, port={self.port}"
            )
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 加载配置时遇到未知错误: "
                f"{exc}\n{traceback.format_exc()}"
            )
            return False

    def get_address(self) -> tuple:
        """获取服务器监听地址元组 (host, port)。"""
        return (self.host, self.port)


# =============================================================================
# 服务器工作线程基类
# =============================================================================

class BaseServerThread(threading.Thread, ABC):
    """
    服务器工作线程抽象基类。

    在后台线程中运行服务器，避免阻塞调用方。
    提供轻量回调信号用于状态反馈和日志；UI 层如需主线程投递，应自行适配。

    Signals:
        status_signal: 发送状态消息字符串
        error_signal: 发送错误消息字符串
        started_signal: 服务器成功启动时发出
        stopped_signal: 服务器停止时发出
        client_access_signal: 客户端访问时发出远端 IP
    """

    def __init__(self, context: BaseServerContext, parent=None):
        super().__init__(daemon=True)
        self.context  = context
        self._server  = None
        self._running = False
        self.status_signal  = _CallbackSignal("status")
        self.error_signal   = _CallbackSignal("error")
        self.started_signal = _CallbackSignal("started")
        self.stopped_signal = _CallbackSignal("stopped")
        self.client_access_signal = _CallbackSignal("client_access")
        _logger.debug(
            f"[{self.__class__.__name__}] 线程实例创建: "
            f"host={context.host!r}, port={context.port}"
        )

    @property
    def server(self) -> Any:
        """获取底层服务器对象。"""
        return self._server

    @server.setter
    def server(self, value: Any) -> None:
        self._server = value

    @property
    def is_running(self) -> bool:
        """检查服务器是否正在运行。"""
        return self._running and self.is_alive()

    def isRunning(self) -> bool:
        """兼容旧调用风格。"""
        return self.is_alive()

    def wait(self, timeout_ms: Optional[int] = None) -> bool:
        """兼容旧调用风格；返回线程是否已经退出。"""
        timeout = None if timeout_ms is None else timeout_ms / 1000
        self.join(timeout)
        return not self.is_alive()

    def run(self) -> None:
        """线程执行入口。"""
        _logger.info(
            f"[{self.__class__.__name__}] 工作线程启动: "
            f"host={self.context.host!r}, port={self.context.port}"
        )
        try:
            self._running = True

            self.status_signal.emit("正在初始化服务器...")
            _logger.info(f"[{self.__class__.__name__}] 开始 setup_server()")
            self.setup_server()

            address = self.context.get_address()
            msg = f"服务器正在启动，监听 {address[0]}:{address[1]} ..."
            self.status_signal.emit(msg)
            _logger.info(f"[{self.__class__.__name__}] {msg}")
            self.started_signal.emit()

            _logger.info(f"[{self.__class__.__name__}] 进入 serve() 阻塞循环")
            self.serve()

        except Exception as exc:
            detail = traceback.format_exc()
            error_msg = f"服务器错误: {exc}"
            _logger.error(
                f"[{self.__class__.__name__}] 线程执行异常: "
                f"{exc}\n{detail}"
            )
            self.error_signal.emit(error_msg)
        finally:
            self._running = False
            _logger.info(f"[{self.__class__.__name__}] 工作线程退出, 发送 stopped_signal")
            self.stopped_signal.emit()

    @abstractmethod
    def setup_server(self) -> None:
        """设置和配置服务器。子类必须实现此方法。"""
        pass

    @abstractmethod
    def serve(self) -> None:
        """启动服务器的主循环。子类必须实现此方法（阻塞直到停止）。"""
        pass

    @abstractmethod
    def stop_server(self) -> None:
        """停止服务器。子类必须实现此方法。"""
        pass

    def safe_stop(self) -> None:
        """安全地停止服务器和线程。"""
        _logger.info(f"[{self.__class__.__name__}] safe_stop() 调用")
        if self._running:
            try:
                self.stop_server()
                _logger.info(f"[{self.__class__.__name__}] stop_server() 完成")
            except Exception as exc:
                _logger.error(
                    f"[{self.__class__.__name__}] stop_server() 异常: "
                    f"{exc}\n{traceback.format_exc()}"
                )

        if self.is_alive():
            _logger.info(f"[{self.__class__.__name__}] 等待线程退出 (3s timeout)")
            if not self.wait(3000):
                _logger.warning(
                    f"[{self.__class__.__name__}] 线程未在 3s 内退出，后台线程将继续自行退出"
                )
            else:
                _logger.info(f"[{self.__class__.__name__}] 线程已正常退出")

        self._running = False
        _logger.info(f"[{self.__class__.__name__}] safe_stop() 结束")


# =============================================================================
# 服务器管理器混入类
# =============================================================================

class ServerManagerMixin:
    """
    服务器管理器混入类。

    提供服务器启动/停止的通用逻辑，可以混入到 Qt 窗口类中使用。
    使用此混入的类需要有以下属性:
        - context: BaseServerContext 实例
        - server_thread: BaseServerThread 实例或 None
    """

    def create_server_thread(self) -> BaseServerThread:
        """创建服务器线程实例。子类必须实现此方法。"""
        raise NotImplementedError("子类必须实现 create_server_thread 方法")

    def start_server(self) -> bool:
        """启动服务器。Returns: 启动成功返回 True。"""
        _logger.info(f"[{self.__class__.__name__}] start_server() 调用")
        if hasattr(self, "server_thread") and self.server_thread and self.server_thread.is_running:
            _logger.warning(f"[{self.__class__.__name__}] 服务器已在运行，忽略启动请求")
            return False

        try:
            self.server_thread = self.create_server_thread()
            self._connect_server_signals(self.server_thread)
            self.server_thread.start()
            _logger.info(f"[{self.__class__.__name__}] 工作线程已 start()")
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] start_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def stop_server(self) -> bool:
        """停止服务器。Returns: 停止成功返回 True。"""
        _logger.info(f"[{self.__class__.__name__}] stop_server() 调用")
        if not hasattr(self, "server_thread") or not self.server_thread:
            _logger.info(f"[{self.__class__.__name__}] 没有正在运行的服务器线程")
            return False

        try:
            self.server_thread.safe_stop()
            self.server_thread = None
            _logger.info(f"[{self.__class__.__name__}] stop_server() 完成")
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] stop_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            raise

    def toggle_server(self) -> bool:
        """切换服务器状态。Returns: 切换后是否运行。"""
        if hasattr(self, "server_thread") and self.server_thread and self.server_thread.is_running:
            self.stop_server()
            return False
        else:
            self.start_server()
            return True

    def _connect_server_signals(self, thread: BaseServerThread) -> None:
        """连接服务器线程的信号。子类可重写自定义连接。"""
        if hasattr(self, "on_server_status"):
            thread.status_signal.connect(self.on_server_status)
        if hasattr(self, "on_server_error"):
            thread.error_signal.connect(self.on_server_error)
        if hasattr(self, "on_server_started"):
            thread.started_signal.connect(self.on_server_started)
        if hasattr(self, "on_server_stopped"):
            thread.stopped_signal.connect(self.on_server_stopped)
        _logger.debug(f"[{self.__class__.__name__}] 服务器线程信号已连接")


# =============================================================================
# 服务器控制器基类
# =============================================================================

class BaseServerController:
    """
    服务器控制器抽象基类。

    封装服务器配置和管理的业务逻辑，提供与UI框架无关的接口。
    通过回调函数机制通知UI更新，便于移植到其他UI框架。
    """

    def __init__(self, context: BaseServerContext):
        self.context = context
        _logger.info(
            f"[{self.__class__.__name__}] 控制器初始化, "
            f"config_path={context.config_path!r}"
        )
        try:
            loaded = self.context.load_from_disk()
            _logger.info(
                f"[{self.__class__.__name__}] 配置加载: {'成功' if loaded else '使用默认值'}"
            )
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] load_from_disk() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )

        self.server_thread: Optional[BaseServerThread] = None

        self.callbacks: Dict[str, List[Callable]] = {
            "on_status":  [],
            "on_error":   [],
            "on_started": [],
            "on_stopped": [],
            "on_client_access": [],
        }

    def register_callback(self, event: str, callback: Callable) -> None:
        """注册回调函数。"""
        if event in self.callbacks:
            self.callbacks[event].append(callback)
            _logger.debug(
                f"[{self.__class__.__name__}] 注册回调: event={event!r}, "
                f"fn={getattr(callback, '__name__', repr(callback))!r}"
            )
        else:
            _logger.error(
                f"[{self.__class__.__name__}] 注册回调失败: 未知事件类型 {event!r}"
            )
            raise ValueError(f"未知的事件类型: {event}")

    def unregister_callback(self, event: str, callback: Callable) -> None:
        """取消注册回调函数。"""
        if event in self.callbacks and callback in self.callbacks[event]:
            self.callbacks[event].remove(callback)
            _logger.debug(
                f"[{self.__class__.__name__}] 取消回调: event={event!r}, "
                f"fn={getattr(callback, '__name__', repr(callback))!r}"
            )

    def _emit_callback(self, event: str, *args, **kwargs) -> None:
        """触发指定事件的所有回调函数。"""
        callbacks = self.callbacks.get(event, [])
        _logger.debug(
            f"[{self.__class__.__name__}] _emit_callback: event={event!r}, "
            f"count={len(callbacks)}"
        )
        for callback in callbacks:
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                _logger.error(
                    f"[{self.__class__.__name__}] 回调执行异常 "
                    f"(event={event!r}, fn={getattr(callback, '__name__', repr(callback))!r}): "
                    f"{exc}\n{traceback.format_exc()}"
                )

    @abstractmethod
    def create_server_thread(self) -> BaseServerThread:
        """创建服务器线程实例。子类必须实现。"""
        pass

    # =====================================================================
    # 服务器控制方法
    # =====================================================================

    def start_server(self, port: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """启动服务器。"""
        _logger.info(
            f"[{self.__class__.__name__}] start_server() 调用: port={port}"
        )
        if self.server_thread and self.server_thread.is_running:
            _logger.warning(f"[{self.__class__.__name__}] 服务器已在运行中")
            return (False, "服务器已在运行中")

        try:
            if port is not None:
                result = self.set_server_port(port)
                if not result[0]:
                    _logger.warning(
                        f"[{self.__class__.__name__}] 设置端口失败: {result[1]}"
                    )
                    return result

            self.server_thread = self.create_server_thread()
            self._connect_thread_signals(self.server_thread)
            self.server_thread.start()
            _logger.info(
                f"[{self.__class__.__name__}] 工作线程已启动: "
                f"host={self.context.host!r}, port={self.context.port}"
            )
            return (True, None)

        except Exception as exc:
            error_msg = f"启动服务器失败: {exc}"
            _logger.error(
                f"[{self.__class__.__name__}] start_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self._emit_callback("on_error", error_msg)
            return (False, error_msg)

    def stop_server(self) -> bool:
        """停止服务器。"""
        _logger.info(f"[{self.__class__.__name__}] stop_server() 调用")
        if not self.server_thread or not self.server_thread.is_running:
            _logger.info(f"[{self.__class__.__name__}] 没有正在运行的服务器线程")
            return False

        try:
            self.server_thread.safe_stop()
            self.server_thread = None
            _logger.info(f"[{self.__class__.__name__}] stop_server() 完成")
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] stop_server() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self._emit_callback("on_error", f"停止服务器失败: {exc}")
            return False

    def is_server_running(self) -> bool:
        """检查服务器是否正在运行。"""
        result = self.server_thread is not None and self.server_thread.is_running
        return result

    def get_server_port(self) -> int:
        """获取当前配置的服务器端口。"""
        return self.context.port

    def set_server_port(self, port: int) -> Tuple[bool, Optional[str]]:
        """设置服务器端口。"""
        _logger.info(f"[{self.__class__.__name__}] set_server_port({port})")
        if port < 1 or port > 65535:
            msg = f"端口号必须在 1-65535 之间: {port}"
            _logger.warning(f"[{self.__class__.__name__}] {msg}")
            return (False, msg)

        if self.is_server_running():
            msg = "服务器运行中无法修改端口"
            _logger.warning(f"[{self.__class__.__name__}] {msg}")
            return (False, msg)

        old_port = self.context.port
        self.context.port = port
        try:
            self.context.save_to_disk()
            _logger.info(
                f"[{self.__class__.__name__}] 端口更新: {old_port} → {port}"
            )
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 端口更新后保存配置失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
        return (True, None)

    def get_server_host(self) -> str:
        """获取当前配置的服务器监听地址。"""
        return self.context.host

    def set_server_host(self, host: str) -> Tuple[bool, Optional[str]]:
        """设置服务器监听地址。"""
        _logger.info(f"[{self.__class__.__name__}] set_server_host({host!r})")
        if self.is_server_running():
            msg = "服务器运行中无法修改监听地址"
            _logger.warning(f"[{self.__class__.__name__}] {msg}")
            return (False, msg)

        old_host = self.context.host
        self.context.host = host
        try:
            self.context.save_to_disk()
            _logger.info(
                f"[{self.__class__.__name__}] 监听地址更新: {old_host!r} → {host!r}"
            )
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] 监听地址更新后保存配置失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
        return (True, None)

    # =====================================================================
    # 配置管理方法
    # =====================================================================

    def save_config(self) -> bool:
        """保存配置到磁盘。"""
        _logger.info(f"[{self.__class__.__name__}] save_config() 调用")
        try:
            self.context.save_to_disk()
            _logger.info(f"[{self.__class__.__name__}] save_config() 成功")
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] save_config() 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self._emit_callback("on_error", f"保存配置失败: {exc}")
            return False

    def reload_config(self) -> bool:
        """从磁盘重新加载配置。"""
        _logger.info(f"[{self.__class__.__name__}] reload_config() 调用")
        try:
            loaded = self.context.load_from_disk()
            _logger.info(
                f"[{self.__class__.__name__}] reload_config() 完成: "
                f"{'已加载' if loaded else '文件不存在'}"
            )
            return True
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] reload_config() 失败: "
                f"{exc}\n{traceback.format_exc()}"
            )
            self._emit_callback("on_error", f"加载配置失败: {exc}")
            return False

    def cleanup(self) -> None:
        """清理资源（程序退出时调用）。"""
        _logger.info(f"[{self.__class__.__name__}] cleanup() 调用")
        try:
            if self.is_server_running():
                self.stop_server()
            _logger.info(f"[{self.__class__.__name__}] cleanup() 完成")
        except Exception as exc:
            _logger.error(
                f"[{self.__class__.__name__}] cleanup() 异常: "
                f"{exc}\n{traceback.format_exc()}"
            )

    # =====================================================================
    # 内部方法
    # =====================================================================

    def _connect_thread_signals(self, thread: BaseServerThread) -> None:
        """连接服务器线程的信号到回调函数。"""
        _logger.debug(f"[{self.__class__.__name__}] 连接线程信号")
        thread.status_signal.connect(
            lambda msg: self._emit_callback("on_status", msg)
        )
        thread.error_signal.connect(
            lambda msg: self._emit_callback("on_error", msg)
        )
        thread.started_signal.connect(
            lambda: self._emit_callback("on_started")
        )
        thread.stopped_signal.connect(
            lambda: self._emit_callback("on_stopped")
        )
        thread.client_access_signal.connect(
            lambda remote_ip: self._emit_callback("on_client_access", remote_ip)
        )
