"""
文件服务器权限模型

提供与协议无关的权限枚举及 FTP 字符串互转工具：
  - FilePermission: 统一权限枚举
  - _PERM_TO_FTP / _FTP_TO_PERM: 双向映射
  - to_ftp_perm_str() / from_ftp_perm_str() / sanitize_ftp_perm_str(): 转换工具
  - PERMISSION_PRESETS: 预设权限组合
  - PERMISSION_DETAILS: 各权限位的可读说明
"""

from __future__ import annotations

from enum import Flag, auto
from typing import Dict, Tuple


# =============================================================================
# 统一权限枚举
# =============================================================================

class FilePermission(Flag):
    """
    与协议无关的文件操作权限枚举。

    每个成员对应一种基础操作，各后端负责将其翻译为协议原生权限表示。
    FTP 对应字符见 _PERM_TO_FTP 映射；SFTP 直接使用此枚举做判断。
    """
    NONE     = 0
    NAVIGATE = auto()   # 进入目录        FTP: e
    LIST     = auto()   # 列出内容        FTP: l
    READ     = auto()   # 下载/读取       FTP: r
    WRITE    = auto()   # 上传/写入       FTP: w
    APPEND   = auto()   # 追加数据        FTP: a
    DELETE   = auto()   # 删除文件/目录   FTP: d
    RENAME   = auto()   # 重命名/移动     FTP: f
    MKDIR    = auto()   # 创建目录        FTP: m
    CHMOD    = auto()   # 修改权限        FTP: M
    MODIFY_TIME = auto()  # 修改时间      FTP: T

    # ------------------------------------------------------------------
    # 常用预设组合
    # ------------------------------------------------------------------
    READONLY  = NAVIGATE | LIST | READ
    ORGANIZE  = NAVIGATE | LIST | READ | RENAME | MKDIR
    UPLOAD    = NAVIGATE | LIST | READ | WRITE
    READWRITE = NAVIGATE | LIST | READ | WRITE | DELETE | RENAME | MKDIR
    FULL      = NAVIGATE | LIST | READ | WRITE | APPEND | DELETE | RENAME | MKDIR | CHMOD | MODIFY_TIME


# ------------------------------------------------------------------
# FTP 字符 <-> FilePermission 互转
# ------------------------------------------------------------------

_PERM_TO_FTP: Dict[FilePermission, str] = {
    FilePermission.NAVIGATE: 'e',
    FilePermission.LIST:     'l',
    FilePermission.READ:     'r',
    FilePermission.WRITE:    'w',
    FilePermission.APPEND:   'a',
    FilePermission.DELETE:   'd',
    FilePermission.RENAME:   'f',
    FilePermission.MKDIR:    'm',
    FilePermission.CHMOD:    'M',
    FilePermission.MODIFY_TIME: 'T',
}

_FTP_TO_PERM: Dict[str, FilePermission] = {v: k for k, v in _PERM_TO_FTP.items()}

# 有效的 FTP 权限字符集合（供外部使用）
FTP_VALID_CHARS: frozenset = frozenset(_FTP_TO_PERM.keys())

_LEGACY_FULL_PERMISSION = (
    FilePermission.NAVIGATE
    | FilePermission.LIST
    | FilePermission.READ
    | FilePermission.WRITE
    | FilePermission.APPEND
    | FilePermission.DELETE
    | FilePermission.RENAME
    | FilePermission.MKDIR
    | FilePermission.CHMOD
)


def to_ftp_perm_str(perm: FilePermission) -> str:
    """将 FilePermission 转换为 FTP 权限字符串（如 "elrw"）。"""
    return ''.join(char for flag, char in _PERM_TO_FTP.items() if perm & flag)


def from_ftp_perm_str(ftp_str: str) -> FilePermission:
    """将 FTP 权限字符串转换为 FilePermission。未知字符将被忽略。"""
    result = FilePermission.NONE
    for ch in (ftp_str or ""):
        if ch in _FTP_TO_PERM:
            result |= _FTP_TO_PERM[ch]
    return result


def from_permission_value(value: int) -> FilePermission:
    """从序列化整数恢复权限，并迁移旧版 FULL 权限到包含 T。"""
    result = FilePermission(value)
    if result == _LEGACY_FULL_PERMISSION:
        return FilePermission.FULL
    return result


def sanitize_ftp_perm_str(ftp_str: str, default: str = "elr") -> str:
    """清理 FTP 权限字符串，只保留有效字符；为空时返回 default。"""
    cleaned = ''.join(c for c in (ftp_str or "") if c in FTP_VALID_CHARS)
    return cleaned or default


# =============================================================================
# 权限预设（FilePermission 版）
# =============================================================================

# {预设名称: (FilePermission, 显示名称, 描述)}
PERMISSION_PRESETS: Dict[str, Tuple[FilePermission, str, str]] = {
    "readonly":  (FilePermission.READONLY,  "只读", "允许浏览目录和下载文件"),
    "organize":  (FilePermission.ORGANIZE,  "整理", "读取 + 重命名 + 创建目录"),
    "upload":    (FilePermission.UPLOAD,    "上传", "读取 + 上传文件"),
    "readwrite": (FilePermission.READWRITE, "读写", "完整读写，含重命名、删除和创建目录"),
    "full":      (FilePermission.FULL,      "完全", "完整读写 + 修改权限/时间"),
}

# 各权限位的可读说明 {FilePermission: (短名, 详细描述)}
PERMISSION_DETAILS: Dict[FilePermission, Tuple[str, str]] = {
    FilePermission.NAVIGATE: ('进入目录', '允许进入此目录及其子目录'),
    FilePermission.LIST:     ('列出文件', '允许列出目录内容'),
    FilePermission.READ:     ('下载文件', '允许下载/读取文件'),
    FilePermission.WRITE:    ('上传/写入', '允许上传和覆盖写入文件'),
    FilePermission.APPEND:   ('追加数据', '允许向已有文件追加内容'),
    FilePermission.DELETE:   ('删除',     '允许删除文件和目录'),
    FilePermission.RENAME:   ('重命名',   '允许重命名文件和目录'),
    FilePermission.MKDIR:    ('创建目录', '允许创建新目录'),
    FilePermission.CHMOD:    ('修改权限', '允许修改文件/目录的权限属性'),
    FilePermission.MODIFY_TIME: ('修改时间', '允许修改文件的最后修改时间'),
}
