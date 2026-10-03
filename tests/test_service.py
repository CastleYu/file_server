import ftplib
import os
import socket
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from abyssfs.control.file_server import FileServerController, FileServerContext
from abyssfs.model.entities import UserProfile, VirtualDirectory
from abyssfs.protocol.base import FileServerProtocol
from abyssfs.protocol.ftp import FtpBackend, FtpsBackend
from abyssfs.protocol.sftp import _SftpVirtualServerInterface2
from abyssfs.service import ServiceFactory
from abyssfs.service.factory import ServiceRuntime
from abyssfs.service.host import ServiceHost, ServiceSpec
from abyssfs.service.performance import MemoryProbe, RuntimeProfiler
from abyssfs.service.registry import DirectoryRegistry


class FakeServerThread:
    def __init__(self) -> None:
        self.is_running = True
        self.applied = []

    def apply_directories(self, users) -> int:
        self.applied.append(list(users))
        return len(self.applied)


class ServiceTests(unittest.TestCase):
    def test_runtime_metrics_bypass_hot_facade_loading(self):
        expected = object()
        kernel = Mock()
        kernel.metrics.return_value = expected
        host = Mock()
        runtime = ServiceRuntime(kernel, host)

        self.assertIs(expected, runtime.metrics())
        host.api.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "仅 Windows 使用此内存探针")
    def test_windows_memory_probe_reports_working_set(self):
        self.assertGreater(MemoryProbe._windows(), 0)

    def test_runtime_profiler_tracks_current_average_and_peaks(self):
        wall = iter((10.0, 12.0, 14.0))
        cpu = iter((1.0, 3.0, 4.0))
        memory = iter((100, 80))
        threads = iter((3, 2))
        profiler = RuntimeProfiler(
            clock=lambda: next(wall),
            cpu_clock=lambda: next(cpu),
            memory=lambda: next(memory),
            thread_count=lambda: next(threads),
            cpu_count=2,
        )

        first = profiler.snap()
        second = profiler.snap()

        self.assertEqual(50.0, first.cpu)
        self.assertEqual(25.0, second.cpu)
        self.assertEqual(37.5, second.cpu_avg)
        self.assertEqual(50.0, second.cpu_peak)
        self.assertEqual(80, second.memory)
        self.assertEqual(100, second.memory_peak)
        self.assertEqual(2, second.threads)
        self.assertEqual(3, second.threads_peak)
        self.assertEqual(2, second.samples)
        self.assertEqual(4.0, second.uptime)

    def test_service_exposes_runtime_metrics(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = ServiceFactory.open(os.path.join(root, "config.json"), hot=False)
            metrics = runtime.api().metrics()

            self.assertGreaterEqual(metrics.uptime, 0.0)
            self.assertGreaterEqual(metrics.memory, 0)
            self.assertGreaterEqual(metrics.threads, 1)
            self.assertEqual(1, metrics.samples)

    def test_service_host_reloads_facade_without_replacing_state(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "facade.py"
            path.write_text(
                "class Api:\n"
                "    def __init__(self, state): self.state = state\n"
                "    def value(self): return 'v1:' + self.state\n",
                encoding="utf-8",
            )
            host = ServiceHost(ServiceSpec("test", path, "Api"))
            self.assertEqual("v1:stable", host.api("stable").value())

            time.sleep(0.01)
            path.write_text(
                "class Api:\n"
                "    def __init__(self, state): self.state = state\n"
                "    def value(self): return 'v2:' + self.state\n",
                encoding="utf-8",
            )
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            self.assertEqual("v2:stable", host.api("stable").value())

    def test_running_directory_update_is_applied_then_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "config.json")
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            controller = FileServerController(config)
            current = UserProfile(
                "alice",
                "secret",
                root_dir=root,
                virtual_dirs=[VirtualDirectory("old", old_dir)],
            )
            controller.context.users = [current]
            fake = FakeServerThread()
            controller.server_thread = fake
            updated = UserProfile.from_dict(current.to_dict())
            updated.virtual_dirs = [VirtualDirectory("new", new_dir)]

            ok, error = controller.update_user("alice", updated)

            self.assertTrue(ok, error)
            self.assertEqual(1, len(fake.applied))
            self.assertEqual("new", fake.applied[0][0].virtual_dirs[0].virtual_name)
            loaded = FileServerContext(config)
            self.assertTrue(loaded.load_from_disk())
            self.assertEqual("new", loaded.users[0].virtual_dirs[0].virtual_name)

    def test_ftps_uses_same_atomic_directory_update_as_ftp(self):
        self.assertIs(FtpsBackend.apply_directories, FtpBackend.apply_directories)

    def test_smb_directory_change_uses_controlled_restart(self):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "config.json")
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            controller = FileServerController(config, FileServerProtocol.SMB)
            current = UserProfile(
                "alice",
                "secret",
                root_dir=root,
                virtual_dirs=[VirtualDirectory("old", old_dir)],
            )
            updated = UserProfile.from_dict(current.to_dict())
            updated.virtual_dirs = [VirtualDirectory("new", new_dir)]
            controller.context.protocol = FileServerProtocol.SMB
            controller.context.users = [current]
            controller.server_thread = FakeServerThread()

            with (
                patch.object(controller, "_validate_runtime_users", return_value=(True, None)),
                patch.object(controller, "stop_server", return_value=True) as stop,
                patch.object(controller, "start_server", return_value=(True, None)) as start,
            ):
                ok, error = controller.update_user("alice", updated)

            self.assertTrue(ok, error)
            stop.assert_called_once_with()
            start.assert_called_once_with()
            self.assertEqual("new", controller.context.users[0].virtual_dirs[0].virtual_name)

    def test_running_auth_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            controller = FileServerController(os.path.join(root, "config.json"))
            current = UserProfile("alice", "secret", root_dir=root)
            controller.context.users = [current]
            fake = FakeServerThread()
            controller.server_thread = fake
            updated = UserProfile("alice", "changed", root_dir=root)

            ok, error = controller.update_user("alice", updated)

            self.assertFalse(ok)
            self.assertIn("密码", error)
            self.assertEqual([], fake.applied)

    def test_service_rejects_stale_config_version(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = ServiceFactory.open(os.path.join(root, "config.json"), hot=False)
            service = runtime.api()
            version = service.stat().version
            user = UserProfile("alice", "secret", root_dir=root)

            first = service.save(user, base_version=version)
            stale = service.save(
                UserProfile("bob", "secret", root_dir=root),
                base_version=version,
            )

            self.assertTrue(first.ok)
            self.assertFalse(stale.ok)
            self.assertEqual("conflict", stale.code)
            self.assertEqual(first.version, stale.version)

    def test_concurrent_writers_cannot_silently_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = ServiceFactory.open(os.path.join(root, "config.json"), hot=False)
            service = runtime.api()
            version = service.stat().version

            def save(name: str):
                return service.save(
                    UserProfile(name, "secret", root_dir=root),
                    base_version=version,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(save, ("alice", "bob")))

            self.assertEqual(1, sum(result.ok for result in results))
            self.assertEqual(1, sum(result.code == "conflict" for result in results))
            self.assertEqual(1, len(service.users()))

    def test_disk_failure_rolls_runtime_snapshot_back(self):
        with tempfile.TemporaryDirectory() as root:
            controller = FileServerController(os.path.join(root, "config.json"))
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            current = UserProfile(
                "alice",
                "secret",
                root_dir=root,
                virtual_dirs=[VirtualDirectory("old", old_dir)],
            )
            updated = UserProfile.from_dict(current.to_dict())
            updated.virtual_dirs = [VirtualDirectory("new", new_dir)]
            controller.context.users = [current]
            fake = FakeServerThread()
            controller.server_thread = fake

            with patch.object(controller.context, "save_to_disk", side_effect=OSError("disk")):
                ok, error = controller.update_user("alice", updated)

            self.assertFalse(ok)
            self.assertIn("disk", error)
            self.assertEqual(2, len(fake.applied))
            self.assertEqual("old", fake.applied[1][0].virtual_dirs[0].virtual_name)
            self.assertIs(current, controller.context.users[0])

    def test_existing_sftp_interface_reads_new_directory_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            old = UserProfile(
                "alice",
                "secret",
                root_dir=root,
                virtual_dirs=[VirtualDirectory("media", old_dir)],
            )
            registry = DirectoryRegistry()
            registry.replace([old])
            iface = _SftpVirtualServerInterface2(
                old,
                registry=registry,
                username="alice",
            )
            self.assertEqual(old_dir, iface._ftp_to_real("/media"))

            updated = UserProfile.from_dict(old.to_dict())
            updated.virtual_dirs = [VirtualDirectory("media", new_dir)]
            registry.replace([updated])

            self.assertEqual(new_dir, iface._ftp_to_real("/media"))

    def test_existing_sftp_connection_sees_hot_directory_update(self):
        import paramiko

        with tempfile.TemporaryDirectory() as root:
            home = os.path.join(root, "home")
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            os.makedirs(home)
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            with open(os.path.join(new_dir, "new.txt"), "w", encoding="utf-8") as stream:
                stream.write("new")

            host_key = os.path.join(root, "host.key")
            paramiko.RSAKey.generate(1024).write_private_key_file(host_key)
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()

            config = os.path.join(root, "config.json")
            context = FileServerContext(config, FileServerProtocol.SFTP)
            context.host = "127.0.0.1"
            context.port = port
            context.ssh_config.host_key_path = host_key
            current = UserProfile(
                "alice",
                "secret",
                root_dir=home,
                root_perm="elr",
                virtual_dirs=[VirtualDirectory("old", old_dir, perm="elr")],
            )
            context.users = [current]
            context.save_to_disk()

            runtime = ServiceFactory.open(config, hot=False)
            service = runtime.api()
            transport = None
            sftp = None
            try:
                result = service.start()
                self.assertTrue(result.ok, result.message)
                deadline = time.time() + 5
                while True:
                    try:
                        transport = paramiko.Transport(("127.0.0.1", port))
                        transport.connect(username="alice", password="secret")
                        break
                    except (OSError, paramiko.SSHException):
                        if transport is not None:
                            transport.close()
                        if time.time() >= deadline:
                            raise
                        time.sleep(0.05)
                sftp = paramiko.SFTPClient.from_transport(transport)
                self.assertIn("old", sftp.listdir("/"))

                updated = UserProfile.from_dict(current.to_dict())
                updated.virtual_dirs = [VirtualDirectory("new", new_dir, perm="elr")]
                applied = service.save(updated, "alice")
                self.assertTrue(applied.ok, applied.message)

                names = sftp.listdir("/")
                self.assertIn("new", names)
                self.assertNotIn("old", names)
                self.assertEqual(["new.txt"], sftp.listdir("/new"))
            finally:
                if sftp is not None:
                    sftp.close()
                if transport is not None:
                    transport.close()
                service.stop()

    def test_existing_ftp_connection_sees_hot_directory_update(self):
        with tempfile.TemporaryDirectory() as root:
            home = os.path.join(root, "home")
            old_dir = os.path.join(root, "old")
            new_dir = os.path.join(root, "new")
            home2 = os.path.join(root, "home2")
            os.makedirs(home)
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            os.makedirs(home2)
            with open(os.path.join(old_dir, "old.txt"), "w", encoding="utf-8") as stream:
                stream.write("old")
            with open(os.path.join(new_dir, "new.txt"), "w", encoding="utf-8") as stream:
                stream.write("new")
            with open(os.path.join(home2, "root2.txt"), "w", encoding="utf-8") as stream:
                stream.write("root2")

            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()

            config = os.path.join(root, "config.json")
            context = FileServerContext(config, FileServerProtocol.FTP)
            context.host = "127.0.0.1"
            context.port = port
            current = UserProfile(
                "alice",
                "secret",
                root_dir=home,
                root_perm="elr",
                virtual_dirs=[VirtualDirectory("old", old_dir, perm="elr")],
            )
            context.users = [current]
            context.save_to_disk()

            runtime = ServiceFactory.open(config, hot=False)
            service = runtime.api()
            ftp = ftplib.FTP()
            try:
                result = service.start()
                self.assertTrue(result.ok, result.message)
                deadline = time.time() + 5
                while True:
                    try:
                        ftp.connect("127.0.0.1", port, timeout=1)
                        break
                    except OSError:
                        if time.time() >= deadline:
                            raise
                        time.sleep(0.05)
                ftp.login("alice", "secret")
                self.assertIn("old", ftp.nlst("/"))

                updated = UserProfile.from_dict(current.to_dict())
                updated.virtual_dirs = [VirtualDirectory("new", new_dir, perm="elr")]
                applied = service.save(updated, "alice")
                self.assertTrue(applied.ok, applied.message)

                names = ftp.nlst("/")
                self.assertIn("new", names)
                self.assertNotIn("old", names)
                self.assertIn("new.txt", ftp.nlst("/new"))

                denied = UserProfile.from_dict(updated.to_dict())
                denied.virtual_dirs = [VirtualDirectory("new", new_dir, perm="el")]
                applied = service.save(denied, "alice")
                self.assertTrue(applied.ok, applied.message)
                with self.assertRaises(ftplib.error_perm):
                    ftp.retrbinary("RETR /new/new.txt", lambda _chunk: None)

                moved = UserProfile.from_dict(denied.to_dict())
                moved.root_dir = home2
                moved.virtual_dirs = [VirtualDirectory("new", new_dir, perm="elr")]
                applied = service.save(moved, "alice")
                self.assertTrue(applied.ok, applied.message)
                self.assertIn("root2.txt", ftp.nlst("/"))
                payload = bytearray()
                ftp.retrbinary("RETR /new/new.txt", payload.extend)
                self.assertEqual(b"new", bytes(payload))
            finally:
                try:
                    ftp.close()
                finally:
                    service.stop()


if __name__ == "__main__":
    unittest.main()
