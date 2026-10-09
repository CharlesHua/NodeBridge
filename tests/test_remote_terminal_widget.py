"""Exercise the real WebEngine/xterm.js frontend without a remote SSH account."""

import os
import unittest

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodebridge.remote_terminal_widget import RemoteTerminalWidget


class RemoteTerminalWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = RemoteTerminalWidget()
        self.widget.resize(700, 350)
        self.widget.show()
        self.assertTrue(self._wait_until(lambda: self.widget._ready, 7000), "xterm.js page did not become ready")

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout_ms=2500):
        remaining = timeout_ms
        while remaining > 0:
            self.app.processEvents()
            if predicate():
                return True
            loop = QEventLoop()
            QTimer.singleShot(25, loop.quit)
            loop.exec()
            remaining -= 25
        return predicate()

    def _evaluate(self, expression):
        values = []
        self.widget.page().runJavaScript(expression, values.append)
        self.assertTrue(self._wait_until(lambda: bool(values)))
        return values[0]

    def test_pty_output_keeps_ansi_and_rewrites_progress_lines(self):
        self.widget.write("qt | 0%\r\nprompt | 0%\r\n")
        self.widget.write("\x1b[2A\r\x1b[34mqt | 46%\x1b[0m\x1b[K\x1b[2B")
        line = "nodebridgeTerminal.buffer.active.getLine(0).translateToString(true)"
        self.assertTrue(self._wait_until(lambda: self._evaluate(line) == "qt | 46%"))
        self.assertEqual(self._evaluate("nodebridgeTerminal.buffer.active.getLine(1).translateToString(true)"),
                         "prompt | 0%")
        self.assertEqual(self._evaluate("nodebridgeTerminal.buffer.active.getLine(0).getCell(0).getFgColor()"), 4)
        self.widget.write("\x1b[1A\rprompt | 100%\x1b[K")
        self.assertTrue(self._wait_until(lambda: self._evaluate(
            "nodebridgeTerminal.buffer.active.getLine(1).translateToString(true)") == "prompt | 100%"))
        self.widget.write("\r\n中文输出\r\n")
        self.assertTrue(self._wait_until(lambda: self._evaluate(
            "nodebridgeTerminal.buffer.active.getLine(2).translateToString(true)") == "中文输出"))
        self.widget.write("\x1b[?1049h\x1b[Hfull screen\x1b[?1049l")
        self.assertTrue(self._wait_until(lambda: self._evaluate(
            "nodebridgeTerminal.buffer.active.getLine(0).translateToString(true)") == "qt | 46%"))
        bells = []
        self.widget.bellRequested.connect(lambda: bells.append(True))
        self.widget.write("\a")
        self.assertTrue(self._wait_until(lambda: bool(bells)))

    def test_keyboard_data_and_resize_cross_the_webchannel(self):
        sent = []
        sizes = []
        self.widget.inputRequested.connect(sent.append)
        self.widget.resizeRequested.connect(lambda cols, rows: sizes.append((cols, rows)))
        self.widget.focus_terminal()
        self.assertTrue(self._wait_until(lambda: self.app.focusWidget() is self.widget.focusProxy()))
        target = self.widget.focusProxy()
        QTest.keyClicks(target, "abc")
        QTest.keyClick(target, Qt.Key.Key_Backspace)
        QTest.keyClick(target, Qt.Key.Key_Return)
        QTest.keyClick(target, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(target, Qt.Key.Key_Tab)
        QTest.keyClick(target, Qt.Key.Key_Up)
        QTest.keyClick(target, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(target, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self._wait_until(lambda: len(sent) >= 10))
        self.assertEqual(sent[:10], [b"a", b"b", b"c", b"\x7f", b"\r", b"\x03", b"\t",
                                     b"\x1b[A", b"\x04", b"\x1a"])
        self.app.clipboard().setText("paste")
        QTest.keyClick(target, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self._wait_until(lambda: b"paste" in sent))
        self.widget.resize(470, 250)
        self.assertTrue(self._wait_until(lambda: bool(sizes)))
        self.assertEqual(sizes[-1], self.widget.terminal_size)
        self.assertLess(sizes[-1][0], 89)
        QTest.keyClick(target, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self._wait_until(lambda: self._evaluate("nodebridgeTerminal.options.fontSize") == 15))
        QTest.keyClick(target, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self._wait_until(lambda: self._evaluate("nodebridgeTerminal.options.fontSize") == 14))
        self.widget.set_input_enabled(False)
        count = len(sent)
        QTest.keyClick(target, Qt.Key.Key_X)
        self._wait_until(lambda: False, 100)
        self.assertEqual(len(sent), count)


if __name__ == "__main__":
    unittest.main()
