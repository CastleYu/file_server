"""文件系统层公共导出。"""
from fs.virtual_fs import (  # noqa: F401
    VirtualFS,
    _build_virtual_mappings,
    _is_safe_raw_path,
    is_safe_raw_path,
    _DANGEROUS_PATH_RE,
)
from fs.authorizer import _VirtualDirAuthorizer, VirtualDirAuthorizer  # noqa: F401
