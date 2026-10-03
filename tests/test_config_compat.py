import json
import os
import tempfile
import unittest
from pathlib import Path

from abyssfs.control.file_server import FileServerContext
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.model.permission import FilePermission
from abyssfs.protocol.base import FileServerProtocol


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ConfigCompatTests(unittest.TestCase):
    def test_legacy_fixture_loads_old_keys_without_smb_hash(self):
        context = FileServerContext(str(FIXTURES / "legacy_users.json"))
        self.assertTrue(context.load_from_disk())
        self.assertEqual(FileServerProtocol.FTP, context.protocol)
        self.assertEqual(1, len(context.users))

        user = context.users[0]
        self.assertEqual("legacy", user.username)
        self.assertEqual("C:\\legacy-root", user.root_dir)
        self.assertTrue(user.root_permission & FilePermission.READ)
        self.assertEqual(2, len(user.ip_rules))
        self.assertEqual("media", user.virtual_dirs[0].virtual_name)
        self.assertEqual("elr", user.virtual_dirs[0].perm)

    def test_save_preserves_legacy_and_dual_permission_keys(self):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "ftp_users.json")
            context = FileServerContext(config)
            context.protocol = FileServerProtocol.FTP
            context.port = 2121
            context.users = [
                UserProfile(
                    "alice",
                    "secret",
                    root_dir=root,
                    root_perm="elr",
                    virtual_dirs=[
                        VirtualDirectory("media", os.path.join(root, "media"), perm="elr"),
                    ],
                )
            ]
            context.save_to_disk()

            with open(config, "r", encoding="utf-8") as stream:
                data = json.load(stream)

            self.assertEqual("ftp", data["protocol"])
            self.assertIn("tls_config", data)
            self.assertIn("ssh_config", data)
            user = data["users"][0]
            self.assertIn("root_perm", user)
            self.assertIn("root_permission", user)
            self.assertIn("smb_nt_hash", user)
            self.assertIn("perm", user["virtual_dirs"][0])
            self.assertIn("permission", user["virtual_dirs"][0])
            self.assertEqual("local", user["virtual_dirs"][0].get("source_type", "local"))
            self.assertIn("webdav_config", data)
            self.assertIn("nfs_config", data)

            reloaded = FileServerContext(config)
            self.assertTrue(reloaded.load_from_disk())
            self.assertEqual("elr", reloaded.users[0].root_perm)
            self.assertEqual(
                int(reloaded.users[0].root_permission.value),
                user["root_permission"],
            )

    def test_unknown_protocol_falls_back_to_ftp(self):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "ftp_users.json")
            with open(config, "w", encoding="utf-8") as stream:
                json.dump({"protocol": "nope", "port": 2121, "users": []}, stream)

            context = FileServerContext(config)
            self.assertTrue(context.load_from_disk())
            self.assertEqual(FileServerProtocol.FTP, context.protocol)

    def test_missing_optional_keys_do_not_require_resave_to_authenticate(self):
        user = UserProfile.from_dict({
            "username": "bob",
            "password": "secret",
            "homedir": "C:\\data",
            "perm": "elr",
        })
        self.assertTrue(user.verify_password("secret"))
        dumped = user.to_dict()
        self.assertIn("root_dir", dumped)
        self.assertIn("root_perm", dumped)
        self.assertIn("virtual_dirs", dumped)
        hashed = UserProfile.from_dict(dumped)
        self.assertTrue(hashed.verify_password("secret"))
        self.assertEqual(user.smb_nt_hash, hashed.smb_nt_hash)
        self.assertEqual("local", hashed.virtual_dirs[0].source_type if hashed.virtual_dirs else "local")
        empty = VirtualDirectory.from_dict({
            "virtual_name": "media",
            "real_path": "C:\\legacy-media",
            "perm": "elr",
        })
        self.assertEqual("local", empty.source_type)


if __name__ == "__main__":
    unittest.main()
