"""Service 统一构造入口。"""

from __future__ import annotations

import sys
from pathlib import Path

from abyssfs.protocol.base import FileServerProtocol
from abyssfs.service.constants import ServiceKey
from abyssfs.service.contracts import RuntimeMetrics
from abyssfs.service.facade import FileService
from abyssfs.service.host import ServiceHost, ServiceSpec, StaticServiceHost
from abyssfs.service.kernel import ServiceKernel


class ServiceRuntime:
    def __init__(self, kernel: ServiceKernel, host) -> None:
        self._kernel = kernel
        self._host = host

    def api(self) -> FileService:
        return self._host.api(self._kernel)

    def metrics(self) -> RuntimeMetrics:
        return self._kernel.metrics()

    def version(self) -> str:
        return self._host.version()


class ServiceFactory:
    @staticmethod
    def open(
        config_path: str,
        protocol: FileServerProtocol = FileServerProtocol.FTP,
        *,
        hot: bool = True,
    ) -> ServiceRuntime:
        kernel = ServiceKernel(config_path, protocol)
        if hot and not getattr(sys, "frozen", False):
            path = Path(__file__).with_name("facade.py")
            spec = ServiceSpec(ServiceKey.NAME, path, ServiceKey.ENTRY)
            host = ServiceHost(spec)
        else:
            host = StaticServiceHost(FileService)
        return ServiceRuntime(kernel, host)
