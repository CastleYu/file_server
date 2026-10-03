import os
import tempfile
import unittest

from abyssfs.fs.session import VfsSession
from abyssfs.fs.virtual_fs import VirtualFS
from abyssfs.model.entities import UserProfile, VirtualDirectory


class DummyCmdChannel:
    use_gmt_times = False
    encoding = "utf-8"
    unicode_errors = "strict"


class VfsSessionParityTests(unittest.TestCase):
    def setUp(self):
        self.orig_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        self.external = os.path.join(self.tmp.name, "external")
        os.makedirs(self.root)
        os.makedirs(self.external)
        with open(os.path.join(self.root, "root.txt"), "w", encoding="utf-8") as fh:
            fh.write("root")
        with open(os.path.join(self.external, "external.txt"), "w", encoding="utf-8") as fh:
            fh.write("external")
        self.profile = UserProfile(
            "alice",
            "secret",
            root_dir=self.root,
            root_perm="elradfmwMT",
            virtual_dirs=[
                VirtualDirectory("media", self.external, perm="elradfmwMT"),
            ],
        )
        self.fs = VirtualFS(self.root, DummyCmdChannel())
        self.fs.set_user_profile(self.profile)
        self.session = VfsSession(self.root)
        self.session.set_user_profile(self.profile)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        self.tmp.cleanup()

    def test_root_listing_matches_virtualfs(self):
        self.assertEqual(
            self.fs.listdir(self.root),
            self.session.listdir(self.root),
        )
        self.assertIn("media", self.session.listdir("/"))
        self.assertIn("root.txt", self.session.listdir("/"))

    def test_virtual_path_resolve_matches_ftp_to_real(self):
        self.assertEqual(
            self.fs._ftp_to_real("/media/external.txt"),
            self.session.resolve_virtual("/media/external.txt"),
        )
        self.assertEqual(
            os.path.normpath(self.external),
            os.path.normpath(self.session.resolve_virtual("/media")),
        )

    def test_open_read_inside_virtual_dir(self):
        with self.session.open("/media/external.txt", "r") as fh:
            self.assertEqual("external", fh.read())
