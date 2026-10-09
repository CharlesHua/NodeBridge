"""Settings location for source and portable runs."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings


def default_settings() -> QSettings:
    if not getattr(sys, "frozen", False):
        return QSettings("NodeBridge", "NodeBridge")
    path = Path(sys.executable).resolve().parent / "data" / "settings.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(path), QSettings.Format.IniFormat)
