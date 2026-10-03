import base64
import http.client
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.protocol import create_backend
from abyssfs.protocol.base import FileServerProtocol
from abyssfs.protocol.webdav import WebdavBackend


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class WebdavSupportTests(unittest.TestCase):
    def test_factory_creates_webdav_backend_without_importing_wsgidav(self):
        with patch.dict("sys.modules", {"wsgidav": None, "wsgidav.wsgidav_app": None}):
            backend = create_backend(FileServerProtocol.WEBDAV)
        self.assertIsInstance(backend, WebdavBackend)
        self.assertTrue(backend.supports_directory_hot_apply)

    def test_webdav_reports_missing_dependency(self):
        backend = WebdavBackend()
        with patch("abyssfs.protocol.webdav.importlib.util.find_spec", return_value=None):
            ok, err = backend.validate_config()
        self.assertFalse(ok)
        self.assertIn("wsgidav", err)

    def test_empty_users_validate_when_dependency_present(self):
        backend = WebdavBackend()
        with patch("abyssfs.protocol.webdav.importlib.util.find_spec", return_value=object()):
            ok, err = backend.validate_config()
        self.assertTrue(ok)
        self.assertIsNone(err)

    @unittest.skipUnless(
        __import__("importlib.util").util.find_spec("wsgidav")
        and __import__("importlib.util").util.find_spec("cheroot"),
        "wsgidav/cheroot not installed",
    )
    def test_webdav_lists_virtual_directory_with_basic_auth(self):
        with tempfile.TemporaryDirectory() as root:
            home = os.path.join(root, "home")
            media = os.path.join(root, "media")
            os.makedirs(home)
            os.makedirs(media)
            with open(os.path.join(media, "photo.txt"), "w", encoding="utf-8") as fh:
                fh.write("pic")
            user = UserProfile(
                "alice",
                "secret",
                root_dir=home,
                root_perm="elr",
                virtual_dirs=[VirtualDirectory("media", media, perm="elr")],
            )
            port = _free_port()
            backend = WebdavBackend()
            backend.configure("127.0.0.1", port, [user])
            thread = threading.Thread(target=backend.start, daemon=True)
            thread.start()
            try:
                self._wait_port(port)
                names = self._propfind_names(port, "alice", "secret", "/")
                self.assertIn("media", names)
                names = self._propfind_names(port, "alice", "secret", "/media")
                self.assertIn("photo.txt", names)
            finally:
                backend.stop()
                thread.join(timeout=5)

    def _wait_port(self, port: int, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(0.2)
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.05)
            finally:
                sock.close()
        self.fail(f"WebDAV did not listen on {port}")

    def _propfind_names(self, port: int, username: str, password: str, path: str):
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'
        ).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(
                "PROPFIND",
                path,
                body=body,
                headers={
                    "Authorization": f"Basic {token}",
                    "Depth": "1",
                    "Content-Type": "application/xml",
                },
            )
            response = conn.getresponse()
            payload = response.read()
            self.assertIn(response.status, (207, 200), payload.decode("utf-8", "replace"))
        finally:
            conn.close()
        names = []
        root = ET.fromstring(payload)
        for href in root.iter("{DAV:}href"):
            text = (href.text or "").rstrip("/")
            names.append(text.rsplit("/", 1)[-1])
        return names


if __name__ == "__main__":
    unittest.main()
