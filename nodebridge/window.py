"""Local and remote file manager UI. All SSH/SFTP calls run outside the GUI thread."""

from __future__ import annotations

import json
import posixpath
import tempfile
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QEventLoop, QMimeData, QModelIndex, QSettings, QSignalBlocker, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nodebridge.local_browser import LocalBrowser
from nodebridge.drag_drop import REMOTE_MIME, RemoteFileTable, local_urls, remote_payload
from nodebridge.conflict_dialog import ConflictDialog
from nodebridge.file_operations import (
    create_local_directory,
    create_remote_directory,
    delete_remote,
    rename_local,
    rename_remote,
    trash_local,
)
from nodebridge.file_icons import FileIcons
from nodebridge.remote import DirectoryListing, NodeConfig, RemoteSession
from nodebridge.site_dialog import SiteManagerDialog
from nodebridge.sites import SiteStore
from nodebridge.transfer import (
    ConflictAction,
    ConflictInfo,
    CopyResult,
    copy_local_to_local,
    copy_local_to_remote,
    copy_remote_to_local,
    copy_remote_to_remote,
)


PATH_ROLE = Qt.ItemDataRole.UserRole
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class ConflictPrompt:
    info: ConflictInfo
    done: threading.Event = field(default_factory=threading.Event)
    action: ConflictAction = ConflictAction.CANCEL


class JobThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], object], parent: QWidget):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.succeeded.emit(self._work())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FilenameRenameDelegate(QStyledItemDelegate):
    """Commit a file-name editor through the normal rename operation."""

    def __init__(self, commit: Callable[[QModelIndex, str], None], is_directory: Callable[[QModelIndex], bool], parent: QWidget):
        super().__init__(parent)
        self._commit = commit
        self._is_directory = is_directory

    def createEditor(self, parent, _option, index):
        if index.column() != 0:
            return None
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        editor.setText(name)
        dot = name.rfind(".")
        if self._is_directory(index) or dot <= 0:
            editor.selectAll()
        else:
            editor.setSelection(0, dot)

    def setModelData(self, editor, _model, index):
        self._commit(index, editor.text())


class MainWindow(QMainWindow):
    transferProgress = Signal(str)
    conflictRequested = Signal(object)

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle("NodeBridge — 本地与远程文件浏览")
        self.resize(1350, 800)
        self._settings = settings or QSettings("NodeBridge", "NodeBridge")
        self._session: RemoteSession | None = None
        self._job: JobThread | None = None
        self._on_job_success: Callable[[object], None] | None = None
        self._path = ""
        self._current_listing: DirectoryListing | None = None
        self._parents: list[tuple[RemoteSession, DirectoryListing]] = []
        self._site_store = SiteStore()
        self._drag_cache: dict[
            tuple[int, int, tuple[str, ...]],
            tuple[float, tempfile.TemporaryDirectory, list[str]],
        ] = {}
        self._listing_version = 0
        self._apply_conflict_choice: ConflictAction | None = None

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction("退出", self.close)
        view_menu = self.menuBar().addMenu("查看")
        self.show_local_action = view_menu.addAction("查看本地站点")
        self.show_local_action.setCheckable(True)
        self.show_local_action.setChecked(
            self._settings.value("view/show_local_site", True, type=bool)
        )
        self.show_local_action.toggled.connect(self._set_local_visible)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("关于 NodeBridge", self._show_about)

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        self.setCentralWidget(body)

        quick = QHBoxLayout()
        self.site_button = QPushButton("站点管理器…")
        self.site_button.clicked.connect(self._open_site_manager)
        quick.addWidget(self.site_button)
        self.host = QLineEdit()
        self.host.setPlaceholderText("主机")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.user = QLineEdit()
        self.user.setPlaceholderText("用户名")
        self.key_file = QLineEdit()
        self.key_file.setPlaceholderText("可选：私钥文件路径")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("密码（可选）")
        quick.addWidget(QLabel("主机:"))
        quick.addWidget(self.host, 3)
        quick.addWidget(QLabel("用户名:"))
        quick.addWidget(self.user, 2)
        quick.addWidget(QLabel("密码:"))
        quick.addWidget(self.password, 2)
        quick.addWidget(QLabel("端口:"))
        quick.addWidget(self.port)
        self.connect_button = QPushButton("连接")
        self.jump_button = QPushButton("SSH 别名中转…")
        self.disconnect_button = QPushButton("断开")
        self.connect_button.clicked.connect(self._connect)
        self.jump_button.clicked.connect(self._prompt_alias)
        self.disconnect_button.clicked.connect(self._disconnect)
        quick.addWidget(self.connect_button)
        quick.addWidget(self.jump_button)
        quick.addWidget(self.disconnect_button)
        layout.addLayout(quick)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("私钥路径:"))
        key_row.addWidget(self.key_file, 1)
        layout.addLayout(key_row)

        self.status = QLabel("未连接。主机密钥须已存在于 OpenSSH known_hosts。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.transferProgress.connect(self.status.setText)
        self.conflictRequested.connect(self._show_conflict_prompt)

        panes = QSplitter(Qt.Orientation.Horizontal)
        self.local = LocalBrowser()
        self.local_tree = self.local.tree
        self.local_table = self.local.table
        self.local_tree.remotePathsDropped.connect(self._copy_remote_to_local)
        self.local_table.remotePathsDropped.connect(self._copy_remote_to_local)
        panes.addWidget(self.local)

        remote = QWidget()
        remote_layout = QVBoxLayout(remote)
        remote_layout.setContentsMargins(2, 2, 2, 2)

        navigation = QHBoxLayout()
        navigation.addWidget(QLabel("远程站点:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("远程 POSIX 路径")
        self.up_button = QPushButton("上一级")
        self.refresh_button = QPushButton("刷新")
        self.up_button.clicked.connect(self._up)
        self.refresh_button.clicked.connect(self._refresh)
        self.path_edit.returnPressed.connect(self._open_path)
        navigation.addWidget(self.path_edit, 1)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.refresh_button)
        remote_layout.addLayout(navigation)

        self.remote_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("远程目录")
        self.tree.itemExpanded.connect(self._expand_tree)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.table = RemoteFileTable()
        self.table.localPathsDropped.connect(self._copy_local_to_remote)
        self.table.preparedDragCancelled.connect(
            lambda: self.status.setText("拖出文件已准备好；如果刚才松开了鼠标，请再拖一次。")
        )
        self.table.prepare_export = self._prepare_drag_export
        self.table.setHorizontalHeaderLabels(["文件名", "文件大小", "文件类型", "最后修改"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._table_activated)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.verticalHeader().setMinimumSectionSize(18)
        self.table.horizontalHeader().setFixedHeight(23)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 185)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 95)
        self._directory_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icons = FileIcons()
        self.remote_splitter.addWidget(self.tree)
        self.remote_splitter.addWidget(self.table)
        self.remote_splitter.setSizes([220, 440])
        remote_layout.addWidget(self.remote_splitter, 1)
        panes.addWidget(remote)
        panes.setSizes([660, 660])
        layout.addWidget(panes, 1)
        self.local.setVisible(self.show_local_action.isChecked())

        self._install_file_actions()
        self.completion_toast = QLabel(self)
        self.completion_toast.setObjectName("completionToast")
        self.completion_toast.setWordWrap(True)
        self.completion_toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.completion_toast.setStyleSheet(
            "QLabel#completionToast { background: #263238; color: white; "
            "border: 1px solid #455a64; border-radius: 7px; padding: 12px; }"
        )
        self.completion_toast.hide()
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self.completion_toast.hide)
        self._update_controls()

    def _install_file_actions(self) -> None:
        self.local.file_model.setReadOnly(False)
        self.table.setItemDelegateForColumn(0, FilenameRenameDelegate(
            self._commit_remote_rename,
            lambda index: bool(index.data(LOADED_ROLE)), self.table,
        ))
        self.local_table.setItemDelegateForColumn(0, FilenameRenameDelegate(
            self._commit_local_rename,
            self.local.file_model.isDir, self.local_table,
        ))
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.timeout.connect(self._begin_pending_rename)
        self._pending_rename = None
        self.table.viewport().installEventFilter(self)
        self.local_table.viewport().installEventFilter(self)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._remote_context_menu)
        self.local_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.local_table.customContextMenuRequested.connect(self._local_context_menu)
        for widget, bindings in (
            (self.table, (
                ("Delete", self._delete_remote_selection),
                ("Ctrl+C", self._copy_remote_selection),
                ("Ctrl+V", self._paste_remote),
                ("F2", self._rename_remote_selection),
            )),
            (self.local_table, (
                ("Delete", self._delete_local_selection),
                ("Ctrl+C", self._copy_local_selection),
                ("Ctrl+V", self._paste_local),
                ("F2", self._rename_local_selection),
            )),
        ):
            for sequence, callback in bindings:
                shortcut = QShortcut(QKeySequence(sequence), widget)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(callback)

    def eventFilter(self, watched, event) -> bool:
        table = next((candidate for candidate in (self.table, self.local_table)
                      if watched is candidate.viewport()), None) if hasattr(self, "table") else None
        if table is not None:
            if event.type() == QEvent.Type.MouseButtonPress:
                self._rename_timer.stop()
                self._pending_rename = None
                if (event.button() == Qt.MouseButton.LeftButton
                        and event.modifiers() == Qt.KeyboardModifier.NoModifier
                        and self._job is None and table.isEnabled()):
                    index = table.indexAt(event.position().toPoint())
                    if (index.isValid() and index.column() == 0
                            and table.selectionModel().isSelected(index)):
                        source = self._rename_source(table, index)
                        if source:
                            self._pending_rename = (table, index.row(), source)
                            self._rename_timer.start(QApplication.doubleClickInterval() + 100)
            elif event.type() in (QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove):
                if event.type() == QEvent.Type.MouseButtonDblClick or event.buttons() & Qt.MouseButton.LeftButton:
                    self._rename_timer.stop()
                    self._pending_rename = None
        return super().eventFilter(watched, event)

    def _rename_source(self, table, index: QModelIndex) -> str:
        if table is self.table:
            item = self.table.item(index.row(), 0)
            return item.data(PATH_ROLE) if item is not None else ""
        return self.local.file_model.filePath(index)

    def _begin_pending_rename(self) -> None:
        pending = self._pending_rename
        self._pending_rename = None
        if pending is None:
            return
        table, row, source = pending
        index = table.model().index(row, 0)
        if (index.isValid() and table.isEnabled() and self._job is None
                and table.selectionModel().isSelected(index)
                and self._rename_source(table, index) == source):
            table.edit(index)

    def _set_local_visible(self, visible: bool) -> None:
        self.local.setVisible(visible)
        self._settings.setValue("view/show_local_site", visible)
        self._settings.sync()

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于 NodeBridge", "NodeBridge — SSH/SFTP 文件管理器")

    def _update_controls(self) -> None:
        busy = self._job is not None
        connected = self._session is not None
        self.connect_button.setEnabled(not busy and not connected)
        self.jump_button.setEnabled(not busy and connected)
        self.disconnect_button.setEnabled(not busy and connected)
        self.disconnect_button.setText("返回上一站点" if self._parents else "断开")
        self.site_button.setEnabled(not busy)
        for widget in (self.host, self.port, self.user, self.key_file, self.password):
            widget.setEnabled(not busy and not connected)
        for widget in (self.up_button, self.refresh_button, self.path_edit, self.tree, self.table):
            widget.setEnabled(not busy and connected)
        self.local_tree.setEnabled(not busy)
        self.local_table.setEnabled(not busy)

    def _open_site_manager(self) -> None:
        try:
            dialog = SiteManagerDialog(
                self._site_store, self,
                jump_available=self._session is not None and getattr(self._session, "_client", None) is not None,
                connect_available=self._session is None or getattr(self._session, "_client", None) is not None,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "站点管理器", f"无法读取站点资料：{exc}")
            return
        if dialog.exec() != SiteManagerDialog.DialogCode.Accepted:
            return
        site = dialog.selected_site
        if site is None:
            return
        if self._session is not None and getattr(self._session, "_client", None) is None:
            QMessageBox.warning(self, "NodeBridge", "当前站点通过 SSH 别名连接；请用“SSH 别名中转”继续跳转。")
            return
        config = NodeConfig(site.host, site.port, site.username, site.key_file or None)
        self._start_connection(config, dialog.connection_password, self._session)

    def _prompt_alias(self) -> None:
        if self._session is None:
            return
        alias, accepted = QInputDialog.getText(
            self, "SSH 别名中转", "目标节点别名（例如 cft02）："
        )
        if accepted and alias.strip():
            self._start_alias_connection(alias.strip())

    def _start_alias_connection(self, alias: str) -> None:
        jump = self._session
        if jump is None or self._job is not None or self._current_listing is None:
            return

        def work():
            session = RemoteSession.connect_alias(alias, jump)
            try:
                listing = session.list_directory(session.home())
                return session, listing
            except Exception:
                session.close()
                raise

        def success(result):
            self._parents.append((jump, self._current_listing))
            self._session, listing = result
            self._show_connection_fields(self._session.config)
            self.tree.clear()
            self._show_listing(listing)
            self.status.setText(f"已通过 {jump.config.host} 的 SSH 别名连接到 {alias}，当前目录：{listing.path}")

        self._run(work, success, f"正在通过当前站点连接 SSH 别名 {alias}…")

    def _show_connection_fields(self, config: NodeConfig) -> None:
        self.host.setText(config.host)
        self.port.setValue(config.port)
        self.user.setText(config.username)
        self.key_file.setText(config.key_file or "")
        self.password.clear()

    def _run(
        self, work: Callable[[], object], on_success: Callable[[object], None],
        description: str, on_finished: Callable[[], None] | None = None,
    ) -> None:
        if self._job is not None:
            return
        self.status.setText(description)
        job = JobThread(work, self)
        self._job = job
        self._on_job_success = on_success
        job.succeeded.connect(self._job_succeeded)
        job.failed.connect(self._job_failed)
        job.finished.connect(self._job_finished)
        if on_finished is not None:
            job.finished.connect(on_finished)
        job.finished.connect(job.deleteLater)
        self._update_controls()
        job.start()

    @Slot(object)
    def _job_succeeded(self, result: object) -> None:
        if self._on_job_success is not None:
            self._on_job_success(result)

    @Slot(str)
    def _job_failed(self, message: str) -> None:
        self._show_error(message)

    @Slot()
    def _job_finished(self) -> None:
        self._job = None
        self._on_job_success = None
        self._update_controls()

    def _show_error(self, message: str) -> None:
        self.status.setText("操作失败")
        QMessageBox.warning(self, "NodeBridge", message)

    def _notify_done(self, message: str) -> None:
        self.status.setText(message)
        self.completion_toast.setText(message)
        self.completion_toast.setFixedWidth(min(420, max(200, self.width() - 32)))
        self.completion_toast.adjustSize()
        self._position_completion_toast()
        self.completion_toast.show()
        self.completion_toast.raise_()
        self._completion_timer.start(5000)

    def _position_completion_toast(self) -> None:
        self.completion_toast.move(
            max(8, self.width() - self.completion_toast.width() - 16),
            max(8, self.height() - self.completion_toast.height() - 16),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "completion_toast") and not self.completion_toast.isHidden():
            self._position_completion_toast()

    def _resolve_conflict(self, info: ConflictInfo) -> ConflictAction:
        prompt = ConflictPrompt(info)
        self.conflictRequested.emit(prompt)
        prompt.done.wait()
        return prompt.action

    @Slot(object)
    def _show_conflict_prompt(self, prompt: ConflictPrompt) -> None:
        try:
            if self._apply_conflict_choice is not None:
                prompt.action = self._apply_conflict_choice
                return
            choice, apply_all = ConflictDialog(prompt.info, self).result_choice()
            prompt.action = choice
            if apply_all and choice is not ConflictAction.CANCEL:
                self._apply_conflict_choice = choice
        finally:
            prompt.done.set()

    @staticmethod
    def _copy_status(direction: str, result: CopyResult) -> str:
        state = "已取消" if result.cancelled else "完成"
        return (
            f"{direction}{state}：{result.files} 个文件，{result.directories} 个文件夹，"
            f"跳过 {result.skipped} 个同名文件。"
        )

    def _connect(self) -> None:
        host = self.host.text().strip()
        username = self.user.text().strip()
        if not host or not username:
            QMessageBox.warning(self, "NodeBridge", "请输入主机和用户名。")
            return
        config = NodeConfig(host, self.port.value(), username, self.key_file.text().strip() or None)
        password = self.password.text() or None
        self.password.clear()
        self._start_connection(config, password)

    def _start_connection(
        self,
        config: NodeConfig,
        password: str | None,
        jump: RemoteSession | None = None,
    ) -> None:
        if self._job is not None:
            return
        if jump is not None and self._current_listing is None:
            QMessageBox.warning(self, "NodeBridge", "当前站点目录尚未加载完成。")
            return

        def work():
            session = (
                RemoteSession.connect_via(config, jump, password)
                if jump is not None else RemoteSession.connect(config, password)
            )
            try:
                listing = session.list_directory(session.home())
                return session, listing
            except Exception:
                session.close()
                raise

        def success(result):
            if jump is not None:
                self._parents.append((jump, self._current_listing))
            self._session, listing = result
            self._show_connection_fields(config)
            self.tree.clear()
            self._show_listing(listing)
            route = f"（经 {jump.config.host} 中转）" if jump is not None else ""
            self.status.setText(f"已连接到 {config.host}{route}，当前目录：{listing.path}")

        description = "正在通过当前站点连接目标…" if jump is not None else "正在连接并读取目录…"
        self._run(work, success, description)

    def _disconnect(self) -> None:
        session = self._session
        if session is None:
            return

        def success(_):
            if self._parents:
                parent, listing = self._parents.pop()
                self._session = parent
                self._show_connection_fields(parent.config)
                self.tree.clear()
                self._show_listing(listing)
                self.status.setText(f"已返回 {parent.config.host}，当前目录：{listing.path}")
            else:
                self._session = None
                self._current_listing = None
                self._path = ""
                self.path_edit.clear()
                self.tree.clear()
                self.table.setRowCount(0)
                self.status.setText("已断开")

        self._run(session.close, success, "正在断开…")

    def _make_directory_item(self, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([posixpath.basename(path.rstrip("/")) or "/"])
        item.setIcon(0, self._directory_icon)
        item.setData(0, PATH_ROLE, path)
        item.setData(0, LOADED_ROLE, False)
        item.addChild(QTreeWidgetItem(["…"]))
        return item

    def _fill_tree(self, parent: QTreeWidgetItem, listing: DirectoryListing) -> None:
        parent.takeChildren()
        for entry in listing.entries:
            if entry.is_dir:
                parent.addChild(self._make_directory_item(entry.path))
        parent.setData(0, LOADED_ROLE, True)

    def _find_tree_item(self, path: str) -> QTreeWidgetItem | None:
        def visit(item):
            if item.data(0, PATH_ROLE) == path:
                return item
            for index in range(item.childCount()):
                found = visit(item.child(index))
                if found:
                    return found
            return None

        for index in range(self.tree.topLevelItemCount()):
            found = visit(self.tree.topLevelItem(index))
            if found:
                return found
        return None

    def _ensure_tree_item(self, path: str) -> QTreeWidgetItem:
        root = self._find_tree_item("/")
        if root is None:
            root = self._make_directory_item("/")
            self.tree.addTopLevelItem(root)
        current = root
        current_path = ""
        ancestors = [root]
        for part in path.strip("/").split("/") if path != "/" else []:
            current_path = posixpath.join(current_path, part)
            target = "/" + current_path.lstrip("/")
            child = next(
                (current.child(i) for i in range(current.childCount())
                 if current.child(i).data(0, PATH_ROLE) == target),
                None,
            )
            if child is None:
                for i in reversed(range(current.childCount())):
                    if current.child(i).data(0, PATH_ROLE) is None:
                        current.takeChild(i)
                child = self._make_directory_item(target)
                current.addChild(child)
            current = child
            ancestors.append(current)
        with QSignalBlocker(self.tree):
            for ancestor in ancestors[:-1]:
                ancestor.setExpanded(True)
        return current

    def _show_listing(self, listing: DirectoryListing) -> None:
        self._listing_version += 1
        self._current_listing = listing
        self._path = listing.path
        self.path_edit.setText(listing.path)
        self.table.current_path = listing.path
        self.table.session_token = str(id(self._session)) if self._session is not None else ""
        self.table.setRowCount(len(listing.entries))
        for row, entry in enumerate(listing.entries):
            name = QTableWidgetItem(entry.name)
            name.setIcon(self._directory_icon if entry.is_dir else self._file_icons.for_filename(entry.name))
            name.setData(PATH_ROLE, entry.path)
            name.setData(LOADED_ROLE, entry.is_dir)
            size = "" if entry.is_dir or entry.size is None else f"{entry.size:,}"
            size_item = QTableWidgetItem(size)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            modified = "" if entry.modified is None else datetime.fromtimestamp(entry.modified).strftime("%Y-%m-%d %H:%M")
            suffix = posixpath.splitext(entry.name)[1].lstrip(".").upper()
            file_type = "文件夹" if entry.is_dir else "符号链接" if entry.is_symlink else f"{suffix} 文件" if suffix else "文件"
            for column, value in enumerate((
                name, size_item, QTableWidgetItem(file_type), QTableWidgetItem(modified)
            )):
                self.table.setItem(row, column, value)
        item = self._ensure_tree_item(listing.path)
        self._fill_tree(item, listing)
        self.tree.setCurrentItem(item)
        self.status.setText(f"{listing.path} — {len(listing.entries)} 项")

    def _browse(self, path: str, tree_only: QTreeWidgetItem | None = None) -> None:
        session = self._session
        if session is None:
            return

        def success(result):
            if tree_only is None:
                self._show_listing(result)
            else:
                self._fill_tree(tree_only, result)
                self.status.setText(f"已展开 {result.path}")

        self._run(lambda: session.list_directory(path), success, f"正在读取 {path}…")

    def _expand_tree(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, LOADED_ROLE):
            self._browse(item.data(0, PATH_ROLE), item)

    def _tree_clicked(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, PATH_ROLE)
        if path and path != self._path:
            self._browse(path)

    def _table_activated(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item and item.data(LOADED_ROLE):
            self._browse(item.data(PATH_ROLE))

    def _open_path(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self._browse(path)

    def _up(self) -> None:
        if self._path:
            self._browse(posixpath.dirname(self._path.rstrip("/")) or "/")

    def _refresh(self) -> None:
        if self._path:
            self._browse(self._path)

    def _remote_selected_paths(self) -> list[str]:
        return [
            self.table.item(index.row(), 0).data(PATH_ROLE)
            for index in self.table.selectionModel().selectedRows(0)
            if self.table.item(index.row(), 0) is not None
        ]

    def _local_selected_paths(self) -> list[str]:
        return [
            self.local.file_model.filePath(index)
            for index in self.local_table.selectionModel().selectedRows(0)
        ]

    def _remote_context_menu(self, point) -> None:
        if self._session is None or self._job is not None:
            return
        item = self.table.itemAt(point)
        if item is None:
            self.table.clearSelection()
        elif item.row() not in [index.row() for index in self.table.selectionModel().selectedRows(0)]:
            self.table.selectRow(item.row())
        paths = self._remote_selected_paths()
        destination = self._path
        if len(paths) == 1 and self.table.itemAt(point) is not None:
            row = self.table.itemAt(point).row()
            if self.table.item(row, 0).data(LOADED_ROLE):
                destination = paths[0]
        menu = QMenu(self.table)
        if paths:
            menu.addAction("复制\tCtrl+C", self._copy_remote_selection)
            menu.addAction("下载到…", self._download_selected_to)
            menu.addAction("重命名\tF2", self._rename_remote_selection).setEnabled(len(paths) == 1)
            menu.addAction("删除\tDelete", self._delete_remote_selection)
            menu.addSeparator()
        menu.addAction("粘贴\tCtrl+V", lambda: self._paste_remote(destination))
        menu.addAction("创建目录", lambda: self._create_remote_directory(False))
        menu.addAction("创建目录并进入", lambda: self._create_remote_directory(True))
        menu.addSeparator()
        menu.addAction("刷新", self._refresh)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _local_context_menu(self, point) -> None:
        if self._job is not None:
            return
        index = self.local_table.indexAt(point)
        if not index.isValid():
            self.local_table.clearSelection()
        elif index.row() not in [selected.row() for selected in self.local_table.selectionModel().selectedRows(0)]:
            self.local_table.selectRow(index.row())
        paths = self._local_selected_paths()
        destination = self.local.current_path
        if index.isValid() and self.local.file_model.isDir(index):
            destination = self.local.file_model.filePath(index)
        menu = QMenu(self.local_table)
        if paths:
            menu.addAction("复制\tCtrl+C", self._copy_local_selection)
            menu.addAction("重命名\tF2", self._rename_local_selection).setEnabled(len(paths) == 1)
            menu.addAction("删除\tDelete", self._delete_local_selection)
            menu.addSeparator()
        menu.addAction("粘贴\tCtrl+V", lambda: self._paste_local(destination))
        menu.addAction("创建目录", lambda: self._create_local_directory(False))
        menu.addAction("创建目录并进入", lambda: self._create_local_directory(True))
        menu.addSeparator()
        menu.addAction("刷新", self.local.refresh)
        menu.exec(self.local_table.viewport().mapToGlobal(point))

    def _copy_remote_selection(self) -> None:
        paths = self._remote_selected_paths()
        if not paths or self._session is None:
            return
        mime = QMimeData()
        mime.setData(REMOTE_MIME, json.dumps({
            "session": str(id(self._session)), "paths": paths,
        }).encode("utf-8"))
        QApplication.clipboard().setMimeData(mime)
        self.status.setText(f"已复制 {len(paths)} 个远程项目；可在 NodeBridge 中粘贴。")

    def _copy_local_selection(self) -> None:
        paths = self._local_selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        QApplication.clipboard().setMimeData(mime)
        self.status.setText(f"已复制 {len(paths)} 个本地项目。")

    def _paste_remote(self, destination: str | None = None) -> None:
        if self._session is None or self._job is not None:
            return
        target = destination or self._path
        mime = QApplication.clipboard().mimeData()
        payload = remote_payload(mime)
        if payload is not None:
            if payload[0] != str(id(self._session)):
                QMessageBox.warning(self, "NodeBridge", "复制的远程文件不属于当前站点。")
                return
            self._copy_remote_to_remote(payload[1], target)
        elif paths := local_urls(mime):
            self._copy_local_to_remote(paths, target)

    def _paste_local(self, destination: str | None = None) -> None:
        if self._job is not None:
            return
        target = destination or self.local.current_path
        mime = QApplication.clipboard().mimeData()
        payload = remote_payload(mime)
        if payload is not None:
            self._copy_remote_to_local(payload[0], payload[1], target, [])
        elif paths := local_urls(mime):
            self._copy_local_to_local(paths, target)

    def _download_selected_to(self) -> None:
        paths = self._remote_selected_paths()
        if not paths or self._session is None:
            return
        destination = QFileDialog.getExistingDirectory(
            self, "选择本地目标目录", self.local.current_path
        )
        if destination:
            self._copy_remote_to_local(str(id(self._session)), paths, destination, [])

    def _delete_remote_selection(self) -> None:
        paths = self._remote_selected_paths()
        session = self._session
        if not paths or session is None or self._job is not None:
            return
        choice = QMessageBox.question(
            self, "确认删除", f"永久删除选中的 {len(paths)} 个远程项目及其内容？此操作不能撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return

        def work():
            count = delete_remote(session, paths)
            return count, session.list_directory(self._path)

        def success(result):
            self.tree.clear()
            self._show_listing(result[1])
            self._notify_done(f"已删除 {result[0]} 个远程项目。")

        self._run(work, success, "正在删除远程项目…")

    def _delete_local_selection(self) -> None:
        paths = self._local_selected_paths()
        if not paths or self._job is not None:
            return
        choice = QMessageBox.question(
            self, "确认删除", f"将选中的 {len(paths)} 个本地项目移入回收站？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return

        def success(count):
            self.local.refresh()
            self._notify_done(f"已将 {count} 个本地项目移入回收站。")

        self._run(lambda: trash_local(paths), success, "正在移入回收站…")

    def _rename_remote_selection(self) -> None:
        selected = self.table.selectionModel().selectedRows(0)
        if len(selected) != 1 or self._session is None or self._job is not None:
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.table.edit(selected[0])

    def _commit_remote_rename(self, index: QModelIndex, name: str) -> None:
        session = self._session
        source = self._rename_source(self.table, index)
        if session is None or self._job is not None or not source or name == posixpath.basename(source):
            return

        def work():
            target = rename_remote(session, source, name)
            return target, session.list_directory(self._path)

        def success(result):
            self.tree.clear()
            self._show_listing(result[1])
            self._notify_done(f"已重命名为 {posixpath.basename(result[0])}。")

        self._run(work, success, "正在重命名远程项目…")

    def _rename_local_selection(self) -> None:
        selected = self.local_table.selectionModel().selectedRows(0)
        if len(selected) != 1 or self._job is not None:
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.local_table.edit(selected[0])

    def _commit_local_rename(self, index: QModelIndex, name: str) -> None:
        source = self._rename_source(self.local_table, index)
        if self._job is not None or not source or name == Path(source).name:
            return

        def success(target):
            self.local.refresh()
            self._notify_done(f"已重命名为 {target.name}。")

        self._run(lambda: rename_local(source, name), success, "正在重命名本地项目…")

    def _create_remote_directory(self, enter: bool) -> None:
        session = self._session
        if session is None or self._job is not None:
            return
        name, accepted = QInputDialog.getText(self, "创建目录", "目录名称：")
        if not accepted:
            return
        parent = self._path

        def work():
            target = create_remote_directory(session, parent, name)
            return target, session.list_directory(target if enter else parent)

        def success(result):
            self.tree.clear()
            self._show_listing(result[1])
            self._notify_done(f"已创建目录 {result[0]}。")

        self._run(work, success, "正在创建远程目录…")

    def _create_local_directory(self, enter: bool) -> None:
        if self._job is not None:
            return
        name, accepted = QInputDialog.getText(self, "创建目录", "目录名称：")
        if not accepted:
            return
        parent = self.local.current_path

        def success(target):
            if enter:
                self.local.navigate(str(target))
            else:
                self.local.refresh()
            self._notify_done(f"已创建目录 {target}。")

        self._run(lambda: create_local_directory(parent, name), success, "正在创建本地目录…")

    def _copy_local_to_local(self, paths: list[str], destination: str) -> None:
        if self._job is not None:
            return
        self._apply_conflict_choice = None

        def work():
            return copy_local_to_local(
                paths, destination,
                lambda path: self.transferProgress.emit(f"正在复制：{path}"),
                self._resolve_conflict,
            )

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("复制", result))

        self._run(work, success, "正在复制本地文件…")

    def _copy_remote_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._job is not None:
            return
        self._apply_conflict_choice = None

        def work():
            result = copy_remote_to_remote(
                session, paths, destination,
                lambda path: self.transferProgress.emit(f"正在复制：{path}"),
                self._resolve_conflict,
            )
            return result, session.list_directory(self._path)

        def success(value):
            result, listing = value
            self._show_listing(listing)
            self._notify_done(self._copy_status("复制", result))

        self._run(work, success, "正在复制远程文件…")

    def _copy_local_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._job is not None:
            return
        self._apply_conflict_choice = None

        def work():
            result = copy_local_to_remote(
                session, paths, destination,
                lambda path: self.transferProgress.emit(f"正在上传：{path}"),
                self._resolve_conflict,
            )
            return result, session.list_directory(self._path)

        def success(value):
            result, listing = value
            self._show_listing(listing)
            self._notify_done(self._copy_status("上传", result))

        self._run(work, success, "正在检查并上传文件…")

    def _copy_remote_to_local(
        self, token: str, paths: list[str], destination: str, cached_paths: list[str]
    ) -> None:
        session = self._session
        if session is None or self._job is not None or token != str(id(session)):
            return
        self._apply_conflict_choice = None

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在下载：{path}")
            if len(cached_paths) == len(paths) and all(Path(path).exists() for path in cached_paths):
                labels = {Path(cached).name: remote for cached, remote in zip(cached_paths, paths)}
                return copy_local_to_local(
                    cached_paths, destination, progress, self._resolve_conflict, labels
                )
            return copy_remote_to_local(session, paths, destination, progress, self._resolve_conflict)

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("下载", result))

        self._run(work, success, "正在检查并下载文件…")

    def _prepare_drag_export(self, paths: list[str]) -> list[str]:
        session = self._session
        if session is None or self._job is not None:
            return []
        key = (id(session), self._listing_version, tuple(paths))
        cached = self._drag_cache.get(key)
        if cached and time.monotonic() - cached[0] < 60 and all(
            Path(path).exists() for path in cached[2]
        ):
            return cached[2]
        if cached:
            cached[1].cleanup()
            del self._drag_cache[key]
        temporary = tempfile.TemporaryDirectory(prefix="nodebridge-drag-")
        loop = QEventLoop(self)
        exported: list[str] = []

        def work():
            copy_remote_to_local(
                session, paths, temporary.name,
                lambda path: self.transferProgress.emit(f"正在准备拖出：{path}"),
            )
            return [str(Path(temporary.name) / posixpath.basename(path)) for path in paths]

        def success(result):
            exported.extend(result)
            self._drag_cache[key] = (time.monotonic(), temporary, exported)
            self.status.setText("文件已准备好，可以拖到本地目录或资源管理器。")

        self._run(work, success, "正在准备拖出的文件…", loop.quit)
        loop.exec()
        if not exported:
            temporary.cleanup()
        return exported

    def closeEvent(self, event) -> None:
        if self._job is not None:
            event.ignore()
            self.status.setText("请等待当前操作完成后再关闭。")
            return
        if self._session is not None:
            event.ignore()
            self._disconnect()
            if self._job is not None:
                self._job.finished.connect(self.close)
            return
        for _created, temporary, _paths in self._drag_cache.values():
            temporary.cleanup()
        self._drag_cache.clear()
        event.accept()
