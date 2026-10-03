import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from abyssfs import ospath
from abyssfs.fs.authorizer import _VirtualDirAuthorizer
from abyssfs.fs.constants import DeleteConst
from abyssfs.fs.delete_queue import AsyncDeleteService
from abyssfs.fs.virtual_fs import VirtualFS
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.model.permission import FilePermission


def _make_long_tree(root, min_len=280):
    current = root
    depth = 0
    while len(os.path.abspath(current)) < min_len:
        depth += 1
        current = os.path.join(current, f"LongDir{depth:02d}_" + ("n" * 60))
        ospath.makedirs(current, exist_ok=True)
    payload = os.path.join(current, "payload.txt")
    with ospath.open_file(payload, "w", encoding="utf-8") as fh:
        fh.write("long-path-ok")
    return current, payload


class DummyCmdChannel:
    use_gmt_times = False
    encoding = "utf-8"
    unicode_errors = "strict"


class VirtualFSTests(unittest.TestCase):
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
            root_perm="el",
            virtual_dirs=[
                VirtualDirectory("media", self.external, perm="elradfmwMT"),
            ],
        )
        self.fs = VirtualFS(self.root, DummyCmdChannel())
        self.fs.set_user_profile(self.profile)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        # 后台删除任务仍可能访问临时目录，清理夹具前先等待其完成。
        AsyncDeleteService.default()._jobs.join()
        try:
            self.tmp.cleanup()
        except OSError:
            ospath.rmtree(self.tmp.name)

    def test_pyftpdlib_real_path_callbacks_still_show_virtual_dirs(self):
        entries = self.fs.listdir(self.root)

        self.assertIn("root.txt", entries)
        self.assertIn("media", entries)

    def test_real_path_callbacks_inside_virtual_dir_use_virtual_mapping(self):
        entries = self.fs.listdir(self.external)

        self.assertEqual(["external.txt"], entries)
        self.assertTrue(self.fs.isdir(self.external))
        with self.fs.open(os.path.join(self.external, "external.txt"), "r") as fh:
            self.assertEqual("external", fh.read())

    def test_list_stat_mlsd_resolve_root_virtual_alias(self):
        entries = self.fs.listdir(self.root)
        alias = os.path.join(self.root, "media")

        self.assertIn("media", entries)
        self.assertTrue(stat.S_ISDIR(self.fs.stat(alias).st_mode))
        self.assertTrue(stat.S_ISDIR(self.fs.lstat(alias).st_mode))
        self.assertIn("external.txt", self.fs.listdir(alias))

        list_output = b"".join(self.fs.format_list(self.root, entries)).decode("utf-8")
        mlsd_output = b"".join(
            self.fs.format_mlsx(
                self.root,
                entries,
                self.profile.root_perm + "adfmwM",
                {"type", "size", "perm", "modify"},
            )
        ).decode("utf-8")

        self.assertIn(" media\r\n", list_output)
        self.assertIn(" media\r\n", mlsd_output)

    def test_cwd_cdup_and_relative_paths_inside_virtual_dir(self):
        self.fs.chdir("/media")

        self.assertEqual("/media", self.fs.cwd)
        self.assertEqual(
            os.path.join(self.external, "external.txt"),
            self.fs.ftp2fs("external.txt"),
        )
        self.assertEqual(self.root, self.fs.ftp2fs(".."))

    def test_write_metadata_and_delete_commands_inside_virtual_dir(self):
        self.fs.chdir("/media")

        self.fs.mkdir("uploads")
        self.assertTrue(os.path.isdir(os.path.join(self.external, "uploads")))

        with self.fs.open("uploads/new.txt", "wb") as fh:
            fh.write(b"new")
        with self.fs.open("uploads/new.txt", "ab") as fh:
            fh.write(b"+append")

        file_path = os.path.join(self.external, "uploads", "new.txt")
        renamed_path = os.path.join(self.external, "uploads", "renamed.txt")
        self.assertEqual(10, self.fs.getsize("uploads/new.txt"))
        self.fs.utime("uploads/new.txt", 1_700_000_000)
        self.assertEqual(1_700_000_000, int(self.fs.getmtime("uploads/new.txt")))
        self.fs.chmod("uploads/new.txt", 0o666)

        self.fs.rename("uploads/new.txt", "uploads/renamed.txt")
        self.assertFalse(os.path.exists(file_path))
        self.assertTrue(os.path.exists(renamed_path))

        self.fs.remove("uploads/renamed.txt")
        # 文件先被隔离到 uploads 内的队列；物理清理完成后目录才真正为空。
        AsyncDeleteService.default()._jobs.join()
        self.fs.rmdir("uploads")
        self.assertFalse(os.path.exists(os.path.join(self.external, "uploads")))

    def test_delete_hides_path_before_physical_cleanup(self):
        self.fs.chdir("/media")
        target = os.path.join(self.external, "instant.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("delete me")

        staged = AsyncDeleteService.default().file(target)

        self.assertFalse(os.path.exists(target))
        self.assertIn(DeleteConst.QUEUE_DIR, staged)

    def test_internal_delete_queue_dir_is_hidden_from_listings(self):
        queue_dir = os.path.join(self.external, DeleteConst.QUEUE_DIR)
        os.makedirs(queue_dir)
        with open(os.path.join(queue_dir, "pending.file"), "w", encoding="utf-8") as fh:
            fh.write("pending")

        self.fs.chdir("/media")

        self.assertNotIn(DeleteConst.QUEUE_DIR, self.fs.listdir("."))

    def test_virtual_root_cannot_be_removed_or_renamed(self):
        alias = os.path.join(self.root, "media")

        with self.assertRaises(PermissionError):
            self.fs.rmdir(alias)
        with self.assertRaises(PermissionError):
            self.fs.rename(alias, os.path.join(self.root, "renamed_media"))

    def test_authorizer_applies_virtual_dir_permission_to_real_paths(self):
        authorizer = _VirtualDirAuthorizer()
        authorizer.add_user_profile(self.profile)

        self.assertTrue(
            authorizer.has_perm(
                "alice",
                "r",
                os.path.join(self.external, "external.txt"),
            )
        )
        self.assertFalse(
            authorizer.has_perm(
                "alice",
                "r",
                os.path.join(self.root, "root.txt"),
            )
        )

    def test_optional_missing_dir_warns_once_outside_permission_hot_path(self):
        missing = VirtualDirectory(
            "missing",
            os.path.join(self.tmp.name, "missing"),
            perm="elr",
        )
        profile = UserProfile(
            "missing-user",
            "secret",
            root_dir=self.root,
            root_perm="elr",
            virtual_dirs=[missing],
        )

        with patch("abyssfs.model.entities._logger.warning") as warning:
            authorizer = _VirtualDirAuthorizer()
            authorizer.add_user_profile(profile)
            for _ in range(100):
                authorizer.has_perm("missing-user", "r", "/missing/page.jpg")
                authorizer.get_perms("missing-user")

        self.assertEqual(1, warning.call_count)
        self.assertIn("配置加载时跳过", warning.call_args.args[0])

    def test_virtual_dir_snapshot_refreshes_only_when_requested(self):
        path = os.path.join(self.tmp.name, "later")
        profile = UserProfile(
            "refresh-user",
            "secret",
            root_dir=self.root,
            virtual_dirs=[VirtualDirectory("later", path, perm="elr")],
        )

        self.assertEqual([], profile.get_mountable_virtual_dirs())
        os.makedirs(path)
        self.assertEqual([], profile.get_mountable_virtual_dirs())
        self.assertEqual(
            ["later"],
            [
                vdir.get_display_name()
                for vdir in profile.get_mountable_virtual_dirs(refresh=True)
            ],
        )

    def test_legacy_full_permission_values_gain_mfmt_permission(self):
        legacy_full_value = int((
            FilePermission.NAVIGATE
            | FilePermission.LIST
            | FilePermission.READ
            | FilePermission.WRITE
            | FilePermission.APPEND
            | FilePermission.DELETE
            | FilePermission.RENAME
            | FilePermission.MKDIR
            | FilePermission.CHMOD
        ).value)

        user = UserProfile.from_dict({
            "username": "legacy",
            "password": "secret",
            "root_dir": self.root,
            "root_permission": legacy_full_value,
            "virtual_dirs": [
                {
                    "virtual_name": "media",
                    "real_path": self.external,
                    "permission": legacy_full_value,
                }
            ],
        })

        self.assertTrue(user.root_permission & FilePermission.MODIFY_TIME)
        self.assertTrue(user.virtual_dirs[0].permission & FilePermission.MODIFY_TIME)
        self.assertIn("T", user.root_perm)
        self.assertIn("T", user.virtual_dirs[0].perm)

    def test_long_folder_names_can_be_listed_and_read(self):
        self.profile.root_perm = "elr"
        self.fs.set_user_profile(self.profile)
        long_dir, payload = _make_long_tree(self.root)
        rel_dir = "/" + os.path.relpath(long_dir, self.root).replace("\\", "/")
        rel_file = "/" + os.path.relpath(payload, self.root).replace("\\", "/")

        self.assertTrue(self.fs.isdir(rel_dir))
        self.assertTrue(self.fs.lexists(rel_file))
        self.assertTrue(self.fs.isfile(rel_file))
        self.assertIn("payload.txt", self.fs.listdir(rel_dir))

        self.fs.chdir(rel_dir)
        self.assertEqual(rel_dir, self.fs.cwd)
        with self.fs.open(rel_file, "r") as fh:
            self.assertEqual("long-path-ok", fh.read())
        self.assertEqual(len("long-path-ok"), self.fs.getsize(rel_file))
        self.assertTrue(stat.S_ISDIR(self.fs.stat(rel_dir).st_mode))

        list_output = b"".join(
            self.fs.format_list(self.fs.ftp2fs(rel_dir), ["payload.txt"])
        ).decode("utf-8")
        self.assertIn("payload.txt", list_output)

    def test_single_very_long_folder_name_can_be_read(self):
        self.profile.root_perm = "elr"
        self.fs.set_user_profile(self.profile)
        name = "F" * 240
        folder = os.path.join(self.root, name)
        ospath.makedirs(folder, exist_ok=True)
        payload = os.path.join(folder, "inside.txt")
        with ospath.open_file(payload, "w", encoding="utf-8") as fh:
            fh.write("named-ok")

        rel_dir = "/" + name
        self.assertTrue(self.fs.isdir(rel_dir))
        self.assertIn(name, self.fs.listdir("/"))
        self.fs.chdir(rel_dir)
        with self.fs.open("inside.txt", "r") as fh:
            self.assertEqual("named-ok", fh.read())

    def test_long_folder_inside_virtual_dir_can_be_read(self):
        long_dir, payload = _make_long_tree(self.external)
        rel_dir = "/media/" + os.path.relpath(long_dir, self.external).replace("\\", "/")
        rel_file = rel_dir.rstrip("/") + "/payload.txt"

        self.assertTrue(self.fs.isdir(rel_dir))
        self.fs.chdir(rel_dir)
        with self.fs.open("payload.txt", "r") as fh:
            self.assertEqual("long-path-ok", fh.read())
        authorizer = _VirtualDirAuthorizer()
        authorizer.add_user_profile(self.profile)
        self.assertTrue(authorizer.has_perm("alice", "r", payload))

    def test_ftp_to_real_ignores_extended_prefix_from_os_realpath(self):
        def _prefixed_realpath(path):
            resolved = os.path.abspath(ospath.strip_extended(path))
            if os.name == "nt":
                drive, tail = os.path.splitdrive(resolved)
                if drive and tail.startswith("\\") and not resolved.startswith("\\\\?\\"):
                    return "\\\\?\\" + resolved
            return resolved

        with patch("os.path.realpath", side_effect=_prefixed_realpath):
            mapped = self.fs._ftp_to_real("/root.txt")
            self.assertIsNotNone(mapped)
            self.assertFalse(str(mapped).startswith("\\\\?\\"))
            self.assertTrue(mapped.endswith("root.txt"))
            self.assertTrue(self.fs.isfile("/root.txt"))
            self.assertEqual(4, self.fs.getsize("/root.txt"))


if __name__ == "__main__":
    unittest.main()
