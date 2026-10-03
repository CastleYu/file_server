"""供 UI、CLI 和其他应用调用的稳定 Service 门面。"""

from __future__ import annotations

from typing import Callable, Optional

from abyssfs.model.entities import UserProfile
from abyssfs.protocol.base import FileServerProtocol, SshConfig, TlsConfig
from abyssfs.service.constants import ServiceEvent, ServiceState
from abyssfs.service.kernel import ServiceKernel


class FileService:
    def __init__(self, kernel: ServiceKernel) -> None:
        self._kernel = kernel

    def start(self, port: Optional[int] = None):
        return self._kernel.start(port)

    def stop(self):
        return self._kernel.stop()

    def stat(self):
        return self._kernel.stat()

    def metrics(self):
        return self._kernel.metrics()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._kernel.wait(timeout)

    def users(self):
        return self._kernel.users()

    def save(
        self,
        user: UserProfile,
        original: Optional[str] = None,
        *,
        base_version: Optional[int] = None,
    ):
        return self._kernel.save_user(user, original, base_version)

    def remove(self, username: str):
        return self._kernel.remove_user(username)

    def reload(self):
        return self._kernel.reload()

    def sub(self, event: ServiceEvent, callback: Callable):
        return self._kernel.sub(event, callback)

    def start_server(self, port: Optional[int] = None) -> tuple:
        return self.start(port).legacy()

    def stop_server(self) -> bool:
        return self.stop().ok

    def is_server_running(self) -> bool:
        return self.stat().state == ServiceState.RUNNING

    def get_server_port(self) -> int:
        return self.stat().port

    def set_server_port(self, port: int) -> tuple:
        return self._kernel.set_port(port)

    def get_server_host(self) -> str:
        return self.stat().host

    def get_protocol(self) -> FileServerProtocol:
        return self._kernel.protocol()

    def get_tls_config(self) -> TlsConfig:
        return self._kernel.tls()

    def get_ssh_config(self) -> SshConfig:
        return self._kernel.ssh()

    def get_max_cons(self) -> int:
        return self._kernel.max_cons()

    def get_max_cons_per_ip(self) -> int:
        return self._kernel.max_cons_per_ip()

    def is_loaded_dir_marker_enabled(self) -> bool:
        return self._kernel.marker()

    def update_global_settings(self, **kwargs) -> tuple:
        return self._kernel.update_global(**kwargs)

    def get_users(self):
        return self.users()

    def get_default_root(self) -> str:
        return self._kernel.default_root()

    def add_user(self, user: UserProfile) -> tuple:
        return self.save(user).legacy()

    def update_user(self, original: str, user: UserProfile) -> tuple:
        return self.save(user, original).legacy()

    def remove_user(self, username: str) -> tuple:
        return self.remove(username).legacy()

    def cleanup(self) -> None:
        self._kernel.cleanup()
