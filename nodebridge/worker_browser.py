"""Browse several SSH-alias nodes through one jump connection."""

from __future__ import annotations

import json
import posixpath
from PySide6.QtCore import QMimeData, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag
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

from nodebridge.drag_drop import REMOTE_MIME, local_urls, remote_payload
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
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def startDrag(self, _supported_actions) -> None:
        base = self.browser.rendered_path
        if not base.startswith("/"):
            return
        rows = sorted(index.row() for index in self.selectionModel().selectedRows(0))
        names = [self.item(row, 0).text() for row in rows if self.item(row, 0)]
        if not names:
            return
        paths = [posixpath.join(base, name) for name in names]
        mime = QMimeData()
        mime.setData(REMOTE_MIME, json.dumps({
            "session": self.browser.drag_token,
            "paths": paths,
        }).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        if payload is not None and payload[0] == self.browser.drag_token:
            event.ignore()
        elif payload is not None or local_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        payload = remote_payload(event.mimeData())
        paths = local_urls(event.mimeData())
        if payload is not None and payload[0] == self.browser.drag_token:
            event.ignore()
            return
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
    createDirectoryRequested = Signal()

    @property
    def drag_token(self) -> str:
        return f"workers:{id(self)}"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aliases: list[str] = []
        self._connected: set[str] = set()
        self._probe_status: dict[str, tuple[str, str]] = {}
        self._hash_results: dict[tuple[str, str], str] = {}
        self._hash_details: dict[tuple[str, str], str] = {}
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
        title.addWidget(QLabel("间接节点"))
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
        self.path_edit.setPlaceholderText("间接节点上的绝对路径")
        self.path_edit.returnPressed.connect(self._browse_path)
        navigation.addWidget(self.path_edit, 1)
        self.up_button = QPushButton("上一级")
        self.refresh_button = QPushButton("刷新")
        self.create_directory_button = QPushButton("新建目录")
        self.up_button.clicked.connect(self._up)
        self.refresh_button.clicked.connect(self._browse_path)
        self.create_directory_button.clicked.connect(self.createDirectoryRequested)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.refresh_button)
        navigation.addWidget(self.create_directory_button)
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
        self.table.setHorizontalHeaderLabels(["文件名", "文件大小", "文件类型", "所在节点", "差异"])
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
        self.summary = QLabel("连接直连节点后，可发现其 SSH 配置及主机列表中的节点。")
        layout.addWidget(self.summary)

    def set_busy(self, busy: bool, connected: bool) -> None:
        for button in (self.discover_button, self.add_button, self.connect_button):
            button.setEnabled(connected and not busy)
        self.disconnect_button.setEnabled(bool(self._connected) and not busy)
        self.table.setEnabled(bool(self._connected) and not busy)
        self.create_directory_button.setEnabled(bool(self._connected) and connected and not busy)
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
            state, detail = self._probe_status.get(alias, ("unknown", "尚未检测 SSH 可连接性"))
            label = {
                "checking": "… 检测中", "reachable": "○ 可连接",
                "unreachable": "○ 不可连接", "untrusted": "○ 主机密钥待核验",
                "auth": "○ 认证失败", "error": "○ 检测失败",
            }.get(state, "○ 未检测")
            item = QTreeWidgetItem(self.nodes, [
                alias, "● 已连接" if alias in self._connected else label,
            ])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if alias in checked else Qt.CheckState.Unchecked)
            if alias not in self._connected:
                item.setToolTip(0, detail)
                item.setToolTip(1, detail)
                if state in {"unreachable", "untrusted", "auth", "error"}:
                    gray = QBrush(QColor("#888888"))
                    item.setForeground(0, gray)
                    item.setForeground(1, gray)
            if alias == current:
                self.nodes.setCurrentItem(item)
        self.summary.setText(f"发现 {len(self._aliases)} 个候选别名；已连接 {len(self._connected)} 个。")

    def set_probe_status(self, alias: str, state: str, detail: str = "") -> None:
        self._probe_status[alias] = (state, detail)
        self.set_aliases([alias])

    def set_probe_statuses(self, statuses: dict[str, tuple[str, str]]) -> None:
        self._probe_status.update(statuses)
        self.set_aliases(list(statuses))

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
        self._hash_results.clear()
        self._hash_details.clear()
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
        self._hash_results.clear()
        self._hash_details.clear()
        self.render()

    def set_listings(self, listings: dict[str, DirectoryListing],
                     failed_aliases: list[str] | tuple[str, ...] = ()) -> None:
        self._hash_results.clear()
        self._hash_details.clear()
        for alias in failed_aliases:
            self._listings.pop(alias, None)
        self._listings.update(listings)
        self.render()

    def set_hash_results(self, path: str, results: list[dict]) -> None:
        labels = {"same": "内容相同", "different": "内容不同", "missing": "部分缺失",
                  "changed": "读取期间变化", "unsupported": "非普通文件", "error": "读取失败"}
        for result in results:
            self._hash_results[(path, result["name"])] = labels[result["result"]]
            issues = [f"{alias}：{node['detail']}" for alias, node in result.get("nodes", {}).items()
                      if node["state"] != "ok"]
            if issues:
                self._hash_details[(path, result["name"])] = "\n".join(issues)
        self.render()

    def auto_hash_candidates(self) -> tuple[str, list[str], list[str]]:
        """Only hash regular, same-size files present on every listed node."""
        path = self.path_edit.text().strip()
        aliases = sorted(self._connected, key=str.casefold)
        if self.mode.currentIndex() != 0 or len(aliases) < 2 or not path.startswith("/"):
            return path, [], []
        listings = {alias: self._listings.get(alias) for alias in aliases}
        if any(listing is None or listing.path != path for listing in listings.values()):
            return path, [], []
        files: dict[str, list[RemoteEntry]] = {}
        for listing in listings.values():
            for entry in listing.entries:
                if not entry.is_dir and not entry.is_symlink:
                    files.setdefault(entry.name, []).append(entry)
        names = [name for name, entries in files.items()
                 if len(entries) == len(aliases)
                 and entries[0].size is not None
                 and len({entry.size for entry in entries}) == 1]
        return path, aliases, sorted(names, key=str.casefold)

    def hash_listing_snapshots(
        self, path: str, aliases: list[str], names: list[str],
    ) -> dict[str, dict[str, RemoteEntry]]:
        wanted = set(names)
        return {
            alias: {entry.name: entry for entry in self._listings[alias].entries
                    if entry.name in wanted}
            for alias in aliases
            if alias in self._listings and self._listings[alias].path == path
        }

    def set_hash_pending(self, path: str, names: list[str], label: str = "等待校验") -> None:
        for name in names:
            self._hash_results[(path, name)] = label
            self._hash_details.pop((path, name), None)
        self.render()

    def clear_connections(self, preserve_aliases: bool = False) -> None:
        aliases = list(self._aliases) if preserve_aliases else []
        self._connected.clear()
        self._listings.clear()
        self._probe_status.clear()
        self._hash_results.clear()
        self._hash_details.clear()
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
        if self.mode.currentIndex() == 1 and self._focused in self._connected:
            self._browse_path()

    def _mode_changed(self, index: int) -> None:
        if index == 1:
            self._merged_path = self.path_edit.text().strip()
            listing = self._listings.get(self._focused or "")
            if listing is not None:
                self.path_edit.setText(listing.path)
        elif self._merged_path:
            self.path_edit.setText(self._merged_path)
        self.render()
        if self._connected:
            self._browse_path()

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
            self.tree.setHeaderLabel(f"{self._focused or '间接节点'} 目录")
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
            count = f"{len(entries)} / {len(aliases)}" if len(aliases) > 1 else entries[0][0]
            difference = ("有节点未读取" if len(listings) < len(aliases)
                          else "缺失" if len(entries) < len(listings)
                          else "大小不同" if len(sizes) > 1 else "内容未校验")
            if is_dir:
                difference = "目录未递归核对" if len(aliases) > 1 else ""
            elif is_symlink:
                difference = "符号链接未核对" if len(aliases) > 1 else ""
            if len(aliases) > 1 and len(listings) < len(aliases):
                difference = "有节点未读取"
            if len(aliases) == 1:
                difference = ""
            elif (path, name) in self._hash_results:
                difference = self._hash_results[(path, name)]
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
                if column == 4 and (path, name) in self._hash_details:
                    item.setToolTip(self._hash_details[(path, name)])
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
