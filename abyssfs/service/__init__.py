"""AbyssFS 服务层惰性公共入口，避免底层运行时模块循环导入。"""

from importlib import import_module


_EXPORTS = {
    "HostMode": ("abyssfs.service.constants", "HostMode"),
    "ServiceEvent": ("abyssfs.service.constants", "ServiceEvent"),
    "ServiceState": ("abyssfs.service.constants", "ServiceState"),
    "ServiceResult": ("abyssfs.service.contracts", "ServiceResult"),
    "ServiceStatus": ("abyssfs.service.contracts", "ServiceStatus"),
    "RuntimeMetrics": ("abyssfs.service.contracts", "RuntimeMetrics"),
    "Subscription": ("abyssfs.service.contracts", "Subscription"),
    "ServiceFactory": ("abyssfs.service.factory", "ServiceFactory"),
    "ServiceRuntime": ("abyssfs.service.factory", "ServiceRuntime"),
    "FileService": ("abyssfs.service.facade", "FileService"),
    "ServiceKernel": ("abyssfs.service.kernel", "ServiceKernel"),
    "DirectoryRegistry": ("abyssfs.service.registry", "DirectoryRegistry"),
    "DirectorySnapshot": ("abyssfs.service.registry", "DirectorySnapshot"),
    "DirectoryUser": ("abyssfs.service.registry", "DirectoryUser"),
}

__all__ = tuple(_EXPORTS)


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value
