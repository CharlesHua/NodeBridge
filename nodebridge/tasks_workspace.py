"""Persistent terminal inventory; all interactive terminal input stays elsewhere."""
import json

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


class TasksWorkspace(QWidget):
    scanRequested = Signal()
    restoreRequested = Signal(str)
    detachRequested = Signal(str)
    terminateRequested = Signal(str)
    restoreManyRequested = Signal(list)
    detachManyRequested = Signal(list)
    terminateManyRequested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel("持久终端会话清单：断开软件后可恢复；会话可用不代表计算任务已经成功完成。终端输入及广播在“终端”页。支持 Ctrl／Shift 多选会话。")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.scan_button = QPushButton("扫描／刷新当前站点")
        self.restore_button = QPushButton("恢复到终端")
        self.detach_button = QPushButton("分离本机连接")
        self.terminate_button = QPushButton("结束远程会话…")
        self.scan_button.clicked.connect(self.scanRequested)
        row.addWidget(self.scan_button)
        for button, signal in ((self.restore_button, self.restoreRequested),
                               (self.detach_button, self.detachRequested),
                               (self.terminate_button, self.terminateRequested)):
            button.clicked.connect(lambda _checked=False, signal=signal: self._emit_selected(signal))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["直连站点", "节点", "终端组", "会话", "状态", "目录", "前台程序", "创建时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(23)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 270)
        self.table.setColumnWidth(5, 260)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._emit_selected(self.restoreRequested))
        layout.addWidget(self.table, 1)
        self.summary = QLabel("连接直连站点后可扫描服务器上的 nodebridge- 会话；本地记录丢失也可重新发现。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self._records = {}
        self._busy = False
        self._scanning = False
        self._connected = True
        self._selection_changed()

    def selected_key(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _emit_selected(self, signal):
        keys = self.selected_keys()
        if keys and not self._busy:
            if len(keys) == 1:
                signal.emit(keys[0])
            else:
                signals = {self.restoreRequested: self.restoreManyRequested,
                           self.detachRequested: self.detachManyRequested,
                           self.terminateRequested: self.terminateManyRequested}
                signals[signal].emit(keys)

    def selected_keys(self):
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in sorted(self.table.selectionModel().selectedRows(), key=lambda index: index.row())]

    def _selection_changed(self):
        records = [self._records[key] for key in self.selected_keys() if key in self._records]
        self.restore_button.setEnabled(bool(records) and not self._busy)
        self.detach_button.setEnabled(any(r.get("local_session") for r in records) and not self._busy)
        self.terminate_button.setEnabled(bool(records) and not self._busy)

    def set_busy(self, busy, connected=True):
        self._busy = busy
        self._connected = connected
        self.scan_button.setEnabled(connected and not self._scanning)
        self._selection_changed()

    def set_scanning(self, scanning):
        self._scanning = scanning
        self.scan_button.setEnabled(self._connected and not scanning)
        self.scan_button.setText("正在扫描…" if scanning else "扫描／刷新当前站点")

    def set_records(self, records):
        from datetime import datetime
        selected = set(self.selected_keys())
        scroll = self.table.verticalScrollBar().value()
        blocker = QSignalBlocker(self.table)
        self.table.clearSelection()
        self._records = {record["key"]: record for record in records}
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            created = record.get("created", 0)
            try:
                created_text = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created else ""
            except (ValueError, OSError, TypeError, OverflowError):
                created_text = "未知"
            values = [f"{record['user']}@{record['host']}:{record['port']}", record["node"],
                      record.get("label", ""), record["name"], record.get("state", "待检查"),
                      record.get("path", ""), record.get("command", ""),
                      created_text]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value) if column != 4 else record.get("detail", str(value)))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record["key"])
                self.table.setItem(row, column, item)
            if record["key"] in selected:
                self.table.selectionModel().select(self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.table.verticalScrollBar().setValue(scroll)
        del blocker
        self._selection_changed()


def load_tasks(settings):
    from nodebridge.tmux_session import NAME
    try:
        records = json.loads(settings.value("terminal/tmux_tasks", "[]"))
        if not isinstance(records, list):
            raise ValueError("会话记录应为列表")
        result = {}
        for record in records:
            if (not isinstance(record, dict) or not all(key in record for key in
                    ("key", "host", "port", "user", "route", "name", "pane", "node"))
                    or not isinstance(record["name"], str) or not NAME.fullmatch(record["name"])
                    or not isinstance(record["route"], list)
                    or not all(isinstance(hop, str) for hop in record["route"])
                    or not isinstance(record["port"], int) or not 1 <= record["port"] <= 65535
                    or not all(isinstance(record[field], str) for field in ("key", "host", "user", "node", "pane"))):
                raise ValueError("持久会话记录格式无效")
            if (any(not isinstance(record.get(field, ""), str) for field in ("group", "label", "path", "command"))
                    or any(not isinstance(record.get(field, 0), int) or record.get(field, 0) < 0
                           for field in ("created", "attached"))):
                raise ValueError("持久会话元数据格式无效")
            record = dict(record, local_session="", state="待检查", detail="上次记录，尚未核实远程会话。")
            result[record["key"]] = record
        return result
    except (ValueError, TypeError) as exc:
        import logging
        logging.getLogger(__name__).warning("无法读取持久终端记录：%s", exc)
        return {}


def save_tasks(settings, records):
    clean = [dict(record, local_session="", state="待检查") for record in records]
    settings.setValue("terminal/tmux_tasks", json.dumps(clean, ensure_ascii=False))
    settings.sync()
    if settings.status() != settings.Status.NoError:
        import logging
        logging.getLogger(__name__).warning("无法保存持久终端记录；仍可通过服务器扫描恢复。")
