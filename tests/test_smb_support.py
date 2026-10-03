import tempfile
import unittest
from unittest.mock import patch

from abyssfs.model.entities import UserProfile, nt_hash_password
from abyssfs.protocol import create_backend
from abyssfs.protocol.base import FileServerProtocol
from abyssfs.protocol.smb import SmbBackend


class SmbSupportTests(unittest.TestCase):
    def test_nt_hash_password_matches_known_value(self):
        self.assertEqual(
            "A4F49C406510BDCAB6824EE7C30FD852",
            nt_hash_password("Password"),
        )

    def test_user_profile_generates_and_preserves_smb_nt_hash(self):
        user = UserProfile("alice", "secret", root_dir="C:\\tmp")
        self.assertEqual(nt_hash_password("secret"), user.smb_nt_hash)

        loaded = UserProfile.from_dict(user.to_dict())
        self.assertEqual(user.password, loaded.password)
        self.assertEqual(user.smb_nt_hash, loaded.smb_nt_hash)

    def test_factory_creates_smb_backend_without_importing_impacket(self):
        backend = create_backend(FileServerProtocol.SMB)
        self.assertIsInstance(backend, SmbBackend)

    def test_smb_backend_reports_missing_impacket_dependency(self):
        with tempfile.TemporaryDirectory() as root:
            user = UserProfile("alice", "secret", root_dir=root)
            backend = SmbBackend()
            backend.configure("127.0.0.1", 1445, [user])

            with patch("abyssfs.protocol.smb.importlib.util.find_spec", return_value=None):
                ok, err = backend.validate_config()

        self.assertFalse(ok)
        self.assertIn("impacket", err)

    def test_smb_backend_preconfigure_validation_allows_controller_flow(self):
        backend = SmbBackend()

        with patch("abyssfs.protocol.smb.importlib.util.find_spec", return_value=object()):
            ok, err = backend.validate_config()

        self.assertTrue(ok)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
