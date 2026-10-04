"""Shared node checkbox tree with Shift range selection."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QWidget


class NodeCheckTree(QTreeWidget):
    """Extend checkbox clicks over a Shift range without clearing other checks."""

    def __init__(self, parent: QWidget | None = None, check_column: int = 0) -> None:
        super().__init__(parent)
        self._check_column = check_column
        self._range_anchor: str | None = None
        self._pending_click: tuple[str, bool, Qt.CheckState] | None = None

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        self._pending_click = (
            (item.text(0), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
             item.checkState(self._check_column))
            if (item is not None and item.parent() is None
                and self.columnAt(event.position().toPoint().x()) == self._check_column
                and event.button() == Qt.MouseButton.LeftButton) else None
        )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        pending = self._pending_click
        self._pending_click = None
        if pending is None or event.button() != Qt.MouseButton.LeftButton:
            return
        alias, shift, previous = pending
        clicked = self.itemAt(event.position().toPoint())
        if clicked is None or clicked.parent() is not None or clicked.text(0) != alias:
            return
        anchor = next(
            (index for index in range(self.topLevelItemCount())
             if self.topLevelItem(index).text(0) == self._range_anchor), None
        )
        if shift and anchor is not None:
            endpoint = self.indexOfTopLevelItem(clicked)
            state = clicked.checkState(self._check_column)
            if state == previous:
                state = self.topLevelItem(anchor).checkState(self._check_column)
            for index in range(min(anchor, endpoint), max(anchor, endpoint) + 1):
                self.topLevelItem(index).setCheckState(self._check_column, state)
        else:
            self._range_anchor = alias

    def reset_range_anchor(self) -> None:
        self._range_anchor = None
        self._pending_click = None
