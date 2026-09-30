"""Small, distinct document icons for local and remote file lists."""

from __future__ import annotations

from pathlib import PurePath

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap


_STYLES = {
    ".py": ("PY", "#2f6fa7"),
    ".txt": ("TXT", "#637384"),
    ".md": ("MD", "#5b71a8"),
    ".json": ("{}", "#b9752e"),
    ".yaml": ("YML", "#9b6844"),
    ".yml": ("YML", "#9b6844"),
    ".csv": ("CSV", "#32805d"),
    ".tsv": ("TSV", "#32805d"),
    ".pdf": ("PDF", "#bb4242"),
    ".png": ("IMG", "#8662a9"),
    ".jpg": ("IMG", "#8662a9"),
    ".jpeg": ("IMG", "#8662a9"),
    ".gif": ("IMG", "#8662a9"),
    ".zip": ("ZIP", "#9b6c38"),
    ".tar": ("TAR", "#9b6c38"),
    ".gz": ("GZ", "#9b6c38"),
}


class FileIcons:
    def __init__(self):
        self._cache: dict[str, QIcon] = {}

    def for_filename(self, filename: str) -> QIcon:
        suffix = PurePath(filename).suffix.lower()
        label, color = _STYLES.get(suffix, ("FILE", "#74808c"))
        key = f"{label}:{color}"
        if key not in self._cache:
            self._cache[key] = self._draw(label, QColor(color))
        return self._cache[key]

    @staticmethod
    def _draw(label: str, accent: QColor) -> QIcon:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#aab2bb"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(3, 1, 18, 22, 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(3, 14, 18, 9, 2, 2)
        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(7 if len(label) > 3 else 8)
        painter.setFont(font)
        painter.drawText(3, 14, 18, 9, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()
        return QIcon(pixmap)
