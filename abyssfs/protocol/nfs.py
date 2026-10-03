"""Experimental user-space NFSv3 backend over VfsSession.

Single-user AUTH_UNIX squash (same isolation constraint as SMB).
Listens on TCP only; does not bind portmapper (111).
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct
import threading
import traceback
from typing import Dict, List, Optional

from abyssfs.fs.session import VfsSession
from abyssfs.model.entities import UserProfile
from abyssfs.model.permission import FilePermission
from abyssfs.protocol.base import AbstractFileServerBackend, FileServerProtocol, NfsConfig
from abyssfs.service.registry import DirectoryRegistry

_logger = logging.getLogger(__name__)

NFS_PROGRAM = 100003
NFS_VERSION = 3
MOUNT_PROGRAM = 100005
MOUNT_VERSION = 3

NFS3_OK = 0
NFS3ERR_PERM = 1
NFS3ERR_NOENT = 2
NFS3ERR_IO = 5
NFS3ERR_ACCES = 13
NFS3ERR_EXIST = 17
NFS3ERR_NOTDIR = 20
NFS3ERR_INVAL = 22
NFS3ERR_FBIG = 27
NFS3ERR_NOSPC = 28
NFS3ERR_ROFS = 30
NFS3ERR_NAMETOOLONG = 63
NFS3ERR_NOTSUPP = 10004
NFS3ERR_SERVERFAULT = 10006

NF3REG = 1
NF3DIR = 2
NF3LNK = 5

ACCESS3_READ = 0x0001
ACCESS3_LOOKUP = 0x0002
ACCESS3_MODIFY = 0x0004
ACCESS3_EXTEND = 0x0008
ACCESS3_DELETE = 0x0010
ACCESS3_EXECUTE = 0x0020


class _Xdr:
    def __init__(self, data: bytes = b""):
        self.data = bytearray(data)
        self.offset = 0

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def u32(self) -> int:
        value = struct.unpack(">I", self.data[self.offset:self.offset + 4])[0]
        self.offset += 4
        return value

    def i32(self) -> int:
        value = struct.unpack(">i", self.data[self.offset:self.offset + 4])[0]
        self.offset += 4
        return value

    def u64(self) -> int:
        value = struct.unpack(">Q", self.data[self.offset:self.offset + 8])[0]
        self.offset += 8
        return value

    def opaque(self) -> bytes:
        length = self.u32()
        padded = (length + 3) & ~3
        value = bytes(self.data[self.offset:self.offset + length])
        self.offset += padded
        return value

    def string(self) -> str:
        return self.opaque().decode("utf-8", "surrogateescape")

    def skip_auth(self) -> None:
        self.u32()
        body = self.opaque()
        del body


class _XdrWriter:
    def __init__(self):
        self.data = bytearray()

    def u32(self, value: int) -> None:
        self.data.extend(struct.pack(">I", value & 0xFFFFFFFF))

    def i32(self, value: int) -> None:
        self.data.extend(struct.pack(">i", int(value)))

    def u64(self, value: int) -> None:
        self.data.extend(struct.pack(">Q", int(value) & 0xFFFFFFFFFFFFFFFF))

    def opaque(self, value: bytes) -> None:
        value = value or b""
        self.u32(len(value))
        self.data.extend(value)
        pad = (4 - (len(value) % 4)) % 4
        self.data.extend(b"\x00" * pad)

    def string(self, value: str) -> None:
        self.opaque((value or "").encode("utf-8", "surrogateescape"))

    def optional(self, present: bool) -> None:
        self.u32(1 if present else 0)


def _recv_record(sock: socket.socket) -> Optional[bytes]:
    chunks = bytearray()
    while True:
        header = b""
        while len(header) < 4:
            piece = sock.recv(4 - len(header))
            if not piece:
                return None if not chunks else bytes(chunks)
            header += piece
        mark = struct.unpack(">I", header)[0]
        last = bool(mark & 0x80000000)
        length = mark & 0x7FFFFFFF
        body = b""
        while len(body) < length:
            piece = sock.recv(length - len(body))
            if not piece:
                return None
            body += piece
        chunks.extend(body)
        if last:
            return bytes(chunks)


def _send_record(sock: socket.socket, payload: bytes) -> None:
    mark = 0x80000000 | len(payload)
    sock.sendall(struct.pack(">I", mark) + payload)


class _Export:
    def __init__(self, profile: UserProfile, nfs_config: NfsConfig, marker: bool):
        self.profile = profile
        self.nfs_config = nfs_config
        self.session = VfsSession(profile.root_dir, loaded_dir_marker_enabled=marker)
        self.session.set_user_profile(profile)
        self._handles: Dict[bytes, str] = {}
        self._paths: Dict[str, bytes] = {}
        self.root_fh = self.handle_for("/")

    def handle_for(self, vpath: str) -> bytes:
        vpath = vpath.replace("\\", "/") or "/"
        if not vpath.startswith("/"):
            vpath = "/" + vpath
        if vpath != "/":
            vpath = vpath.rstrip("/") or "/"
        existing = self._paths.get(vpath)
        if existing:
            return existing
        digest = hashlib.sha256(vpath.encode("utf-8")).digest()[:16]
        self._handles[digest] = vpath
        self._paths[vpath] = digest
        return digest

    def path_for(self, fh: bytes) -> Optional[str]:
        return self._handles.get(fh)

    def child(self, parent: str, name: str) -> str:
        if parent == "/":
            return "/" + name
        return parent.rstrip("/") + "/" + name

    def fattr(self, writer: _XdrWriter, vpath: str) -> None:
        is_dir = self.session.isdir(vpath)
        ftype = NF3DIR if is_dir else (NF3LNK if self.session.islink(vpath) else NF3REG)
        try:
            st = self.session.stat(vpath)
            mode = getattr(st, "st_mode", 0o755 if is_dir else 0o644) & 0o777
            nlink = getattr(st, "st_nlink", 1)
            size = getattr(st, "st_size", 0)
            mtime = int(getattr(st, "st_mtime", 0))
            atime = int(getattr(st, "st_atime", mtime))
            ctime = int(getattr(st, "st_ctime", mtime))
            fileid = getattr(st, "st_ino", abs(hash(vpath)) & 0xFFFFFFFF)
        except Exception:
            mode = 0o755 if is_dir else 0o644
            nlink = 1
            size = 0
            mtime = atime = ctime = 0
            fileid = abs(hash(vpath)) & 0xFFFFFFFF
        writer.u32(ftype)
        writer.u32(mode)
        writer.u32(nlink)
        writer.u32(self.nfs_config.uid)
        writer.u32(self.nfs_config.gid)
        writer.u64(size)
        writer.u64(size)
        writer.u32(4096)
        writer.u64(max(1, (size + 4095) // 4096))
        writer.u64(0)
        writer.u64(fileid)
        writer.u32(atime)
        writer.u32(0)
        writer.u32(mtime)
        writer.u32(0)
        writer.u32(ctime)
        writer.u32(0)


class NfsBackend(AbstractFileServerBackend):
    supports_directory_hot_apply = False

    def __init__(self, nfs_config: Optional[NfsConfig] = None) -> None:
        self.nfs_config = nfs_config or NfsConfig()
        self._host = "0.0.0.0"
        self._port = 2049
        self._users: List[UserProfile] = []
        self._loaded_dir_marker_enabled = False
        self._registry = DirectoryRegistry()
        self._export: Optional[_Export] = None
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        _logger.info("[NfsBackend] 初始化")

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.NFS

    def validate_config(self):
        if not self._users:
            return True, None
        if len(self._users) > 1:
            return (
                False,
                "当前 NFS 后端处于单用户安全模式；NFSv3 AUTH_UNIX 无法复用 "
                "AbyssFS 的多用户口令隔离，请仅配置一个用户后启动 NFS",
            )
        user = self._users[0]
        if not user.root_dir:
            return False, "NFS 导出需要用户根目录"
        return True, None

    def configure(
        self,
        host: str,
        port: int,
        users: List[UserProfile],
        *,
        max_cons: int = 256,
        max_cons_per_ip: int = 5,
        banner: str = "",
        loaded_dir_marker_enabled: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._users = list(users)
        self._loaded_dir_marker_enabled = loaded_dir_marker_enabled
        self._registry.replace(self._users, refresh=True)
        if self._users:
            profile = self._registry.profiles()[0] if self._registry.profiles() else self._users[0]
            self._export = _Export(profile, self.nfs_config, loaded_dir_marker_enabled)

    def start(self) -> None:
        ok, err = self.validate_config()
        if not ok:
            raise RuntimeError(err or "NFS 配置无效")
        if self._export is None and self._users:
            self._export = _Export(self._users[0], self.nfs_config, self._loaded_dir_marker_enabled)

        self._stop.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        sock.bind((self._host, self._port))
        sock.listen(16)
        sock.settimeout(1.0)
        self._sock = sock
        _logger.info(f"[NfsBackend] 监听 {self._host}:{self._port} (NFSv3 TCP, 无 portmapper)")
        try:
            while not self._stop.is_set():
                try:
                    client, addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                remote = addr[0] if addr else ""
                if self._export and remote and not self._export.profile.check_ip(remote):
                    _logger.warning(f"[NFS] IP 过滤拒绝: {remote}")
                    client.close()
                    continue
                self._emit_access(remote)
                threading.Thread(
                    target=self._handle_client,
                    args=(client, addr),
                    daemon=True,
                ).start()
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _handle_client(self, sock: socket.socket, addr) -> None:
        try:
            while not self._stop.is_set():
                payload = _recv_record(sock)
                if not payload:
                    break
                reply = self._dispatch(payload)
                if reply is not None:
                    _send_record(sock, reply)
        except Exception as exc:
            _logger.warning(f"[NFS] 客户端 {addr} 异常: {exc}")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _dispatch(self, payload: bytes) -> Optional[bytes]:
        reader = _Xdr(payload)
        try:
            xid = reader.u32()
            msg_type = reader.u32()
            if msg_type != 0:
                return None
            reader.u32()
            prog = reader.u32()
            vers = reader.u32()
            proc = reader.u32()
            reader.skip_auth()
            reader.skip_auth()
        except Exception:
            return None

        writer = _XdrWriter()
        writer.u32(xid)
        writer.u32(1)
        writer.u32(0)
        writer.u32(0)
        writer.opaque(b"")

        if prog == MOUNT_PROGRAM and vers == MOUNT_VERSION:
            self._mount_proc(proc, reader, writer)
        elif prog == NFS_PROGRAM and vers == NFS_VERSION:
            self._nfs_proc(proc, reader, writer)
        else:
            writer.data = bytearray()
            writer.u32(xid)
            writer.u32(1)
            writer.u32(1)
            writer.u32(1)
        return bytes(writer.data)

    def _mount_proc(self, proc: int, reader: _Xdr, writer: _XdrWriter) -> None:
        export = self._export
        if proc == 0:
            return
        if proc == 5:
            if export is None:
                return
            writer.string("/")
            writer.string(export.profile.username)
            writer.u32(0)
            return
        if proc in {1, 3}:
            try:
                path = reader.string()
            except Exception:
                path = "/"
            if export is None:
                writer.u32(NFS3ERR_ACCES)
                return
            writer.u32(NFS3_OK)
            writer.opaque(export.root_fh)
            writer.u32(0)
            _logger.info(f"[NFS] MNT {path!r} -> /")
            return
        if proc in {2, 4}:
            return
        writer.u32(NFS3ERR_NOTSUPP)

    def _nfs_proc(self, proc: int, reader: _Xdr, writer: _XdrWriter) -> None:
        export = self._export
        if proc == 0:
            return
        if export is None:
            writer.u32(NFS3ERR_ACCES)
            return
        try:
            if proc == 1:
                self._nfs_getattr(export, reader, writer)
            elif proc == 3:
                self._nfs_lookup(export, reader, writer)
            elif proc == 4:
                self._nfs_access(export, reader, writer)
            elif proc == 6:
                self._nfs_read(export, reader, writer)
            elif proc == 7:
                self._nfs_write(export, reader, writer)
            elif proc == 8:
                self._nfs_create(export, reader, writer)
            elif proc == 9:
                self._nfs_mkdir(export, reader, writer)
            elif proc == 12:
                self._nfs_remove(export, reader, writer)
            elif proc == 13:
                self._nfs_rmdir(export, reader, writer)
            elif proc == 14:
                self._nfs_rename(export, reader, writer)
            elif proc == 16:
                self._nfs_readdir(export, reader, writer, plus=False)
            elif proc == 17:
                self._nfs_readdir(export, reader, writer, plus=True)
            elif proc == 18:
                self._nfs_fsstat(export, reader, writer)
            elif proc == 19:
                self._nfs_fsinfo(export, reader, writer)
            elif proc == 20:
                self._nfs_pathconf(export, reader, writer)
            elif proc == 21:
                reader.opaque()
                reader.u64()
                reader.u32()
                writer.u32(NFS3_OK)
                writer.optional(False)
                writer.optional(False)
                writer.u32(1)
            else:
                writer.u32(NFS3ERR_NOTSUPP)
        except PermissionError:
            writer.u32(NFS3ERR_ACCES)
        except FileNotFoundError:
            writer.u32(NFS3ERR_NOENT)
        except Exception as exc:
            _logger.error(f"[NFS] proc={proc} 异常: {exc}\n{traceback.format_exc()}")
            writer.u32(NFS3ERR_SERVERFAULT)

    def _nfs_getattr(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        vpath = export.path_for(fh)
        if vpath is None:
            writer.u32(NFS3ERR_NOENT)
            return
        writer.u32(NFS3_OK)
        export.fattr(writer, vpath)

    def _nfs_lookup(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        name = reader.string()
        parent = export.path_for(fh)
        if parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        if not export.session.has_perm(parent, FilePermission.NAVIGATE):
            writer.u32(NFS3ERR_ACCES)
            writer.optional(False)
            return
        child = export.child(parent, name)
        if not export.session.lexists(child) and not export.session.isdir(child):
            writer.u32(NFS3ERR_NOENT)
            writer.optional(False)
            return
        writer.u32(NFS3_OK)
        writer.opaque(export.handle_for(child))
        writer.optional(True)
        export.fattr(writer, child)
        writer.optional(True)
        export.fattr(writer, parent)

    def _nfs_access(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        wanted = reader.u32()
        vpath = export.path_for(fh)
        if vpath is None:
            writer.u32(NFS3ERR_NOENT)
            return
        granted = 0
        if export.session.has_perm(vpath, FilePermission.READ):
            granted |= ACCESS3_READ | ACCESS3_EXECUTE
        if export.session.has_perm(vpath, FilePermission.LIST) or export.session.has_perm(
            vpath, FilePermission.NAVIGATE
        ):
            granted |= ACCESS3_LOOKUP
        if export.session.has_perm(vpath, FilePermission.WRITE):
            granted |= ACCESS3_MODIFY | ACCESS3_EXTEND
        if export.session.has_perm(vpath, FilePermission.DELETE):
            granted |= ACCESS3_DELETE
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u32(wanted & granted)

    def _nfs_read(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        offset = reader.u64()
        count = reader.u32()
        vpath = export.path_for(fh)
        if vpath is None:
            writer.u32(NFS3ERR_NOENT)
            return
        if not export.session.has_perm(vpath, FilePermission.READ):
            writer.u32(NFS3ERR_ACCES)
            writer.optional(False)
            return
        with export.session.open(vpath, "rb") as handle:
            handle.seek(offset)
            data = handle.read(count)
        eof = len(data) < count
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u32(len(data))
        writer.opaque(data)
        writer.u32(1 if eof else 0)

    def _nfs_write(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        offset = reader.u64()
        count = reader.u32()
        stable = reader.u32()
        data = reader.opaque()
        vpath = export.path_for(fh)
        if vpath is None:
            writer.u32(NFS3ERR_NOENT)
            return
        if not export.session.has_perm(vpath, FilePermission.WRITE):
            writer.u32(NFS3ERR_ACCES)
            writer.optional(False)
            return
        payload = data[:count]
        with export.session.open(vpath, "r+b") as handle:
            handle.seek(offset)
            handle.write(payload)
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u32(len(payload))
        writer.u32(stable)
        writer.opaque(hashlib.sha256(payload).digest()[:8])

    def _nfs_create(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        name = reader.string()
        parent = export.path_for(fh)
        if parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        child = export.child(parent, name)
        if not export.session.has_perm(child, FilePermission.WRITE):
            writer.u32(NFS3ERR_ACCES)
            writer.optional(False)
            writer.optional(False)
            return
        with export.session.open(child, "wb"):
            pass
        writer.u32(NFS3_OK)
        writer.optional(True)
        writer.opaque(export.handle_for(child))
        writer.optional(True)
        export.fattr(writer, child)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, parent)

    def _nfs_mkdir(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        name = reader.string()
        parent = export.path_for(fh)
        if parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        child = export.child(parent, name)
        export.session.mkdir(child)
        writer.u32(NFS3_OK)
        writer.optional(True)
        writer.opaque(export.handle_for(child))
        writer.optional(True)
        export.fattr(writer, child)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, parent)

    def _nfs_remove(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        name = reader.string()
        parent = export.path_for(fh)
        if parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        child = export.child(parent, name)
        export.session.remove(child)
        writer.u32(NFS3_OK)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, parent)

    def _nfs_rmdir(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        name = reader.string()
        parent = export.path_for(fh)
        if parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        child = export.child(parent, name)
        export.session.rmdir(child)
        writer.u32(NFS3_OK)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, parent)

    def _nfs_rename(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        from_fh = reader.opaque()
        from_name = reader.string()
        to_fh = reader.opaque()
        to_name = reader.string()
        src_parent = export.path_for(from_fh)
        dst_parent = export.path_for(to_fh)
        if src_parent is None or dst_parent is None:
            writer.u32(NFS3ERR_NOENT)
            return
        export.session.rename(export.child(src_parent, from_name), export.child(dst_parent, to_name))
        writer.u32(NFS3_OK)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, src_parent)
        writer.optional(False)
        writer.optional(True)
        export.fattr(writer, dst_parent)

    def _nfs_readdir(
        self,
        export: _Export,
        reader: _Xdr,
        writer: _XdrWriter,
        *,
        plus: bool,
    ) -> None:
        fh = reader.opaque()
        cookie = reader.u64()
        reader.opaque()
        if plus:
            reader.u32()
        reader.u32()
        vpath = export.path_for(fh)
        if vpath is None:
            writer.u32(NFS3ERR_NOENT)
            return
        if not export.session.has_perm(vpath, FilePermission.LIST):
            writer.u32(NFS3ERR_ACCES)
            writer.optional(False)
            return
        names = ["."] + export.session.listdir(vpath)
        start = int(cookie)
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.opaque(b"cookieverf")
        last_index = start
        for index, name in enumerate(names):
            if index < start:
                continue
            writer.u32(1)
            fileid = abs(hash(export.child(vpath, name) if name != "." else vpath)) & 0xFFFFFFFF
            writer.u64(fileid)
            writer.string(name)
            writer.u64(index + 1)
            if plus:
                child = vpath if name == "." else export.child(vpath, name)
                writer.optional(True)
                export.fattr(writer, child)
                writer.optional(True)
                writer.opaque(export.handle_for(child))
            last_index = index + 1
        writer.u32(0)
        writer.u32(1 if last_index >= len(names) else 0)

    def _nfs_fsstat(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        vpath = export.path_for(fh) or "/"
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u64(1 << 40)
        writer.u64(1 << 40)
        writer.u64(1 << 40)
        writer.u64(1 << 20)
        writer.u64(1 << 20)
        writer.u64(1 << 20)
        writer.u32(0)

    def _nfs_fsinfo(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        vpath = export.path_for(fh) or "/"
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u32(1048576)
        writer.u32(4096)
        writer.u32(1048576)
        writer.u32(1048576)
        writer.u32(4096)
        writer.u32(1048576)
        writer.u64((1 << 63) - 1)
        writer.u32(0)
        writer.u32(1)
        writer.u32(0x001B)

    def _nfs_pathconf(self, export: _Export, reader: _Xdr, writer: _XdrWriter) -> None:
        fh = reader.opaque()
        vpath = export.path_for(fh) or "/"
        writer.u32(NFS3_OK)
        writer.optional(True)
        export.fattr(writer, vpath)
        writer.u32(32768)
        writer.u32(255)
        writer.u32(1)
        writer.u32(0)
        writer.u32(1)
        writer.u32(1)
