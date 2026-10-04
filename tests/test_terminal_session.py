import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nodebridge.terminal_session import TerminalManager


class TerminalManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_output_and_close_are_owned_per_session(self):
        manager = TerminalManager()
        output = []
        closed = []
        manager.outputReady.connect(lambda session_id, text: output.append((session_id, text)))
        manager.terminalClosed.connect(lambda session_id, unexpected: closed.append((session_id, unexpected)))
        first = Mock()
        first.recv_ready.side_effect = [True, False]
        first.recv.return_value = "你好".encode("utf-8")
        first.closed = False
        first.exit_status_ready.return_value = False
        second = Mock()
        second.recv_ready.return_value = False
        second.closed = False
        second.exit_status_ready.return_value = False
        manager.add("one", "node01", first)
        manager.add("two", "node02", second)

        manager._poll()
        self.assertEqual(output, [("one", "你好")])
        manager.close_nodes(["node01"])
        self.assertEqual(closed, [("one", False)])
        first.close.assert_called_once_with()
        second.close.assert_not_called()
        manager.close_all()
        second.close.assert_called_once_with()

    def test_remote_exit_is_reported_as_unexpected(self):
        manager = TerminalManager()
        closed = []
        manager.terminalClosed.connect(lambda session_id, unexpected: closed.append((session_id, unexpected)))
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.closed = True
        manager.add("one", "node01", channel)
        manager._poll()
        self.assertEqual(closed, [("one", True)])

    def test_command_input_and_resize_reach_the_same_pty_in_order(self):
        manager = TerminalManager()
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = False
        channel.closed = False
        manager.add("one", "node01", channel)
        manager.resize("one", 120, 40).result(timeout=2)
        manager.send("one", b"pwd\n").result(timeout=2)
        manager.send("one", b"\x03").result(timeout=2)
        self.assertEqual(channel.method_calls[:3], [
            unittest.mock.call.resize_pty(width=120, height=40),
            unittest.mock.call.sendall(b"pwd\n"),
            unittest.mock.call.sendall(b"\x03"),
        ])
        manager.close_all()
