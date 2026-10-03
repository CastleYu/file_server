import unittest
from unittest.mock import patch

from abyssfs.protocol import create_backend
from abyssfs.protocol.base import AbstractFileServerBackend, FileServerProtocol
from abyssfs.protocol.ftp import FtpBackend, FtpsBackend
from abyssfs.protocol.sftp import SftpBackend
from abyssfs.protocol.smb import SmbBackend
from abyssfs.protocol.webdav import WebdavBackend
from abyssfs.protocol.nfs import NfsBackend


class ProtocolFactoryTests(unittest.TestCase):
    def test_create_backend_types(self):
        self.assertIsInstance(create_backend(FileServerProtocol.FTP), FtpBackend)
        self.assertIsInstance(create_backend(FileServerProtocol.FTPS), FtpsBackend)
        self.assertIsInstance(create_backend(FileServerProtocol.SFTP), SftpBackend)
        self.assertIsInstance(create_backend(FileServerProtocol.SMB), SmbBackend)
        self.assertIsInstance(create_backend(FileServerProtocol.WEBDAV), WebdavBackend)
        self.assertIsInstance(create_backend(FileServerProtocol.NFS), NfsBackend)

    def test_hot_apply_capability_matches_existing_protocols(self):
        self.assertTrue(create_backend(FileServerProtocol.FTP).supports_directory_hot_apply)
        self.assertTrue(create_backend(FileServerProtocol.FTPS).supports_directory_hot_apply)
        self.assertTrue(create_backend(FileServerProtocol.SFTP).supports_directory_hot_apply)
        self.assertFalse(create_backend(FileServerProtocol.SMB).supports_directory_hot_apply)
        self.assertTrue(create_backend(FileServerProtocol.WEBDAV).supports_directory_hot_apply)
        self.assertFalse(create_backend(FileServerProtocol.NFS).supports_directory_hot_apply)
        self.assertFalse(AbstractFileServerBackend.supports_directory_hot_apply)

    def test_factory_does_not_import_impacket_for_smb(self):
        with patch.dict("sys.modules", {"impacket": None, "impacket.smbserver": None}):
            backend = create_backend(FileServerProtocol.SMB)
        self.assertIsInstance(backend, SmbBackend)

    def test_ftp_and_sftp_empty_users_validate_without_runtime_dirs(self):
        ftp = FtpBackend()
        ok, err = ftp.validate_config()
        self.assertTrue(ok)
        self.assertIsNone(err)

        sftp = SftpBackend()
        ok, err = sftp.validate_config()
        self.assertFalse(ok)
        self.assertIn("host_key_path", err or "")


if __name__ == "__main__":
    unittest.main()
