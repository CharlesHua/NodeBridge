"""Local filesystem browser used by the left side of the main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, Qt
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from nodebridge.file_icons import FileIcons
from nodebridge.drag_drop import LocalDirectoryTree, LocalFileTable


class LocalFileModel(QFileSystemModel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._icons = FileIcons()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and not self.isDir(index):
            return self._icons.for_filename(self.fileName(index))
        return super().data(index, role)


class FileListDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 1:
            option.displayAlignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class LocalBrowser(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        address = QHBoxLayout()
        address.addWidget(QLabel("本地站点:"))
        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._open_address)
        address.addWidget(self.path_edit, 1)
        self.up_button = QPushButton("上一级")
        self.up_button.clicked.connect(self._up)
        address.addWidget(self.up_button)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        address.addWidget(self.refresh_button)
        layout.addLayout(address)

        self.directory_model = QFileSystemModel(self)
        self.directory_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        self.directory_model.setRootPath("")
        self.tree = LocalDirectoryTree(self)
        self.tree.setModel(self.directory_model)
        self.tree.setHeaderHidden(True)
        for column in range(1, self.directory_model.columnCount()):
            self.tree.hideColumn(column)
        self.tree.clicked.connect(self._tree_clicked)

        self.file_model = LocalFileModel(self)
        self.file_model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.file_model.setRootPath(QDir.homePath())
        self.table = LocalFileTable(self)
        self.table.setModel(self.file_model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._table_activated)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.verticalHeader().setMinimumSectionSize(18)
        self.table.horizontalHeader().setFixedHeight(23)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setItemDelegateForColumn(1, FileListDelegate(self.table))
        self.table.setColumnWidth(0, 185)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 95)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.addWidget(self.tree)
        self.content_splitter.addWidget(self.table)
        self.content_splitter.setSizes([220, 440])
        layout.addWidget(self.content_splitter, 1)
        self._path = ""
        self.navigate(QDir.homePath())

    @property
    def current_path(self) -> str:
        return self._path

    def navigate(self, path: str) -> bool:
        candidate = Path(path).expanduser()
        if not candidate.is_dir():
            QMessageBox.warning(self, "NodeBridge", f"本地目录不存在或无法访问：\n{path}")
            self.path_edit.setText(self._path)
            return False
        resolved = str(candidate.resolve())
        self._path = resolved
        self.path_edit.setText(resolved)
        self.table.setRootIndex(self.file_model.setRootPath(resolved))
        index = self.directory_model.index(resolved)
        if index.isValid():
            ancestor = index.parent()
            while ancestor.isValid():
                self.tree.expand(ancestor)
                ancestor = ancestor.parent()
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        return True

    def refresh(self) -> None:
        if self._path:
            self.file_model.setRootPath("")
            self.table.setRootIndex(self.file_model.setRootPath(self._path))

    def _open_address(self) -> None:
        self.navigate(self.path_edit.text().strip())

    def _up(self) -> None:
        if self._path:
            self.navigate(str(Path(self._path).parent))

    def _tree_clicked(self, index: QModelIndex) -> None:
        self.navigate(self.directory_model.filePath(index))

    def _table_activated(self, index: QModelIndex) -> None:
        if self.file_model.isDir(index):
            self.navigate(self.file_model.filePath(index))
