import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nodebridge.file_operations import (
    create_local_directory, create_remote_directory, create_remote_directory_with_policy,
    delete_remote, remote_directory_conflict,
    rename_local, rename_remote, trash_local,
)
from tests.test_transfer import DiskBackedSFTP


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sftp = DiskBackedSFTP(self.root)
        self.session = SimpleNamespace(sftp=self.sftp)

    def test_remote_create_rename_and_recursive_delete(self):
        folder = create_remote_directory(self.session, "/", "folder")
        nested = create_remote_directory(self.session, folder, "nested")
        (self.sftp.path(nested) / "note.txt").write_text("hello", encoding="utf-8")
        renamed = rename_remote(self.session, folder, "renamed")
        self.assertTrue(self.sftp.path(renamed + "/nested/note.txt").exists())
        self.assertEqual(delete_remote(self.session, [renamed]), 3)
        self.assertFalse(self.sftp.path(renamed).exists())

    def test_remote_rename_does_not_replace_existing_file(self):
        (self.root / "one.txt").write_text("one", encoding="utf-8")
        (self.root / "two.txt").write_text("two", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            rename_remote(self.session, "/one.txt", "two.txt")
        self.assertEqual((self.root / "two.txt").read_text(encoding="utf-8"), "two")
        with self.assertRaises(ValueError):
            delete_remote(self.session, ["/"])

    def test_worker_directory_conflict_policies_preserve_existing_items(self):
        original = create_remote_directory(self.session, "/", "results")
        self.assertTrue(remote_directory_conflict(self.session, "/", "results"))
        self.assertEqual(
            create_remote_directory_with_policy(self.session, "/", "results", "skip"),
            (original, False),
        )
        second, created = create_remote_directory_with_policy(self.session, "/", "results", "number")
        self.assertEqual((second, created), ("/results (2)", True))
        third, created = create_remote_directory_with_policy(self.session, "/", "results", "number")
        self.assertEqual((third, created), ("/results (3)", True))
        self.assertTrue(self.sftp.path(original).is_dir())
        with self.assertRaises(FileExistsError):
            create_remote_directory_with_policy(self.session, "/", "results", "error")

    def test_local_create_rename_and_trash(self):
        folder = create_local_directory(str(self.root), "folder")
        renamed = rename_local(str(folder), "renamed")
        self.assertTrue(renamed.is_dir())
        with patch("nodebridge.file_operations.QFile.moveToTrash", return_value=True) as trash:
            self.assertEqual(trash_local([str(renamed)]), 1)
        trash.assert_called_once_with(str(renamed))
        with self.assertRaises(ValueError):
            create_local_directory(str(self.root), "../escape")
        with self.assertRaises(ValueError):
            create_local_directory(str(self.root), "CON.txt")

    def test_local_trash_reports_failed_move(self):
        path = str(self.root / "not-moved.txt")
        with patch("nodebridge.file_operations.QFile.moveToTrash", return_value=False) as trash:
            with self.assertRaisesRegex(OSError, "无法移入回收站"):
                trash_local([path])
        trash.assert_called_once_with(path)
