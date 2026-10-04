import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nodebridge.transfer import (
    ConflictAction,
    copy_local_to_local,
    copy_local_to_remote,
    copy_remote_to_local,
    copy_remote_to_remote,
    copy_remote_between_sessions,
)


class DiskBackedSFTP:
    def __init__(self, root):
        self.root = Path(root)
        self.write_modes = []

    def path(self, remote):
        return self.root.joinpath(*Path(remote.lstrip("/")).parts)

    def lstat(self, remote):
        return self.path(remote).lstat()

    def stat(self, remote):
        return self.path(remote).stat()

    def listdir_attr(self, remote):
        return [SimpleNamespace(filename=path.name) for path in self.path(remote).iterdir()]

    def mkdir(self, remote):
        self.path(remote).mkdir()

    def open(self, remote, mode):
        self.write_modes.append(mode)
        return self.path(remote).open("xb" if mode == "wbx" else mode)

    def remove(self, remote):
        self.path(remote).unlink()

    def rmdir(self, remote):
        self.path(remote).rmdir()

    def rename(self, source, target):
        self.path(source).rename(self.path(target))

    def posix_rename(self, source, target):
        self.path(source).replace(self.path(target))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.local = root / "local"
        self.remote = root / "remote"
        self.download = root / "download"
        for path in (self.local, self.remote, self.download):
            path.mkdir()
        self.sftp = DiskBackedSFTP(self.remote)
        self.session = SimpleNamespace(sftp=self.sftp)

    def test_recursive_upload_and_download_preserve_contents(self):
        folder = self.local / "data"
        (folder / "nested").mkdir(parents=True)
        (folder / "nested" / "notes.txt").write_bytes(b"hello\x00world")
        (folder / "empty").mkdir()

        uploaded = copy_local_to_remote(self.session, [str(folder)], "/")
        downloaded = copy_remote_to_local(self.session, ["/data"], self.download)

        self.assertEqual((uploaded.files, uploaded.directories, uploaded.bytes_copied), (1, 3, 11))
        self.assertEqual(downloaded, uploaded)
        self.assertEqual((self.download / "data/nested/notes.txt").read_bytes(), b"hello\x00world")
        self.assertTrue((self.download / "data/empty").is_dir())
        self.assertIn("wbx", self.sftp.write_modes)

    def test_local_to_worker_ignores_different_same_named_jump_file(self):
        source = self.local / "input.txt"
        source.write_bytes(b"local version")
        jump_root = Path(self.temporary.name) / "jump"
        worker_root = Path(self.temporary.name) / "worker"
        jump_root.mkdir()
        worker_root.mkdir()
        (jump_root / "input.txt").write_bytes(b"jump version")
        worker = SimpleNamespace(sftp=DiskBackedSFTP(worker_root))

        copy_local_to_remote(worker, [str(source)], "/")

        self.assertEqual((worker_root / "input.txt").read_bytes(), b"local version")
        self.assertEqual((jump_root / "input.txt").read_bytes(), b"jump version")

    def test_existing_destination_is_never_overwritten(self):
        source = self.local / "notes.txt"
        source.write_text("new", encoding="utf-8")
        target = self.remote / "notes.txt"
        target.write_text("old", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            copy_local_to_remote(self.session, [str(source)], "/")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")

        local_target = self.download / "notes.txt"
        local_target.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            copy_remote_to_local(self.session, ["/notes.txt"], self.download)
        self.assertEqual(local_target.read_text(encoding="utf-8"), "keep")

    def test_local_copy_rejects_collision_and_copies_directory(self):
        folder = self.local / "data"
        folder.mkdir()
        (folder / "file.txt").write_text("copy", encoding="utf-8")
        copy_local_to_local([str(folder)], self.download)
        self.assertEqual((self.download / "data/file.txt").read_text(encoding="utf-8"), "copy")
        with self.assertRaises(FileExistsError):
            copy_local_to_local([str(folder)], self.download)

    def test_local_copy_rejects_self_and_descendant(self):
        folder = self.local / "data"
        child = folder / "child"
        child.mkdir(parents=True)
        (folder / "note.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            copy_local_to_local([str(folder / "note.txt")], folder)
        with self.assertRaises(ValueError):
            copy_local_to_local([str(folder)], child)
        self.assertEqual((folder / "note.txt").read_text(encoding="utf-8"), "keep")

    def test_remote_copy_recurses_and_prompts_for_conflict(self):
        (self.remote / "source").mkdir()
        (self.remote / "target").mkdir()
        (self.remote / "source" / "note.txt").write_text("new", encoding="utf-8")
        first = copy_remote_to_remote(self.session, ["/source"], "/target")
        self.assertEqual((first.files, first.directories), (1, 1))
        target = self.remote / "target" / "source" / "note.txt"
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        (self.remote / "source" / "note.txt").write_text("changed", encoding="utf-8")
        prompts = []
        second = copy_remote_to_remote(
            self.session, ["/source"], "/target",
            resolve_conflict=lambda info: prompts.append(info) or ConflictAction.SKIP,
        )
        self.assertEqual(second.skipped, 1)
        self.assertEqual(prompts[0].target_path, "/target/source/note.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        with self.assertRaises(ValueError):
            copy_remote_to_remote(self.session, ["/source"], "/source")

    def test_cross_session_copy_preserves_source_and_asks_before_overwrite(self):
        other_root = Path(self.temporary.name) / "other"
        other_root.mkdir()
        (self.remote / "data").mkdir()
        (self.remote / "data" / "result.txt").write_text("new", encoding="utf-8")
        target_session = SimpleNamespace(sftp=DiskBackedSFTP(other_root))

        first = copy_remote_between_sessions(self.session, target_session, ["/data"], "/")
        self.assertEqual((first.files, first.directories), (1, 1))
        self.assertEqual((other_root / "data" / "result.txt").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.remote / "data" / "result.txt").exists())

        (self.remote / "data" / "result.txt").write_text("changed", encoding="utf-8")
        skipped = copy_remote_between_sessions(
            self.session, target_session, ["/data"], "/",
            resolve_conflict=lambda _info: ConflictAction.SKIP,
        )
        self.assertEqual(skipped.skipped, 1)
        self.assertEqual((other_root / "data" / "result.txt").read_text(encoding="utf-8"), "new")

    def test_upload_overwrite_and_skip_show_both_file_details(self):
        source = self.local / "notes.txt"
        source.write_text("new", encoding="utf-8")
        target = self.remote / "notes.txt"
        target.write_text("old data", encoding="utf-8")
        prompts = []

        def overwrite(info):
            prompts.append(info)
            return ConflictAction.OVERWRITE

        result = copy_local_to_remote(self.session, [str(source)], "/", resolve_conflict=overwrite)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(result.files, 1)
        self.assertEqual(prompts[0].source_size, 3)
        self.assertEqual(prompts[0].target_size, 8)
        self.assertEqual(prompts[0].target_path, "/notes.txt")
        self.assertFalse(list(self.remote.glob(".nodebridge-*.tmp")))

        source.write_text("newer", encoding="utf-8")
        skipped = copy_local_to_remote(
            self.session, [str(source)], "/",
            resolve_conflict=lambda _info: ConflictAction.SKIP,
        )
        self.assertEqual((skipped.files, skipped.skipped), (0, 1))
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_download_overwrite_skip_and_cancel(self):
        remote = self.remote / "notes.txt"
        remote.write_text("remote", encoding="utf-8")
        local = self.download / "notes.txt"
        local.write_text("local", encoding="utf-8")
        skipped = copy_remote_to_local(
            self.session, ["/notes.txt"], self.download,
            resolve_conflict=lambda _info: ConflictAction.SKIP,
        )
        self.assertEqual((skipped.files, skipped.skipped), (0, 1))
        self.assertEqual(local.read_text(encoding="utf-8"), "local")

        cancelled = copy_remote_to_local(
            self.session, ["/notes.txt"], self.download,
            resolve_conflict=lambda _info: ConflictAction.CANCEL,
        )
        self.assertTrue(cancelled.cancelled)
        self.assertEqual(local.read_text(encoding="utf-8"), "local")

        replaced = copy_remote_to_local(
            self.session, ["/notes.txt"], self.download,
            resolve_conflict=lambda _info: ConflictAction.OVERWRITE,
        )
        self.assertEqual(replaced.files, 1)
        self.assertEqual(local.read_text(encoding="utf-8"), "remote")
        self.assertFalse(list(self.download.glob(".nodebridge-*")))

    def test_existing_directory_merges_but_conflicting_file_is_skipped(self):
        source = self.local / "data"
        source.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")
        (source / "same.txt").write_text("source", encoding="utf-8")
        target = self.remote / "data"
        target.mkdir()
        (target / "same.txt").write_text("keep", encoding="utf-8")
        result = copy_local_to_remote(
            self.session, [str(source)], "/",
            resolve_conflict=lambda _info: ConflictAction.SKIP,
        )
        self.assertEqual((result.files, result.skipped), (1, 1))
        self.assertEqual((target / "new.txt").read_text(encoding="utf-8"), "new")
        self.assertEqual((target / "same.txt").read_text(encoding="utf-8"), "keep")

    def test_failed_remote_replace_preserves_original(self):
        source = self.local / "notes.txt"
        source.write_text("new", encoding="utf-8")
        target = self.remote / "notes.txt"
        target.write_text("old", encoding="utf-8")
        self.sftp.posix_rename = lambda _source, _target: (_ for _ in ()).throw(OSError("unsupported"))
        with self.assertRaisesRegex(OSError, "unsupported"):
            copy_local_to_remote(
                self.session, [str(source)], "/",
                resolve_conflict=lambda _info: ConflictAction.OVERWRITE,
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertFalse(list(self.remote.glob(".nodebridge-*.tmp")))

    def test_cached_remote_copy_shows_remote_source_path(self):
        cached = self.local / "notes.txt"
        cached.write_text("new", encoding="utf-8")
        target = self.download / "notes.txt"
        target.write_text("old", encoding="utf-8")
        prompts = []
        result = copy_local_to_local(
            [str(cached)], self.download,
            resolve_conflict=lambda info: prompts.append(info) or ConflictAction.SKIP,
            source_labels={"notes.txt": "/home/alice/notes.txt"},
        )
        self.assertEqual(result.skipped, 1)
        self.assertEqual(prompts[0].source_path, "/home/alice/notes.txt")

    def test_changed_target_after_confirmation_is_not_overwritten(self):
        source = self.local / "notes.txt"
        source.write_text("new", encoding="utf-8")
        target = self.remote / "notes.txt"
        target.write_text("old", encoding="utf-8")

        def change_target(_info):
            target.write_text("changed while transferring", encoding="utf-8")
            return ConflictAction.OVERWRITE

        with self.assertRaisesRegex(FileExistsError, "确认后发生变化"):
            copy_local_to_remote(self.session, [str(source)], "/", resolve_conflict=change_target)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed while transferring")
        self.assertFalse(list(self.remote.glob(".nodebridge-*.tmp")))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_is_rejected_before_any_copy(self):
        folder = self.local / "data"
        folder.mkdir()
        (folder / "file.txt").write_text("x", encoding="utf-8")
        try:
            (folder / "link").symlink_to(folder / "file.txt")
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            copy_local_to_remote(self.session, [str(folder)], "/")
        self.assertFalse((self.remote / "data").exists())


if __name__ == "__main__":
    unittest.main()
