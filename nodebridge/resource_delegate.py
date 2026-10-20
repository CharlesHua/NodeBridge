"""Align resource text without changing the application's normal font."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class MemoryUsageDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text = index.data() or ""
        if "% · " not in text:
            return super().paint(painter, option, index)
        percent, amount = text.split(" · ", 1)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = styled.widget.style() if styled.widget else QApplication.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, styled, styled.widget)
        percent_width = styled.fontMetrics.horizontalAdvance("100%")
        separator_width = styled.fontMetrics.horizontalAdvance(" · ")
        # Reserve only the widest visible amount, rather than stretching the
        # gap between the separator and the right-aligned amount to fill a cell.
        amount_width = styled.fontMetrics.horizontalAdvance(amount)
        for row in range(index.model().rowCount(index.parent())):
            sibling = index.model().index(row, index.column(), index.parent()).data() or ""
            if "% · " in sibling:
                amount_width = max(amount_width, styled.fontMetrics.horizontalAdvance(sibling.split(" · ", 1)[1]))
        group_width = percent_width + separator_width + amount_width
        if rect.width() < group_width:
            return super().paint(painter, option, index)
        styled.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(styled.font)
        role = QPalette.ColorRole.Text
        enabled = bool(styled.state & QStyle.StateFlag.State_Enabled)
        flags = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        start = rect.width() - group_width
        percent_rect = rect.adjusted(start, 0, -(amount_width + separator_width), 0)
        style.drawItemText(painter, percent_rect, flags, styled.palette, enabled, percent, role)
        separator_rect = rect.adjusted(start + percent_width, 0, -amount_width, 0)
        style.drawItemText(painter, separator_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           styled.palette, enabled, " · ", role)
        amount_rect = rect.adjusted(rect.width() - amount_width, 0, 0, 0)
        style.drawItemText(painter, amount_rect, flags, styled.palette, enabled, amount, role)
        painter.restore()
