import os
import tempfile
import unittest

from abyssfs import ospath


class OspathTests(unittest.TestCase):
    def test_strip_extended_drive_and_unc(self):
        self.assertEqual(r"C:\data\file.txt", ospath.strip_extended(r"\\?\C:\data\file.txt"))
        self.assertEqual(
            r"\\server\share\dir",
            ospath.strip_extended(r"\\?\UNC\server\share\dir"),
        )
        self.assertEqual(r"C:\data", ospath.strip_extended(r"C:\data"))

    def test_to_os_path_is_identity_off_windows(self):
        if os.name == "nt":
            self.skipTest("non-Windows identity")
        self.assertEqual("/tmp/foo", ospath.to_os_path("/tmp/foo"))

    @unittest.skipUnless(os.name == "nt", "Windows extended prefix")
    def test_to_os_path_prefixes_absolute_and_unc(self):
        self.assertEqual(r"\\?\C:\data\file.txt", ospath.to_os_path(r"C:\data\file.txt"))
        self.assertEqual(
            r"\\?\UNC\server\share\dir",
            ospath.to_os_path(r"\\server\share\dir"),
        )
        self.assertEqual(
            r"\\?\C:\data\file.txt",
            ospath.to_os_path(r"\\?\C:\data\file.txt"),
        )
        self.assertEqual(
            r"\\?\C:\data",
            ospath.to_os_path(r"C:\data\foo\.."),
        )
        self.assertEqual("relative\\path", ospath.to_os_path("relative/path"))

    @unittest.skipUnless(os.name == "nt", "Windows extended prefix")
    def test_realpath_always_returns_unprefixed_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = ospath.realpath(tmp)
            self.assertFalse(resolved.startswith("\\\\?\\"))
            self.assertTrue(os.path.isabs(resolved))
            # Windows realpath 会展开 8.3 短名称（例如 RUNNER~1），
            # 字符串可以与 abspath 不同，但必须指向同一个目录。
            self.assertTrue(os.path.samefile(tmp, resolved))
            self.assertEqual(resolved, ospath.realpath(ospath.to_os_path(tmp)))

    def test_roundtrip_file_ops_on_nested_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = tmp
            while len(os.path.abspath(current)) < 280:
                current = os.path.join(current, "n" * 40)
                ospath.makedirs(current, exist_ok=True)
            payload = os.path.join(current, "payload.txt")
            with ospath.open_file(payload, "w", encoding="utf-8") as fh:
                fh.write("ok")
            self.assertTrue(ospath.isdir(current))
            self.assertTrue(ospath.isfile(payload))
            self.assertEqual(["payload.txt"], ospath.listdir(current))
            with ospath.open_file(payload, "r", encoding="utf-8") as fh:
                self.assertEqual("ok", fh.read())
            self.assertGreater(ospath.getsize(payload), 0)
            ospath.stat(payload)
            ospath.lstat(current)


if __name__ == "__main__":
    unittest.main()
