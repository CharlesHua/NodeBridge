"""Use native Windows subprocesses: offscreen cannot detect HWND recreation."""

import json
import os
import subprocess
import sys
import unittest


NATIVE_PROBE = r'''
import json
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent, QObject, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication([])
from nodebridge.window import MainWindow

def ready(view):
    deadline = time.monotonic() + 8
    while not view._ready and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    return view._ready

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    setup = patch('nodebridge.window.prepare_terminal_rendering', return_value=None) if sys.argv[1] == 'baseline' else nullcontext()
    with setup:
        window = MainWindow(QSettings(str(folder / 'settings.ini'), QSettings.Format.IniFormat),
                            log_path=folder / 'activity.log')
    if sys.argv[2] == 'maximized':
        window.showMaximized()
    else:
        window.show()
    QTest.qWait(100)
    before = int(window.winId())
    surface_before = window.windowHandle().surfaceType().name
    events = []

    class Watch(QObject):
        def eventFilter(self, obj, event):
            if event.type() in (QEvent.Type.Hide, QEvent.Type.Show,
                                QEvent.Type.WinIdChange, QEvent.Type.PlatformSurface):
                events.append((type(obj).__name__, event.type().name))
            return False

    watch = Watch()
    window.installEventFilter(watch)
    window.windowHandle().installEventFilter(watch)
    terminal = window.terminal_workspace
    terminal.set_nodes([('node1', 'node1'), ('node2', 'node2')])
    window.workspace_tabs.setCurrentWidget(terminal)
    group = terminal.create_group(['node1', 'node2'])
    first = terminal.add_terminal('node1', group_id=group)
    loaded = [ready(terminal._views[first].output)]
    second = terminal.add_terminal('node2', group_id=group)
    loaded.append(ready(terminal._views[second].output))
    terminal.show_group_combined(group)
    loaded.append(ready(terminal._combined_views[group].output))
    terminal.remove_terminal(first)
    terminal.remove_terminal(second)
    QTest.qWait(100)
    third = terminal.add_terminal('node1')
    loaded.append(ready(terminal._views[third].output))
    result = dict(platform=app.platformName(), before=before, after=int(window.winId()),
                  surface_before=surface_before, surface_after=window.windowHandle().surfaceType().name,
                  visible=window.isVisible(), maximized=window.isMaximized(),
                  loaded=loaded, events=events)
    window.removeEventFilter(watch)
    window.windowHandle().removeEventFilter(watch)
    window.close()
    print(json.dumps(result), flush=True)
'''


@unittest.skipUnless(sys.platform == "win32", "native Windows window regression")
class NativeTerminalRenderingTests(unittest.TestCase):
    def _check_mode(self, mode):
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "windows"
        # Test processes only; do not change the application's Chromium settings.
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
        result = subprocess.run([sys.executable, "-c", NATIVE_PROBE, "fixed", mode],
                                env=env, capture_output=True, text=True, errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(data["platform"], "windows")
        self.assertTrue(all(data["loaded"]), data)
        self.assertEqual(data["before"], data["after"], data)
        self.assertEqual(data["surface_before"], data["surface_after"], data)
        self.assertEqual(data["events"], [], data)
        self.assertTrue(data["visible"], data)
        self.assertEqual(data["maximized"], mode == "maximized", data)

    def test_normal_window_survives_first_terminal_combined_view_and_reopen(self):
        self._check_mode("normal")

    def test_maximized_window_survives_first_terminal_combined_view_and_reopen(self):
        self._check_mode("maximized")


if __name__ == "__main__":
    unittest.main()
