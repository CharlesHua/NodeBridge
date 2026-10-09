"""Broadcast delivery and per-terminal working-directory checks."""

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from nodebridge.window import MainWindow


class TerminalBroadcastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.log_dir.cleanup)
        self.window = MainWindow(QSettings(QSettings.Format.IniFormat,
                                           QSettings.Scope.UserScope,
                                           "NodeBridgeBroadcastTest", "temporary"),
                                 log_path=Path(self.log_dir.name) / "nodebridge.log")
        workspace = self.window.terminal_workspace
        workspace.set_nodes([("worker:cft02", "cft02"), ("worker:cft03", "cft03")])
        self.group_id = workspace.create_group(["worker:cft02", "worker:cft03"])
        self.first = workspace.add_terminal("worker:cft02", group_id=self.group_id)
        self.second = workspace.add_terminal("worker:cft03", group_id=self.group_id)
        workspace.append_output(self.first, "user@cft02:/work$ ")
        workspace.append_output(self.second, "user@cft03:/work$ ")
        workspace.broadcast_input.setText("echo hello")
        self.sent = []
        self.send_patch = patch.object(self.window._terminal_manager, "send",
                                       side_effect=lambda sid, data: self.sent.append((sid, data)))
        self.has_patch = patch.object(self.window._terminal_manager, "has_session", return_value=True)
        self.send_patch.start()
        self.has_patch.start()

    def tearDown(self):
        self.has_patch.stop()
        self.send_patch.stop()
        self.window._broadcast_timer.stop()
        self.window.close()

    def _begin(self):
        previous = len(self.sent)
        self.window.terminal_workspace.send_button.click()
        self.assertEqual(len(self.sent), previous + 2)
        self.assertTrue(all(b"NodeBridgePwd:" in data for _, data in self.sent[previous:]))
        self.assertFalse(self.window.terminal_workspace.send_button.isEnabled())
        return dict(self.window._broadcast_check.pending)

    def _answer(self, tokens, paths):
        for sid, path in paths.items():
            marker = f"\x1b]9;NodeBridgePwd:{tokens[sid]}:{path}\x07"
            self.window._terminal_output(sid, marker[:15])
            self.window._terminal_output(sid, marker[15:])
        self.app.processEvents()

    def test_matching_directories_send_once_to_each_node(self):
        self.window.show()
        self.app.processEvents()
        tokens = self._begin()
        self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertEqual(self.sent[2:], [(self.first, b"echo hello\r"),
                                         (self.second, b"echo hello\r")])
        self.assertEqual(self.window.terminal_workspace.broadcast_input.text(), "")
        self.assertIs(self.app.focusWidget(), self.window.terminal_workspace.broadcast_input)
        records = [json.loads(line) for line in self.window._activity_log.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[-1]["category"], "broadcast")
        self.assertEqual(records[-1]["state"], "已提交发送")
        self.assertEqual(records[-1]["context"]["sent_nodes"], ["cft02", "cft03"])
        self.assertNotIn("echo hello", self.window._activity_log.path.read_text(encoding="utf-8"))

    def test_conda_prefixed_prompts_still_allow_matching_directory_probe(self):
        workspace = self.window.terminal_workspace
        workspace.append_output(self.first, "\r\n(base) (condmat) user@cft02:/work$ ")
        workspace.append_output(self.second, "\r\n(base) (condmat) user@cft03:/work$ ")
        self.assertTrue(workspace.shell_ready(self.first))
        self.assertTrue(workspace.shell_ready(self.second))

        tokens = self._begin()
        with patch.object(QMessageBox, "exec", side_effect=AssertionError("same directory should not warn")):
            self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertEqual(self.sent[2:], [(self.first, b"echo hello\r"),
                                         (self.second, b"echo hello\r")])

    def test_different_conda_prefixes_default_to_not_sending(self):
        workspace = self.window.terminal_workspace
        workspace.append_output(self.first, "\r\n(condmat) user@cft02:/work$ ")
        workspace.append_output(self.second, "\r\n(base) user@cft03:/work$ ")
        tokens = self._begin()

        def cancel(dialog):
            self.assertIn("cft02：(condmat)", dialog.informativeText())
            self.assertIn("cft03：(base)", dialog.informativeText())
            next(button for button in dialog.buttons() if button.text() == "不发送").click()
            return 0

        with patch.object(QMessageBox, "exec", cancel):
            self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(workspace.broadcast_input.text(), "echo hello")
        self.assertFalse(workspace.ignores_conda_mismatch(self.group_id))

    def test_different_conda_prefixes_can_send_once_or_ignore_for_group(self):
        workspace = self.window.terminal_workspace
        workspace.append_output(self.first, "\r\n(condmat) user@cft02:/work$ ")
        workspace.append_output(self.second, "\r\nuser@cft03:/work$ ")

        def choose(label):
            def click(dialog):
                next(button for button in dialog.buttons() if button.text() == label).click()
                return 0
            return click

        tokens = self._begin()
        with patch.object(QMessageBox, "exec", choose("此次仍要发送")):
            self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertEqual(len(self.sent), 4)
        self.assertFalse(workspace.ignores_conda_mismatch(self.group_id))

        for sid, prompt in ((self.first, "(condmat) user@cft02:/work$ "),
                            (self.second, "user@cft03:/work$ ")):
            self.window._terminal_output(sid, f"echo hello\r\nok\r\n{prompt}")
        workspace.broadcast_input.setText("echo hello")
        tokens = self._begin()
        with patch.object(QMessageBox, "exec", choose("该终端组不再提示")):
            self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertEqual(len(self.sent), 8)
        self.assertTrue(workspace.ignores_conda_mismatch(self.group_id))

        other_group = workspace.create_group(["worker:cft02"])
        self.assertFalse(workspace.ignores_conda_mismatch(other_group))

    def test_ignoring_conda_difference_does_not_skip_directory_warning(self):
        workspace = self.window.terminal_workspace
        workspace.ignore_conda_mismatch(self.group_id)
        workspace.append_output(self.first, "\r\n(condmat) user@cft02:/work$ ")
        workspace.append_output(self.second, "\r\n(base) user@cft03:/work$ ")
        tokens = self._begin()

        def cancel_directory_warning(dialog):
            self.assertIn("工作目录不同", dialog.text())
            next(button for button in dialog.buttons() if button.text() == "取消").click()
            return 0

        with patch.object(QMessageBox, "exec", cancel_directory_warning):
            self._answer(tokens, {self.first: "/work", self.second: "/other"})
        self.assertEqual(len(self.sent), 2)

    def test_probe_echo_is_hidden_and_command_output_reaches_combined_view(self):
        tokens = self._begin()
        workspace = self.window.terminal_workspace
        for sid, node in ((self.first, "cft02"), (self.second, "cft03")):
            token = tokens[sid]
            echo = (f" printf '\\033]9;NodeBridgePwd:{token}:%s\\007' "
                    '"$(pwd -P)"\r\n')
            marker = f"\x1b]9;NodeBridgePwd:{token}:/work\x07"
            self.window._terminal_output(sid, echo[:25])
            self.window._terminal_output(sid, echo[25:] + marker[:12])
            self.window._terminal_output(sid, marker[12:] + f"\r\nuser@{node}:/work$ ")
        self.app.processEvents()
        self.assertEqual(len(self.sent), 4)
        for sid, result in ((self.first, "a.py"), (self.second, "b.py")):
            self.window._terminal_output(sid, f"echo hello\r\n{result}\r\n")
            pane = workspace._views[sid]
            visible = pane._recent_output
            self.assertNotIn("printf", visible)
            self.assertNotIn("NodeBridgePwd", visible)
            self.assertIn(result, visible)
        workspace.show_group_combined(self.group_id)
        joint = workspace.terminal_tabs.currentWidget()._rendered
        self.assertLess(joint.index("user@cft02:/work$ echo hello"),
                        joint.index("user@cft03:/work$ echo hello"))
        self.assertNotIn("[cft", joint)
        self.assertIn("a.py", joint)
        self.assertIn("b.py", joint)

    def test_different_directories_require_confirmation(self):
        tokens = self._begin()

        def cancel(dialog):
            self.assertIn("/other", dialog.informativeText())
            dialog.buttons()[-1].click()
            return 0

        with patch.object(QMessageBox, "exec", cancel):
            self._answer(tokens, {self.first: "/work", self.second: "/other"})
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.window.terminal_workspace.broadcast_input.text(), "echo hello")

    def test_unknown_directory_requires_confirmation(self):
        workspace = self.window.terminal_workspace
        workspace.append_output(self.second, "running")
        with patch.object(QMessageBox, "exec", lambda dialog: dialog.buttons()[-1].click()):
            self.window.terminal_workspace.send_button.click()
            self.assertEqual(len(self.sent), 1)
            tokens = dict(self.window._broadcast_check.pending)
            self._answer(tokens, {self.first: "/work"})
        self.assertEqual(len(self.sent), 1)

    def test_other_group_cannot_receive_broadcast(self):
        workspace = self.window.terminal_workspace
        other_group = workspace.create_group(["worker:cft02"])
        other_terminal = workspace.add_terminal("worker:cft02", group_id=other_group)
        workspace.group_selector.setCurrentIndex(workspace.group_selector.findData(self.group_id))
        workspace.broadcast_input.setText("echo hello")
        self.assertFalse(workspace.valid_broadcast(self.group_id, {
            "worker:cft02": other_terminal,
        }))
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as warning:
            self.window._start_broadcast(self.group_id, "echo hello", {
                "worker:cft02": other_terminal,
            })
        warning.assert_called_once()
        self.assertEqual(self.sent, [])
        tokens = self._begin()
        self._answer(tokens, {self.first: "/work", self.second: "/work"})
        self.assertNotIn(other_terminal, [sid for sid, _data in self.sent])

    def test_destructive_broadcast_requires_separate_confirmation(self):
        workspace = self.window.terminal_workspace
        workspace.broadcast_input.setText("rm -rf scratch")
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel) as warning:
            tokens = self._begin()
            self._answer(tokens, {self.first: "/work", self.second: "/work"})
        warning.assert_called_once()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(workspace.broadcast_input.text(), "rm -rf scratch")
