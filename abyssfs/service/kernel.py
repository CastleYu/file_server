"""持久运行的文件服务内核。"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from abyssfs.control.file_server import FileServerController
from abyssfs.model.entities import UserProfile
from abyssfs.protocol.base import FileServerProtocol, SshConfig, TlsConfig
from abyssfs.service.constants import (
    ControllerEvent,
    ResultCode,
    ServiceEvent,
    ServiceState,
    ServiceText,
)
from abyssfs.service.contracts import (
    RuntimeMetrics,
    ServiceResult,
    ServiceStatus,
    Subscription,
)
from abyssfs.service.performance import RuntimeProfiler


class ServiceKernel:
    def __init__(
        self,
        config_path: str,
        protocol: FileServerProtocol = FileServerProtocol.FTP,
    ) -> None:
        self._lock = threading.RLock()
        self._controller = FileServerController(config_path, protocol)
        self._profiler = RuntimeProfiler()
        self._state = ServiceState.STOPPED
        self._version = 1
        self._error: Optional[str] = None
        self._subs: Dict[ServiceEvent, List[Callable]] = {
            event: [] for event in ServiceEvent
        }
        self._bind()

    def _bind(self) -> None:
        mapping = {
            ControllerEvent.STATUS: ServiceEvent.STATUS,
            ControllerEvent.ERROR: ServiceEvent.ERROR,
            ControllerEvent.STARTED: ServiceEvent.STARTED,
            ControllerEvent.STOPPED: ServiceEvent.STOPPED,
            ControllerEvent.USER_CHANGED: ServiceEvent.USER_CHANGED,
            ControllerEvent.CLIENT_ACCESS: ServiceEvent.CLIENT_ACCESS,
        }
        for source, target in mapping.items():
            self._controller.register_callback(
                source,
                lambda *args, event=target: self._on(event, *args),
            )

    def _on(self, event: ServiceEvent, *args) -> None:
        if event == ServiceEvent.STARTED:
            self._state = ServiceState.RUNNING
            self._error = None
        elif event == ServiceEvent.STOPPED:
            self._state = ServiceState.STOPPED
        elif event == ServiceEvent.ERROR:
            self._state = ServiceState.FAILED
            self._error = str(args[0]) if args else None
        self._emit(event, *args)

    def _emit(self, event: ServiceEvent, *args) -> None:
        for callback in list(self._subs[event]):
            callback(*args)

    def sub(self, event: ServiceEvent, callback: Callable) -> Subscription:
        with self._lock:
            if callback not in self._subs[event]:
                self._subs[event].append(callback)

        def cancel() -> None:
            with self._lock:
                if callback in self._subs[event]:
                    self._subs[event].remove(callback)

        return Subscription(event, callback, cancel)

    def start(self, port: Optional[int] = None) -> ServiceResult:
        with self._lock:
            if self._controller.is_server_running():
                return ServiceResult.failure(ResultCode.RUNNING, "服务器已在运行中", version=self._version)
            self._state = ServiceState.STARTING
            ok, error = self._controller.start_server(port)
            if not ok:
                self._state = ServiceState.FAILED
                self._error = error
                return ServiceResult.failure(ResultCode.FAILED, error or "启动失败", version=self._version)
            return ServiceResult.success(ServiceText.STARTED, version=self._version)

    def stop(self) -> ServiceResult:
        with self._lock:
            if not self._controller.is_server_running():
                self._state = ServiceState.STOPPED
                return ServiceResult.success(ServiceText.STOPPED, version=self._version)
            self._state = ServiceState.STOPPING
            if not self._controller.stop_server():
                self._state = ServiceState.FAILED
                return ServiceResult.failure(ResultCode.FAILED, "停止失败", version=self._version)
            self._state = ServiceState.STOPPED
            return ServiceResult.success(ServiceText.STOPPED, version=self._version)

    def wait(self, timeout: Optional[float] = None) -> bool:
        thread = self._controller.server_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def stat(self) -> ServiceStatus:
        return ServiceStatus(
            self._state,
            self._controller.get_protocol(),
            self._controller.get_server_host(),
            self._controller.get_server_port(),
            self._version,
            self._error,
        )

    def metrics(self) -> RuntimeMetrics:
        return self._profiler.snap()

    def reload(self) -> ServiceResult:
        with self._lock:
            if self._controller.is_server_running():
                return ServiceResult.failure(
                    ResultCode.RESTART_REQUIRED,
                    "运行中配置重载必须通过目录热更新接口",
                    version=self._version,
                )
            self._state = ServiceState.RELOADING
            if not self._controller.reload_config():
                self._state = ServiceState.FAILED
                return ServiceResult.failure(ResultCode.FAILED, "配置加载失败", version=self._version)
            self._version += 1
            self._state = ServiceState.STOPPED
            return ServiceResult.success(ServiceText.RELOADED, version=self._version)

    def users(self) -> List[UserProfile]:
        return self._controller.get_users()

    def save_user(
        self,
        user: UserProfile,
        original: Optional[str] = None,
        base_version: Optional[int] = None,
    ) -> ServiceResult:
        with self._lock:
            if self._state in {
                ServiceState.STARTING,
                ServiceState.RELOADING,
                ServiceState.STOPPING,
            }:
                return ServiceResult.failure(
                    ResultCode.FAILED,
                    f"服务状态为 {self._state.value}，暂不能保存配置",
                    version=self._version,
                )
            if base_version is not None and base_version != self._version:
                return ServiceResult.failure(
                    ResultCode.CONFLICT,
                    f"配置版本冲突: 当前={self._version}, 请求={base_version}",
                    version=self._version,
                )
            was_running = self._controller.is_server_running()
            if original is None:
                ok, error = self._controller.add_user(user)
            else:
                ok, error = self._controller.update_user(original, user)
            if not ok:
                return ServiceResult.failure(ResultCode.INVALID, error or "保存用户失败", version=self._version)
            self._version += 1
            self._emit(ServiceEvent.CONFIG_APPLIED, self._version)
            if was_running and original is not None:
                message = (
                    "目录配置已通过受控重启应用"
                    if not self._controller.supports_directory_hot_apply()
                    else "目录配置已热更新"
                )
            else:
                message = "用户配置已保存"
            return ServiceResult.success(message, version=self._version)

    def remove_user(self, username: str) -> ServiceResult:
        with self._lock:
            ok, error = self._controller.remove_user(username)
            if not ok:
                return ServiceResult.failure(ResultCode.INVALID, error or "删除用户失败", version=self._version)
            self._version += 1
            self._emit(ServiceEvent.CONFIG_APPLIED, self._version)
            return ServiceResult.success("用户已删除", version=self._version)

    def cleanup(self) -> None:
        self._controller.cleanup()

    def protocol(self) -> FileServerProtocol:
        return self._controller.get_protocol()

    def tls(self) -> TlsConfig:
        return self._controller.context.tls_config

    def ssh(self) -> SshConfig:
        return self._controller.context.ssh_config

    def port(self) -> int:
        return self._controller.get_server_port()

    def set_port(self, port: int) -> tuple:
        return self._controller.set_server_port(port)

    def host(self) -> str:
        return self._controller.get_server_host()

    def max_cons(self) -> int:
        return self._controller.get_max_cons()

    def max_cons_per_ip(self) -> int:
        return self._controller.get_max_cons_per_ip()

    def marker(self) -> bool:
        return self._controller.is_loaded_dir_marker_enabled()

    def update_global(self, **kwargs) -> tuple:
        return self._controller.update_global_settings(**kwargs)

    def default_root(self) -> str:
        return self._controller.get_default_root()
