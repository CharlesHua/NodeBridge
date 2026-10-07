import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import paramiko

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDir, QPointF, QSettings, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from nodebridge.remote import DirectoryListing, NodeConfig, NodeDiscovery, RemoteEntry
from nodebridge.batch_dialog import BatchDialog
from nodebridge.batch import BatchResult
from nodebridge.sites import Site
from nodebridge.transfer import ConflictAction, ConflictInfo, CopyResult
from nodebridge.window import MainWindow, PATH_ROLE
from nodebridge.worker_browser import WorkerBrowser


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

    def test_file_operation_log_updates_success_and_failure(self):
        self.window._run(
            lambda: CopyResult(1, 0, 4), lambda _result: None, "复制中",
            log_action="本地复制：one.txt → target",
            log_result=self.window._copy_log_result,
        )
        self._finish_job()
        log = self.window.operation_log.toPlainText()
        self.assertIn("本地复制：one.txt → target — 成功", log)
        self.assertIn("1 个文件", log)
        self.assertNotIn("进行中", log)

        def fail():
            raise PermissionError("目标目录不可写")

        with patch("nodebridge.window.QMessageBox.warning"):
            self.window._run(fail, lambda _result: None, "删除中", log_action="SFTP 删除：/blocked")
            self._finish_job()
        self.assertIn("SFTP 删除：/blocked — 失败：PermissionError: 目标目录不可写", self.window.operation_log.toPlainText())

    def test_copy_log_marks_skips_and_cancelled(self):
        self.assertEqual(self.window._copy_log_result(CopyResult(1, 0, 4, skipped=1))[0], "部分完成")
        self.assertEqual(self.window._copy_log_result(CopyResult(0, 0, 0, cancelled=True))[0], "取消")

    def test_batch_log_keeps_node_error(self):
        self.window._batch_mode_label = "并行删除"
        self.window._show_batch_result(BatchResult("cft02", error="PermissionError: 无权删除"))
        self.assertEqual(self.window.batch_results.rowCount(), 1)
        self.assertEqual(self.window.operation_log.toPlainText(), "")
        state, detail = self.window._batch_log_result(([BatchResult("cft02", error="无权删除")], None, {}))
        self.assertEqual(state, "失败")
        self.assertIn("cft02：无权删除", detail)

    def test_batch_log_groups_identical_node_errors(self):
        results = [
            BatchResult("cft02", error="FileNotFoundError: missing"),
            BatchResult("cft03", error="FileNotFoundError: missing"),
            BatchResult("cft04", deleted=1),
            BatchResult("cft05", error="PermissionError: denied"),
        ]
        state, detail = self.window._batch_log_result((results, None, {}))
        self.assertEqual(state, "失败")
        self.assertIn("1/4 个节点成功", detail)
        self.assertIn("cft02、cft03：FileNotFoundError: missing", detail)
        self.assertEqual(detail.count("FileNotFoundError: missing"), 1)
        self.assertIn("cft05：PermissionError: denied", detail)

    def test_batch_log_compacts_consecutive_failed_nodes(self):
        results = [BatchResult(f"cft{i:02d}", error="FileNotFoundError: missing") for i in range(2, 6)]
        state, detail = self.window._batch_log_result((results, None, {}))
        self.assertEqual(state, "失败")
        self.assertIn("cft02..05：FileNotFoundError: missing", detail)

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

    def test_local_directory_restores_even_when_pane_starts_hidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            child = Path(temporary) / "child"
            child.mkdir()
            self.assertTrue(self.window.local.navigate(str(child)))
            self.window.show_local_action.setChecked(False)
            reopened = MainWindow(QSettings(self.settings_path, QSettings.Format.IniFormat))
            self.assertFalse(reopened.show_local_action.isChecked())
            self.assertEqual(Path(reopened.local.current_path), child.resolve())
            reopened.show_local_action.setChecked(True)
            self.assertEqual(Path(reopened.local.current_path), child.resolve())
            reopened.close()

    def test_missing_saved_local_directory_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertTrue(self.window.local.navigate(temporary))
        reopened = MainWindow(QSettings(self.settings_path, QSettings.Format.IniFormat))
        self.assertEqual(Path(reopened.local.current_path), Path(QDir.homePath()).resolve())
        reopened.close()

    def test_file_list_headers_use_the_same_chinese_labels(self):
        self.assertEqual(self.window.jump_title.text(), "直连节点 · 尚未连接")
        self.assertEqual(self.window.jump_button.text(), "添加间接节点…")
        expected = ["文件名", "文件大小", "文件类型", "最后修改"]
        local = self.window.local.file_model
        self.assertEqual([
            local.headerData(column, Qt.Orientation.Horizontal)
            for column in range(4)
        ], expected)
        self.assertEqual([
            self.window.table.horizontalHeaderItem(column).text()
            for column in range(4)
        ], expected)
        self.assertEqual([
            self.window.worker_browser.table.horizontalHeaderItem(column).text()
            for column in range(3)
        ], expected[:3])

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

    def test_view_menu_remembers_log_visibility_and_splitter_is_resizable(self):
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.show_log_action.isChecked())
        self.assertFalse(self.window.log_panel.isHidden())
        self.assertEqual(self.window.workspace_splitter.orientation(), Qt.Orientation.Vertical)

        self.window.workspace_splitter.setSizes([480, 220])
        self.app.processEvents()
        sizes = self.window.workspace_splitter.sizes()
        self.assertGreater(sizes[0], 0)
        self.assertGreater(sizes[1], 0)
        self.window.workspace_splitter.setSizes([330, 370])
        self.app.processEvents()
        self.assertNotEqual(self.window.workspace_splitter.sizes()[1], sizes[1])

        self.window.show_log_action.setChecked(False)
        self.assertTrue(self.window.log_panel.isHidden())
        self.window._settings.sync()
        reopened = MainWindow(QSettings(self.settings_path, QSettings.Format.IniFormat))
        self.assertFalse(reopened.show_log_action.isChecked())
        self.assertTrue(reopened.log_panel.isHidden())
        reopened.show_log_action.setChecked(True)
        self.assertFalse(reopened.log_panel.isHidden())
        reopened.close()

    def test_worker_browser_connects_alias_without_replacing_jump(self):
        jump = Mock()
        jump.config.host = "jump.example.invalid"
        self.window._session = jump
        self.window.worker_browser.path_edit.setText("/shared")
        self.window.worker_browser.set_aliases(["node02"])
        self.window.worker_browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        worker = Mock()
        worker.list_directory.return_value = DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 12, None),
        ))
        self.window._update_controls()
        with patch("nodebridge.window.RemoteSession.connect_alias", return_value=worker) as connect:
            self.window.worker_browser.connect_button.click()
            self._finish_job()
        connect.assert_called_once_with("node02", jump)
        self.assertIs(self.window._session, jump)
        self.assertIs(self.window._workers["node02"], worker)
        self.assertEqual(self.window.worker_browser.table.item(0, 0).text(), "input.py")
        self.assertEqual(self.window.worker_browser.checked_aliases(), [])

    def test_file_and_terminal_views_show_connected_nodes(self):
        self.assertEqual([self.window.workspace_tabs.tabText(index) for index in range(2)],
                         ["文件", "终端"])
        self.assertIs(self.window.workspace_tabs.widget(0), self.window.file_view)
        self.assertIs(self.window.workspace_tabs.widget(1), self.window.terminal_workspace)
        self.window._session = Mock(config=NodeConfig("cft01", 22, "alice"))
        self.window._workers["cft02"] = Mock()
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        self.assertEqual(
            [terminal.node_tree.topLevelItem(index).text(0) for index in range(2)],
            ["cft01", "cft02"],
        )
        self.window.workspace_tabs.setCurrentIndex(1)
        self.assertIs(self.window.workspace_tabs.currentWidget(), terminal)
        self.window._workers.clear()
        self.window._sync_terminal_nodes()
        self.assertEqual(terminal.node_tree.topLevelItemCount(), 1)

    def test_terminal_connect_sends_command_and_interrupt_and_can_close(self):
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        session = Mock(config=NodeConfig("cft01", 22, "alice"))
        session.open_shell.return_value = channel
        self.window._session = session
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace

        terminal.new_terminal_button.click()
        self._finish_job()
        session.open_shell.assert_called_once_with()
        self.assertEqual(terminal.node_tree.topLevelItem(0).childCount(), 1)
        self.assertEqual(terminal.terminal_tabs.count(), 1)
        self.assertEqual(terminal._combined_views, {})
        pane = terminal.terminal_tabs.widget(0)
        channel.recv_ready.return_value = True
        channel.recv.return_value = b"shell ready\r\n"
        self.window._terminal_manager._poll()
        channel.recv_ready.return_value = False
        self.assertIn("shell ready", pane.output.toPlainText())
        QTest.keyClicks(pane.output, "echo test")
        QTest.keyClick(pane.output, Qt.Key.Key_Return)
        session_id = next(iter(self.window._terminal_manager._channels))
        self.window._terminal_manager._pending_send[session_id].result(timeout=2)
        self.assertEqual([call.args[0] for call in channel.sendall.call_args_list], [
            *(character.encode() for character in "echo test"), b"\r",
        ])
        channel.recv_ready.return_value = True
        channel.recv.return_value = b"\x1b[1;32mcolored\x1b[0m normal\r\n"
        self.window._terminal_manager._poll()
        channel.recv_ready.return_value = False
        colored = pane.output.document().find("colored")
        self.assertEqual(colored.charFormat().foreground().color().name(), "#00e500")
        normal = pane.output.document().find("normal")
        self.assertEqual(normal.charFormat().foreground().color().name(), "#e1eaf4")
        QTest.keyClick(pane.output, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.window._terminal_manager._pending_send[session_id].result(timeout=2)
        channel.sendall.assert_called_with(b"\x03")

        terminal.terminal_tabs.tabCloseRequested.emit(0)
        channel.close.assert_not_called()
        terminal._item_clicked(terminal.node_tree.topLevelItem(0).child(0), 0)
        self.assertEqual(terminal.terminal_tabs.count(), 1)

        terminal.disconnect_terminal_button.click()
        channel.close.assert_called_once_with()
        self.assertEqual(terminal.node_tree.topLevelItem(0).childCount(), 0)

    def test_terminal_on_unconnected_worker_uses_jump_without_worker_sftp(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        self.window._session = jump
        self.window.worker_browser.set_aliases(["cft02"])
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        terminal.node_tree.setCurrentItem(terminal.node_tree.topLevelItem(1))
        with patch("nodebridge.window.RemoteSession.open_alias_shell", return_value=channel) as open_shell:
            terminal.new_terminal_button.click()
            self._finish_job()

        open_shell.assert_called_once_with("cft02", jump)
        jump.open_shell.assert_not_called()
        self.assertEqual(self.window._workers, {})
        self.assertIn("cft02", terminal.terminal_tabs.tabText(0))
        terminal.disconnect_terminal_button.click()

    def test_terminal_batch_connect_and_disconnect_without_file_worker_connections(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        self.window._session = jump
        self.window.worker_browser.set_aliases(["cft02", "cft03"])
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        channels = {alias: Mock() for alias in ("cft02", "cft03")}
        for channel in channels.values():
            channel.recv_ready.return_value = False
            channel.exit_status_ready.return_value = False
            channel.closed = False
        for index in (1, 2):
            terminal.node_tree.topLevelItem(index).setCheckState(2, Qt.CheckState.Checked)

        with patch("nodebridge.window.RemoteSession.open_alias_shell",
                   side_effect=lambda alias, _jump: channels[alias]) as open_shell:
            terminal.connect_checked_button.click()
            self._finish_job()

        self.assertEqual(open_shell.call_count, 2)
        self.assertEqual(self.window._workers, {})
        self.assertEqual(terminal.checked_nodes(), [])
        self.assertEqual(terminal.terminal_tabs.count(), 3)
        self.assertEqual(terminal.group_selector.count(), 1)
        group = terminal._groups[terminal.active_group_id()]
        self.assertEqual(len(group.members), 2)
        self.assertEqual(set(terminal.broadcast_targets()), {"worker:cft02", "worker:cft03"})
        another = Mock(recv_ready=Mock(return_value=False),
                       exit_status_ready=Mock(return_value=False), closed=False)
        terminal.node_tree.setCurrentItem(terminal.node_tree.topLevelItem(1))
        with patch("nodebridge.window.RemoteSession.open_alias_shell", return_value=another):
            terminal.new_terminal_button.click()
            self._finish_job()
        self.assertEqual(terminal.group_selector.count(), 2)
        self.assertEqual(len(terminal._groups[terminal.active_group_id()].members), 1)
        self.assertEqual(terminal.terminal_tabs.count(), 4)
        for index in (1, 2):
            terminal.node_tree.topLevelItem(index).setCheckState(2, Qt.CheckState.Checked)
        terminal.disconnect_checked_button.click()
        self.assertEqual(terminal.checked_nodes(), [])
        self.assertEqual(terminal.terminal_tabs.count(), 0)
        self.assertEqual(terminal.group_selector.count(), 0)
        for channel in channels.values():
            channel.close.assert_called_once_with()
        another.close.assert_called_once_with()

    def test_terminal_groups_default_to_combined_and_release_extra_jump_connections(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        helper = Mock()
        aliases = [f"cft{number:02d}" for number in range(2, 10)]
        channels = {alias: Mock(recv_ready=Mock(return_value=False),
                                exit_status_ready=Mock(return_value=False), closed=False)
                    for alias in aliases}
        self.window._session = jump
        self.window.worker_browser.set_aliases(aliases)
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        with (patch("nodebridge.window.RemoteSession.connect_shell_host", return_value=helper) as connect,
              patch("nodebridge.window.RemoteSession.open_alias_shell",
                    side_effect=lambda alias, _host: channels[alias]) as open_shell):
            self.window._connect_terminals([f"worker:{alias}" for alias in aliases])
            self._finish_job()

        connect.assert_called_once_with(jump.config, None)
        self.assertEqual(sum(call.args[1] is jump for call in open_shell.call_args_list), 4)
        self.assertEqual(sum(call.args[1] is helper for call in open_shell.call_args_list), 4)
        group_id = terminal.active_group_id()
        self.assertIs(terminal.terminal_tabs.currentWidget(), terminal._combined_views[group_id])
        self.assertEqual(len(terminal.group_sessions(group_id)), 8)
        with patch("nodebridge.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Cancel):
            terminal.disconnect_group_button.click()
        self.assertEqual(len(terminal.group_sessions(group_id)), 8)
        with patch("nodebridge.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            terminal.disconnect_group_button.click()
        self.assertEqual(terminal.group_selector.count(), 0)
        self.assertEqual(terminal.terminal_tabs.count(), 0)
        helper.close.assert_called_once_with()
        for channel in channels.values():
            channel.close.assert_called_once_with()

    def test_disconnect_current_group_keeps_other_group_on_same_node(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        channels = [Mock(recv_ready=Mock(return_value=False),
                         exit_status_ready=Mock(return_value=False), closed=False)
                    for _ in range(2)]
        self.window._session = jump
        self.window.worker_browser.set_aliases(["cft02"])
        self.window._sync_terminal_nodes()
        with patch("nodebridge.window.RemoteSession.open_alias_shell", side_effect=channels):
            self.window._connect_terminals(["worker:cft02"])
            self._finish_job()
            first_group = self.window.terminal_workspace.active_group_id()
            self.window._connect_terminals(["worker:cft02"])
            self._finish_job()
        terminal = self.window.terminal_workspace
        second_group = terminal.active_group_id()
        self.assertNotEqual(first_group, second_group)
        self.assertEqual(terminal._combined_views, {})
        terminal.group_selector.setCurrentIndex(terminal.group_selector.findData(first_group))
        self.assertIs(terminal.terminal_tabs.currentWidget(),
                      terminal._views[terminal.group_sessions(first_group)[0]])
        terminal.group_selector.setCurrentIndex(terminal.group_selector.findData(second_group))
        self.assertIs(terminal.terminal_tabs.currentWidget(),
                      terminal._views[terminal.group_sessions(second_group)[0]])
        with patch("nodebridge.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            terminal.disconnect_group_button.click()
        self.assertEqual(terminal.group_selector.count(), 1)
        self.assertEqual(terminal.active_group_id(), first_group)
        self.assertEqual(len(terminal.group_sessions(first_group)), 1)
        channels[0].close.assert_not_called()
        channels[1].close.assert_called_once_with()

    def test_terminal_channel_refusal_retries_on_new_jump_connection(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        fallback = Mock()
        channel = Mock(recv_ready=Mock(return_value=False),
                       exit_status_ready=Mock(return_value=False), closed=False)
        self.window._session = jump
        self.window.worker_browser.set_aliases(["cft02"])
        self.window._sync_terminal_nodes()

        def open_shell(_alias, host):
            if host is jump:
                raise paramiko.ssh_exception.ChannelException(2, "Connect failed")
            return channel

        with (patch("nodebridge.window.RemoteSession.connect_shell_host",
                    return_value=fallback) as connect,
              patch("nodebridge.window.RemoteSession.open_alias_shell",
                    side_effect=open_shell) as open_alias):
            self.window._connect_terminals(["worker:cft02"])
            self._finish_job()
        connect.assert_called_once_with(jump.config, None)
        self.assertEqual([call.args[1] for call in open_alias.call_args_list],
                         [jump, fallback])
        self.assertEqual(self.window.terminal_workspace.group_selector.count(), 1)
        self.assertEqual(len(self.window._terminal_manager._channels), 1)
        session_id = next(iter(self.window._terminal_manager._channels))
        self.window._terminal_manager.close(session_id)
        fallback.close.assert_called_once_with()

    def test_terminal_batch_adapts_when_each_transport_allows_one_shell(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        aliases = [f"cft{number:02d}" for number in range(2, 7)]
        self.window._session = jump
        self.window.worker_browser.set_aliases(aliases)
        self.window._sync_terminal_nodes()
        used = {}
        lock = threading.Lock()

        def open_shell(_alias, host):
            with lock:
                count = used.get(id(host), 0)
                used[id(host)] = count + 1
            if count:
                raise paramiko.ssh_exception.ChannelException(2, "Connect failed")
            return Mock(recv_ready=Mock(return_value=False),
                        exit_status_ready=Mock(return_value=False), closed=False)

        with (patch("nodebridge.window.RemoteSession.connect_shell_host",
                    side_effect=lambda *_args: Mock()) as connect,
              patch("nodebridge.window.RemoteSession.open_alias_shell",
                    side_effect=open_shell)):
            self.window._connect_terminals([f"worker:{alias}" for alias in aliases])
            self._finish_job()
        self.assertEqual(len(self.window._terminal_manager._channels), 5)
        self.assertGreaterEqual(connect.call_count, 4)

    def test_file_worker_disconnect_preserves_independent_terminal(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        worker = Mock()
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        self.window._session = jump
        self.window._workers["cft02"] = worker
        self.window.worker_browser.set_connection("cft02", DirectoryListing("/shared", ()))
        self.window._sync_terminal_nodes()
        with patch("nodebridge.window.RemoteSession.open_alias_shell", return_value=channel):
            self.window.terminal_workspace.node_tree.setCurrentItem(
                self.window.terminal_workspace.node_tree.topLevelItem(1)
            )
            self.window.terminal_workspace.new_terminal_button.click()
            self._finish_job()
        self.window.worker_browser._check_all(True)
        self.window._disconnect_selected_workers()
        self._finish_job()
        self.assertEqual(self.window.worker_browser.checked_aliases(), [])
        self.assertEqual(self.window.terminal_workspace.node_tree.topLevelItemCount(), 2)
        self.assertEqual(self.window.terminal_workspace.terminal_tabs.count(), 1)
        channel.close.assert_not_called()

    def test_jump_disconnect_closes_terminal_only_worker(self):
        jump = Mock(config=NodeConfig("cft01", 22, "alice"))
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        self.window._session = jump
        self.window.worker_browser.set_aliases(["cft02"])
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        terminal.node_tree.setCurrentItem(terminal.node_tree.topLevelItem(1))
        with patch("nodebridge.window.RemoteSession.open_alias_shell", return_value=channel):
            terminal.new_terminal_button.click()
            self._finish_job()

        self.window._disconnect()
        self._finish_job()

        channel.close.assert_called_once_with()
        self.assertEqual(terminal.node_tree.topLevelItemCount(), 0)

    def test_site_disconnect_closes_its_shell_channel(self):
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        session = Mock(config=NodeConfig("cft01", 22, "alice"))
        session.open_shell.return_value = channel
        self.window._session = session
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        terminal.new_terminal_button.click()
        self._finish_job()

        self.window._disconnect()
        self._finish_job()
        channel.close.assert_called_once_with()
        self.assertEqual(terminal.node_tree.topLevelItemCount(), 0)

    def test_terminal_list_keeps_parent_jump_when_current_site_changes(self):
        parent = Mock(config=NodeConfig("cft01", 22, "alice"))
        current = Mock(config=NodeConfig("cft02", 22, "alice"))
        self.window._parents.append((parent, DirectoryListing("/shared", ())))
        self.window._session = current
        self.window._sync_terminal_nodes()
        terminal = self.window.terminal_workspace
        self.assertEqual(
            [terminal.node_tree.topLevelItem(index).text(0) for index in range(2)],
            ["cft01", "cft02"],
        )

    def test_discover_button_reports_when_ssh_config_has_no_aliases(self):
        jump = Mock()
        jump.config.host = "jump.example.invalid"
        jump.discover_work_nodes.return_value = NodeDiscovery((), (), (), "jump01")
        self.window._session = jump
        self.window._update_controls()

        with patch("nodebridge.window.QMessageBox.information") as message:
            self.window.worker_browser.discover_button.click()
            self._finish_job()

        jump.discover_work_nodes.assert_called_once_with()
        self.assertIn("未找到", self.window.worker_browser.summary.text())
        message.assert_called_once()

    def test_worker_browser_merges_names_but_marks_different_sizes(self):
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        browser.set_aliases(["node02", "node03"])
        browser.set_connection("node02", DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 12, None),
        )))
        browser.set_connection("node03", DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 13, None),
        )))
        self.assertEqual(browser.table.rowCount(), 1)
        self.assertEqual(browser.table.item(0, 3).text(), "2 / 2")
        self.assertEqual(browser.table.item(0, 4).text(), "大小不同")
        browser.mode.setCurrentIndex(1)
        self.assertEqual(browser.table.item(0, 3).text(), "node02")

    def test_worker_directory_tree_shows_folder_icons(self):
        browser = self.window.worker_browser
        browser.path_edit.setText("/lab_mem/alice")
        browser.set_connection("node02", DirectoryListing("/lab_mem/alice", (
            RemoteEntry("code", "/lab_mem/alice/code", True, False, None, None),
        )))
        root = browser.tree.topLevelItem(0)
        self.assertFalse(root.icon(0).isNull())
        self.assertFalse(root.child(0).icon(0).isNull())
        self.assertFalse(root.child(0).child(0).icon(0).isNull())
        self.assertFalse(root.child(0).child(0).child(0).icon(0).isNull())

    def test_shift_click_checks_and_unchecks_contiguous_worker_nodes(self):
        browser = WorkerBrowser()
        self.addCleanup(browser.close)
        self.addCleanup(lambda: QTest.keyRelease(browser.nodes.viewport(), Qt.Key.Key_Shift))
        browser.set_aliases([f"node{number:02d}" for number in range(2, 8)])
        browser.show()
        self.app.processEvents()

        def click_checkbox(row, modifiers=Qt.KeyboardModifier.NoModifier):
            item = browser.nodes.topLevelItem(row)
            point = browser.nodes.visualItemRect(item).center()
            point.setX(browser.nodes.visualItemRect(item).left() + 14)
            QTest.mouseClick(browser.nodes.viewport(), Qt.MouseButton.LeftButton, modifiers, pos=point)

        click_checkbox(0)
        self.assertEqual(browser.checked_aliases(), ["node02"])
        click_checkbox(4, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(browser.checked_aliases(), [
            "node02", "node03", "node04", "node05", "node06",
        ])
        click_checkbox(2)
        click_checkbox(4, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(browser.checked_aliases(), ["node02", "node03"])

    def test_drop_on_work_nodes_copies_to_chosen_scope_and_drop_directory(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        self.window._session = jump
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        listing = DirectoryListing("/shared", ())
        for alias in ("node02", "node03"):
            browser.set_connection(alias, listing)
            worker = Mock()
            worker.list_directory.return_value = listing
            self.window._workers[alias] = worker
        browser._check_all(True)

        with patch.object(self.window, "_choose_worker_scope", return_value=["node02", "node03"]), \
             patch("nodebridge.window.copy_local_to_remote", return_value=CopyResult(0, 1, 5)) as copy:
            browser.table.localPathsDropped.emit(["C:/temp/project"], "/shared/output")
            self._finish_job()

        self.assertEqual(copy.call_count, 2)
        self.assertEqual({call.args[0] for call in copy.call_args_list}, set(self.window._workers.values()))
        self.assertTrue(all(call.args[1] == ["C:/temp/project"] for call in copy.call_args_list))
        self.assertTrue(all(call.args[2] == "/shared/output" for call in copy.call_args_list))
        log = self.window.operation_log.toPlainText()
        self.assertIn("并行复制（node02、node03）", log)
        self.assertNotIn("Shell 参考", log)
        self.assertFalse(hasattr(self.window, "operations_button"))
        self.assertFalse(hasattr(browser, "batch_button"))

    def test_worker_scope_dialog_targets_connected_nodes_even_if_unchecked(self):
        browser = self.window.worker_browser
        browser.set_aliases(["node04"])
        for alias in ("node02", "node03"):
            browser.set_connection(alias, DirectoryListing("/shared", ()))
            self.window._workers[alias] = Mock()
        browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(browser.checked_aliases(), ["node02"])

        def choose_multiple(dialog):
            self.assertIn("已连接的 2 个节点", dialog.text())
            next(button for button in dialog.buttons() if button.text().startswith("已连接的")).click()
            return 0

        with patch("nodebridge.window.QMessageBox.exec", new=choose_multiple):
            self.assertEqual(self.window._choose_worker_scope("复制"), ["node02", "node03"])

    def test_jump_drop_on_work_nodes_uses_jump_session(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        jump.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._session = jump
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        browser.set_connection("node02", DirectoryListing("/shared", ()))
        worker = Mock()
        worker.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._workers["node02"] = worker
        helper = Mock()

        with patch("nodebridge.window.RemoteSession.connect", return_value=helper), \
             patch("nodebridge.window.copy_remote_between_sessions", return_value=CopyResult(1, 0, 5)) as copy:
            browser.table.remotePathsDropped.emit(str(id(jump)), ["/shared/input.py"], "/shared/output")
            self._finish_job()

        copy.assert_called_once()
        self.assertIs(copy.call_args.args[0], helper)
        self.assertIs(copy.call_args.args[1], worker)
        self.assertEqual(copy.call_args.args[2:4], (["/shared/input.py"], "/shared/output"))

    def test_collect_workers_to_local_uses_suffix_and_parallel_results(self):
        jump = Mock()
        self.window._session = jump
        for alias in ("node02", "node03"):
            worker = Mock()
            worker.sftp.lstat.return_value.st_mode = stat.S_IFREG
            self.window._workers[alias] = worker
            self.window.worker_browser.set_connection(alias, DirectoryListing("/shared", ()))
        with tempfile.TemporaryDirectory() as destination, \
             patch.object(self.window, "_choose_worker_scope", return_value=["node02", "node03"]), \
             patch("nodebridge.window.copy_remote_to_local", return_value=CopyResult(1, 0, 4)) as copy:
            self.window._copy_remote_to_local(
                self.window.worker_browser.drag_token, ["/shared/result.txt"], destination, [],
            )
            self._finish_job()
        self.assertEqual(copy.call_count, 2)
        self.assertEqual({call.kwargs["root_suffix"] for call in copy.call_args_list}, {"node02", "node03"})
        self.assertEqual(self.window.batch_results.rowCount(), 2)
        self.assertIn("并行汇集（node02、node03）", self.window.operation_log.toPlainText())

    def test_collect_workers_to_jump_uses_independent_jump_sftp(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        jump.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._session = jump
        worker = Mock()
        worker.sftp.lstat.return_value.st_mode = stat.S_IFDIR
        self.window._workers["node02"] = worker
        self.window.worker_browser.set_connection("node02", DirectoryListing("/shared", ()))
        helper = Mock()
        with patch("nodebridge.window.RemoteSession.connect", return_value=helper), \
             patch("nodebridge.window.copy_remote_between_sessions", return_value=CopyResult(1, 1, 4)) as copy:
            self.window._collect_workers_to_jump(
                self.window.worker_browser.drag_token, ["/shared/task"], "/shared",
            )
            self._finish_job()
        copy.assert_called_once()
        self.assertIs(copy.call_args.args[0], worker)
        self.assertIs(copy.call_args.args[1], helper)
        self.assertEqual(copy.call_args.args[2:4], (["/shared/task"], "/shared"))
        self.assertEqual(copy.call_args.kwargs["root_suffix"], "node02")
        helper.close.assert_called_once()

    def test_collect_reports_missing_source_on_one_node_without_blocking_others(self):
        self.window._session = Mock()
        good = Mock()
        good.sftp.lstat.return_value.st_mode = stat.S_IFREG
        missing = Mock()
        missing.sftp.lstat.side_effect = FileNotFoundError("missing on node03")
        self.window._workers.update({"node02": good, "node03": missing})
        with tempfile.TemporaryDirectory() as destination, \
             patch.object(self.window, "_choose_worker_scope", return_value=["node02", "node03"]), \
             patch("nodebridge.window.copy_remote_to_local", return_value=CopyResult(1, 0, 4)) as copy:
            self.window._copy_remote_to_local(
                self.window.worker_browser.drag_token, ["/shared/result.txt"], destination, [],
            )
            self._finish_job()
        copy.assert_called_once()
        self.assertIn("node03：FileNotFoundError", self.window.operation_log.toPlainText())
        self.assertEqual(self.window.batch_results.rowCount(), 2)

    def test_worker_drag_asks_scope_after_local_destination_is_chosen(self):
        self.window._session = Mock()
        worker = Mock()
        worker.sftp.lstat.return_value.st_mode = stat.S_IFREG
        self.window._workers["node02"] = worker
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        browser.set_connection("node02", DirectoryListing("/shared", (
            RemoteEntry("result.txt", "/shared/result.txt", False, False, 5, None),
        )))
        browser.table.selectRow(0)
        with tempfile.TemporaryDirectory() as destination, \
             patch.object(self.window, "_choose_worker_scope", return_value=["node02"]) as scope, \
             patch("nodebridge.window.copy_remote_to_local", return_value=CopyResult(1, 0, 5)) as copy, \
             patch("nodebridge.worker_browser.QDrag") as drag_type:
            browser.table.startDrag(Qt.DropAction.CopyAction)
            scope.assert_not_called()
            copy.assert_not_called()
            mime = drag_type.return_value.setMimeData.call_args.args[0]
            self.window.local.navigate(destination)
            event = QDropEvent(
                QPointF(999, 999), Qt.DropAction.CopyAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            self.window.local_table.dropEvent(event)
            self.assertTrue(event.isAccepted())
            scope.assert_not_called()
            self.app.processEvents()
            scope.assert_called_once_with("汇集复制")
            self._finish_job()
            copy.assert_called_once()
            self.assertTrue(Path(copy.call_args.args[2]).samefile(destination))

    def test_worker_delete_uses_selected_names_on_chosen_nodes(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        self.window._session = jump
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        listing = DirectoryListing("/shared", (
            RemoteEntry("scratch.txt", "/shared/scratch.txt", False, False, 5, None),
        ))
        for alias in ("node02", "node03"):
            browser.set_connection(alias, listing)
            worker = Mock()
            worker.list_directory.return_value = listing
            self.window._workers[alias] = worker
        browser._check_all(True)
        browser.table.selectRow(0)
        self.assertEqual(browser.selected_names(), ["scratch.txt"])

        with patch.object(self.window, "_choose_worker_scope", return_value=["node02", "node03"]), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.delete_remote", return_value=1) as delete:
            self.window._delete_worker_selection()
            self._finish_job()

        self.assertEqual(delete.call_count, 2)
        self.assertTrue(all(call.args[1] == ["/shared/scratch.txt"] for call in delete.call_args_list))
        self.assertIn("并行删除（node02、node03）", self.window.operation_log.toPlainText())
        self.assertNotIn("Shell 参考", self.window.operation_log.toPlainText())

    def test_single_worker_move_keeps_jump_source_if_copy_skips(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        jump.list_directory.return_value = DirectoryListing("/shared", ())
        workers = {alias: Mock() for alias in ("node02", "node03")}
        for worker in workers.values():
            worker.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._session = jump
        self.window._workers.update(workers)
        self.window._show_listing(DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 5, None),
        )))
        self.window.table.selectRow(0)
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        browser.set_aliases(list(workers))
        browser.render()
        browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)

        def accept_move(dialog):
            dialog.move.setChecked(True)
            return 1

        with patch.object(BatchDialog, "exec", new=accept_move), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.source_snapshot", return_value={"source": (0, 5, 1)}), \
             patch("nodebridge.window.RemoteSession.connect", return_value=Mock()), \
             patch("nodebridge.window.copy_remote_between_sessions", return_value=CopyResult(0, 0, 0, skipped=1)), \
             patch("nodebridge.window.delete_remote") as delete:
            self.window._batch_operation()
            self._finish_job()
            self.app.processEvents()

        delete.assert_not_called()
        self.assertEqual(self.window.batch_results.rowCount(), 1)
        self.assertIn("0/1", self.window.status.text())

    def test_single_worker_move_deletes_jump_source_after_copy(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        jump.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._session = jump
        self.window._show_listing(DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 5, None),
        )))
        self.window.table.selectRow(0)
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        browser.set_aliases(["node02", "node03"])
        browser.render()
        browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        for alias in ("node02", "node03"):
            worker = Mock()
            worker.list_directory.return_value = DirectoryListing("/shared", ())
            self.window._workers[alias] = worker

        def accept_move(dialog):
            dialog.move.setChecked(True)
            return 1

        with patch.object(BatchDialog, "exec", new=accept_move), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.source_snapshot", return_value={"source": (0, 5, 1)}), \
             patch("nodebridge.window.RemoteSession.connect", return_value=Mock()), \
             patch("nodebridge.window.copy_remote_between_sessions", return_value=CopyResult(1, 0, 5)), \
             patch("nodebridge.window.delete_remote", return_value=1) as delete:
            self.window._batch_operation()
            self._finish_job()
            self.app.processEvents()

        delete.assert_called_once_with(jump, ["/shared/input.py"])
        self.assertEqual(self.window.batch_results.rowCount(), 1)
        self.assertNotIn("Shell 参考", self.window.operation_log.toPlainText())

    def test_batch_delete_requires_confirmation_and_targets_checked_nodes(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        self.window._session = jump
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        listing = DirectoryListing("/shared", (
            RemoteEntry("scratch.txt", "/shared/scratch.txt", False, False, 5, None),
        ))
        browser.set_aliases(["node02", "node03"])
        browser.set_connection("node02", listing)
        browser.set_connection("node03", listing)
        browser.table.selectRow(0)
        browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        for alias in ("node02", "node03"):
            worker = Mock()
            worker.list_directory.return_value = listing
            self.window._workers[alias] = worker

        def choose_delete(dialog):
            dialog.mode.setCurrentIndex(7)
            return 1

        with patch.object(BatchDialog, "exec", new=choose_delete), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch("nodebridge.window.delete_remote") as delete:
            self.window._batch_operation()
        delete.assert_not_called()
        self.assertEqual(self.window.batch_results.rowCount(), 0)

        with patch.object(BatchDialog, "exec", new=choose_delete), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.delete_remote", return_value=1) as delete:
            self.window._batch_operation()
            self._finish_job()
        self.assertEqual(delete.call_count, 1)
        self.assertEqual(delete.call_args.args[1], ["/shared/scratch.txt"])

    def test_local_to_jump_move_uses_single_target_and_trashes_source_after_copy(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        jump.list_directory.return_value = DirectoryListing("/shared", ())
        self.window._session = jump
        self.window._path = "/shared"

        def choose_move(dialog):
            dialog.mode.setCurrentIndex(2)
            dialog.move.setChecked(True)
            return 1

        with patch.object(self.window, "_local_selected_paths", return_value=["C:/temp/input.py"]), \
             patch.object(BatchDialog, "exec", new=choose_move), \
             patch("nodebridge.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("nodebridge.window.source_snapshot", return_value={"source": (0, 5, 1)}), \
             patch("nodebridge.window.copy_local_to_remote", return_value=CopyResult(1, 0, 5)) as copy, \
             patch("nodebridge.window.trash_local", return_value=1) as trash:
            self.window._batch_operation()
            self._finish_job()

        self.assertIs(copy.call_args.args[0], jump)
        trash.assert_called_once_with(["C:/temp/input.py"])

    def test_single_worker_can_copy_to_another_worker(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        self.window._session = jump
        browser = self.window.worker_browser
        browser.path_edit.setText("/shared")
        listing = DirectoryListing("/shared", (
            RemoteEntry("input.py", "/shared/input.py", False, False, 5, None),
        ))
        browser.set_aliases(["node02", "node03"])
        browser.set_connection("node02", listing)
        browser.set_connection("node03", listing)
        browser.nodes.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        browser.table.selectRow(0)
        self.assertEqual(browser.selected_names(), ["input.py"])
        for alias in ("node02", "node03"):
            worker = Mock()
            worker.list_directory.return_value = listing
            self.window._workers[alias] = worker

        def choose_copy(dialog):
            dialog.mode.setCurrentIndex(6)
            return 1

        with patch.object(BatchDialog, "exec", new=choose_copy), \
             patch("nodebridge.window.copy_remote_between_sessions", return_value=CopyResult(1, 0, 5)) as copy:
            self.window._batch_operation()
            self._finish_job()

        self.assertIs(copy.call_args.args[0], self.window._workers["node02"])
        self.assertIs(copy.call_args.args[1], self.window._workers["node03"])
        self.assertEqual(copy.call_args.args[2], ["/shared/input.py"])

    def test_multiple_worker_destinations_disable_move(self):
        dialog = BatchDialog(
            ["node02", "node03"], ["node02", "node03"],
            (1, 1, 0), ("/shared", "/jump", self.window.local.current_path), self.window,
        )
        self.assertEqual(dialog.operation, "jump_to_workers")
        self.assertFalse(dialog.move.isEnabled())

    def test_worker_connections_open_extra_jump_after_eight_nodes(self):
        jump = Mock()
        jump.config = NodeConfig("jump.example.invalid", 22, "alice")
        helper = Mock()
        self.window._session = jump
        self.window._jump_password = "session-only-password"
        self.window.worker_browser.path_edit.setText("/shared")
        aliases = [f"node{index:02}" for index in range(2, 11)]
        self.window.worker_browser.set_aliases(aliases)
        self.window.worker_browser._check_all(True)

        def connect_alias(alias, _host):
            worker = Mock()
            worker.list_directory.return_value = DirectoryListing("/shared", ())
            return worker

        with patch("nodebridge.window.RemoteSession.connect", return_value=helper) as connect_root, \
             patch("nodebridge.window.RemoteSession.connect_alias", side_effect=connect_alias) as connect_worker:
            self.window._connect_selected_workers()
            self._finish_job()

        connect_root.assert_called_once_with(jump.config, "session-only-password")
        self.assertEqual(len(self.window._workers), 9)
        self.assertEqual(self.window.worker_browser.checked_aliases(), [])
        self.assertEqual(sum(args.args[1] is helper for args in connect_worker.call_args_list), 1)
        self.assertEqual(len(self.window._jump_helpers), 1)

    def test_disconnecting_jump_closes_work_nodes(self):
        jump = Mock()
        jump.config.host = "jump.example.invalid"
        worker = Mock()
        self.window._session = jump
        self.window._workers["node02"] = worker
        self.window.worker_browser.set_aliases(["node02"])
        self.window.worker_browser.set_connection("node02", DirectoryListing("/shared", ()))

        self.window._disconnect()
        self._finish_job()

        worker.close.assert_called_once_with()
        jump.close.assert_called_once_with()
        self.assertEqual(self.window._workers, {})
        self.assertIsNone(self.window._session)

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
        self.assertIn("SFTP 上传", self.window.operation_log.toPlainText())
        self.assertNotIn("Shell 参考", self.window.operation_log.toPlainText())

        with patch("nodebridge.window.copy_remote_to_local", return_value=CopyResult(1, 0, 4)) as download:
            self.window._copy_remote_to_local(
                str(id(session)), ["/home/alice/notes.txt"], self.window.local.current_path, []
            )
            self._finish_job()
        download.assert_called_once()
        self.assertIs(download.call_args.args[0], session)

    def test_cached_download_uses_local_copy_without_command_log(self):
        class FakeSession:
            pass

        session = FakeSession()
        self.window._session = session
        self.window._show_listing(DirectoryListing("/home/alice", ()))
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory, "notes.txt")
            cached.write_text("example", encoding="utf-8")
            with patch("nodebridge.window.copy_local_to_local", return_value=CopyResult(1, 0, 7)) as copy, \
                 patch("nodebridge.window.copy_remote_to_local") as download:
                self.window._copy_remote_to_local(
                    str(id(session)), ["/home/alice/notes.txt"], directory, [str(cached)]
                )
                self._finish_job()
            copy.assert_called_once()
            download.assert_not_called()
            log = self.window.operation_log.toPlainText()
            self.assertIn("SFTP 下载", log)
            self.assertNotIn("Shell 参考", log)

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
