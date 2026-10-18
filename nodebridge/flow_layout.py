"""Compact layout that wraps command helpers to the available width."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(3)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        left, top, right, bottom = self.getContentsMargins()
        available = rect.adjusted(left, top, -right, -bottom)
        x, y, row_height = available.x(), available.y(), 0
        row = []

        def place_row():
            if apply:
                for item, item_x, size in row:
                    item.setGeometry(QRect(item_x, y + (row_height - size.height()) // 2,
                                           size.width(), size.height()))

        for item in self._items:
            size = item.sizeHint()
            if row_height and x + size.width() > available.x() + available.width():
                place_row()
                row.clear()
                x = available.x()
                y += row_height + self.spacing()
                row_height = 0
            row.append((item, x, size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        place_row()
        return y + row_height - rect.y() + bottom
