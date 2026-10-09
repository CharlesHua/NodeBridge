import faulthandler
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import qInstallMessageHandler, qWarning

import nodebridge.diagnostics as diagnostics
import nodebridge.portable as portable
from nodebridge.portable import default_settings


class DiagnosticsTests(unittest.TestCase):
    def test_warnings_and_unhandled_exceptions_are_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            old_sys_hook = sys.excepthook
            old_thread_hook = threading.excepthook
            root = logging.getLogger()
            old_level = root.level
            old_handlers = set(root.handlers)
            try:
                path = diagnostics.install_diagnostics(Path(folder))
                logging.getLogger("nodebridge.test").warning("test warning")
                qWarning("test Qt warning")
                try:
                    raise RuntimeError("test uncaught failure")
                except RuntimeError:
                    sys.excepthook(*sys.exc_info())
                content = path.read_text(encoding="utf-8")
                self.assertIn("test warning", content)
                self.assertIn("test Qt warning", content)
                self.assertIn("test uncaught failure", content)
            finally:
                sys.excepthook = old_sys_hook
                threading.excepthook = old_thread_hook
                qInstallMessageHandler(None)
                faulthandler.disable()
                diagnostics._fault_stream.close()
                diagnostics._fault_stream = None
                for handler in set(root.handlers) - old_handlers:
                    root.removeHandler(handler)
                    handler.close()
                root.setLevel(old_level)

    def test_source_settings_keep_native_location(self):
        settings = default_settings()
        self.assertTrue(settings.fileName())

    def test_frozen_settings_live_beside_executable(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(portable.sys, "frozen", True, create=True), \
             patch.object(portable.sys, "executable", str(Path(folder) / "NodeBridge.exe")):
            settings = default_settings()
            settings.setValue("view/show_log", False)
            settings.sync()
            self.assertEqual(Path(settings.fileName()).resolve(),
                             (Path(folder) / "data" / "settings.ini").resolve())
            self.assertTrue(Path(settings.fileName()).exists())


if __name__ == "__main__":
    unittest.main()
