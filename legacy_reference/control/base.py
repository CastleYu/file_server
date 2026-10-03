"""
服务器抽象基类模块

提供通用的服务器对象基类和服务器QThread基类，
支持不同类型服务器的基本共同运行逻辑接口和在Qt应用中的使用。
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List, Callable, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

_logger = logging.getLogger(__name__)


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
        self.host = "0.0.0.0"
        self.port = default_port
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """将上下文转换为可序列化的字典。"""
        pass
    
    @abstractmethod
    def from_dict(self, data: Dict[str, Any]) -> None:
        """从字典加载配置数据。"""
        pass
    
    def save_to_disk(self) -> None:
        """将配置保存到磁盘文件。"""
        data = self.to_dict()
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    
    def load_from_disk(self) -> bool:
        """
        从磁盘文件加载配置。
        
        Returns:
            如果成功加载返回 True，文件不存在返回 False
        """
        if not os.path.exists(self.config_path):
            return False
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.from_dict(data)
        return True
    
    def get_address(self) -> tuple:
        """获取服务器监听地址元组 (host, port)。"""
        return (self.host, self.port)


# =============================================================================
# 服务器工作线程基类
# =============================================================================

class BaseServerThread(QThread):
    """
    服务器工作线程抽象基类。
    
    在后台线程中运行服务器，避免阻塞 GUI。
    提供标准的信号接口用于状态反馈和日志。
    
    Signals:
        status_signal: 发送状态消息字符串
        error_signal: 发送错误消息字符串
        started_signal: 服务器成功启动时发出
        stopped_signal: 服务器停止时发出
    """
    
    status_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    started_signal = pyqtSignal()
    stopped_signal = pyqtSignal()
    
    def __init__(self, context: BaseServerContext, parent=None):
        super().__init__(parent)
        self.context = context
        self._server = None
        self._running = False
    
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
        return self._running and self.isRunning()
    
    def run(self) -> None:
        """线程执行入口。"""
        try:
            self._running = True
            
            self.status_signal.emit("正在初始化服务器...")
            self.setup_server()
            
            address = self.context.get_address()
            self.status_signal.emit(f"服务器正在启动，监听 {address[0]}:{address[1]} ...")
            self.started_signal.emit()
            
            self.serve()
            
        except Exception as e:
            self.error_signal.emit(f"服务器错误: {str(e)}")
        finally:
            self._running = False
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
        if self._running:
            self.stop_server()
        
        if self.isRunning():
            if not self.wait(3000):
                self.terminate()
                self.wait()
        
        self._running = False


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
        if hasattr(self, 'server_thread') and self.server_thread and self.server_thread.is_running:
            return False
        
        self.server_thread = self.create_server_thread()
        self._connect_server_signals(self.server_thread)
        self.server_thread.start()
        return True
    
    def stop_server(self) -> bool:
        """停止服务器。Returns: 停止成功返回 True。"""
        if not hasattr(self, 'server_thread') or not self.server_thread:
            return False
        
        self.server_thread.safe_stop()
        self.server_thread = None
        return True
    
    def toggle_server(self) -> bool:
        """切换服务器状态。Returns: 切换后是否运行。"""
        if hasattr(self, 'server_thread') and self.server_thread and self.server_thread.is_running:
            self.stop_server()
            return False
        else:
            self.start_server()
            return True
    
    def _connect_server_signals(self, thread: BaseServerThread) -> None:
        """连接服务器线程的信号。子类可重写自定义连接。"""
        if hasattr(self, 'on_server_status'):
            thread.status_signal.connect(self.on_server_status)
        if hasattr(self, 'on_server_error'):
            thread.error_signal.connect(self.on_server_error)
        if hasattr(self, 'on_server_started'):
            thread.started_signal.connect(self.on_server_started)
        if hasattr(self, 'on_server_stopped'):
            thread.stopped_signal.connect(self.on_server_stopped)


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
        self.context.load_from_disk()
        
        self.server_thread: Optional[BaseServerThread] = None
        
        self.callbacks: Dict[str, List[Callable]] = {
            'on_status': [],
            'on_error': [],
            'on_started': [],
            'on_stopped': [],
        }
    
    def register_callback(self, event: str, callback: Callable) -> None:
        """注册回调函数。"""
        if event in self.callbacks:
            self.callbacks[event].append(callback)
        else:
            raise ValueError(f"未知的事件类型: {event}")
    
    def unregister_callback(self, event: str, callback: Callable) -> None:
        """取消注册回调函数。"""
        if event in self.callbacks and callback in self.callbacks[event]:
            self.callbacks[event].remove(callback)
    
    def _emit_callback(self, event: str, *args, **kwargs) -> None:
        """触发指定事件的所有回调函数。"""
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                _logger.error(f"回调函数执行错误 ({event}): {e}")
    
    @abstractmethod
    def create_server_thread(self) -> BaseServerThread:
        """创建服务器线程实例。子类必须实现。"""
        pass
    
    # =====================================================================
    # 服务器控制方法
    # =====================================================================
    
    def start_server(self, port: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """启动服务器。"""
        if self.server_thread and self.server_thread.is_running:
            return (False, "服务器已在运行中")
        
        try:
            if port is not None:
                result = self.set_server_port(port)
                if not result[0]:
                    return result
            
            self.server_thread = self.create_server_thread()
            self._connect_thread_signals(self.server_thread)
            self.server_thread.start()
            return (True, None)
            
        except Exception as e:
            error_msg = f"启动服务器失败: {str(e)}"
            self._emit_callback('on_error', error_msg)
            return (False, error_msg)
    
    def stop_server(self) -> bool:
        """停止服务器。"""
        if not self.server_thread or not self.server_thread.is_running:
            return False
        
        try:
            self.server_thread.safe_stop()
            self.server_thread = None
            return True
        except Exception as e:
            self._emit_callback('on_error', f"停止服务器失败: {str(e)}")
            return False
    
    def is_server_running(self) -> bool:
        """检查服务器是否正在运行。"""
        return self.server_thread is not None and self.server_thread.is_running
    
    def get_server_port(self) -> int:
        """获取当前配置的服务器端口。"""
        return self.context.port
    
    def set_server_port(self, port: int) -> Tuple[bool, Optional[str]]:
        """设置服务器端口。"""
        if port < 1 or port > 65535:
            return (False, f"端口号必须在 1-65535 之间: {port}")
        
        if self.is_server_running():
            return (False, "服务器运行中无法修改端口")
        
        self.context.port = port
        self.context.save_to_disk()
        return (True, None)
    
    def get_server_host(self) -> str:
        """获取当前配置的服务器监听地址。"""
        return self.context.host
    
    def set_server_host(self, host: str) -> Tuple[bool, Optional[str]]:
        """设置服务器监听地址。"""
        if self.is_server_running():
            return (False, "服务器运行中无法修改监听地址")
        
        self.context.host = host
        self.context.save_to_disk()
        return (True, None)
    
    # =====================================================================
    # 配置管理方法
    # =====================================================================
    
    def save_config(self) -> bool:
        """保存配置到磁盘。"""
        try:
            self.context.save_to_disk()
            return True
        except Exception as e:
            self._emit_callback('on_error', f"保存配置失败: {str(e)}")
            return False
    
    def reload_config(self) -> bool:
        """从磁盘重新加载配置。"""
        try:
            self.context.load_from_disk()
            return True
        except Exception as e:
            self._emit_callback('on_error', f"加载配置失败: {str(e)}")
            return False
    
    def cleanup(self) -> None:
        """清理资源（程序退出时调用）。"""
        if self.is_server_running():
            self.stop_server()
    
    # =====================================================================
    # 内部方法
    # =====================================================================
    
    def _connect_thread_signals(self, thread: BaseServerThread) -> None:
        """连接服务器线程的信号到回调函数。"""
        thread.status_signal.connect(
            lambda msg: self._emit_callback('on_status', msg)
        )
        thread.error_signal.connect(
            lambda msg: self._emit_callback('on_error', msg)
        )
        thread.started_signal.connect(
            lambda: self._emit_callback('on_started')
        )
        thread.stopped_signal.connect(
            lambda: self._emit_callback('on_stopped')
        )
