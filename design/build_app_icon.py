"""Render the editable SVG into PNG and a multi-size Windows ICO."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ASSETS = Path(__file__).resolve().parents[1] / "nodebridge" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Could not render {size}px application icon")
    return bytes(buffer.data())


def main() -> None:
    renderer = QSvgRenderer(str(ASSETS / "nodebridge.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid application icon SVG")
    images = [(size, render_png(renderer, size)) for size in SIZES]
    (ASSETS / "nodebridge.png").write_bytes(images[-1][1])
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = []
    for size, image in images:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(image), offset))
        offset += len(image)
    (ASSETS / "nodebridge.ico").write_bytes(header + b"".join(entries) +
                                             b"".join(image for _, image in images))


if __name__ == "__main__":
    main()
