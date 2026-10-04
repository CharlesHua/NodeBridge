"""Browse several SSH-alias nodes through one jump connection."""

from __future__ import annotations

import posixpath

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nodebridge.drag_drop import local_urls, remote_payload
from nodebridge.file_icons import FileIcons
from nodebridge.node_check_tree import NodeCheckTree
from nodebridge.remote import DirectoryListing, RemoteEntry


PATH_ROLE = Qt.ItemDataRole.UserRole


class WorkerFileTable(QTableWidget):
    localPathsDropped = Signal(list, str)
    remotePathsDropped = Signal(str, list, str)

    def __init__(self, browser: "WorkerBrowser") -> None:
        super().__init__(0, 5)
        self.browser = browser
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event) -> None:
        if remote_payload(event.mimeData()) or local_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        paths = local_urls(event.mimeData())
        if payload is None and not paths:
            event.ignore()
            return
        destination = self.browser.path_edit.text().strip()
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            folder = self.item(item.row(), 0)
            if folder is not None and folder.data(PATH_ROLE):
                destination = folder.data(PATH_ROLE)
        if payload is not None:
            self.remotePathsDropped.emit(payload[0], payload[1], destination)
        else:
            self.localPathsDropped.emit(paths, destination)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class WorkerBrowser(QWidget):
    discoverRequested = Signal()
    addRequested = Signal()
    connectRequested = Signal()
    disconnectRequested = Signal()
    browseRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aliases: list[str] = []
        self._connected: set[str] = set()
        self._listings: dict[str, DirectoryListing] = {}
        self._focused: str | None = None
        self._merged_path = ""
        self._rendered_path = ""
        self._icons = FileIcons()
        self._folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        title_widget = QWidget()
        title_widget.setFixedHeight(28)
        title = QHBoxLayout(title_widget)
        title.setContentsMargins(0, 0, 0, 0)
        title.addWidget(QLabel("工作节点"))
        self.mode = QComboBox()
        self.mode.addItems(["合并查看", "单节点查看"])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        title.addWidget(self.mode)
        self.discover_button = QPushButton("发现节点")
        self.add_button = QPushButton("添加别名…")
        self.select_all_button = QPushButton("全选")
        self.clear_selection_button = QPushButton("清空")
        self.connect_button = QPushButton("连接勾选")
        self.disconnect_button = QPushButton("断开勾选")
        self.select_all_button.clicked.connect(lambda: self._check_all(True))
        self.clear_selection_button.clicked.connect(lambda: self._check_all(False))
        for button in (self.discover_button, self.add_button, self.select_all_button, self.clear_selection_button,
                       self.connect_button, self.disconnect_button):
            title.addWidget(button)
        for button, signal in (
            (self.discover_button, self.discoverRequested),
            (self.add_button, self.addRequested),
            (self.connect_button, self.connectRequested),
            (self.disconnect_button, self.disconnectRequested),
        ):
            button.clicked.connect(signal)
        title.addStretch(1)
        layout.addWidget(title_widget)

        navigation = QHBoxLayout()
        navigation.addWidget(QLabel("共同路径:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("工作节点上的绝对路径")
        self.path_edit.returnPressed.connect(self._browse_path)
        navigation.addWidget(self.path_edit, 1)
        self.up_button = QPushButton("上一级")
        self.refresh_button = QPushButton("刷新")
        self.up_button.clicked.connect(self._up)
        self.refresh_button.clicked.connect(self._browse_path)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.refresh_button)
        layout.addLayout(navigation)

        panes = QSplitter(Qt.Orientation.Horizontal)
        self.nodes = NodeCheckTree()
        self.nodes.setHeaderLabels(["节点", "连接"])
        self.nodes.header().setFixedHeight(23)
        self.nodes.setRootIsDecorated(False)
        self.nodes.setColumnWidth(0, 85)
        self.nodes.itemClicked.connect(self._focus_item)
        self.nodes.itemDoubleClicked.connect(lambda *_: self.connectRequested.emit())
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("合并目录")
        self.tree.header().setFixedHeight(23)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.table = WorkerFileTable(self)
        self.table.setHorizontalHeaderLabels(["文件名", "大小", "类型", "所在节点", "差异"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setFixedHeight(23)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((170, 80, 85, 78)):
            self.table.setColumnWidth(column, width)
        self.table.cellDoubleClicked.connect(self._table_activated)
        for widget in (self.nodes, self.tree, self.table):
            panes.addWidget(widget)
        panes.setSizes([150, 190, 500])
        layout.addWidget(panes, 1)
        self.summary = QLabel("连接跳板节点后，可发现其 SSH 配置及主机列表中的节点。")
        layout.addWidget(self.summary)

    def set_busy(self, busy: bool, connected: bool) -> None:
        for button in (self.discover_button, self.add_button, self.connect_button):
            button.setEnabled(connected and not busy)
        self.disconnect_button.setEnabled(bool(self._connected) and not busy)
        self.table.setEnabled(bool(self._connected) and not busy)
        for widget in (self.path_edit, self.up_button, self.refresh_button):
            widget.setEnabled(bool(self._connected) and not busy)

    def _check_all(self, checked: bool) -> None:
        self.nodes.reset_range_anchor()
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.nodes.topLevelItemCount()):
            self.nodes.topLevelItem(index).setCheckState(0, state)

    def set_aliases(self, aliases: list[str]) -> None:
        checked = set(self.checked_aliases())
        current = self._focused
        self._aliases = sorted(set(self._aliases).union(aliases), key=str.casefold)
        self.nodes.clear()
        for alias in self._aliases:
            item = QTreeWidgetItem(self.nodes, [alias, "● 已连接" if alias in self._connected else "○ 未连接"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if alias in checked else Qt.CheckState.Unchecked)
            if alias == current:
                self.nodes.setCurrentItem(item)
        self.summary.setText(f"发现 {len(self._aliases)} 个候选别名；已连接 {len(self._connected)} 个。")

    def checked_aliases(self) -> list[str]:
        return [
            self.nodes.topLevelItem(index).text(0)
            for index in range(self.nodes.topLevelItemCount())
            if self.nodes.topLevelItem(index).checkState(0) == Qt.CheckState.Checked
        ]

    def known_aliases(self) -> list[str]:
        return list(self._aliases)

    def target_aliases(self) -> list[str]:
        return self.checked_aliases() or ([self._focused] if self._focused else [])

    @property
    def focused_alias(self) -> str | None:
        return self._focused

    def selected_names(self) -> list[str]:
        return [
            self.table.item(index.row(), 0).text()
            for index in self.table.selectionModel().selectedRows(0)
            if self.table.item(index.row(), 0) is not None
        ]

    @property
    def rendered_path(self) -> str:
        return self._rendered_path

    def set_connection(self, alias: str, listing: DirectoryListing | None) -> None:
        if listing is None:
            self._connected.discard(alias)
            self._listings.pop(alias, None)
        else:
            self._connected.add(alias)
            self._listings[alias] = listing
            if self._focused is None:
                self._focused = alias
            if not self.path_edit.text():
                self.path_edit.setText(listing.path)
        self.set_aliases([alias])
        self.render()

    def set_listing(self, alias: str, listing: DirectoryListing) -> None:
        self._listings[alias] = listing
        self.render()

    def clear_connections(self, preserve_aliases: bool = False) -> None:
        aliases = list(self._aliases) if preserve_aliases else []
        self._connected.clear()
        self._listings.clear()
        self._focused = None
        self.nodes.clear()
        self.nodes.reset_range_anchor()
        self._aliases.clear()
        self.path_edit.clear()
        if aliases:
            self.set_aliases(aliases)
        self.render()

    def _focus_item(self, item: QTreeWidgetItem, _column: int) -> None:
        self._focused = item.text(0)
        listing = self._listings.get(self._focused)
        if listing is not None and self.mode.currentIndex() == 1:
            self.path_edit.setText(listing.path)
        self.render()

    def _mode_changed(self, index: int) -> None:
        if index == 1:
            self._merged_path = self.path_edit.text().strip()
            listing = self._listings.get(self._focused or "")
            if listing is not None:
                self.path_edit.setText(listing.path)
        elif self._merged_path:
            self.path_edit.setText(self._merged_path)
        self.render()

    def _browse_path(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self.browseRequested.emit(path)

    def _up(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self.browseRequested.emit(posixpath.dirname(path.rstrip("/")) or "/")

    def _tree_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.browseRequested.emit(path)

    def _table_activated(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item and item.data(PATH_ROLE):
            self.browseRequested.emit(item.data(PATH_ROLE))

    def render(self) -> None:
        path = self.path_edit.text().strip()
        self._rendered_path = path
        if self.mode.currentIndex() == 1:
            aliases = [self._focused] if self._focused in self._connected else []
            self.tree.setHeaderLabel(f"{self._focused or '工作节点'} 目录")
        else:
            aliases = sorted(self._connected)
            self.tree.setHeaderLabel("合并目录")
        listings = {
            alias: self._listings[alias]
            for alias in aliases
            if alias in self._listings and self._listings[alias].path == path
        }
        groups: dict[tuple[str, bool, bool], list[tuple[str, RemoteEntry]]] = {}
        for alias, listing in listings.items():
            for entry in listing.entries:
                groups.setdefault((entry.name, entry.is_dir, entry.is_symlink), []).append((alias, entry))
        self.table.setRowCount(0)
        for (name, is_dir, is_symlink), entries in sorted(groups.items(), key=lambda pair: (not pair[0][1], pair[0][0].casefold())):
            sample = entries[0][1]
            sizes = {entry.size for _, entry in entries}
            size = "" if is_dir or len(sizes) != 1 or sample.size is None else f"{sample.size:,}"
            suffix = posixpath.splitext(name)[1].lstrip(".").upper()
            kind = "文件夹" if is_dir else "符号链接" if is_symlink else f"{suffix} 文件" if suffix else "文件"
            count = f"{len(entries)} / {len(listings)}" if len(aliases) > 1 else entries[0][0]
            difference = "缺失" if len(entries) < len(listings) else "大小不同" if len(sizes) > 1 else "内容未校验"
            if len(aliases) == 1:
                difference = ""
            values = (name, size, kind, count, difference)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setIcon(self._folder_icon if is_dir else self._icons.for_filename(name))
                    if is_dir:
                        item.setData(PATH_ROLE, posixpath.join(path, name))
                if column == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self._render_tree(path, groups)
        self.summary.setText(
            f"已连接 {len(self._connected)} 个节点；当前路径已读取 {len(listings)} 个。"
            + (" 同名、同大小不代表内容相同。" if self.mode.currentIndex() == 0 else "")
        )

    def _render_tree(self, path: str, groups: dict[tuple[str, bool, bool], list[tuple[str, RemoteEntry]]]) -> None:
        with QSignalBlocker(self.tree):
            self.tree.clear()
            root = QTreeWidgetItem(self.tree, ["/"])
            root.setIcon(0, self._folder_icon)
            root.setData(0, PATH_ROLE, "/")
            current = root
            current_path = ""
            for part in path.strip("/").split("/") if path and path != "/" else []:
                current_path = posixpath.join(current_path, part)
                current = QTreeWidgetItem(current, [part])
                current.setIcon(0, self._folder_icon)
                current.setData(0, PATH_ROLE, "/" + current_path.lstrip("/"))
            for (name, is_dir, _), _entries in sorted(groups.items()):
                if is_dir:
                    child = QTreeWidgetItem(current, [name])
                    child.setIcon(0, self._folder_icon)
                    child.setData(0, PATH_ROLE, posixpath.join(path, name))
            item = current
            while item is not None:
                item.setExpanded(True)
                item = item.parent()
            self.tree.setCurrentItem(current)
