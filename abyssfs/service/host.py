"""与 UI 和业务无关的 Service 动态加载器。"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from abyssfs.service.constants import ServiceKey, ServiceText


class ServiceSpec:
    def __init__(self, name: str, path: Path, entry: str) -> None:
        self.name = name
        self.path = path
        self.entry = entry


class ServiceSig:
    def __init__(self, mtime_ns: int, size: int) -> None:
        self.mtime_ns = mtime_ns
        self.size = size

    def same(self, other: "ServiceSig") -> bool:
        return self.mtime_ns == other.mtime_ns and self.size == other.size

    def text(self) -> str:
        return f"{self.mtime_ns}{ServiceKey.DASH}{self.size}"


class ServiceRef:
    def __init__(self, module: ModuleType, sig: ServiceSig) -> None:
        self.module = module
        self.sig = sig


class ServiceName:
    @staticmethod
    def clean(value: str) -> str:
        chars = []
        for char in value:
            if char.isalnum() or char == ServiceKey.UNDERLINE:
                chars.append(char)
            else:
                chars.append(ServiceKey.UNDERLINE)
        return "".join(chars)

    @staticmethod
    def module(spec: ServiceSpec, sig: ServiceSig) -> str:
        name = ServiceName.clean(spec.name)
        token = ServiceName.clean(sig.text())
        return f"{ServiceKey.MODULE_PREFIX}{name}{ServiceKey.UNDERLINE}{token}"

    @staticmethod
    def prefix(spec: ServiceSpec) -> str:
        return f"{ServiceKey.MODULE_PREFIX}{ServiceName.clean(spec.name)}{ServiceKey.UNDERLINE}"


class ModuleBox:
    @staticmethod
    def sig(path: Path) -> ServiceSig:
        stat = path.stat()
        return ServiceSig(stat.st_mtime_ns, stat.st_size)

    @staticmethod
    def purge(spec: ServiceSpec, keep: str) -> None:
        prefix = ServiceName.prefix(spec)
        for name in list(sys.modules):
            if name.startswith(prefix) and name != keep:
                del sys.modules[name]

    @staticmethod
    def load(spec: ServiceSpec) -> ServiceRef:
        path = spec.path.resolve()
        sig = ModuleBox.sig(path)
        module_name = ServiceName.module(spec, sig)
        module = sys.modules.get(module_name)
        if module is None:
            module_spec = importlib.util.spec_from_file_location(module_name, path)
            if module_spec is None:
                raise RuntimeError(ServiceText.MODULE_SPEC)
            if module_spec.loader is None:
                raise RuntimeError(ServiceText.LOADER)
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = module
            module_spec.loader.exec_module(module)
        ModuleBox.purge(spec, module_name)
        return ServiceRef(module, sig)


class ServiceHost:
    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        self.ref: ServiceRef | None = None
        self._lock = threading.RLock()

    def load(self) -> ModuleType:
        with self._lock:
            sig = ModuleBox.sig(self.spec.path.resolve())
            if self.ref is None or not self.ref.sig.same(sig):
                self.ref = ModuleBox.load(self.spec)
            return self.ref.module

    def api(self, *args, **kwargs) -> Any:
        module = self.load()
        entry = getattr(module, self.spec.entry)
        return entry(*args, **kwargs)

    def version(self) -> str:
        return ModuleBox.sig(self.spec.path.resolve()).text()


class StaticServiceHost:
    def __init__(self, entry: type) -> None:
        self.entry = entry

    def api(self, *args, **kwargs) -> Any:
        return self.entry(*args, **kwargs)

    def version(self) -> str:
        return "static"

