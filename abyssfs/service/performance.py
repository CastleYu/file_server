"""无第三方依赖的进程运行时性能采集。"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from abyssfs.service.constants import PerformancePath, PerformanceValue
from abyssfs.service.contracts import RuntimeMetrics


class WindowsMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


class MemoryProbe:
    _win_lock = threading.Lock()
    _win_handle = None
    _win_query = None

    @staticmethod
    def read() -> int:
        try:
            if os.name == "nt":
                return MemoryProbe._windows()
            if sys.platform.startswith("linux"):
                return MemoryProbe._linux()
            return MemoryProbe._resource()
        except (OSError, ValueError, AttributeError):
            return 0

    @staticmethod
    def _windows() -> int:
        counters = WindowsMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if MemoryProbe._win_query is None:
            with MemoryProbe._win_lock:
                if MemoryProbe._win_query is None:
                    kernel32 = ctypes.windll.kernel32
                    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
                    MemoryProbe._win_handle = kernel32.GetCurrentProcess()
                    query = ctypes.windll.psapi.GetProcessMemoryInfo
                    query.argtypes = (
                        ctypes.c_void_p,
                        ctypes.POINTER(WindowsMemoryCounters),
                        ctypes.c_ulong,
                    )
                    query.restype = ctypes.c_bool
                    MemoryProbe._win_query = query
        ok = MemoryProbe._win_query(
            MemoryProbe._win_handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            raise ctypes.WinError()
        return int(counters.working_set_size)

    @staticmethod
    def _linux() -> int:
        text = Path(PerformancePath.PROC_STATM).read_text(encoding="ascii")
        pages = int(text.split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")

    @staticmethod
    def _resource() -> int:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024


class RuntimeProfiler:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.perf_counter,
        cpu_clock: Callable[[], float] = time.process_time,
        memory: Callable[[], int] = MemoryProbe.read,
        thread_count: Callable[[], int] = threading.active_count,
        cpu_count: Optional[int] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._cpu_clock = cpu_clock
        self._memory = memory
        self._thread_count = thread_count
        self._cpu_count = max(1, cpu_count or os.cpu_count() or 1)
        self._started = self._clock()
        self._last_wall = self._started
        self._last_cpu = self._cpu_clock()
        self._started_cpu = self._last_cpu
        self._cpu_peak = 0.0
        self._memory_peak = 0
        self._threads_peak = 0
        self._samples = 0

    def snap(self) -> RuntimeMetrics:
        with self._lock:
            now = self._clock()
            cpu_now = self._cpu_clock()
            interval = max(now - self._last_wall, PerformanceValue.MIN_INTERVAL)
            cpu_delta = max(0.0, cpu_now - self._last_cpu)
            cpu = min(
                PerformanceValue.PERCENT,
                cpu_delta
                / interval
                / self._cpu_count
                * PerformanceValue.PERCENT,
            )
            memory = max(0, int(self._memory()))
            threads = max(0, int(self._thread_count()))

            self._samples += 1
            self._cpu_peak = max(self._cpu_peak, cpu)
            self._memory_peak = max(self._memory_peak, memory)
            self._threads_peak = max(self._threads_peak, threads)
            self._last_wall = now
            self._last_cpu = cpu_now

            uptime = max(0.0, now - self._started)
            cpu_avg = min(
                PerformanceValue.PERCENT,
                max(0.0, cpu_now - self._started_cpu)
                / max(uptime, PerformanceValue.MIN_INTERVAL)
                / self._cpu_count
                * PerformanceValue.PERCENT,
            )
            return RuntimeMetrics(
                uptime=uptime,
                cpu=cpu,
                cpu_avg=cpu_avg,
                cpu_peak=self._cpu_peak,
                memory=memory,
                memory_peak=self._memory_peak,
                threads=threads,
                threads_peak=self._threads_peak,
                samples=self._samples,
            )
