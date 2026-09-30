"""PySide6 entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from nodebridge.window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NodeBridge")
    window = MainWindow()
    window.show()
    return app.exec()
