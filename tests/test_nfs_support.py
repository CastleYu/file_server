import os
import tempfile
import unittest

from abyssfs.fs.session import VfsSession
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.protocol import create_backend
from abyssfs.protocol.base import FileServerProtocol
from abyssfs.protocol.nfs import NfsBackend, _Export, _XdrWriter, NFS3_OK


class NfsSupportTests(unittest.TestCase):
    def test_factory_creates_nfs_backend(self):
        backend = create_backend(FileServerProtocol.NFS)
        self.assertIsInstance(backend, NfsBackend)
        self.assertFalse(backend.supports_directory_hot_apply)

    def test_empty_users_validate(self):
        backend = NfsBackend()
        ok, err = backend.validate_config()
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_multi_user_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            users = [
                UserProfile("alice", "secret", root_dir=root),
                UserProfile("bob", "secret", root_dir=root),
            ]
            backend = NfsBackend()
            backend.configure("127.0.0.1", 2049, users)
            ok, err = backend.validate_config()
        self.assertFalse(ok)
        self.assertIn("单用户", err)

    def test_export_readdir_and_lookup_use_vfs_session(self):
        with tempfile.TemporaryDirectory() as root:
            home = os.path.join(root, "home")
            media = os.path.join(root, "media")
            os.makedirs(home)
            os.makedirs(media)
            with open(os.path.join(media, "a.txt"), "w", encoding="utf-8") as fh:
                fh.write("ok")
            user = UserProfile(
                "alice",
                "secret",
                root_dir=home,
                root_perm="elr",
                virtual_dirs=[VirtualDirectory("media", media, perm="elr")],
            )
            backend = NfsBackend()
            backend.configure("127.0.0.1", 2049, [user])
            export = backend._export
            self.assertIsNotNone(export)
            self.assertIsInstance(export.session, VfsSession)
            names = export.session.listdir("/")
            self.assertIn("media", names)
            child = export.session.resolve_virtual("/media/a.txt")
            self.assertTrue(child.endswith("a.txt"))
            self.assertEqual(os.path.normpath(media), os.path.normpath(export.session.resolve_virtual("/media")))

    def test_getattr_root_handle_encodes_ok(self):
        with tempfile.TemporaryDirectory() as root:
            user = UserProfile("alice", "secret", root_dir=root, root_perm="elr")
            export = _Export(user, create_backend(FileServerProtocol.NFS).nfs_config, False)
            writer = _XdrWriter()
            export.fattr(writer, "/")
            self.assertGreater(len(writer.data), 0)
            self.assertEqual(NFS3_OK, 0)


if __name__ == "__main__":
    unittest.main()
