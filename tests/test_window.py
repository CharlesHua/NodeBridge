import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from nodebridge.remote import DirectoryListing, NodeConfig, RemoteEntry
from nodebridge.sites import Site
from nodebridge.transfer import ConflictAction, ConflictInfo, CopyResult
from nodebridge.window import MainWindow, PATH_ROLE


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_dir.cleanup)
        self.settings_path = str(Path(self.settings_dir.name) / "settings.ini")
        settings = QSettings(self.settings_path, QSettings.Format.IniFormat)
        self.window = MainWindow(settings)

    def tearDown(self):
        self.window._session = None
        self.window.close()

    def _finish_job(self):
        deadline = time.monotonic() + 5
        while self.window._job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.window._job)

    def test_listing_populates_tree_and_file_table(self):
        listing = DirectoryListing(
            "/home/alice",
            (
                RemoteEntry("data", "/home/alice/data", True, False, 4096, None),
                RemoteEntry("notes.txt", "/home/alice/notes.txt", False, False, 5, None),
            ),
        )

        self.window._show_listing(listing)

        self.assertEqual(self.window.path_edit.text(), "/home/alice")
        self.assertEqual(self.window.table.rowCount(), 2)
        root = self.window.tree.topLevelItem(0)
        self.assertEqual(root.data(0, PATH_ROLE), "/")
        self.assertEqual(root.child(0).data(0, PATH_ROLE), "/home")
        self.assertEqual(root.child(0).child(0).data(0, PATH_ROLE), "/home/alice")

    def test_remote_context_menu_and_clipboard_copy(self):
        class FakeSession:
            pass

        self.window._session = FakeSession()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("note.txt", "/home/alice/note.txt", False, False, 4, None),
        )))
        self.window.table.selectRow(0)
        # Qt's Windows offscreen plugin can crash on process exit if a test owns
        # the real clipboard with a custom MIME format.
        clipboard = Mock()
        with patch("nodebridge.window.QApplication.clipboard", return_value=clipboard):
            self.window._copy_remote_selection()
        from nodebridge.drag_drop import remote_payload
        self.assertEqual(remote_payload(clipboard.setMimeData.call_args.args[0])[1], [
            "/home/alice/note.txt",
        ])

    def test_remote_delete_requires_confirmation(self):
        class FakeSession:
            def list_directory(self, path):
                return DirectoryListing(path, ())

        self.window._session = FakeSession()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("note.txt", "/home/alice/note.txt", False, False, 4, None),
        )))
        self.window.table.selectRow(0)
        with patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch("nodebridge.window.delete_remote") as delete:
            self.window._delete_remote_selection()
            delete.assert_not_called()
        with patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.delete_remote", return_value=1) as delete:
            self.window._delete_remote_selection()
            self._finish_job()
            delete.assert_called_once_with(self.window._session, ["/home/alice/note.txt"])
        self.assertEqual(self.window.table.rowCount(), 0)

    def test_remote_rename_edits_filename_in_table(self):
        class FakeSession:
            def list_directory(self, path):
                return DirectoryListing(path, ())

        self.window._session = FakeSession()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("note.txt", "/home/alice/note.txt", False, False, 4, None),
        )))
        self.window._update_controls()
        self.window.show()
        self.app.processEvents()
        self.window.table.selectRow(0)
        with patch("nodebridge.window.QInputDialog.getText") as popup, patch(
            "nodebridge.window.rename_remote", return_value="/home/alice/renamed.txt"
        ) as rename:
            self.window._rename_remote_selection()
            editor = self.window.table.viewport().findChild(QLineEdit)
            self.assertIsNotNone(editor)
            self.assertEqual(editor.text(), "note.txt")
            self.assertIn(editor.selectedText(), ("note", "note.txt"))
            editor.setText("renamed.txt")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.app.processEvents()
            self._finish_job()
            popup.assert_not_called()
            rename.assert_called_once_with(
                self.window._session, "/home/alice/note.txt", "renamed.txt",
            )

    def test_selected_filename_click_starts_inline_rename(self):
        self.window._session = object()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("note.txt", "/home/alice/note.txt", False, False, 4, None),
        )))
        self.window._update_controls()
        self.window.show()
        self.app.processEvents()
        self.window.table.selectRow(0)
        point = self.window.table.visualItemRect(self.window.table.item(0, 0)).center()
        QTest.mousePress(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(self.window._rename_timer.isActive())
        QTest.mouseRelease(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.qWait(QApplication.doubleClickInterval() + 150)
        self.assertIsNotNone(self.window.table.viewport().findChild(QLineEdit))

    def test_double_click_folder_cancels_inline_rename(self):
        self.window._session = object()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("folder", "/home/alice/folder", True, False, 0, None),
        )))
        self.window._update_controls()
        self.window.show()
        self.app.processEvents()
        self.window.table.selectRow(0)
        point = self.window.table.visualItemRect(self.window.table.item(0, 0)).center()
        with patch.object(self.window, "_browse") as browse:
            QTest.mousePress(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertTrue(self.window._rename_timer.isActive())
            QTest.mouseRelease(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertFalse(self.window._rename_timer.isActive())
            browse.assert_called_once_with("/home/alice/folder")

    def test_escape_inline_rename_keeps_original_name(self):
        self.window._session = object()
        self.window._show_listing(DirectoryListing("/home/alice", (
            RemoteEntry("note.txt", "/home/alice/note.txt", False, False, 4, None),
        )))
        self.window._update_controls()
        self.window.show()
        self.window.table.selectRow(0)
        with patch("nodebridge.window.rename_remote") as rename:
            self.window._rename_remote_selection()
            editor = self.window.table.viewport().findChild(QLineEdit)
            editor.setText("changed.txt")
            QTest.keyClick(editor, Qt.Key.Key_Escape)
            self.app.processEvents()
            rename.assert_not_called()
        self.assertEqual(self.window.table.item(0, 0).text(), "note.txt")

    def test_local_rename_edits_filename_in_table(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "note.txt"
            source.write_text("hello", encoding="utf-8")
            self.window.local.navigate(temporary)
            self.window.show()
            deadline = time.monotonic() + 5
            while self.window.local.file_model.rowCount(self.window.local_table.rootIndex()) == 0 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertGreater(self.window.local.file_model.rowCount(self.window.local_table.rootIndex()), 0)
            self.window.local_table.selectRow(0)
            with patch("nodebridge.window.QInputDialog.getText") as popup:
                self.window._rename_local_selection()
                editor = self.window.local_table.viewport().findChild(QLineEdit)
                self.assertIsNotNone(editor)
                editor.setText("renamed.txt")
                QTest.keyClick(editor, Qt.Key.Key_Return)
                self.app.processEvents()
                self._finish_job()
                popup.assert_not_called()
            self.assertTrue((Path(temporary) / "renamed.txt").exists())
            self.assertFalse(source.exists())

    def test_remote_drop_into_local_pane_uses_nodebridge_conflict_dialog(self):
        class FakeSession:
            pass

        session = FakeSession()
        self.window._session = session
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "destination"
            staging = Path(temporary) / "staging"
            destination.mkdir()
            staging.mkdir()
            (destination / "note.txt").write_text("keep", encoding="utf-8")
            cached = staging / "note.txt"
            cached.write_text("remote", encoding="utf-8")
            with patch("nodebridge.window.ConflictDialog.result_choice", return_value=(
                ConflictAction.SKIP, False,
            )) as prompt:
                self.window._copy_remote_to_local(
                    str(id(session)), ["/remote/note.txt"], str(destination), [str(cached)],
                )
                self._finish_job()
            prompt.assert_called_once()
            self.assertEqual((destination / "note.txt").read_text(encoding="utf-8"), "keep")

    def test_browse_uses_background_job(self):
        class FakeSession:
            def list_directory(self, path):
                return DirectoryListing(path, ())

        self.window._session = FakeSession()
        self.window._browse("/tmp")
        self.assertIsNotNone(self.window._job)
        self._finish_job()
        self.assertEqual(self.window.path_edit.text(), "/tmp")

    def test_completion_toast_appears_at_lower_right_and_updates(self):
        self.window.show()
        self.app.processEvents()
        self.window._notify_done("上传完成：1 个文件。")
        toast = self.window.completion_toast
        self.assertTrue(toast.isVisible())
        self.assertTrue(self.window._completion_timer.isActive())
        self.assertIn("上传完成", toast.text())
        self.window.resize(900, 600)
        self.app.processEvents()
        self.assertLessEqual(toast.geometry().right(), self.window.width())
        self.assertLessEqual(toast.geometry().bottom(), self.window.height())
        self.window._notify_done("已删除 1 个远程项目。")
        self.assertIn("已删除", toast.text())
        self.window._completion_timer.stop()

    def test_local_browser_navigates_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            child = Path(temporary) / "child"
            child.mkdir()
            self.assertTrue(self.window.local.navigate(str(child)))
            self.assertEqual(self.window.local.current_path, str(child.resolve()))
            self.window.local._up()
            self.assertEqual(self.window.local.current_path, str(Path(temporary).resolve()))

    def test_view_menu_remembers_local_site_visibility(self):
        self.assertEqual([action.text() for action in self.window.menuBar().actions()], [
            "文件", "查看", "帮助",
        ])
        self.window.show_local_action.setChecked(False)
        self.assertTrue(self.window.local.isHidden())
        self.window._settings.sync()
        reopened = MainWindow(QSettings(self.settings_path, QSettings.Format.IniFormat))
        self.assertFalse(reopened.show_local_action.isChecked())
        self.assertTrue(reopened.local.isHidden())
        reopened.close()

    def test_drop_copy_jobs_use_current_remote_session(self):
        class FakeSession:
            def list_directory(self, path):
                return DirectoryListing(path, ())

        session = FakeSession()
        self.window._session = session
        self.window._show_listing(DirectoryListing("/home/alice", ()))
        with patch("nodebridge.window.copy_local_to_remote", return_value=CopyResult(1, 0, 4)) as upload:
            self.window._copy_local_to_remote(["C:/Users/alice/notes.txt"], "/home/alice")
            self._finish_job()
        upload.assert_called_once()
        self.assertIs(upload.call_args.args[0], session)
        self.assertIn("上传完成", self.window.completion_toast.text())

        with patch("nodebridge.window.copy_remote_to_local", return_value=CopyResult(1, 0, 4)) as download:
            self.window._copy_remote_to_local(
                str(id(session)), ["/home/alice/notes.txt"], self.window.local.current_path, []
            )
            self._finish_job()
        download.assert_called_once()
        self.assertIs(download.call_args.args[0], session)

    def test_remote_drag_prepares_temporary_file_without_blocking_job_state(self):
        class FakeSession:
            pass

        session = FakeSession()
        self.window._session = session
        self.window._show_listing(DirectoryListing("/home/alice", ()))

        def stage(_session, _paths, destination, _progress):
            Path(destination, "notes.txt").write_text("hello", encoding="utf-8")
            return CopyResult(1, 0, 5)

        with patch("nodebridge.window.copy_remote_to_local", side_effect=stage) as copy:
            exported = self.window._prepare_drag_export(["/home/alice/notes.txt"])
            again = self.window._prepare_drag_export(["/home/alice/notes.txt"])
        self.assertEqual(exported, again)
        self.assertEqual(Path(exported[0]).read_text(encoding="utf-8"), "hello")
        self.assertEqual(copy.call_count, 1)
        self.assertIsNone(self.window._job)

    def test_apply_to_all_conflicts_prompts_once_on_gui_thread(self):
        class FakeSession:
            def list_directory(self, path):
                return DirectoryListing(path, ())

        self.window._session = FakeSession()
        self.window._show_listing(DirectoryListing("/home/alice", ()))
        decisions = []

        def copy(_session, _paths, _destination, _progress, resolve):
            for name in ("one.txt", "two.txt"):
                decisions.append(resolve(ConflictInfo(
                    f"C:/source/{name}", 3, None, f"/home/alice/{name}", 5, None,
                )))
            return CopyResult(0, 0, 0, 2)

        with patch("nodebridge.window.copy_local_to_remote", side_effect=copy), patch(
            "nodebridge.window.ConflictDialog.result_choice",
            return_value=(ConflictAction.SKIP, True),
        ) as dialog:
            self.window._copy_local_to_remote(["C:/source/one.txt"], "/home/alice")
            self._finish_job()
        self.assertEqual(decisions, [ConflictAction.SKIP, ConflictAction.SKIP])
        dialog.assert_called_once_with()
        self.assertIn("跳过 2 个", self.window.status.text())

    def test_saved_site_can_start_remote_connection(self):
        class FakeSession:
            def home(self):
                return "/home/user"

            def list_directory(self, path):
                return DirectoryListing(path, ())

            def close(self):
                pass

        site = Site("site-id", "计算节点", "example.invalid", 22, "user")
        with patch("nodebridge.window.SiteManagerDialog") as dialog_type, patch(
            "nodebridge.window.RemoteSession.connect", return_value=FakeSession()
        ) as connect:
            dialog = dialog_type.return_value
            dialog.exec.return_value = dialog_type.DialogCode.Accepted
            dialog.selected_site = site
            dialog.connection_password = "example-password"
            self.window._open_site_manager()
            self._finish_job()

        self.assertIsNone(self.window._job)
        self.assertEqual(self.window.host.text(), "example.invalid")
        self.assertEqual(self.window.path_edit.text(), "/home/user")
        self.assertEqual(self.window.password.text(), "")
        connect.assert_called_once()

    def test_jump_connection_returns_to_previous_site(self):
        class FakeSession:
            def __init__(self, config, home_path):
                self.config = config
                self.home_path = home_path
                self.closed = False

            def home(self):
                return self.home_path

            def list_directory(self, path):
                return DirectoryListing(path, ())

            def close(self):
                self.closed = True

        jump = FakeSession(NodeConfig("jump", 22, "alice"), "/home/alice")
        target_config = NodeConfig("target", 22, "bob")
        target = FakeSession(target_config, "/home/bob")
        self.window._session = jump
        self.window._show_listing(jump.list_directory(jump.home()))

        with patch("nodebridge.window.RemoteSession.connect_via", return_value=target) as connect:
            self.window._start_connection(target_config, "secret", jump)
            self._finish_job()

        connect.assert_called_once_with(target_config, jump, "secret")
        self.assertIs(self.window._session, target)
        self.assertEqual(self.window.path_edit.text(), "/home/bob")
        self.assertEqual(self.window.disconnect_button.text(), "返回上一站点")

        second_config = NodeConfig("second", 22, "carol")
        second = FakeSession(second_config, "/home/carol")
        with patch("nodebridge.window.RemoteSession.connect_via", return_value=second):
            self.window._start_connection(second_config, None, target)
            self._finish_job()
        self.assertEqual(len(self.window._parents), 2)

        self.window._disconnect()
        self._finish_job()
        self.assertTrue(second.closed)
        self.assertIs(self.window._session, target)
        self.assertEqual(self.window.path_edit.text(), "/home/bob")

        self.window._disconnect()
        self._finish_job()
        self.assertTrue(target.closed)
        self.assertIs(self.window._session, jump)
        self.assertEqual(self.window.path_edit.text(), "/home/alice")

    def test_failed_jump_leaves_current_site_visible(self):
        class FakeSession:
            config = NodeConfig("jump", 22, "alice")

        jump = FakeSession()
        self.window._session = jump
        self.window._show_listing(DirectoryListing("/home/alice", ()))
        with patch("nodebridge.window.RemoteSession.connect_via", side_effect=OSError("unreachable")), patch(
            "nodebridge.window.QMessageBox.warning"
        ):
            self.window._start_connection(NodeConfig("target", 22, "bob"), None, jump)
            self._finish_job()

        self.assertIs(self.window._session, jump)
        self.assertEqual(self.window.path_edit.text(), "/home/alice")
        self.assertEqual(self.window._parents, [])

    def test_alias_jump_only_needs_alias_and_can_return(self):
        class FakeSession:
            def __init__(self, host, home_path):
                self.config = NodeConfig(host, 22, "alice")
                self.home_path = home_path
                self.closed = False

            def home(self):
                return self.home_path

            def list_directory(self, path):
                return DirectoryListing(path, ())

            def close(self):
                self.closed = True

        jump = FakeSession("jump", "/home/alice")
        target = FakeSession("cft02", "/home/alice/work")
        self.window._session = jump
        self.window._show_listing(jump.list_directory(jump.home()))
        with patch("nodebridge.window.QInputDialog.getText", return_value=("cft02", True)), patch(
            "nodebridge.window.RemoteSession.connect_alias", return_value=target
        ) as connect:
            self.window._prompt_alias()
            self._finish_job()
        connect.assert_called_once_with("cft02", jump)
        self.assertIs(self.window._session, target)
        self.assertEqual(self.window.path_edit.text(), "/home/alice/work")
        self.window._disconnect()
        self._finish_job()
        self.assertTrue(target.closed)
        self.assertIs(self.window._session, jump)
        self.assertEqual(self.window.path_edit.text(), "/home/alice")


if __name__ == "__main__":
    unittest.main()
