"""Qt file-list drag and drop adapters."""

from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QTableView, QTableWidget, QTreeView


REMOTE_MIME = "application/x-nodebridge-remote-paths"
PATH_ROLE = Qt.ItemDataRole.UserRole


def remote_payload(mime: QMimeData) -> tuple[str, list[str]] | None:
    if not mime.hasFormat(REMOTE_MIME):
        return None
    try:
        data = json.loads(bytes(mime.data(REMOTE_MIME)))
        token, paths = data["session"], data["paths"]
        if isinstance(token, str) and isinstance(paths, list) and all(
            isinstance(path, str) and path.startswith("/") for path in paths
        ):
            return token, paths
    except (ValueError, KeyError, TypeError):
        pass
    return None


def local_urls(mime: QMimeData) -> list[str]:
    if not mime.hasUrls():
        return []
    urls = mime.urls()
    if not urls or not all(url.isLocalFile() for url in urls):
        return []
    return [url.toLocalFile() for url in urls]


class RemoteFileTable(QTableWidget):
    localPathsDropped = Signal(list, str)
    workerPathsDropped = Signal(str, list, str)
    preparedDragCancelled = Signal()

    def __init__(self):
        super().__init__(0, 4)
        self.session_token = ""
        self.current_path = ""
        self.prepare_export: Callable[[list[str]], list[str]] | None = None
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def startDrag(self, _supported_actions) -> None:
        rows = sorted(index.row() for index in self.selectionModel().selectedRows())
        paths = [self.item(row, 0).data(PATH_ROLE) for row in rows if self.item(row, 0)]
        if not paths or not self.session_token:
            return
        mime = QMimeData()
        mime.setData(REMOTE_MIME, json.dumps({
            "session": self.session_token, "paths": paths,
        }).encode("utf-8"))
        exported = []
        if self.prepare_export is not None:
            exported = self.prepare_export(paths)
            if exported:
                mime.setUrls([QUrl.fromLocalFile(path) for path in exported])
        drag = QDrag(self)
        drag.setMimeData(mime)
        action = drag.exec(Qt.DropAction.CopyAction)
        if exported and action == Qt.DropAction.IgnoreAction:
            self.preparedDragCancelled.emit()

    def dragEnterEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        if local_urls(event.mimeData()) or payload is not None and payload[0].startswith("workers:"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        if local_urls(event.mimeData()) or payload is not None and payload[0].startswith("workers:"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = local_urls(event.mimeData())
        payload = remote_payload(event.mimeData())
        worker = payload is not None and payload[0].startswith("workers:")
        if not paths and not worker:
            event.ignore()
            return
        target = self.current_path
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            row = item.row()
            directory = self.item(row, 0)
            if directory is not None and directory.data(Qt.ItemDataRole.UserRole + 1):
                target = directory.data(PATH_ROLE)
        if worker:
            token, sources = payload
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            QTimer.singleShot(0, lambda: self.workerPathsDropped.emit(token, sources, target))
            return
        else:
            self.localPathsDropped.emit(paths, target)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class LocalFileTable(QTableView):
    remotePathsDropped = Signal(str, list, str, list)

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:
        if remote_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if remote_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        if payload is None:
            event.ignore()
            return
        destination = self.browser.current_path
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and self.browser.file_model.isDir(index):
            destination = self.browser.file_model.filePath(index)
        token, sources = payload
        if token.startswith("workers:"):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            QTimer.singleShot(0, lambda: self.remotePathsDropped.emit(token, sources, destination, []))
            return
        self.remotePathsDropped.emit(token, sources, destination, local_urls(event.mimeData()))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class LocalDirectoryTree(QTreeView):
    remotePathsDropped = Signal(str, list, str, list)

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:
        if remote_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if remote_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        index = self.indexAt(event.position().toPoint())
        if payload is None or not index.isValid():
            event.ignore()
            return
        destination = self.browser.directory_model.filePath(index)
        token, sources = payload
        if token.startswith("workers:"):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            QTimer.singleShot(0, lambda: self.remotePathsDropped.emit(token, sources, destination, []))
            return
        self.remotePathsDropped.emit(token, sources, destination, local_urls(event.mimeData()))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
