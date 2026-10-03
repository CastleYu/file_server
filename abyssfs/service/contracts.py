"""服务层稳定数据契约。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from abyssfs.service.constants import ResultCode, ServiceEvent, ServiceState


class ServiceResult:
    def __init__(
        self,
        ok: bool,
        code: str = ResultCode.OK,
        message: Optional[str] = None,
        version: int = 0,
        data: Any = None,
    ) -> None:
        self.ok = ok
        self.code = code
        self.message = message
        self.version = version
        self.data = data

    @classmethod
    def success(
        cls,
        message: Optional[str] = None,
        *,
        version: int = 0,
        data: Any = None,
    ) -> "ServiceResult":
        return cls(True, ResultCode.OK, message, version, data)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        version: int = 0,
    ) -> "ServiceResult":
        return cls(False, code, message, version)

    def legacy(self) -> tuple:
        return self.ok, self.message


class ServiceStatus:
    def __init__(
        self,
        state: ServiceState,
        protocol: Any,
        host: str,
        port: int,
        version: int,
        error: Optional[str] = None,
    ) -> None:
        self.state = state
        self.protocol = protocol
        self.host = host
        self.port = port
        self.version = version
        self.error = error


class RuntimeMetrics:
    def __init__(
        self,
        uptime: float,
        cpu: float,
        cpu_avg: float,
        cpu_peak: float,
        memory: int,
        memory_peak: int,
        threads: int,
        threads_peak: int,
        samples: int,
    ) -> None:
        self.uptime = uptime
        self.cpu = cpu
        self.cpu_avg = cpu_avg
        self.cpu_peak = cpu_peak
        self.memory = memory
        self.memory_peak = memory_peak
        self.threads = threads
        self.threads_peak = threads_peak
        self.samples = samples


class Subscription:
    def __init__(
        self,
        event: ServiceEvent,
        callback: Callable,
        cancel: Callable[[], None],
    ) -> None:
        self.event = event
        self.callback = callback
        self._cancel = cancel

    def close(self) -> None:
        self._cancel()
