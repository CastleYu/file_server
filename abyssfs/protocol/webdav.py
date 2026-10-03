"""WebDAV backend (WsgiDAV + Cheroot) over VfsSession."""

from __future__ import annotations

import importlib.util
import logging
import traceback
from typing import List, Optional

from abyssfs.fs.session import VfsSession
from abyssfs.model.entities import UserProfile
from abyssfs.model.permission import FilePermission
from abyssfs.protocol.base import (
    AbstractFileServerBackend,
    FileServerProtocol,
    TlsConfig,
    WebdavConfig,
)
from abyssfs.service.registry import DirectoryRegistry

_logger = logging.getLogger(__name__)


def _has_wsgidav() -> bool:
    try:
        return importlib.util.find_spec("wsgidav") is not None
    except ModuleNotFoundError:
        return False


def _child_path(parent: str, name: str) -> str:
    parent = (parent or "/").replace("\\", "/")
    if parent == "/":
        return "/" + name
    return parent.rstrip("/") + "/" + name


class _AbyssDomainController:
    def __init__(self, wsgidav_app, config):
        self._registry: DirectoryRegistry = config["_abyss_registry"]
        self._realm = config.get("_abyss_realm", "AbyssFS")

    def get_domain_realm(self, path_info, environ):
        return self._realm

    def require_authentication(self, realm, environ):
        return True

    def is_share_anonymous(self, path_info):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        entry = self._registry.user(user_name)
        if entry is None:
            return False
        remote = ""
        if environ:
            remote = (
                environ.get("REMOTE_ADDR")
                or (environ.get("REMOTE_HOST") or "")
            )
        if remote and not entry.profile.check_ip(remote):
            _logger.warning(
                f"[WebDAV] IP 过滤拒绝: user={user_name!r}, ip={remote!r}"
            )
            return False
        if not entry.profile.verify_password(password):
            return False
        if environ is not None:
            environ["wsgidav.auth.user_name"] = user_name
        return True

    def supports_http_digest_auth(self):
        return False


def _bind_session(environ, registry: DirectoryRegistry) -> Optional[VfsSession]:
    username = (environ or {}).get("wsgidav.auth.user_name") or ""
    if not username:
        return None
    cached = (environ or {}).get("abyssfs.vfs")
    if cached is not None:
        cached.sync_profile()
        return cached
    entry = registry.user(username)
    if entry is None:
        return None
    session = VfsSession(
        entry.profile.root_dir,
        loaded_dir_marker_enabled=bool(
            (environ or {}).get("abyssfs.loaded_dir_marker")
        ),
    )
    session.set_user_registry(registry, username)
    if environ is not None:
        environ["abyssfs.vfs"] = session
    return session


def _make_provider_classes():
    from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError
    from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider

    class _File(DAVNonCollection):
        def __init__(self, path, environ, session: VfsSession):
            super().__init__(path, environ)
            self._session = session

        def get_content_length(self):
            return self._session.getsize(self.path)

        def get_content_type(self):
            return "application/octet-stream"

        def get_last_modified(self):
            return self._session.getmtime(self.path)

        def get_etag(self):
            mtime = int(self.get_last_modified() or 0)
            return f'"{self.get_content_length()}-{mtime}"'

        def get_content(self):
            if not self._session.has_perm(self.path, FilePermission.READ):
                raise DAVError(HTTP_FORBIDDEN)
            return self._session.open(self.path, "rb")

        def begin_write(self, *, content_type=None):
            if not self._session.has_perm(
                self.path, FilePermission.WRITE
            ) and not self._session.has_perm(self.path, FilePermission.APPEND):
                raise DAVError(HTTP_FORBIDDEN)
            return self._session.open(self.path, "wb")

        def delete(self):
            if not self._session.has_perm(self.path, FilePermission.DELETE):
                raise DAVError(HTTP_FORBIDDEN)
            self._session.remove(self.path)

        def handle_delete(self):
            self.delete()
            return True

        def support_etag(self):
            return False

    class _Folder(DAVCollection):
        def __init__(self, path, environ, session: VfsSession):
            super().__init__(path, environ)
            self._session = session

        def get_member_names(self):
            if not self._session.has_perm(self.path, FilePermission.LIST):
                raise DAVError(HTTP_FORBIDDEN)
            return self._session.listdir(self.path)

        def get_member(self, name):
            child = _child_path(self.path, name)
            provider = self.provider
            return provider.get_resource_inst(child, self.environ)

        def create_collection(self, name):
            child = _child_path(self.path, name)
            if not self._session.has_perm(child, FilePermission.MKDIR):
                raise DAVError(HTTP_FORBIDDEN)
            self._session.mkdir(child)

        def create_empty_resource(self, name):
            child = _child_path(self.path, name)
            if not self._session.has_perm(child, FilePermission.WRITE):
                raise DAVError(HTTP_FORBIDDEN)
            with self._session.open(child, "wb"):
                pass
            return self.provider.get_resource_inst(child, self.environ)

        def delete(self):
            if not self._session.has_perm(self.path, FilePermission.DELETE):
                raise DAVError(HTTP_FORBIDDEN)
            self._session.rmdir(self.path)

        def handle_delete(self):
            self.delete()
            return True

        def get_last_modified(self):
            try:
                return self._session.getmtime(self.path)
            except Exception:
                return None

    class _Provider(DAVProvider):
        def __init__(self, registry: DirectoryRegistry, loaded_dir_marker_enabled: bool = False):
            super().__init__()
            self._registry = registry
            self._loaded_dir_marker_enabled = loaded_dir_marker_enabled

        def get_resource_inst(self, path: str, environ: dict):
            if environ is not None:
                environ["abyssfs.loaded_dir_marker"] = self._loaded_dir_marker_enabled
            session = _bind_session(environ, self._registry)
            if session is None:
                return None
            vpath = path or "/"
            if not vpath.startswith("/"):
                vpath = "/" + vpath
            if session.isdir(vpath):
                return _Folder(vpath, environ, session)
            if session.isfile(vpath) or session.lexists(vpath):
                return _File(vpath, environ, session)
            return None

    return _Provider


class WebdavBackend(AbstractFileServerBackend):
    supports_directory_hot_apply = True

    def __init__(
        self,
        tls_config: Optional[TlsConfig] = None,
        webdav_config: Optional[WebdavConfig] = None,
    ) -> None:
        self.tls_config = tls_config or TlsConfig()
        self.webdav_config = webdav_config or WebdavConfig()
        self._server = None
        self._host = "0.0.0.0"
        self._port = 8080
        self._users: List[UserProfile] = []
        self._max_cons = 256
        self._loaded_dir_marker_enabled = False
        self._registry = DirectoryRegistry()
        _logger.info("[WebdavBackend] 初始化")

    @property
    def protocol(self) -> FileServerProtocol:
        return FileServerProtocol.WEBDAV

    def validate_config(self):
        if not _has_wsgidav():
            return False, "WebDAV 后端需要 wsgidav 库，请执行: pip install wsgidav cheroot"
        if self.tls_config.certfile:
            ok, err = self.tls_config.is_valid()
            if not ok:
                return False, f"WebDAV TLS 配置无效: {err}"
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
        self._max_cons = max_cons
        self._loaded_dir_marker_enabled = loaded_dir_marker_enabled
        self._registry.replace(self._users, refresh=True)

    def apply_directories(self, users: List[UserProfile]) -> int:
        previous = self._registry.snapshot()
        old_users = self._users
        try:
            snapshot = self._registry.replace(users, refresh=True)
            self._users = list(users)
            return snapshot.version
        except Exception:
            self._registry.restore(previous)
            self._users = old_users
            raise

    def start(self) -> None:
        try:
            from cheroot import wsgi
            from wsgidav.wsgidav_app import WsgiDAVApp
        except ImportError as exc:
            raise RuntimeError(
                "WebDAV 后端需要 wsgidav 和 cheroot，请执行: pip install wsgidav cheroot"
            ) from exc

        ok, err = self.validate_config()
        if not ok:
            raise RuntimeError(err or "WebDAV 配置无效")

        provider_cls = _make_provider_classes()
        config = {
            "host": self._host,
            "port": self._port,
            "provider_mapping": {
                "/": provider_cls(self._registry, self._loaded_dir_marker_enabled),
            },
            "http_authenticator": {
                "domain_controller": _AbyssDomainController,
                "accept_basic": True,
                "accept_digest": False,
                "default_to_digest": False,
            },
            "_abyss_registry": self._registry,
            "_abyss_realm": self.webdav_config.realm,
            "verbose": 1,
            "dir_browser": {"enable": True},
        }
        app = WsgiDAVApp(config)
        bind_host = self._host if self._host not in {"", "*"} else "0.0.0.0"
        server = wsgi.Server((bind_host, self._port), app)
        if self._max_cons:
            try:
                server.maxthreads = max(10, min(self._max_cons, 256))
            except Exception:
                pass
        if self.tls_config.certfile and self.tls_config.is_valid()[0]:
            from cheroot.ssl.builtin import BuiltinSSLAdapter

            server.ssl_adapter = BuiltinSSLAdapter(
                self.tls_config.certfile,
                self.tls_config.keyfile or self.tls_config.certfile,
            )
        self._server = server
        try:
            _logger.info(
                f"[WebdavBackend] 监听 {bind_host}:{self._port} realm={self.webdav_config.realm!r}"
            )
            server.start()
        finally:
            self._server = None

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            except Exception as exc:
                _logger.error(f"[WebdavBackend] stop() 异常: {exc}\n{traceback.format_exc()}")
            finally:
                self._server = None
