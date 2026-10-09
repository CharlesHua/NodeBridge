import hashlib
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nodebridge.content_check import compare_remote_files, compare_remote_files_isolated
from nodebridge.remote import DirectoryListing, RemoteEntry


class MemorySFTP:
    def __init__(self, files):
        self.files = files

    def lstat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)

    def open(self, path, _mode):
        raise AssertionError("哈希核对不得经 SFTP 读取文件内容")


class MemorySession:
    def __init__(self, files):
        self.sftp = MemorySFTP(files)

    def sha256_file(self, path):
        return hashlib.sha256(self.sftp.files[path]).hexdigest()


class ContentCheckTests(unittest.TestCase):
    def test_compares_same_name_by_content_and_reports_missing(self):
        sessions = {
            "cft02": MemorySession({"/work/a.txt": b"one", "/work/b.txt": b"X"}),
            "cft03": MemorySession({"/work/a.txt": b"one", "/work/b.txt": b"Y"}),
            "cft04": MemorySession({"/work/a.txt": b"one"}),
        }
        results = compare_remote_files(sessions, "/work", ["a.txt", "b.txt"])
        self.assertEqual([entry["result"] for entry in results], ["same", "missing"])
        self.assertEqual(results[0]["nodes"]["cft02"]["detail"],
                         results[0]["nodes"]["cft03"]["detail"])
        self.assertEqual(results[1]["nodes"]["cft04"]["state"], "missing")

    def test_reports_different_bytes_with_same_file_size(self):
        sessions = {
            "cft02": MemorySession({"/work/a.txt": b"one"}),
            "cft03": MemorySession({"/work/a.txt": b"two"}),
        }
        self.assertEqual(compare_remote_files(sessions, "/work", ["a.txt"])[0]["result"], "different")

    def test_uses_separate_command_client_for_hashing(self):
        class Session(MemorySession):
            def sha256_file(self, path, *, command_client=None):
                if command_client is not dedicated:
                    raise AssertionError("必须通过独立跳板连接执行哈希命令")
                return super().sha256_file(path)

        dedicated = object()
        sessions = {"cft02": Session({"/work/a.txt": b"same"}),
                    "cft03": Session({"/work/a.txt": b"same"})}
        results = compare_remote_files(sessions, "/work", ["a.txt"], command_client=dedicated)
        self.assertEqual(results[0]["result"], "same")

    def test_automatic_check_uses_isolated_worker_sessions(self):
        class Session(MemorySession):
            def __init__(self, files):
                super().__init__(files)
                self.closed = False

            def sha256_file(self, path, *, command_client=None, cancel_event=None):
                self.assert_client = command_client
                return super().sha256_file(path)

            def close(self):
                self.closed = True

        jump_client = object()
        jump = SimpleNamespace(_client=jump_client)
        sessions = {
            "cft02": Session({"/work/a.txt": b"one"}),
            "cft03": Session({"/work/a.txt": b"two"}),
        }
        with patch("nodebridge.remote.RemoteSession.connect_alias",
                   side_effect=lambda alias, _jump: sessions[alias]) as connect:
            results = compare_remote_files_isolated(jump, list(sessions), "/work", ["a.txt"])
        self.assertEqual(results[0]["result"], "different")
        self.assertEqual(connect.call_count, 2)
        self.assertTrue(all(session.closed and session.assert_client is jump_client
                            for session in sessions.values()))

    def test_automatic_check_batches_files_and_reuses_directory_metadata(self):
        class BatchSession:
            def __init__(self, data):
                self.data = data
                self.calls = 0
                self.sftp = SimpleNamespace(lstat=lambda _path: self.fail_lstat())

            def fail_lstat(self):
                raise AssertionError("批量核对不应逐文件执行 SFTP lstat")

            def sha256_files(self, paths, *, command_client=None, cancel_event=None):
                self.calls += 1
                return {path: hashlib.sha256(self.data[path].encode()).hexdigest()
                        for path in paths}

            def list_directory(self, directory):
                return DirectoryListing(directory, tuple(
                    RemoteEntry(path.rsplit("/", 1)[-1], path, False, False, 3, 1)
                    for path in self.data
                ))

            def close(self):
                pass

        sessions = {
            "cft02": BatchSession({"/work/a.txt": "one", "/work/b.txt": "one"}),
            "cft03": BatchSession({"/work/a.txt": "two", "/work/b.txt": "one"}),
        }
        snapshots = {
            alias: {name: RemoteEntry(name, f"/work/{name}", False, False, 3, 1)
                    for name in ("a.txt", "b.txt")}
            for alias in sessions
        }
        jump = SimpleNamespace(_client=object())
        with patch("nodebridge.remote.RemoteSession.connect_alias",
                   side_effect=lambda alias, _jump: sessions[alias]):
            results = compare_remote_files_isolated(
                jump, list(sessions), "/work", ["a.txt", "b.txt"], snapshots=snapshots,
            )
        self.assertEqual([result["result"] for result in results], ["different", "same"])
        self.assertEqual([session.calls for session in sessions.values()], [1, 1])

    def test_batched_check_rejects_file_changed_during_hash(self):
        class ChangedSession:
            sftp = SimpleNamespace(lstat=lambda _path: SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_size=3, st_mtime=2,
            ))

            def sha256_files(self, paths, **_kwargs):
                return {paths[0]: "a" * 64}

            def close(self):
                pass

        snapshots = {"cft02": {
            "a.txt": RemoteEntry("a.txt", "/work/a.txt", False, False, 3, 1),
        }}
        jump = SimpleNamespace(_client=object())
        with patch("nodebridge.remote.RemoteSession.connect_alias", return_value=ChangedSession()):
            results = compare_remote_files_isolated(
                jump, ["cft02"], "/work", ["a.txt"], snapshots=snapshots,
            )
        self.assertEqual(results[0]["result"], "changed")


if __name__ == "__main__":
    unittest.main()
