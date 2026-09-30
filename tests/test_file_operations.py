import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nodebridge.file_operations import (
    create_local_directory, create_remote_directory, delete_remote,
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

    def test_local_create_rename_and_trash(self):
        folder = create_local_directory(str(self.root), "folder")
        renamed = rename_local(str(folder), "renamed")
        self.assertTrue(renamed.is_dir())
        with patch("nodebridge.file_operations.QFile.moveToTrash", return_value=(True, "")) as trash:
            self.assertEqual(trash_local([str(renamed)]), 1)
        trash.assert_called_once_with(str(renamed))
        with self.assertRaises(ValueError):
            create_local_directory(str(self.root), "../escape")
        with self.assertRaises(ValueError):
            create_local_directory(str(self.root), "CON.txt")
