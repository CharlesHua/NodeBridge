"""Persistent, structured activity records for the desktop application."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def default_log_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data" / "logs" / "nodebridge.log"
    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    return Path(root) / "logs" / "nodebridge.log"


class ActivityLog:
    """Append UTF-8 JSON lines, keeping a bounded set of older files."""

    def __init__(self, path: Path | None = None, *, max_bytes: int = 5_000_000, backups: int = 5):
        self.path = Path(path) if path is not None else default_log_path()
        self.max_bytes = max_bytes
        self.backups = backups

    def ensure_file(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        return self.path

    def write(self, *, event_id: str, category: str, action: str, state: str,
              detail: str = "", duration_ms: int | None = None,
              context: dict | None = None) -> None:
        record = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event_id": event_id,
            "category": category,
            "action": action,
            "state": state,
            "detail": " ".join(detail.split()),
        }
        if duration_ms is not None:
            record["duration_ms"] = duration_ms
        if context:
            record["context"] = context
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        self.ensure_file()
        if self.max_bytes > 0 and self.path.stat().st_size + len(encoded) > self.max_bytes:
            for index in range(self.backups, 0, -1):
                old = self.path.with_name(f"{self.path.name}.{index}")
                if index == self.backups:
                    old.unlink(missing_ok=True)
                else:
                    previous = self.path.with_name(f"{self.path.name}.{index - 1}")
                    if previous.exists():
                        os.replace(previous, old)
            if self.backups:
                os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
            else:
                self.path.unlink(missing_ok=True)
        with self.path.open("ab") as stream:
            stream.write(encoded)
