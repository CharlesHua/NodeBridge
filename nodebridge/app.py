"""PySide6 entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NodeBridge")
    from nodebridge.diagnostics import install_diagnostics
    install_diagnostics()
    from nodebridge.window import MainWindow
    icon_file = "nodebridge.ico" if sys.platform == "win32" else "nodebridge.png"
    icon = QIcon(str(Path(__file__).with_name("assets") / icon_file))
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    return app.exec()
