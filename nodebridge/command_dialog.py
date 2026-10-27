"""Editable command snippets, persisted with the application's settings."""

from nodebridge.i18n import tr, ui

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


DEFAULT_COMMANDS = [
    {"command": command, "description": description, "visible": True}
    for command, description in (
        ("cd ", "进入目录"), ("mkdir ", "新建目录"),
        ("rm -r ", "删除目录及子目录"), ("cd ..", "退回上级"),
        ("ls", "列出文件"), ("ls -a", "含隐藏"),
        ("ls -lh", "详细列表"), ("pwd", "当前路径"),
    )
]


def load_commands(settings):
    try:
        values = json.loads(settings.value("terminal/commands", "null"))
        if isinstance(values, list) and all(
            isinstance(item, dict) and isinstance(item.get("command"), str)
            and item["command"].strip() and not any(c in item["command"] for c in "\r\n\x00")
            and isinstance(item.get("description"), str)
            and isinstance(item.get("visible"), bool) for item in values
        ):
            return values
    except (TypeError, ValueError):
        pass
    return [dict(item) for item in DEFAULT_COMMANDS]


class CommandManagerDialog(QDialog):
    def __init__(self, commands, parent=None):
        super().__init__(parent)
        ui(self.setWindowTitle, tr('常用命令管理'))
        self.resize(700, 420)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        ui(self.table.setHorizontalHeaderLabels, [tr('显示'), tr('命令（单行）'), tr('功能介绍（可选）')])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 55)
        layout.addWidget(self.table)
        for item in commands:
            self.add_command(item)
        row = QHBoxLayout()
        add = ui(QPushButton, tr('添加命令'))
        add.clicked.connect(lambda: self.add_command())
        remove = ui(QPushButton, tr('删除选中命令'))
        remove.clicked.connect(self.remove_selected)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        ui(buttons.button(QDialogButtonBox.StandardButton.Save).setText, tr('保存'))
        ui(buttons.button(QDialogButtonBox.StandardButton.Cancel).setText, tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_command(self, item=None):
        item = item or {"command": "", "description": "", "visible": True}
        row = self.table.rowCount()
        self.table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        check.setCheckState(Qt.CheckState.Checked if item["visible"] else Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, check)
        self.table.setItem(row, 1, ui(QTableWidgetItem, item["command"]))
        self.table.setItem(row, 2, ui(QTableWidgetItem, item["description"]))
        self.table.setCurrentCell(row, 1)

    def remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def commands(self):
        return [{"command": self.table.item(row, 1).text(),
                 "description": self.table.item(row, 2).text().strip(),
                 "visible": self.table.item(row, 0).checkState() == Qt.CheckState.Checked}
                for row in range(self.table.rowCount())]

    def accept(self):
        if any(not item["command"].strip() for item in self.commands()):
            QMessageBox.warning(self, tr('常用命令'), tr('命令不能为空；请填写或删除空白行。'))
            return
        if any(any(c in item["command"] for c in "\r\n\x00") for item in self.commands()):
            QMessageBox.warning(self, tr('常用命令'), tr('常用命令请填写单行文本，多行内容请使用广播输入框。'))
            return
        super().accept()
