import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from nodebridge.recovery import ConnectionRecovery, KEY, snapshot
from nodebridge.remote import NodeConfig
from nodebridge.window import MainWindow


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(self.temp.name + "/settings.ini", QSettings.Format.IniFormat)
        self.w = MainWindow(self.settings, log_path=Path(self.temp.name) / "log")
        self.r = ConnectionRecovery(self.w)
        self.r.timer.stop()
        self.r.started = True

    def tearDown(self):
        self.w._session = None
        self.w._parents.clear()
        self.w._workers.clear()
        self.w._terminal_manager._channels.clear()
        self.w.close()
        self.temp.cleanup()

    def site(self):
        self.w._session = SimpleNamespace(config=NodeConfig("server", 22, "user"))
        return self.w._session

    def test_checkpoint_contains_only_live_sessions_without_secrets(self):
        site = self.site()
        self.w._jump_password = "never-save-this"
        workspace = self.w.terminal_workspace
        workspace._groups = {"g": SimpleNamespace(name="my group")}
        workspace._sessions = {
            "a": SimpleNamespace(node_id=f"primary:{id(site)}", group_id="g"),
            "b": SimpleNamespace(node_id="worker:cft02", group_id="g"),
            "detached": SimpleNamespace(node_id="worker:cft03", group_id="g")}
        self.w._persistent_sessions = {"b": "tmux-live", "detached": "tmux-detached"}
        with patch.object(self.w._terminal_manager, "has_session", side_effect=lambda s: s in {"a", "b"}):
            self.r.checkpoint()
        saved = self.settings.value(KEY)
        self.assertNotIn("never-save-this", saved)
        self.assertNotIn("tmux-detached", saved)
        self.assertEqual(len(json.loads(saved)["terminals"]), 2)

    def test_normal_shutdown_does_not_replace_snapshot_with_empty_state(self):
        self.site()
        self.r.checkpoint()
        saved = self.settings.value(KEY)
        self.r.exiting = True
        self.w._session = None
        self.r.checkpoint()
        self.assertEqual(self.settings.value(KEY), saved)

    def test_decline_recovery_overrides_startup_auto_connect(self):
        self.site()
        self.r.checkpoint()
        self.w._session = None
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.w, "connect_startup_site") as auto:
            self.r.start()
        auto.assert_not_called()
        self.assertEqual(json.loads(self.settings.value(KEY))["sites"], [])

    def test_timer_cannot_connect_while_startup_confirmation_is_open(self):
        self.site()
        self.r.checkpoint()
        self.w._session = None

        def answer(*args):
            self.r.tick()
            return QMessageBox.StandardButton.No

        with patch.object(QMessageBox, "question", side_effect=answer), \
                patch.object(self.r, "advance") as advance:
            self.r.start()
        advance.assert_not_called()

    def test_crash_checkpoint_offers_recovery_and_does_not_replay_commands(self):
        self.site()
        self.r.checkpoint()
        self.w._session = None
        fresh = ConnectionRecovery(self.w)
        fresh.timer.stop()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as prompt:
            fresh.start()
        prompt.assert_called_once()
        with patch.object(fresh, "password", return_value=(None, True)), \
                patch.object(self.w, "_start_connection") as connect:
            fresh.tick()
        self.assertEqual(connect.call_args.args[0].host, "server")
        self.assertIsNone(connect.call_args.args[2])
        # A failed root prevents restoring any dependent terminals.
        fresh.tick()
        self.assertIsNone(fresh.plan)

    def test_no_live_connections_does_not_prompt_for_detached_tasks(self):
        self.w._tmux_records = {"detached": {"state": "可恢复"}}
        with patch("nodebridge.recovery.QMessageBox") as dialog:
            self.assertTrue(self.r.allow_close())
        dialog.assert_not_called()

    def test_restore_workers_then_multiple_terminals_preserves_group(self):
        self.site()
        data = snapshot(self.w)
        data["workers"] = ["cft02"]
        data["terminals"] = [dict(worker="cft02", group="g", label="experiment") for _ in range(2)]
        self.r.plan = data
        self.r.step = self.r.expected_sites = 1
        with patch.object(self.w, "_connect_selected_workers") as workers:
            self.r.advance()
        workers.assert_called_once()
        self.assertEqual(self.w.worker_browser.target_aliases(), ["cft02"])
        with patch.object(self.w, "_connect_terminals") as connect:
            self.r.advance()
            self.r.advance()
        self.assertEqual(connect.call_count, 2)
        groups = [call.kwargs["recovery_group"] for call in connect.call_args_list]
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(self.w.terminal_workspace._groups[groups[0]].name, "experiment")

    def test_attached_tmux_uses_restore_instead_of_create(self):
        self.site()
        self.w._tmux_records = {"attached": {"name": "original"}, "detached": {"name": "other"}}
        self.r.plan = dict(snapshot(self.w), terminals=[{"task": "attached"}])
        self.r.step = 2
        self.r.expected_sites = 1
        with patch.object(self.w, "_restore_tmux_task") as restore:
            self.r.advance()
        restore.assert_called_once_with("attached")

    def test_minimize_keeps_connection_and_checkpoint(self):
        site = self.site()
        box = Mock()
        quit_button, minimize, cancel = object(), object(), object()
        box.addButton.side_effect = [quit_button, minimize, cancel]
        box.clickedButton.return_value = minimize
        # Preserve Qt enums while substituting only the dialog constructor.
        with patch("nodebridge.recovery.QMessageBox", wraps=QMessageBox) as factory, \
                patch.object(self.w, "showMinimized") as minimized:
            factory.return_value = box
            self.assertFalse(self.r.allow_close())
        minimized.assert_called_once()
        self.assertIs(self.w._session, site)
        self.assertFalse(self.r.exiting)


if __name__ == "__main__":
    unittest.main()
