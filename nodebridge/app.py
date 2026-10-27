"""PySide6 entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NodeBridge")
    from nodebridge.diagnostics import install_diagnostics
    install_diagnostics()
    from nodebridge.i18n import install_language
    from nodebridge.portable import default_settings
    settings = default_settings()
    install_language(app, settings)
    from nodebridge.window import MainWindow
    icon_file = "nodebridge.ico" if sys.platform == "win32" else "nodebridge.png"
    icon = QIcon(str(Path(__file__).with_name("assets") / icon_file))
    app.setWindowIcon(icon)
    window = MainWindow(settings)
    from nodebridge.recovery import ConnectionRecovery
    window._recovery = ConnectionRecovery(window)
    window.setWindowIcon(icon)
    window.show()
    QTimer.singleShot(0, window._recovery.start)
    return app.exec()
