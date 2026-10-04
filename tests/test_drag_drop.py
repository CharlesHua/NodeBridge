import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from nodebridge.drag_drop import REMOTE_MIME, RemoteFileTable
from nodebridge.local_browser import LocalBrowser
from nodebridge.remote import DirectoryListing, RemoteEntry
from nodebridge.worker_browser import WorkerBrowser


class DragDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explorer_url_dropped_into_remote_file_list(self):
        table = RemoteFileTable()
        table.current_path = "/home/alice"
        received = []
        table.localPathsDropped.connect(lambda paths, target: received.append((paths, target)))
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "notes.txt"
            source.write_text("hello", encoding="utf-8")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(source))])
            event = QDropEvent(QPointF(999, 999), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            table.dropEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertEqual(Path(received[0][0][0]), source)
            self.assertEqual(received[0][1], "/home/alice")

    def test_remote_path_dropped_into_local_browser(self):
        browser = LocalBrowser()
        received = []
        browser.table.remotePathsDropped.connect(lambda *args: received.append(args))
        mime = QMimeData()
        mime.setData(REMOTE_MIME, json.dumps({
            "session": "session-1", "paths": ["/home/alice/notes.txt"],
        }).encode("utf-8"))
        event = QDropEvent(QPointF(999, 999), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        browser.table.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(received, [(
            "session-1", ["/home/alice/notes.txt"], browser.current_path, [],
        )])

    def test_remote_drag_with_staged_windows_url_uses_nodebridge_path(self):
        browser = LocalBrowser()
        received = []
        browser.table.remotePathsDropped.connect(lambda *args: received.append(args))
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary) / "notes.txt"
            staged.write_text("remote", encoding="utf-8")
            mime = QMimeData()
            mime.setData(REMOTE_MIME, json.dumps({
                "session": "session-1", "paths": ["/home/alice/notes.txt"],
            }).encode("utf-8"))
            mime.setUrls([QUrl.fromLocalFile(str(staged))])
            event = QDropEvent(QPointF(999, 999), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            browser.table.dropEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertEqual(received[0][:3], (
                "session-1", ["/home/alice/notes.txt"], browser.current_path,
            ))
            self.assertEqual(Path(received[0][3][0]), staged)

    def test_remote_drag_exports_file_url_for_windows_explorer(self):
        table = RemoteFileTable()
        table.session_token = "session-1"
        table.setRowCount(1)
        item = QTableWidgetItem("notes.txt")
        item.setData(Qt.ItemDataRole.UserRole, "/home/alice/notes.txt")
        table.setItem(0, 0, item)
        table.selectRow(0)
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "notes.txt"
            local.write_text("hello", encoding="utf-8")
            table.prepare_export = lambda _paths: [str(local)]
            with patch("nodebridge.drag_drop.QDrag") as drag_type:
                table.startDrag(Qt.DropAction.CopyAction)
            mime = drag_type.return_value.setMimeData.call_args.args[0]
            self.assertEqual(mime.urls()[0].toLocalFile().replace("/", "\\"), str(local))
            self.assertEqual(json.loads(bytes(mime.data(REMOTE_MIME)))["paths"], [
                "/home/alice/notes.txt",
            ])

    def test_worker_drop_on_folder_uses_that_directory(self):
        browser = WorkerBrowser()
        self.addCleanup(browser.close)
        browser.path_edit.setText("/shared")
        browser.set_connection("node02", DirectoryListing("/shared", (
            RemoteEntry("output", "/shared/output", True, False, None, None),
        )))
        browser.show()
        self.app.processEvents()
        received = []
        browser.table.localPathsDropped.connect(lambda *args: received.append(("local", *args)))
        browser.table.remotePathsDropped.connect(lambda *args: received.append(("remote", *args)))
        point = browser.table.visualItemRect(browser.table.item(0, 0)).center()

        local = QMimeData()
        local.setUrls([QUrl.fromLocalFile("C:/temp/input.py")])
        event = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, local,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        browser.table.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(received[-1], ("local", ["C:/temp/input.py"], "/shared/output"))

        remote = QMimeData()
        remote.setData(REMOTE_MIME, json.dumps({
            "session": "jump-1", "paths": ["/shared/input.py"],
        }).encode("utf-8"))
        remote.setUrls([QUrl.fromLocalFile("C:/temp/staged.py")])
        event = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, remote,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        browser.table.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(received[-1], ("remote", "jump-1", ["/shared/input.py"], "/shared/output"))
