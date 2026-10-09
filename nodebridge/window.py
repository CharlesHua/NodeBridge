"""Local and remote file manager UI. All SSH/SFTP calls run outside the GUI thread."""

from __future__ import annotations

import json
import logging
import posixpath
import re
import secrets
import stat
import tempfile
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import paramiko
from PySide6.QtCore import QEvent, QEventLoop, QMimeData, QModelIndex, QSettings, QSignalBlocker, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nodebridge.local_browser import LocalBrowser
from nodebridge.portable import default_settings
from nodebridge.activity_log import ActivityLog
from nodebridge.batch import BatchResult, compact_node_aliases, run_parallel, source_snapshot
from nodebridge.batch_dialog import BatchDialog
from nodebridge.drag_drop import REMOTE_MIME, RemoteFileTable, local_urls, remote_payload
from nodebridge.conflict_dialog import ConflictDialog
from nodebridge.content_check import compare_remote_files_isolated
from nodebridge.file_operations import (
    create_local_directory,
    create_remote_directory,
    create_remote_directory_with_policy,
    delete_remote,
    remote_directory_conflict,
    rename_local,
    rename_remote,
    trash_local,
    validate_remote_name,
)
from nodebridge.file_icons import FileIcons
from nodebridge.remote import DirectoryListing, NodeConfig, NodeDiscovery, RemoteSession
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
    copy_remote_between_sessions,
    node_suffixed_name,
)
from nodebridge.terminal_workspace import ANSI_ESCAPE, PLAIN_PROMPT, TerminalWorkspace
from nodebridge.terminal_session import TerminalManager
from nodebridge.worker_browser import WorkerBrowser


PATH_ROLE = Qt.ItemDataRole.UserRole
DESTRUCTIVE_SHELL_COMMAND = re.compile(
    r"(?:^\s*|[;&|]\s*)(?:sudo\s+)?(?:rm|rmdir|mv|dd|mkfs(?:\.[\w-]+)?|"
    r"shutdown|reboot|poweroff|chmod|chown)\b",
    re.IGNORECASE,
)
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class ConflictPrompt:
    info: ConflictInfo
    done: threading.Event = field(default_factory=threading.Event)
    action: ConflictAction = ConflictAction.CANCEL


@dataclass
class WorkerDirectoryPrompt:
    path: str
    aliases: list[str]
    done: threading.Event = field(default_factory=threading.Event)
    choice: str = "cancel"


@dataclass(frozen=True)
class WorkerDirectoryResult:
    alias: str
    path: str = ""
    created: bool = False
    error: str = ""


@dataclass
class WorkerDirectoryOutcome:
    results: list[WorkerDirectoryResult]
    listings: dict[str, DirectoryListing]
    cancelled: bool = False


@dataclass
class PendingBroadcast:
    group_id: str
    command: str
    targets: dict[str, str]
    pending: dict[str, str] = field(default_factory=dict)
    buffers: dict[str, str] = field(default_factory=dict)
    directories: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    conda_prefixes: dict[str, tuple[str, ...] | None] = field(default_factory=dict)


@dataclass
class BatchAction:
    mode: str
    aliases: list[str]
    sources: list[str]
    destination: str
    worker_path: str
    max_workers: int = 4
    moving: bool = False
    target_alias: str = ""


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
    batchResultReady = Signal(object)
    workerDirectoryConflictRequested = Signal(object)
    autoCheckProgress = Signal(object)

    def __init__(self, settings: QSettings | None = None, *, log_path: Path | None = None):
        super().__init__()
        self.setWindowTitle("NodeBridge — 多节点文件浏览")
        self.resize(1550, 850)
        self._settings = settings if settings is not None else default_settings()
        self._session: RemoteSession | None = None
        self._job: JobThread | None = None
        self._copy_job: JobThread | None = None
        self._on_job_success: Callable[[object], None] | None = None
        self._active_log_index: int | None = None
        self._log_result: Callable[[object], tuple[str, str]] | None = None
        self._operation_lines: list[str] = []
        self._activity_log = ActivityLog(log_path)
        self._active_log_id: str | None = None
        self._active_log_action: str | None = None
        self._active_log_category = "file"
        self._active_log_started = 0.0
        self._active_log_context: dict | None = None
        self._log_context_result: Callable[[object], dict] | None = None
        self._log_secrets: set[str] = set()
        self._path = ""
        self._current_listing: DirectoryListing | None = None
        self._parents: list[tuple[RemoteSession, DirectoryListing]] = []
        self._workers: dict[str, RemoteSession] = {}
        self._worker_jumps: dict[str, RemoteSession] = {}
        self._jump_helpers: list[RemoteSession] = []
        self._terminal_jump_helpers: list[RemoteSession] = []
        self._terminal_hosts: dict[str, RemoteSession] = {}
        self._terminal_helper_reservations: set[int] = set()
        self._jump_password: str | None = None
        self._pending_worker_discovery = False
        self._pending_worker_refresh: str | None = None
        self._pending_worker_probe = False
        self._probe_job: JobThread | None = None
        self._probe_cancel = threading.Event()
        self._probe_generation = 0
        self._hash_job: JobThread | None = None
        self._hash_cancel = threading.Event()
        self._hash_generation = 0
        self._hash_pending: tuple[str, list[str], list[str], int] | None = None
        self._close_when_background_idle = False
        self._site_store = SiteStore()
        self._help_dialog: QDialog | None = None
        self._drag_cache: dict[
            tuple[int, int, tuple[str, ...]],
            tuple[float, tempfile.TemporaryDirectory, list[str]],
        ] = {}
        self._listing_version = 0
        self._apply_conflict_choice: ConflictAction | None = None
        self._conflict_lock = threading.Lock()
        self._terminal_manager = TerminalManager(self)
        self._broadcast_check: PendingBroadcast | None = None
        self._probe_prompt_pending: dict[str, tuple[str, str]] = {}
        self._broadcast_timer = QTimer(self)
        self._broadcast_timer.setSingleShot(True)
        self._broadcast_timer.setInterval(8000)
        self._broadcast_timer.timeout.connect(self._finish_broadcast_check)
        self.autoCheckProgress.connect(self._auto_check_progress)

        self.file_menu = QMenu("文件", self.menuBar())
        self.menuBar().addMenu(self.file_menu)
        self.file_menu.addAction("退出", self.close)
        self.view_menu = QMenu("查看", self.menuBar())
        self.menuBar().addMenu(self.view_menu)
        self.show_local_action = self.view_menu.addAction("查看本地站点")
        self.show_local_action.setCheckable(True)
        self.show_local_action.setChecked(
            self._settings.value("view/show_local_site", True, type=bool)
        )
        self.show_local_action.toggled.connect(self._set_local_visible)
        self.show_log_action = self.view_menu.addAction("查看日志")
        self.show_log_action.setCheckable(True)
        self.show_log_action.setChecked(
            self._settings.value("view/show_operation_log", True, type=bool)
        )
        self.show_log_action.toggled.connect(self._set_operation_log_visible)
        self.view_menu.addAction("打开日志文件", self._open_log_file)
        self.help_menu = QMenu("帮助", self.menuBar())
        self.menuBar().addMenu(self.help_menu)
        self.help_menu.addAction("使用指南", self._show_user_guide)
        self.help_menu.addAction("关于 NodeBridge", self._show_about)

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
        self.jump_button = QPushButton("添加间接节点…")
        self.disconnect_button = QPushButton("断开")
        self.connect_button.clicked.connect(self._connect)
        self.jump_button.clicked.connect(self._add_worker_alias)
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
        self.workerDirectoryConflictRequested.connect(self._show_worker_directory_conflict)
        self.workspace_tabs = QTabWidget()
        self.file_view = QWidget()
        file_layout = QVBoxLayout(self.file_view)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(5)
        self.workspace_tabs.addTab(self.file_view, "文件")
        self.terminal_workspace = TerminalWorkspace()
        self.terminal_workspace.connectRequested.connect(self._connect_terminal)
        self.terminal_workspace.disconnectRequested.connect(self._close_terminal)
        self.terminal_workspace.connectManyRequested.connect(self._connect_terminals)
        self.terminal_workspace.disconnectManyRequested.connect(self._close_terminals)
        self.terminal_workspace.disconnectGroupRequested.connect(self._close_terminal_group)
        self.terminal_workspace.inputRequested.connect(self._send_terminal_input)
        self.terminal_workspace.resizeRequested.connect(self._resize_terminal)
        self.terminal_workspace.broadcastRequested.connect(self._start_broadcast)
        self._terminal_manager.outputReady.connect(self._terminal_output)
        self._terminal_manager.terminalClosed.connect(self._terminal_closed)
        self._terminal_manager.inputFailed.connect(self._terminal_input_failed)
        self.workspace_tabs.addTab(self.terminal_workspace, "终端")
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.workspace_tabs)
        self.log_panel = QWidget()
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(3)
        log_layout.addWidget(QLabel("操作日志（界面仅本次运行；文件会保存）"))
        self.operation_log = QPlainTextEdit()
        self.operation_log.setReadOnly(True)
        self.operation_log.setMinimumHeight(45)
        self.operation_log.setPlaceholderText("文件操作、连接和广播的结果会显示在这里。")
        log_layout.addWidget(self.operation_log)
        self.workspace_splitter.addWidget(self.log_panel)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([700, 120])
        layout.addWidget(self.workspace_splitter, 1)
        self.log_panel.setVisible(self.show_log_action.isChecked())
        panes = QSplitter(Qt.Orientation.Horizontal)
        self.local = LocalBrowser()
        self.local.pathChanged.connect(self._save_local_path)
        saved_local_path = self._settings.value("files/local_path", "", type=str)
        if saved_local_path and Path(saved_local_path).is_dir():
            self.local.navigate(saved_local_path)
        else:
            self._save_local_path(self.local.current_path)
        self.local_tree = self.local.tree
        self.local_table = self.local.table
        self.local_tree.remotePathsDropped.connect(self._copy_remote_to_local)
        self.local_table.remotePathsDropped.connect(self._copy_remote_to_local)
        panes.addWidget(self.local)

        remote = QWidget()
        remote_layout = QVBoxLayout(remote)
        remote_layout.setContentsMargins(2, 2, 2, 2)
        remote_layout.setSpacing(5)
        self.jump_title = QLabel("直连节点 · 尚未连接")
        self.jump_title.setFixedHeight(28)
        remote_layout.addWidget(self.jump_title)

        navigation = QHBoxLayout()
        navigation.addWidget(QLabel("直连节点:"))
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
        self.tree.setHeaderLabel("直连目录")
        self.tree.itemExpanded.connect(self._expand_tree)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.table = RemoteFileTable()
        self.table.localPathsDropped.connect(self._copy_local_to_remote)
        self.table.workerPathsDropped.connect(self._collect_workers_to_jump)
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
        self.worker_browser = WorkerBrowser()
        self.worker_browser.discoverRequested.connect(lambda: self._discover_worker_aliases(manual=True))
        self.worker_browser.addRequested.connect(self._add_worker_alias)
        self.worker_browser.connectRequested.connect(self._connect_selected_workers)
        self.worker_browser.disconnectRequested.connect(self._disconnect_selected_workers)
        self.worker_browser.browseRequested.connect(self._browse_workers)
        self.worker_browser.createDirectoryRequested.connect(self._create_worker_directory)
        self.worker_browser.table.localPathsDropped.connect(self._drop_local_on_workers)
        self.worker_browser.table.remotePathsDropped.connect(self._drop_jump_on_workers)
        panes.addWidget(self.worker_browser)
        panes.setSizes([350, 510, 690])
        file_layout.addWidget(panes, 1)
        self.local.setVisible(self.show_local_action.isChecked())

        self.batch_results = QTableWidget(0, 4)
        self.batch_results.setHorizontalHeaderLabels(["节点", "操作", "结果", "详情"])
        self.batch_results.setShowGrid(False)
        self.batch_results.verticalHeader().hide()
        self.batch_results.setFixedHeight(135)
        self.batch_results.setColumnWidth(0, 95)
        self.batch_results.setColumnWidth(1, 150)
        self.batch_results.setColumnWidth(2, 100)
        self.batch_results.horizontalHeader().setStretchLastSection(True)
        file_layout.addWidget(self.batch_results)
        self.batchResultReady.connect(self._show_batch_result)

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

    def _terminal_sources(self) -> dict[str, tuple[str, RemoteSession]]:
        sources = {}
        for session, _listing in self._parents:
            sources[f"primary:{id(session)}"] = (session.config.host, session)
        if self._session is not None:
            host = getattr(getattr(self._session, "config", None), "host", None)
            sources[f"primary:{id(self._session)}"] = (
                host or self.host.text().strip() or "当前站点", self._session,
            )
        sources.update((f"worker:{alias}", (alias, session)) for alias, session in self._workers.items())
        return sources

    def _sync_terminal_nodes(self) -> None:
        sources = self._terminal_sources()
        nodes = {node_id: label for node_id, (label, _session) in sources.items()}
        if self._session is not None:
            nodes.update((f"worker:{alias}", alias) for alias in self.worker_browser.known_aliases())
        self._terminal_manager.retain_nodes(set(nodes))
        self.terminal_workspace.set_nodes(list(nodes.items()))

    def _connect_terminal(self, node_id: str) -> None:
        self._connect_terminals([node_id])

    def _connect_terminals(self, node_ids: list[str]) -> None:
        if self._job is not None or not node_ids:
            return
        sources = self._terminal_sources()
        available = set(sources) | {f"worker:{alias}" for alias in self.worker_browser.known_aliases()}
        node_ids = list(dict.fromkeys(node_id for node_id in node_ids if node_id in available))
        if not node_ids:
            return
        jump = self._parents[0][0] if self._parents else self._session
        terminal_hosts = list(self._terminal_hosts.values())
        worker_hosts = list(self._worker_jumps.values())
        existing_helpers = list(self._terminal_jump_helpers)
        self._terminal_helper_reservations.update(id(host) for host in existing_helpers)

        def open_one(node_id: str, host: RemoteSession | None):
            if node_id.startswith("worker:"):
                if host is None:
                    raise ConnectionError("请先连接直连节点。")
                return RemoteSession.open_alias_shell(node_id.removeprefix("worker:"), host), None, ""
            source = sources[node_id][1]
            if source is jump and isinstance(source, RemoteSession) and not source._alias_route:
                # The site's existing transport already opened SFTP. A fresh login
                # keeps its authentication notice and first interactive MOTD together.
                shell_host = RemoteSession.connect_shell_host(source.config, self._jump_password)
                try:
                    channel = shell_host.open_shell()
                    return channel, shell_host, shell_host.authentication_banner()
                except Exception:
                    shell_host.close()
                    raise
            return source.open_shell(), None, ""

        def work():
            channels = {}
            errors = {}
            banners = {}
            assigned: dict[str, RemoteSession | None] = {}
            new_helpers: list[RemoteSession] = []
            roots = [jump, *existing_helpers] if jump is not None else []
            counts = {id(root): 0 for root in roots}
            for host in [*terminal_hosts, *worker_hosts]:
                if id(host) in counts:
                    counts[id(host)] += 1
            for node_id in node_ids:
                if not node_id.startswith("worker:"):
                    assigned[node_id] = None
                    continue
                if jump is None:
                    errors[node_id] = "请先连接直连节点。"
                    continue
                # Leave space for other jump-host work; actual server limits vary.
                host = next((root for root in roots if counts[id(root)] < 4), None)
                if host is None:
                    try:
                        host = RemoteSession.connect_shell_host(jump.config, self._jump_password)
                    except Exception as exc:
                        errors[node_id] = (
                            f"无法创建额外的跳板 SSH 连接：{type(exc).__name__}: {exc}"
                        )
                        continue
                    roots.append(host)
                    new_helpers.append(host)
                    counts[id(host)] = 0
                counts[id(host)] += 1
                assigned[node_id] = host
            with ThreadPoolExecutor(max_workers=min(4, len(assigned)) or 1) as pool:
                futures = {pool.submit(open_one, node_id, host): node_id
                           for node_id, host in assigned.items()}
                exhausted = []
                for future in as_completed(futures):
                    node_id = futures[future]
                    try:
                        channel, shell_host, banner = future.result()
                        channels[node_id] = channel
                        if shell_host is not None:
                            assigned[node_id] = shell_host
                            new_helpers.append(shell_host)
                        if banner:
                            banners[node_id] = banner
                    except paramiko.ssh_exception.ChannelException as exc:
                        if node_id.startswith("worker:") and exc.code == 2:
                            exhausted.append(node_id)
                        else:
                            errors[node_id] = f"{type(exc).__name__}: {exc}"
                    except Exception as exc:
                        errors[node_id] = f"{type(exc).__name__}: {exc}"
            # OpenSSH may reject a session before our conservative budget is reached.
            # Retry only these refused channels, on fresh authenticated transports.
            fallback_host = None
            fallback_count = 0
            empty_fallback_refusals = 0
            for node_id in node_ids:
                if node_id not in exhausted:
                    continue
                if empty_fallback_refusals >= 2:
                    errors[node_id] = "跳板 SSH 拒绝新会话；两条新连接也未能打开首个终端通道。"
                    continue
                for attempt in range(2):
                    if fallback_host is None or fallback_count >= 4:
                        try:
                            fallback_host = RemoteSession.connect_shell_host(
                                jump.config, self._jump_password
                            )
                        except Exception as exc:
                            errors[node_id] = (
                                f"跳板 SSH 会话被拒绝；新建连接也失败：{type(exc).__name__}: {exc}"
                            )
                            fallback_host = None
                            break
                        new_helpers.append(fallback_host)
                        fallback_count = 0
                    try:
                        channel, _shell_host, _banner = open_one(node_id, fallback_host)
                    except paramiko.ssh_exception.ChannelException as exc:
                        if exc.code == 2 and fallback_count == 0:
                            empty_fallback_refusals += 1
                        errors[node_id] = (
                            f"跳板 SSH 会话被拒绝；更换连接后仍失败：{type(exc).__name__}: {exc}"
                        )
                        fallback_host = None
                        fallback_count = 0
                        if exc.code == 2 and attempt == 0 and empty_fallback_refusals < 2:
                            continue
                        break
                    except Exception as exc:
                        errors[node_id] = f"{type(exc).__name__}: {exc}"
                        break
                    else:
                        channels[node_id] = channel
                        assigned[node_id] = fallback_host
                        fallback_count += 1
                        errors.pop(node_id, None)
                        break
            kept_helpers = []
            for host in new_helpers:
                if any(assigned[node_id] is host for node_id in channels):
                    kept_helpers.append(host)
                else:
                    try:
                        host.close()
                    except Exception:
                        pass
            return channels, errors, assigned, kept_helpers, banners

        def success(result):
            channels, errors, assigned, helpers, banners = result
            self._terminal_jump_helpers.extend(helpers)
            ready = [node_id for node_id in node_ids
                     if node_id in channels and self.terminal_workspace.has_node(node_id)]
            group_id = self.terminal_workspace.create_group(ready) if ready else None
            if group_id is not None and len(ready) > 1:
                self.terminal_workspace.show_group_combined(group_id)
            for node_id in node_ids:
                if node_id not in channels:
                    continue
                channel = channels[node_id]
                if not self.terminal_workspace.has_node(node_id):
                    channel.close()
                    continue
                session_id = self.terminal_workspace.add_terminal(
                    node_id, group_id=group_id, activate=len(ready) == 1
                )
                if node_id in banners:
                    notice = banners[node_id].replace("\r\n", "\n").replace("\n", "\r\n")
                    self.terminal_workspace.append_output(session_id, notice.rstrip("\r\n") + "\r\n")
                self._terminal_manager.add(session_id, node_id, channel)
                if assigned[node_id] is not None:
                    self._terminal_hosts[session_id] = assigned[node_id]
                size = self.terminal_workspace.terminal_size(session_id)
                if size is not None:
                    self._terminal_manager.resize(session_id, *size)
            self.status.setText(
                f"SSH Shell：连接 {len(channels)} 个，失败 {len(errors)} 个；Enter 可发送命令。"
            )
            if errors:
                lines = [f"{self.terminal_workspace.node_label(node_id)}: {message}"
                         for node_id, message in errors.items()]
                omitted = f"\n另有 {len(lines) - 12} 个节点失败。" if len(lines) > 12 else ""
                QMessageBox.warning(
                    self, "终端连接结果",
                    "以下节点连接失败：\n" + "\n".join(lines[:12]) + omitted,
                )

        def finished():
            self._terminal_helper_reservations.difference_update(id(host) for host in existing_helpers)
            self._release_unused_terminal_helpers()

        labels = [self.terminal_workspace.node_label(node_id) for node_id in node_ids]
        self._run(
            work, success, f"正在连接 {len(node_ids)} 个 SSH Shell…",
            on_finished=finished,
            log_action=f"SSH 终端连接（{compact_node_aliases(labels)}）",
            log_category="connection", log_context={"nodes": labels},
            log_result=lambda value: (
                "成功" if not value[1] else "部分完成" if value[0] else "失败",
                f"{len(value[0])}/{len(node_ids)} 个终端连接成功"
                + ("；" + "；".join(
                    f"{self.terminal_workspace.node_label(node_id)}：{error}"
                    for node_id, error in value[1].items()
                ) if value[1] else ""),
            ),
            log_context_result=lambda value: {"errors": {
                self.terminal_workspace.node_label(node_id): error
                for node_id, error in value[1].items()
            }},
        )

    def _close_terminal(self, session_id: str) -> None:
        self._terminal_manager.close(session_id)
        self.terminal_workspace.remove_terminal(session_id)
        self.status.setText("终端连接已关闭。")

    def _close_terminal_group(self, group_id: str) -> None:
        sessions = self.terminal_workspace.group_sessions(group_id)
        if not sessions:
            return
        name = self.terminal_workspace.group_name(group_id)
        choice = QMessageBox.question(
            self, "断开终端组",
            f"断开 {name} 的 {len(sessions)} 个终端连接？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        for session_id in sessions:
            self._terminal_manager.close(session_id)
            self.terminal_workspace.remove_terminal(session_id)
        self.status.setText(f"已断开 {name} 的 {len(sessions)} 个终端连接。")

    def _send_terminal_input(self, session_id: str, data: bytes) -> None:
        try:
            self._terminal_manager.send(session_id, data)
        except ConnectionError as exc:
            self.status.setText(str(exc))

    def _record_broadcast(self, state: PendingBroadcast, status: str,
                          detail: str = "", *, sent_nodes: list[str] | None = None,
                          errors: list[str] | None = None) -> None:
        labels = [self.terminal_workspace.node_label(node_id) for node_id in state.targets]
        self._record_activity(
            "broadcast", f"终端广播（{self.terminal_workspace.group_name(state.group_id)}）",
            status, detail,
            context={"targets": labels, "sent_nodes": sent_nodes or [], "errors": errors or [],
                     "directories": {
                         self.terminal_workspace.node_label(node_id): state.directories.get(session_id)
                         for node_id, session_id in state.targets.items()
                     }},
        )

    def _start_broadcast(self, group_id: str, command: str, targets: dict[str, str]) -> None:
        if self._broadcast_check is not None or not command.strip() or not targets:
            return
        targets = dict(targets)
        if not self.terminal_workspace.valid_broadcast(group_id, targets):
            QMessageBox.warning(self, "广播未发送", "终端组或目标已变化，请重新选择当前组的终端。")
            self._record_activity("broadcast", "终端广播", "未发送", "终端组或目标已变化")
            return
        if any(not self._terminal_manager.has_session(sid) for sid in targets.values()):
            QMessageBox.warning(self, "广播未发送", "有目标终端已断开，请重新选择广播目标。")
            self._record_activity("broadcast", "终端广播", "未发送", "有目标终端已断开")
            return
        state = PendingBroadcast(group_id, command, targets)
        self._broadcast_check = state
        self.terminal_workspace.set_broadcast_busy(True)
        for session_id in targets.values():
            state.conda_prefixes[session_id] = self.terminal_workspace.conda_prefixes(session_id)
            self.terminal_workspace.set_terminal_input_enabled(session_id, False)
            if not self.terminal_workspace.shell_ready(session_id):
                continue
            token = secrets.token_hex(8)
            state.pending[session_id] = token
            state.prompts[session_id] = self.terminal_workspace.shell_prompt(session_id)
            probe = f" printf '\\033]9;NodeBridgePwd:{token}:%s\\007' \"$(pwd -P)\"\r"
            try:
                self._terminal_manager.send(session_id, probe.encode("utf-8"))
            except ConnectionError:
                state.pending.pop(session_id, None)
        if state.pending:
            self._broadcast_timer.start()
        else:
            QTimer.singleShot(0, self._finish_broadcast_check)

    def _terminal_output(self, session_id: str, text: str) -> None:
        state = self._broadcast_check
        if state is None or session_id not in state.pending:
            visible = self._filter_probe_prompt(session_id, text)
            if visible:
                self.terminal_workspace.append_output(session_id, visible)
            return
        token = state.pending[session_id]
        marker = f"\x1b]9;NodeBridgePwd:{token}:"
        buffered = state.buffers.get(session_id, "") + text
        start = buffered.find(marker)
        end = buffered.find("\x07", start + len(marker)) if start >= 0 else -1
        if end >= 0:
            directory = buffered[start + len(marker):end]
            if directory.startswith("/"):
                state.directories[session_id] = directory
            self._probe_prompt_pending[session_id] = (state.prompts.get(session_id, ""), "")
            QTimer.singleShot(2000, lambda sid=session_id: self._expire_probe_prompt(sid))
            visible = self._filter_probe_prompt(session_id, buffered[end + 1:])
            if visible:
                self.terminal_workspace.append_output(session_id, visible)
            state.pending.pop(session_id, None)
            state.buffers.pop(session_id, None)
            if not state.pending:
                QTimer.singleShot(0, self._finish_broadcast_check)
            return
        if len(buffered) > 16384:
            buffered = buffered[-256:]
        state.buffers[session_id] = buffered

    def _filter_probe_prompt(self, session_id: str, text: str) -> str:
        awaiting = self._probe_prompt_pending.get(session_id)
        if awaiting is None:
            return text
        prompt, prior = awaiting
        cleaned = ANSI_ESCAPE.sub("", prior + text)
        index = cleaned.find(prompt) if prompt else -1
        if index < 0:
            for line in re.finditer(r"(?:^|[\r\n])([^\r\n]*)", cleaned):
                match = PLAIN_PROMPT.match(line.group(1))
                if match is not None and not line.group(1)[match.end():].strip():
                    index = line.start(1)
                    prompt = line.group(1)
                    break
        if index >= 0:
            self._probe_prompt_pending.pop(session_id, None)
            return cleaned[index + len(prompt):]
        self._probe_prompt_pending[session_id] = (prompt, prior + text)
        return ""

    def _expire_probe_prompt(self, session_id: str) -> None:
        awaiting = self._probe_prompt_pending.pop(session_id, None)
        if awaiting is not None and awaiting[1]:
            self.terminal_workspace.append_output(session_id, ANSI_ESCAPE.sub("", awaiting[1]))

    def _finish_broadcast_check(self) -> None:
        state = self._broadcast_check
        if state is None:
            return
        self._broadcast_check = None
        self._broadcast_timer.stop()
        # A probe that never produced its marker is unverified; its echoed
        # internal command is intentionally kept out of the visible terminal.
        for session_id in state.targets.values():
            self.terminal_workspace.set_terminal_input_enabled(session_id, True)
        if (not self.terminal_workspace.valid_broadcast(state.group_id, state.targets)
                or any(not self._terminal_manager.has_session(sid) for sid in state.targets.values())):
            QMessageBox.warning(self, "广播未发送", "检查期间有目标终端断开。请重新选择后再试。")
            self.terminal_workspace.set_broadcast_busy(False)
            self._record_broadcast(state, "未发送", "检查期间有目标终端断开")
            return
        directories = [state.directories.get(sid) for sid in state.targets.values()]
        same_directory = all(directories) and len(set(directories)) == 1
        if not same_directory:
            details = [
                f"{self.terminal_workspace.node_label(node_id)}："
                f"{state.directories.get(sid) or '目录未知或终端不在可识别的 Shell 提示符'}"
                for node_id, sid in state.targets.items()
            ]
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("广播前确认")
            dialog.setText("目标终端的工作目录不同或无法核实。仍向全部目标发送命令？")
            dialog.setInformativeText("\n".join(details[:12]) +
                                      (f"\n其余 {len(details) - 12} 个节点见详情。" if len(details) > 12 else ""))
            dialog.setDetailedText("\n".join(details))
            send_button = dialog.addButton("仍然发送", QMessageBox.ButtonRole.AcceptRole)
            cancel_button = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(cancel_button)
            dialog.exec()
            if dialog.clickedButton() is not send_button:
                self.status.setText("广播已取消；命令保留在广播输入框中。")
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "工作目录不同或无法核实")
                return

        if not self.terminal_workspace.valid_broadcast(state.group_id, state.targets):
            self.status.setText("广播未发送：终端组或目标已变化。")
            self.terminal_workspace.set_broadcast_busy(False)
            self._record_broadcast(state, "未发送", "终端组或目标已变化")
            return

        prefixes = [state.conda_prefixes.get(sid) for sid in state.targets.values()]
        if (all(prefix is not None for prefix in prefixes)
                and len(set(prefixes)) > 1
                and not self.terminal_workspace.ignores_conda_mismatch(state.group_id)):
            details = [
                f"{self.terminal_workspace.node_label(node_id)}："
                + (" ".join(f"({name})" for name in state.conda_prefixes[sid])
                   if state.conda_prefixes[sid] else "无 Conda 前缀")
                for node_id, sid in state.targets.items()
            ]
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("广播前确认 Conda 环境")
            dialog.setText("目标终端显示的 Conda 环境前缀不同。是否继续广播？")
            dialog.setInformativeText("\n".join(details[:12]) +
                                      (f"\n其余 {len(details) - 12} 个节点见详情。"
                                       if len(details) > 12 else ""))
            dialog.setDetailedText("\n".join(details))
            cancel_button = dialog.addButton("不发送", QMessageBox.ButtonRole.RejectRole)
            send_button = dialog.addButton("此次仍要发送", QMessageBox.ButtonRole.AcceptRole)
            ignore_button = dialog.addButton(
                "该终端组不再提示", QMessageBox.ButtonRole.AcceptRole
            )
            dialog.setDefaultButton(cancel_button)
            dialog.exec()
            if dialog.clickedButton() is ignore_button:
                self.terminal_workspace.ignore_conda_mismatch(state.group_id)
            elif dialog.clickedButton() is not send_button:
                self.status.setText("广播已取消：Conda 环境前缀不同；命令保留在输入框中。")
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "Conda 环境前缀不同")
                return

        if DESTRUCTIVE_SHELL_COMMAND.search(state.command):
            group_name = self.terminal_workspace.group_name(state.group_id)
            response = QMessageBox.warning(
                self, "确认高风险广播",
                f"{group_name} 将向 {len(state.targets)} 个终端广播可能修改或删除数据的命令。\n"
                "请核对组和目标后决定是否继续。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response != QMessageBox.StandardButton.Yes:
                self.status.setText("高风险广播已取消；命令保留在输入框中。")
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "高风险命令确认未通过")
                return
            if not self.terminal_workspace.valid_broadcast(state.group_id, state.targets):
                self.status.setText("广播未发送：终端组或目标已变化。")
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "未发送", "终端组或目标已变化")
                return

        sent = 0
        errors = []
        sent_nodes = []
        self.terminal_workspace.begin_broadcast_round(state.group_id, state.command, state.targets)
        for node_id, session_id in state.targets.items():
            try:
                self._terminal_manager.send(session_id, (state.command + "\r").encode("utf-8"))
                sent += 1
                sent_nodes.append(self.terminal_workspace.node_label(node_id))
            except ConnectionError as exc:
                self.terminal_workspace.mark_broadcast_send_failed(state.group_id, session_id)
                errors.append(f"{self.terminal_workspace.node_label(node_id)}：{exc}")
        if sent:
            self.terminal_workspace.broadcast_input.clear()
        self.status.setText(
            f"{self.terminal_workspace.group_name(state.group_id)}：命令已发送至 "
            f"{sent}/{len(state.targets)} 个终端；可在联合显示或各终端查看输出。"
        )
        self.terminal_workspace.set_broadcast_busy(False)
        if sent:
            self.terminal_workspace.broadcast_input.setFocus()
        self._record_broadcast(
            state, "已提交发送" if not errors else "部分发送" if sent else "发送失败",
            f"{sent}/{len(state.targets)} 个终端已提交发送；远程执行结果需查看终端输出",
            sent_nodes=sent_nodes, errors=errors,
        )
        if errors:
            QMessageBox.warning(self, "广播发送失败", "\n".join(errors))

    def _resize_terminal(self, session_id: str, columns: int, rows: int) -> None:
        self._terminal_manager.resize(session_id, columns, rows)

    def _terminal_input_failed(self, _session_id: str, error: str) -> None:
        state = self._broadcast_check
        if state is not None and _session_id in state.pending:
            state.pending.pop(_session_id, None)
            state.buffers.pop(_session_id, None)
            if not state.pending:
                QTimer.singleShot(0, self._finish_broadcast_check)
        self.status.setText(f"终端输入失败：{error}")
        self._record_activity("terminal", "SSH 终端输入", "发送失败", error)

    def _close_terminals(self, node_ids: list[str]) -> None:
        closed = self._terminal_manager.close_nodes(node_ids)
        self.status.setText(f"已关闭 {closed} 个终端连接。")

    def _terminal_closed(self, session_id: str, unexpected: bool) -> None:
        label = self.terminal_workspace.session_node_label(session_id)
        self._record_activity("connection", f"SSH 终端断开（{label}）",
                              "意外断开" if unexpected else "成功")
        host = self._terminal_hosts.pop(session_id, None)
        if host is not None:
            self._release_unused_terminal_helpers()
        state = self._broadcast_check
        if state is not None and session_id in state.pending:
            state.pending.pop(session_id, None)
            state.buffers.pop(session_id, None)
            if not state.pending:
                QTimer.singleShot(0, self._finish_broadcast_check)
        self._probe_prompt_pending.pop(session_id, None)
        if unexpected:
            self.terminal_workspace.mark_terminal_closed(session_id)
            self.status.setText("SSH Shell 已关闭；终端输出仍可查看。")
        else:
            self.terminal_workspace.remove_terminal(session_id)

    def _release_unused_terminal_helpers(self) -> None:
        for host in list(self._terminal_jump_helpers):
            if (id(host) not in self._terminal_helper_reservations
                    and host not in self._terminal_hosts.values()):
                self._terminal_jump_helpers.remove(host)
                try:
                    host.close()
                except Exception:
                    pass

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
        self.worker_browser.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.worker_browser.table.customContextMenuRequested.connect(self._worker_context_menu)
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
            (self.worker_browser.table, (("Delete", self._delete_worker_selection),)),
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

    def _save_local_path(self, path: str) -> None:
        self._settings.setValue("files/local_path", path)
        self._settings.sync()

    def _set_operation_log_visible(self, visible: bool) -> None:
        self.log_panel.setVisible(visible)
        self._settings.setValue("view/show_operation_log", visible)
        self._settings.sync()

    def _open_log_file(self) -> None:
        try:
            path = self._activity_log.ensure_file()
        except OSError as exc:
            QMessageBox.warning(self, "打开日志文件", f"无法创建日志文件：{exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "打开日志文件", f"无法打开日志文件：{path}")

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于 NodeBridge", "NodeBridge — SSH/SFTP 文件管理器")

    def _show_user_guide(self) -> None:
        if self._help_dialog is None:
            path = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
            try:
                guide = path.read_text(encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "使用指南", f"无法读取使用指南：{exc}")
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("NodeBridge 使用指南")
            dialog.resize(860, 650)
            layout = QVBoxLayout(dialog)
            browser = QTextBrowser(dialog)
            browser.setObjectName("userGuideBrowser")
            browser.setMarkdown(guide)
            browser.setOpenExternalLinks(True)
            layout.addWidget(browser)
            close_button = QPushButton("关闭", dialog)
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            self._help_dialog = dialog
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()

    def _update_controls(self) -> None:
        busy = self._job is not None
        copying = self._copy_job is not None
        connected = self._session is not None
        self.connect_button.setEnabled(not busy and not copying and not connected)
        self.jump_button.setEnabled(not busy and not copying and connected)
        self.disconnect_button.setEnabled(not busy and not copying and connected)
        self.disconnect_button.setText("返回上一站点" if self._parents else "断开")
        self.site_button.setEnabled(not busy and not copying)
        for widget in (self.host, self.port, self.user, self.key_file, self.password):
            widget.setEnabled(not busy and not connected)
        for widget in (self.up_button, self.refresh_button, self.path_edit, self.tree, self.table):
            widget.setEnabled(not busy and connected)
        self.local_tree.setEnabled(not busy)
        self.local_table.setEnabled(not busy)
        self.worker_browser.set_busy(busy, connected and not self._parents)
        if copying:
            self.worker_browser.connect_button.setEnabled(False)
            self.worker_browser.disconnect_button.setEnabled(False)
            self.worker_browser.add_button.setEnabled(False)
        self.terminal_workspace.set_busy(busy)

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
        if (jump is None or self._job is not None or self._copy_job is not None
                or self._current_listing is None):
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
            self._sync_terminal_nodes()
            self.jump_title.setText(f"当前站点 · {alias}")
            self._show_connection_fields(self._session.config)
            self.tree.clear()
            self._show_listing(listing)
            self.status.setText(f"已通过 {jump.config.host} 的 SSH 别名连接到 {alias}，当前目录：{listing.path}")

        self._run(
            work, success, f"正在通过当前站点连接 SSH 别名 {alias}…",
            log_action=f"SSH 连接（{jump.config.host} → {alias}）",
            log_category="connection", log_context={"node": alias, "via": jump.config.host},
        )

    def _discover_worker_aliases(self, manual: bool = False) -> None:
        jump = self._session
        if jump is None:
            if manual:
                QMessageBox.information(self, "发现间接节点", "请先连接直连节点。")
            return
        if self._parents:
            if manual:
                QMessageBox.information(self, "发现间接节点", "请先返回直连节点，再发现间接节点。")
            return
        if self._job is not None:
            if manual:
                self.status.setText("请等待当前任务结束后再发现节点。")
            return

        self.worker_browser.summary.setText("正在读取 SSH 配置及 /etc/hosts 中的编号主机名…")

        def success(result: NodeDiscovery) -> None:
            self.worker_browser.set_aliases(list(result.aliases))
            self._sync_terminal_nodes()
            if result.aliases:
                self._pending_worker_probe = True
                message = (
                    f"在 {jump.config.host} 发现 {len(result.aliases)} 个候选节点："
                    f"SSH 配置 {len(result.ssh_config_aliases)} 个，"
                    f"编号主机名解析 {len(result.numbered_host_aliases)} 个。"
                )
            else:
                message = (
                    f"在 {jump.config.host} 未找到可列出的 SSH 别名或编号主机名。"
                    "可用“添加别名…”手动连接，并检查直连节点的主机名及名称解析。"
                )
                self.worker_browser.summary.setText(message)
            self.status.setText(message)
            if manual:
                QMessageBox.information(self, "发现间接节点", message)

        self._run(jump.discover_work_nodes, success, "正在发现直连节点可解析的工作主机…")

    def _probe_worker_aliases(self) -> None:
        jump = self._session
        if jump is None or self._parents:
            return
        if self._probe_job is not None:
            self._pending_worker_probe = True
            return
        aliases = [alias for alias in self.worker_browser.known_aliases() if alias not in self._workers]
        if not aliases:
            return
        self._probe_generation += 1
        generation = self._probe_generation
        cancel = threading.Event()
        self._probe_cancel = cancel
        config, password = jump.config, self._jump_password
        self.worker_browser.set_probe_statuses({
            alias: ("checking", "正在通过直连节点测试 SSH 连接") for alias in aliases
        })

        def work():
            statuses = {}
            helper = RemoteSession.connect_shell_host(config, password)
            try:
                with ThreadPoolExecutor(max_workers=min(4, len(aliases))) as pool:
                    futures = {pool.submit(helper.probe_alias, alias): alias for alias in aliases}
                    for future in as_completed(futures):
                        alias = futures[future]
                        if cancel.is_set():
                            break
                        try:
                            statuses[alias] = future.result()
                        except Exception as exc:
                            statuses[alias] = ("error", f"{type(exc).__name__}: {exc}")
            finally:
                helper.close()
            return statuses

        def success(statuses):
            if generation != self._probe_generation or self._session is not jump or self._parents:
                return
            self.worker_browser.set_probe_statuses(statuses)
            reachable = sum(state == "reachable" for state, _ in statuses.values())
            self.status.setText(
                f"SSH 检测完成：{reachable}/{len(statuses)} 个候选节点可连接。"
                " 灰色节点仍可勾选并重新尝试连接。"
            )

        job = JobThread(work, self)
        self._probe_job = job
        job.succeeded.connect(success)
        def failed(message):
            if generation != self._probe_generation or cancel.is_set():
                return
            self.worker_browser.set_probe_statuses({
                alias: ("error", message) for alias in aliases
            })
            self.status.setText(f"SSH 后台探测失败：{message}")
            logging.getLogger("nodebridge.probe").warning(
                "SSH 节点后台探测失败：%s", self._redact_log_value(message),
            )
        job.failed.connect(failed)
        job.finished.connect(lambda: self._probe_finished(job))
        job.finished.connect(job.deleteLater)
        job.start()

    def _probe_finished(self, job: JobThread) -> None:
        if self._probe_job is job:
            self._probe_job = None
        if self._pending_worker_probe and self._session is not None and not self._parents:
            self._pending_worker_probe = False
            QTimer.singleShot(0, self._probe_worker_aliases)
        self._finish_background_close()

    def _add_worker_alias(self) -> None:
        if (self._session is None or self._parents or self._job is not None
                or self._copy_job is not None):
            return
        alias, accepted = QInputDialog.getText(self, "添加间接节点", "直连节点上的 SSH 别名：")
        alias = alias.strip()
        if not accepted or not alias:
            return
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
            QMessageBox.warning(self, "间接节点", "别名只能包含字母、数字、点、下划线、@ 和连字符。")
            return
        self.worker_browser.set_aliases([alias])
        self._sync_terminal_nodes()
        for index in range(self.worker_browser.nodes.topLevelItemCount()):
            self.worker_browser.nodes.topLevelItem(index).setCheckState(0, Qt.CheckState.Unchecked)
        for index in range(self.worker_browser.nodes.topLevelItemCount()):
            item = self.worker_browser.nodes.topLevelItem(index)
            if item.text(0) == alias:
                item.setCheckState(0, Qt.CheckState.Checked)
                self.worker_browser.nodes.setCurrentItem(item)
                break
        self._connect_selected_workers()

    def _connect_selected_workers(self) -> None:
        jump = self._session
        if jump is None or self._parents or self._job is not None or self._copy_job is not None:
            return
        aliases = [alias for alias in self.worker_browser.target_aliases() if alias not in self._workers]
        self.worker_browser._check_all(False)
        if not aliases:
            self.status.setText("请勾选尚未连接的间接节点，或通过“添加别名…”输入节点。")
            return
        self._cancel_auto_hash()
        path = self.worker_browser.path_edit.text().strip()

        def connect_one(alias: str, host: RemoteSession):
            worker = RemoteSession.connect_alias(alias, host)
            try:
                try:
                    listing = worker.list_directory(path) if path else worker.list_directory(worker.home())
                except OSError:
                    listing = worker.list_directory(worker.home())
                return worker, listing
            except Exception:
                worker.close()
                raise

        def work():
            results = {}
            errors = {}
            new_helpers = []
            roots = [jump, *self._jump_helpers]
            counts = {id(root): 0 for root in roots}
            for host in self._worker_jumps.values():
                counts[id(host)] += 1
            assigned = {}
            for alias in aliases:
                host = next((root for root in roots if counts[id(root)] < 8), None)
                if host is None:
                    try:
                        host = RemoteSession.connect(jump.config, self._jump_password)
                    except Exception as exc:
                        for remaining in aliases[aliases.index(alias):]:
                            errors[remaining] = f"无法建立额外跳板连接：{exc}"
                        break
                    roots.append(host)
                    new_helpers.append(host)
                    counts[id(host)] = 0
                counts[id(host)] += 1
                assigned[alias] = host
            with ThreadPoolExecutor(max_workers=min(4, len(assigned)) or 1) as pool:
                futures = {
                    pool.submit(connect_one, alias, host): alias
                    for alias, host in assigned.items()
                }
                for future in as_completed(futures):
                    alias = futures[future]
                    try:
                        results[alias] = future.result()
                    except Exception as exc:
                        errors[alias] = str(exc)
            kept_helpers = []
            for host in new_helpers:
                if any(alias in results and assigned[alias] is host for alias in results):
                    kept_helpers.append(host)
                else:
                    try:
                        host.close()
                    except Exception:
                        pass
            return results, errors, kept_helpers, assigned

        def success(result):
            results, errors, helpers, assigned = result
            self._jump_helpers.extend(helpers)
            for alias, (worker, listing) in results.items():
                self._workers[alias] = worker
                self._worker_jumps[alias] = assigned[alias]
                self.worker_browser.set_connection(alias, listing)
            for alias, message in errors.items():
                self.worker_browser.set_probe_status(alias, "error", message)
            self._sync_terminal_nodes()
            self._schedule_auto_hash()
            self.status.setText(f"间接节点：新连接 {len(results)} 个，失败 {len(errors)} 个。")
            if errors:
                lines = [f"{alias}: {message}" for alias, message in list(errors.items())[:8]]
                QMessageBox.warning(self, "间接节点连接结果", "以下节点连接失败：\n" + "\n".join(lines))

        self._run(
            work, success, f"正在经 {jump.config.host} 连接 {len(aliases)} 个间接节点…",
            log_action=f"SFTP 并行连接（{compact_node_aliases(aliases)}）",
            log_category="connection", log_context={"nodes": aliases, "via": jump.config.host},
            log_result=lambda value: (
                "成功" if not value[1] else "部分完成" if value[0] else "失败",
                f"{len(value[0])}/{len(aliases)} 个节点连接成功"
                + ("；" + "；".join(f"{alias}：{error}" for alias, error in value[1].items())
                   if value[1] else ""),
            ),
            log_context_result=lambda value: {"errors": value[1]},
        )

    def _disconnect_selected_workers(self) -> None:
        if self._job is not None or self._copy_job is not None:
            return
        aliases = [alias for alias in self.worker_browser.target_aliases() if alias in self._workers]
        self.worker_browser._check_all(False)
        if not aliases:
            return
        self._cancel_auto_hash()
        workers = {alias: self._workers[alias] for alias in aliases}

        def work():
            errors = {}
            for alias, worker in workers.items():
                try:
                    worker.close()
                except Exception as exc:
                    errors[alias] = str(exc)
            return errors

        def success(errors):
            for alias in aliases:
                self._workers.pop(alias, None)
                self._worker_jumps.pop(alias, None)
                self.worker_browser.set_connection(alias, None)
            self._sync_terminal_nodes()
            self._schedule_auto_hash()
            self.status.setText(f"已断开 {len(aliases)} 个间接节点；关闭错误 {len(errors)} 个。")

        self._run(
            work, success, f"正在断开 {len(aliases)} 个间接节点…",
            log_action=f"SFTP 并行断开（{compact_node_aliases(aliases)}）",
            log_category="connection", log_context={"nodes": aliases},
            log_result=lambda errors: (
                "成功" if not errors else "部分完成",
                f"关闭时 {len(errors)} 个节点报错"
                + ("；" + "；".join(f"{alias}：{error}" for alias, error in errors.items())
                   if errors else ""),
            ),
            log_context_result=lambda errors: {"errors": errors},
        )

    def _browse_workers(self, path: str) -> None:
        if path.startswith("/"):
            self._cancel_auto_hash()
        if self._job is not None:
            if path.startswith("/"):
                self._pending_worker_refresh = path
            return
        if not path.startswith("/"):
            self.status.setText("间接节点路径必须是绝对路径。")
            return
        if self.worker_browser.mode.currentIndex() == 1:
            alias = self.worker_browser.focused_alias
            names = [alias] if alias in self._workers else []
        else:
            names = sorted(self._workers)
        if not names:
            self.status.setText("请先连接间接节点。")
            return
        sessions = {name: self._workers[name] for name in names}

        def work():
            results = {}
            errors = {}
            with ThreadPoolExecutor(max_workers=min(4, len(sessions))) as pool:
                futures = {pool.submit(session.list_directory, path): alias for alias, session in sessions.items()}
                for future in as_completed(futures):
                    alias = futures[future]
                    try:
                        results[alias] = future.result()
                    except Exception as exc:
                        errors[alias] = str(exc)
            return results, errors

        def success(result):
            results, errors = result
            self.worker_browser.path_edit.setText(path)
            self.worker_browser.set_listings(results, list(errors))
            self._schedule_auto_hash()
            self.status.setText(f"间接节点目录：已读取 {len(results)} 个，失败 {len(errors)} 个。")
            if errors:
                lines = [f"{alias}: {message}" for alias, message in list(errors.items())[:8]]
                QMessageBox.warning(self, "读取间接节点目录", "以下节点读取失败：\n" + "\n".join(lines))

        self._run(work, success, f"正在读取 {len(names)} 个间接节点的 {path}…")

    def _cancel_auto_hash(self) -> None:
        self._hash_generation += 1
        self._hash_pending = None
        self._hash_cancel.set()

    def _schedule_auto_hash(self) -> None:
        self._cancel_auto_hash()
        if self._session is None or self._parents:
            return
        path, aliases, names = self.worker_browser.auto_hash_candidates()
        if not names:
            return
        generation = self._hash_generation
        self._hash_pending = (path, aliases, names, generation)
        self.worker_browser.set_hash_pending(path, names)
        if self._hash_job is None:
            self._start_auto_hash()

    def _start_auto_hash(self) -> None:
        request = self._hash_pending
        if request is None or self._hash_job is not None or self._session is None:
            return
        self._hash_pending = None
        path, aliases, names, generation = request
        jump = self._session
        config, password = jump.config, self._jump_password
        snapshots = self.worker_browser.hash_listing_snapshots(path, aliases, names)
        cancel = threading.Event()
        self._hash_cancel = cancel
        self.worker_browser.set_hash_pending(path, names, "校验中")

        def work():
            helper = RemoteSession.connect_shell_host(config, password)
            try:
                return compare_remote_files_isolated(
                    helper, aliases, path, names, cancel_event=cancel,
                    snapshots=snapshots,
                    progress=lambda done, total: self.autoCheckProgress.emit(
                        (generation, done, total, path),
                    ),
                )
            finally:
                helper.close()

        def success(results):
            if (generation != self._hash_generation or self._session is not jump
                    or self.worker_browser.rendered_path != path
                    or self.worker_browser.mode.currentIndex() != 0):
                return
            self.worker_browser.set_hash_results(path, results)
            differences = sum(result["result"] != "same" for result in results)
            self.status.setText(
                f"后台 SHA-256 校验完成：{len(results)} 个文件，{differences} 项差异或错误。"
            )
            errors = [
                f"{entry['name']}@{alias}: {node['detail']}"
                for entry in results for alias, node in entry["nodes"].items()
                if node["state"] == "error"
            ]
            if errors:
                logging.getLogger("nodebridge.verification").warning(
                    "自动核对 %s：%s 个节点文件失败；%s", path, len(errors),
                    self._redact_log_value("；".join(errors[:10])),
                )

        def failed(message):
            if generation != self._hash_generation or cancel.is_set():
                return
            self.worker_browser.set_hash_pending(path, names, "校验失败")
            self.status.setText(f"后台 SHA-256 校验失败：{message}")
            logging.getLogger("nodebridge.verification").warning(
                "后台 SHA-256 校验失败：%s", self._redact_log_value(message),
            )

        job = JobThread(work, self)
        self._hash_job = job
        job.succeeded.connect(success)
        job.failed.connect(failed)
        job.finished.connect(lambda: self._hash_finished(job))
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot(object)
    def _auto_check_progress(self, value: object) -> None:
        generation, done, total, path = value
        if generation == self._hash_generation and self._hash_job is not None:
            self.status.setText(f"后台 SHA-256 校验 {path}：已检查 {done}/{total} 个节点…")

    def _hash_finished(self, job: JobThread) -> None:
        if self._hash_job is job:
            self._hash_job = None
        if self._hash_pending is not None:
            QTimer.singleShot(0, self._start_auto_hash)
        self._finish_background_close()

    def _choose_worker_scope(self, verb: str) -> list[str]:
        connected = sorted(self._workers, key=str.casefold)
        current = self.worker_browser.focused_alias
        if current not in self._workers:
            current = connected[0] if connected else None
        if current is None:
            QMessageBox.warning(self, "间接节点", "请先连接并选中一个间接节点。")
            return []
        if len(connected) < 2:
            return [current]
        dialog = QMessageBox(self)
        dialog.setWindowTitle(f"{verb}范围")
        dialog.setText(f"要在当前节点 {current} 上{verb}，还是在已连接的 {len(connected)} 个节点上并行{verb}？")
        single = dialog.addButton(f"仅 {current}", QMessageBox.ButtonRole.AcceptRole)
        multiple = dialog.addButton(f"已连接的 {len(connected)} 个节点", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is single:
            return [current]
        if dialog.clickedButton() is multiple:
            return connected
        return []

    def _drop_local_on_workers(self, paths: list[str], destination: str) -> None:
        aliases = self._choose_worker_scope("复制")
        if aliases:
            self._batch_operation(BatchAction(
                "local_to_workers", aliases, paths, destination,
                self.worker_browser.rendered_path,
            ))

    def _drop_jump_on_workers(self, token: str, paths: list[str], destination: str) -> None:
        if self._session is None or token != str(id(self._session)):
            QMessageBox.warning(self, "复制文件", "拖动来源不是当前直连节点。")
            return
        aliases = self._choose_worker_scope("复制")
        if aliases:
            self._batch_operation(BatchAction(
                "jump_to_workers", aliases, paths, destination,
                self.worker_browser.rendered_path,
            ))

    def _collect_workers_to_jump(self, token: str, paths: list[str], destination: str) -> None:
        self._collect_worker_files(token, paths, destination, to_jump=True)

    @staticmethod
    def _inspect_collection_roots(
        paths: list[str], sessions: dict[str, RemoteSession],
    ) -> tuple[dict[str, list[str]], dict[str, BatchResult]]:
        mapped: dict[str, list[str]] = {}
        failures: dict[str, BatchResult] = {}

        def inspect_one(alias: str, session: RemoteSession) -> list[str]:
            return [node_suffixed_name(
                posixpath.basename(path), alias,
                stat.S_ISDIR(session.sftp.lstat(path).st_mode or 0),
            ) for path in paths]

        with ThreadPoolExecutor(max_workers=min(4, len(sessions))) as pool:
            futures = {pool.submit(inspect_one, alias, session): alias for alias, session in sessions.items()}
            for future in as_completed(futures):
                alias = futures[future]
                try:
                    mapped[alias] = future.result()
                except Exception as exc:
                    failures[alias] = BatchResult(alias, error=f"{type(exc).__name__}: {exc}")
        targets: dict[str, str] = {}
        for alias in sessions:
            for name in mapped.get(alias, []):
                previous = targets.setdefault(name.casefold(), alias)
                if previous != alias:
                    raise FileExistsError(f"不同节点的目标名称冲突：{name}（{previous}、{alias}）")
        return mapped, failures

    def _collect_worker_files(
        self, token: str, paths: list[str], destination: str, *, to_jump: bool,
    ) -> None:
        jump = self._session
        if (jump is None or self._parents or self._job is not None or self._copy_job is not None
                or token != self.worker_browser.drag_token):
            return
        if not paths or any(not path.startswith("/") for path in paths):
            return
        parents = {posixpath.dirname(path.rstrip("/")) for path in paths}
        if len(parents) != 1 or not next(iter(parents)).startswith("/"):
            QMessageBox.warning(self, "汇集文件", "请从同一个间接节点目录选择文件或文件夹。")
            return
        if to_jump and not destination.startswith("/"):
            QMessageBox.warning(self, "汇集文件", "跳板目标必须是绝对路径。")
            return
        if not to_jump and not Path(destination).is_dir():
            QMessageBox.warning(self, "汇集文件", "本地目标目录不存在。")
            return
        aliases = self._choose_worker_scope("汇集复制")
        if not aliases:
            return
        sessions = {alias: self._workers[alias] for alias in aliases}
        browsed_jump_path = self._path
        self._apply_conflict_choice = None
        self._batch_mode_label = "汇集到跳板" if to_jump else "汇集到本地"
        self.batch_results.setRowCount(0)

        def work():
            peers = {}
            connection_failures = {}
            try:
                for alias, session in sessions.items():
                    try:
                        peers[alias] = session.open_sftp_peer()
                    except Exception as exc:
                        connection_failures[alias] = BatchResult(
                            alias, error=f"{type(exc).__name__}: {exc}",
                        )
                return collect_with_peers(peers, connection_failures)
            finally:
                for peer in peers.values():
                    peer.close()

        def collect_with_peers(peers, connection_failures):
            mapped, failures = self._inspect_collection_roots(paths, peers) if peers else ({}, {})
            failures.update(connection_failures)

            def copy_one(alias: str, session: RemoteSession) -> BatchResult:
                progress = lambda path: self.transferProgress.emit(f"{alias}：正在汇集 {path}")
                if not to_jump:
                    result = copy_remote_to_local(
                        session, paths, destination, progress, self._resolve_conflict,
                        root_suffix=alias,
                    )
                else:
                    # Every worker gets a separate jump SFTP connection for writes.
                    target = jump.open_sftp_peer()
                    try:
                        result = copy_remote_between_sessions(
                            session, target, paths, destination, progress,
                            self._resolve_conflict, root_suffix=alias,
                        )
                    finally:
                        target.close()
                return BatchResult(alias, copied=result)

            for result in failures.values():
                self.batchResultReady.emit(result)
            good = {alias: session for alias, session in peers.items() if alias in mapped}
            copied = run_parallel(good, copy_one, self.batchResultReady.emit) if good else []
            results = {result.alias: result for result in [*failures.values(), *copied]}
            listing = None
            if to_jump:
                try:
                    listing = jump.list_directory(browsed_jump_path)
                except Exception:
                    pass
            return [results[alias] for alias in aliases], listing

        def success(value):
            results, listing = value
            if to_jump and listing is not None and self._path == browsed_jump_path:
                self._show_listing(listing)
            elif not to_jump:
                self.local.refresh()
            complete = sum(result.complete for result in results)
            self._notify_done(f"汇集完成：{complete}/{len(results)} 个节点成功；详情见下方结果表。")

        self._run(
            work, success, f"正在从 {len(aliases)} 个间接节点汇集文件…",
            log_action=f"并行汇集（{compact_node_aliases(aliases)}）：{self._log_paths(paths)} → {destination}；顶层名称加节点后缀",
            log_result=self._batch_log_result,
            log_context={"sources": paths, "destination": destination, "nodes": aliases},
            log_context_result=self._batch_log_context,
            background_copy=True,
        )

    def _delete_worker_selection(self) -> None:
        if self._copy_job is not None:
            return
        names = self.worker_browser.selected_names()
        if not names:
            return
        aliases = self._choose_worker_scope("删除")
        if aliases:
            self._batch_operation(BatchAction(
                "delete_workers", aliases, names, "", self.worker_browser.rendered_path,
            ))

    def _create_worker_directory(self) -> None:
        if (self._session is None or self._parents or self._job is not None
                or self._copy_job is not None):
            return
        parent = self.worker_browser.rendered_path
        if not parent.startswith("/"):
            QMessageBox.warning(self, "新建目录", "请先打开间接节点上的绝对目录。")
            return
        name, accepted = QInputDialog.getText(self, "新建目录", "目录名称：")
        if not accepted:
            return
        try:
            name = validate_remote_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, "新建目录", str(exc))
            return
        aliases = self._choose_worker_scope("新建目录")
        if not aliases:
            return
        nodes = {alias: self._workers[alias] for alias in aliases}
        target = posixpath.join(parent, name)
        self.batch_results.setRowCount(0)

        def work() -> WorkerDirectoryOutcome:
            conflicts: list[str] = []
            errors: dict[str, str] = {}

            def inspect(alias: str, session: RemoteSession) -> bool:
                parent_info = session.sftp.stat(parent)
                if not stat.S_ISDIR(parent_info.st_mode or 0):
                    raise NotADirectoryError(f"目标不是目录：{parent}")
                return remote_directory_conflict(session, parent, name)

            with ThreadPoolExecutor(max_workers=min(4, len(nodes))) as pool:
                futures = {pool.submit(inspect, alias, session): alias
                           for alias, session in nodes.items()}
                for future in as_completed(futures):
                    alias = futures[future]
                    try:
                        if future.result():
                            conflicts.append(alias)
                    except Exception as exc:
                        errors[alias] = f"{type(exc).__name__}: {exc}"
            if errors:
                raise RuntimeError("创建前检查失败，未在任何节点创建目录：" + "；".join(
                    f"{alias}：{errors[alias]}" for alias in aliases if alias in errors
                ))
            policy = "error"
            if conflicts:
                prompt = WorkerDirectoryPrompt(target, [alias for alias in aliases if alias in conflicts])
                self.workerDirectoryConflictRequested.emit(prompt)
                prompt.done.wait()
                policy = prompt.choice
                if policy == "cancel":
                    return WorkerDirectoryOutcome([], {}, cancelled=True)

            def create_one(alias: str, session: RemoteSession):
                try:
                    path, created = create_remote_directory_with_policy(session, parent, name, policy)
                    result = WorkerDirectoryResult(alias, path, created)
                except Exception as exc:
                    return WorkerDirectoryResult(alias, error=f"{type(exc).__name__}: {exc}"), None
                try:
                    listing = session.list_directory(parent)
                except Exception:
                    listing = None
                return result, listing

            completed: dict[str, WorkerDirectoryResult] = {}
            listings: dict[str, DirectoryListing] = {}
            with ThreadPoolExecutor(max_workers=min(4, len(nodes))) as pool:
                futures = {pool.submit(create_one, alias, session): alias
                           for alias, session in nodes.items()}
                for future in as_completed(futures):
                    alias = futures[future]
                    result, listing = future.result()
                    completed[alias] = result
                    if listing is not None:
                        listings[alias] = listing
            return WorkerDirectoryOutcome([completed[alias] for alias in aliases], listings)

        def success(outcome: WorkerDirectoryOutcome) -> None:
            if outcome.cancelled:
                self.status.setText("新建目录已取消；没有创建任何目录。")
                return
            if outcome.listings:
                self.worker_browser.set_listings(outcome.listings)
                self._schedule_auto_hash()
            for result in outcome.results:
                row = self.batch_results.rowCount()
                self.batch_results.insertRow(row)
                state = "失败" if result.error else "已创建" if result.created else "已跳过"
                detail = result.error or result.path
                for column, value in enumerate((result.alias, "新建目录", state, detail)):
                    self.batch_results.setItem(row, column, QTableWidgetItem(value))
            created = sum(result.created for result in outcome.results)
            skipped = sum(not result.created and not result.error for result in outcome.results)
            failed = sum(bool(result.error) for result in outcome.results)
            self._notify_done(
                f"新建目录完成：{created} 个节点已创建，{skipped} 个跳过，{failed} 个失败；详情见下方结果表。"
            )

        def log_result(outcome: WorkerDirectoryOutcome) -> tuple[str, str]:
            if outcome.cancelled:
                return "取消", "检测到同名项目后取消；没有创建目录"
            created = sum(result.created for result in outcome.results)
            skipped = sum(not result.created and not result.error for result in outcome.results)
            failures: dict[str, list[str]] = {}
            for result in outcome.results:
                if result.error:
                    failures.setdefault(result.error, []).append(result.alias)
            detail = f"{created}/{len(aliases)} 个节点已创建，跳过 {skipped} 个"
            if failures:
                detail += "；" + "；".join(
                    f"{compact_node_aliases(group)}：{error}" for error, group in failures.items()
                )
            return "失败" if failures else "部分完成" if skipped else "成功", detail

        self._run(
            work, success, f"正在 {len(aliases)} 个间接节点上新建目录…",
            log_action=f"{'并行' if len(aliases) > 1 else ''}新建目录（{compact_node_aliases(aliases)}）：{target}",
            log_result=log_result,
            log_context={"destination": target, "nodes": aliases},
            log_context_result=lambda outcome: {"results": [
                {"node": result.alias, "path": result.path,
                 "created": result.created, "error": result.error}
                for result in outcome.results
            ]},
        )

    def _batch_operation(self, action: BatchAction | None = None) -> None:
        jump = self._session
        if jump is None or self._parents or self._job is not None or self._copy_job is not None:
            return
        if action is None:
            aliases = self.worker_browser.checked_aliases()
            jump_sources = self._remote_selected_paths()
            local_sources = self._local_selected_paths()
            worker_names = self.worker_browser.selected_names()
            worker_path = self.worker_browser.rendered_path
            dialog = BatchDialog(
                aliases, sorted(self._workers), (len(jump_sources), len(local_sources), len(worker_names)),
                (worker_path, self._path, self.local.current_path), self,
            )
            if not dialog.exec():
                return
            mode = dialog.operation
            moving = dialog.move.isChecked()
            destination = dialog.destination.text().strip()
            target_alias = dialog.target_node.currentText() if mode == "worker_to_worker" else ""
            max_workers = dialog.parallelism.value()
            sources = local_sources if mode.startswith("local_") else (
                jump_sources if mode.startswith("jump_") else worker_names
            )
        else:
            aliases = action.aliases
            worker_path = action.worker_path
            mode = action.mode
            moving = action.moving
            destination = action.destination.strip()
            target_alias = action.target_alias
            max_workers = action.max_workers
            sources = action.sources
        uses_workers = mode.endswith("to_workers") or mode.startswith("worker_") or mode == "delete_workers"
        if uses_workers and (not aliases or any(alias not in self._workers for alias in aliases)):
            QMessageBox.warning(self, "批量操作", "请先勾选并连接全部要操作的间接节点。")
            return
        if mode in {"worker_to_jump", "worker_to_local", "worker_to_worker"} and len(aliases) != 1:
            QMessageBox.warning(self, "批量操作", "从间接节点移动或复制时，请只勾选一个来源节点。")
            return
        if mode == "worker_to_worker" and (not target_alias or target_alias not in self._workers or target_alias == aliases[0]):
            QMessageBox.warning(self, "批量操作", "请选择另一个已连接的目标间接节点。")
            return
        if moving and mode.endswith("to_workers") and len(aliases) > 1:
            QMessageBox.warning(self, "批量操作", "向多个间接节点只能复制；请取消“移动”选项。")
            return
        if not sources:
            QMessageBox.warning(self, "批量操作", "请先在对应的文件列表中选中文件或文件夹。")
            return
        if mode != "delete_workers" and not destination:
            QMessageBox.warning(self, "批量操作", "请填写目标目录。")
            return
        if mode not in {"worker_to_local", "jump_to_local", "delete_workers"} and not destination.startswith("/"):
            QMessageBox.warning(self, "批量操作", "远程目标目录必须是绝对路径。")
            return
        if mode in {"worker_to_jump", "worker_to_local", "worker_to_worker", "delete_workers"} and not worker_path.startswith("/"):
            QMessageBox.warning(self, "批量操作", "间接节点当前路径必须是绝对路径。")
            return
        if mode in {"worker_to_local", "jump_to_local"} and not Path(destination).is_dir():
            QMessageBox.warning(self, "批量操作", "本地目标目录不存在。")
            return
        if mode == "delete_workers":
            choice = QMessageBox.question(
                self, "确认批量删除",
                f"在 {len(aliases)} 个间接节点上永久删除所选 {len(sources)} 个名称及其内容？\n"
                "此操作不能撤销；缺失项目将作为该节点的失败结果报告。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        elif moving:
            choice = QMessageBox.question(
                self, "确认批量移动",
                "目标完整复制成功后删除来源？\n"
                "任何跳过或取消都不会删除对应来源；已完成的节点不会自动回滚。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        nodes = ({alias: self._workers[alias] for alias in aliases} if uses_workers
                 else {"直连节点": jump})
        if uses_workers:
            self._cancel_auto_hash()
        self._apply_conflict_choice = None
        self.batch_results.setRowCount(0)
        self._batch_mode_label = next(label for label, key in BatchDialog.MODES if key == mode)

        def work_one(alias: str, session: RemoteSession) -> BatchResult:
            progress = lambda path: self.transferProgress.emit(f"{alias}：正在处理 {path}")
            if mode == "delete_workers":
                paths = [posixpath.join(worker_path, name) for name in sources]
                for path in paths:
                    session.sftp.lstat(path)
                return BatchResult(alias, deleted=delete_remote(session, paths))
            worker_sources = [posixpath.join(worker_path, name) for name in sources] if mode.startswith("worker_") else []
            source_session = session if worker_sources else jump if mode.startswith("jump_") else None
            source_paths = worker_sources or sources
            before = source_snapshot(source_session, source_paths) if moving else None
            if mode == "local_to_workers":
                result = copy_local_to_remote(session, sources, destination, progress, self._resolve_conflict)
            elif mode == "local_to_jump":
                result = copy_local_to_remote(session, sources, destination, progress, self._resolve_conflict)
            elif mode == "jump_to_local":
                result = copy_remote_to_local(session, sources, destination, progress, self._resolve_conflict)
            elif mode == "worker_to_local":
                result = copy_remote_to_local(session, worker_sources, destination, progress, self._resolve_conflict)
            elif mode == "worker_to_worker":
                target_session = self._workers[target_alias].open_sftp_peer()
                try:
                    result = copy_remote_between_sessions(
                        session, target_session, worker_sources, destination,
                        progress, self._resolve_conflict,
                    )
                finally:
                    target_session.close()
            else:
                # Give each worker an independent jump SFTP session so source
                # and destination streams can run concurrently without sharing
                # a Paramiko SFTP client across threads.
                extra_jump = RemoteSession.connect(jump.config, self._jump_password)
                try:
                    if mode == "jump_to_workers":
                        result = copy_remote_between_sessions(
                            extra_jump, session, sources, destination, progress, self._resolve_conflict
                        )
                    else:
                        result = copy_remote_between_sessions(
                            session, extra_jump, worker_sources, destination, progress, self._resolve_conflict
                        )
                finally:
                    extra_jump.close()
            deleted = 0
            if moving and not result.cancelled and result.skipped == 0:
                try:
                    if source_snapshot(source_session, source_paths) != before:
                        raise RuntimeError("来源在传输期间发生变化，未删除来源。")
                    deleted = (trash_local(sources) if source_session is None
                               else delete_remote(source_session, source_paths))
                except Exception as exc:
                    return BatchResult(alias, copied=result, error=f"复制成功，来源未完整删除：{exc}")
            return BatchResult(alias, copied=result, deleted=deleted)

        browsed_jump_path = self._path
        def work():
            peers = {}
            try:
                connection_failures = {}
                if not moving and mode != "delete_workers":
                    for alias, session in nodes.items():
                        try:
                            peers[alias] = session.open_sftp_peer()
                        except Exception as exc:
                            result = BatchResult(alias, error=f"{type(exc).__name__}: {exc}")
                            connection_failures[alias] = result
                            self.batchResultReady.emit(result)
                active_nodes = peers if not moving and mode != "delete_workers" else nodes
                completed = (run_parallel(active_nodes, work_one, self.batchResultReady.emit, max_workers)
                             if active_nodes else [])
                by_alias = {result.alias: result for result in [*completed, *connection_failures.values()]}
                results = [by_alias[alias] for alias in nodes]
                return build_batch_result(results, active_nodes)
            finally:
                for peer in peers.values():
                    peer.close()

        def build_batch_result(results, active_nodes):
            jump_listing = None
            worker_listings = {}
            if mode in {"jump_to_workers", "worker_to_jump", "jump_to_local", "local_to_jump"}:
                try:
                    jump_listing = jump.list_directory(browsed_jump_path)
                except Exception:
                    pass
            if uses_workers and worker_path.startswith("/"):
                for alias, session in active_nodes.items():
                    try:
                        worker_listings[alias] = session.list_directory(worker_path)
                    except Exception:
                        pass
            if mode == "worker_to_worker" and target_alias not in worker_listings:
                try:
                    worker_listings[target_alias] = self._workers[target_alias].list_directory(worker_path)
                except Exception:
                    pass
            return results, jump_listing, worker_listings

        def success(value):
            results, jump_listing, worker_listings = value
            if jump_listing is not None and self._path == browsed_jump_path:
                self._show_listing(jump_listing)
            if self.worker_browser.rendered_path == worker_path:
                for alias, listing in worker_listings.items():
                    self.worker_browser.set_listing(alias, listing)
            if worker_listings and self.worker_browser.rendered_path == worker_path:
                self._schedule_auto_hash()
            self.local.refresh()
            complete = sum(result.complete for result in results)
            self._notify_done(f"批量操作完成：{complete}/{len(results)} 个节点成功；详情见下方结果表。")

        targets = compact_node_aliases(list(nodes))
        action_name = "删除" if mode == "delete_workers" else "移动" if moving else "复制"
        source_text = self._log_paths(sources)
        target_text = "" if mode == "delete_workers" else f" → {destination}"
        self._run(
            work, success, f"正在对 {len(nodes)} 个间接节点执行批量操作…",
            log_action=f"并行{action_name}（{targets}）：{source_text}{target_text}",
            log_result=self._batch_log_result,
            log_context={"sources": sources, "destination": destination, "nodes": list(nodes)},
            log_context_result=self._batch_log_context,
            background_copy=not moving and mode != "delete_workers",
        )

    @Slot(object)
    def _show_batch_result(self, result: BatchResult) -> None:
        row = self.batch_results.rowCount()
        self.batch_results.insertRow(row)
        state = "失败" if result.error else "取消" if result.copied and result.copied.cancelled else (
            "已跳过" if result.copied and result.copied.skipped else "完成"
        )
        detail = result.error or (
            f"{result.copied.files} 文件，{result.copied.directories} 目录，"
            f"跳过 {result.copied.skipped}，删除来源 {result.deleted}"
            if result.copied else f"已删除 {result.deleted} 项"
        )
        for column, value in enumerate((result.alias, self._batch_mode_label, state, detail)):
            self.batch_results.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def _log_paths(paths: list[str]) -> str:
        return "、".join(paths[:3]) + (f" 等 {len(paths)} 项" if len(paths) > 3 else "")

    def _session_label(self, session: RemoteSession) -> str:
        return getattr(getattr(session, "config", None), "host", None) or self.host.text().strip() or "当前站点"

    @staticmethod
    def _copy_log_result(value: object) -> tuple[str, str]:
        result = value[0] if isinstance(value, tuple) else value
        if not isinstance(result, CopyResult):
            return "成功", ""
        detail = f"{result.files} 个文件，{result.directories} 个目录，跳过 {result.skipped} 项"
        if result.cancelled:
            return "取消", detail
        return ("部分完成" if result.skipped else "成功"), detail

    @staticmethod
    def _copy_log_context(value: object) -> dict:
        result = value[0] if isinstance(value, tuple) else value
        if not isinstance(result, CopyResult):
            return {}
        return {"files": result.files, "directories": result.directories,
                "skipped": result.skipped, "cancelled": result.cancelled}

    @staticmethod
    def _batch_log_result(value: object) -> tuple[str, str]:
        results = value[0]
        complete = sum(result.complete for result in results)
        failures: dict[str, list[str]] = {}
        for result in results:
            if result.error:
                failures.setdefault(result.error, []).append(result.alias)
        detail = f"{complete}/{len(results)} 个节点成功"
        if failures:
            detail += "；" + "；".join(
                f"{compact_node_aliases(aliases)}：{error}" for error, aliases in failures.items()
            )
        return ("成功" if complete == len(results) else "失败" if failures else "部分完成"), detail

    @staticmethod
    def _batch_log_context(value: object) -> dict:
        return {"nodes": [
            {
                "alias": result.alias,
                "complete": result.complete,
                "error": result.error,
                "files": result.copied.files if result.copied else 0,
                "directories": result.copied.directories if result.copied else 0,
                "skipped": result.copied.skipped if result.copied else 0,
                "deleted": result.deleted,
            }
            for result in value[0]
        ]}

    def _append_operation_log(self, action: str, state: str, detail: str = "") -> int:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp}：{self._redact_log_value(action)} — {state}"
        if detail:
            line += f"：{self._redact_log_value(' '.join(detail.split()))}"
        self._operation_lines.append(line)
        if len(self._operation_lines) > 300:
            discard = 1 if self._active_log_index == 0 else 0
            self._operation_lines.pop(discard)
            if self._active_log_index is not None and self._active_log_index > discard:
                self._active_log_index -= 1
        self.operation_log.setPlainText("\n".join(self._operation_lines))
        bar = self.operation_log.verticalScrollBar()
        bar.setValue(bar.maximum())
        return len(self._operation_lines) - 1

    def _redact_log_value(self, value):
        passwords = [secret for secret in (
            *self._log_secrets, self._jump_password, self.password.text()
        ) if secret]
        if isinstance(value, str):
            for password in passwords:
                value = value.replace(password, "[已隐藏]")
            return value
        if isinstance(value, dict):
            return {key: self._redact_log_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_log_value(item) for item in value]
        return value

    def _write_activity(self, *, event_id: str, category: str, action: str,
                        state: str, detail: str = "", duration_ms: int | None = None,
                        context: dict | None = None) -> None:
        try:
            self._activity_log.write(
                event_id=event_id, category=category, action=self._redact_log_value(action),
                state=state, detail=self._redact_log_value(detail), duration_ms=duration_ms,
                context=self._redact_log_value(context),
            )
        except OSError as exc:
            self.status.setText(f"日志文件写入失败：{exc}")
            logging.getLogger("nodebridge.activity").warning("操作日志写入失败：%s", exc)

    def _record_activity(self, category: str, action: str, state: str,
                         detail: str = "", context: dict | None = None) -> None:
        self._append_operation_log(action, state, detail)
        self._write_activity(event_id=uuid.uuid4().hex, category=category,
                             action=action, state=state, detail=detail, context=context)

    def _finish_operation_log(self, state: str, detail: str = "",
                              context: dict | None = None) -> None:
        index = self._active_log_index
        if index is None or index < 0 or index >= len(self._operation_lines):
            return
        line = self._operation_lines[index]
        if line.endswith(" — 进行中"):
            line = line[:-len("进行中")] + state
        if detail:
            line += f"：{self._redact_log_value(' '.join(detail.split()))}"
        self._operation_lines[index] = line
        self.operation_log.setPlainText("\n".join(self._operation_lines))
        bar = self.operation_log.verticalScrollBar()
        bar.setValue(bar.maximum())
        if self._active_log_id is not None and self._active_log_action is not None:
            merged_context = dict(self._active_log_context or {})
            merged_context.update(context or {})
            self._write_activity(
                event_id=self._active_log_id, category=self._active_log_category,
                action=self._active_log_action, state=state, detail=detail,
                duration_ms=round((time.monotonic() - self._active_log_started) * 1000),
                context=merged_context,
            )

    def _show_connection_fields(self, config: NodeConfig) -> None:
        self.host.setText(config.host)
        self.port.setValue(config.port)
        self.user.setText(config.username)
        self.key_file.setText(config.key_file or "")
        self.password.clear()

    def _run(
        self, work: Callable[[], object], on_success: Callable[[object], None],
        description: str, on_finished: Callable[[], None] | None = None,
        *, log_action: str | None = None,
        log_result: Callable[[object], tuple[str, str]] | None = None,
        log_category: str = "file", log_context: dict | None = None,
        log_context_result: Callable[[object], dict] | None = None,
        background_copy: bool = False,
    ) -> None:
        if background_copy:
            self._run_background_copy(
                work, on_success, description, log_action=log_action,
                log_result=log_result, log_category=log_category,
                log_context=log_context, log_context_result=log_context_result,
                on_finished=on_finished,
            )
            return
        if self._job is not None:
            return
        self.status.setText(description)
        self._active_log_index = self._append_operation_log(log_action, "进行中") if log_action else None
        self._active_log_id = uuid.uuid4().hex if log_action else None
        self._active_log_action = log_action
        self._active_log_category = log_category
        self._active_log_started = time.monotonic()
        self._active_log_context = log_context
        self._log_context_result = log_context_result
        if log_action and self._active_log_id is not None:
            self._write_activity(event_id=self._active_log_id, category=log_category,
                                 action=log_action, state="进行中", context=log_context)
        self._log_result = log_result
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

    def _run_background_copy(
        self, work: Callable[[], object], on_success: Callable[[object], None],
        description: str, *, log_action: str | None,
        log_result: Callable[[object], tuple[str, str]] | None,
        log_category: str, log_context: dict | None,
        log_context_result: Callable[[object], dict] | None,
        on_finished: Callable[[], None] | None,
    ) -> None:
        if self._job is not None or self._copy_job is not None:
            return
        self.status.setText(description)
        event_id = uuid.uuid4().hex
        started = time.monotonic()
        index = self._append_operation_log(log_action, "进行中") if log_action else None
        if log_action:
            self._write_activity(event_id=event_id, category=log_category,
                                 action=log_action, state="进行中", context=log_context)
        job = JobThread(work, self)
        self._copy_job = job

        def finish_log(state: str, detail: str = "", extra: dict | None = None) -> None:
            if log_action is None:
                return
            if index is not None and index < len(self._operation_lines):
                line = self._operation_lines[index]
                if line.endswith(" — 进行中"):
                    self._operation_lines[index] = line[:-len("进行中")] + state + (
                        f"：{self._redact_log_value(' '.join(detail.split()))}" if detail else ""
                    )
                    self.operation_log.setPlainText("\n".join(self._operation_lines))
            context = dict(log_context or {})
            context.update(extra or {})
            self._write_activity(
                event_id=event_id, category=log_category, action=log_action,
                state=state, detail=detail,
                duration_ms=round((time.monotonic() - started) * 1000), context=context,
            )

        def succeeded(result: object) -> None:
            on_success(result)
            state, detail = log_result(result) if log_result else ("成功", "")
            finish_log(state, detail, log_context_result(result) if log_context_result else None)
            if state in {"失败", "部分完成"}:
                logger = logging.getLogger("nodebridge.jobs")
                (logger.error if state == "失败" else logger.warning)(
                    "%s：%s", self._redact_log_value(log_action or "复制"),
                    self._redact_log_value(detail),
                )

        def failed(message: str) -> None:
            logging.getLogger("nodebridge.jobs").error(
                "后台复制失败：%s", self._redact_log_value(message),
            )
            finish_log("失败", message)
            self._show_error(message)

        def finished() -> None:
            if self._copy_job is job:
                self._copy_job = None
            self._update_controls()
            self._finish_background_close()

        job.succeeded.connect(succeeded)
        job.failed.connect(failed)
        job.finished.connect(finished)
        if on_finished is not None:
            job.finished.connect(on_finished)
        job.finished.connect(job.deleteLater)
        self._update_controls()
        job.start()

    @Slot(object)
    def _job_succeeded(self, result: object) -> None:
        if self._on_job_success is not None:
            self._on_job_success(result)
        if self._active_log_index is not None:
            state, detail = self._log_result(result) if self._log_result else ("成功", "")
            context = self._log_context_result(result) if self._log_context_result else None
            self._finish_operation_log(state, detail, context)
            if state == "失败":
                logging.getLogger("nodebridge.jobs").error(
                    "%s：%s", self._redact_log_value(self._active_log_action or "操作失败"),
                    self._redact_log_value(detail),
                )
            elif state == "部分完成":
                logging.getLogger("nodebridge.jobs").warning(
                    "%s：%s", self._redact_log_value(self._active_log_action or "部分完成"),
                    self._redact_log_value(detail),
                )

    @Slot(str)
    def _job_failed(self, message: str) -> None:
        logging.getLogger("nodebridge.jobs").error("后台任务失败：%s", self._redact_log_value(message))
        self._finish_operation_log("失败", message)
        self._show_error(message)

    @Slot()
    def _job_finished(self) -> None:
        self._job = None
        self._on_job_success = None
        self._active_log_index = None
        self._active_log_id = None
        self._active_log_action = None
        self._active_log_context = None
        self._log_context_result = None
        self._log_result = None
        self._update_controls()
        if self._pending_worker_discovery:
            self._pending_worker_discovery = False
            QTimer.singleShot(0, self._discover_worker_aliases)
        elif self._pending_worker_probe:
            self._pending_worker_probe = False
            QTimer.singleShot(0, self._probe_worker_aliases)
        elif self._pending_worker_refresh:
            path = self._pending_worker_refresh
            self._pending_worker_refresh = None
            QTimer.singleShot(0, lambda: self._browse_workers(path))

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
        with self._conflict_lock:
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

    @Slot(object)
    def _show_worker_directory_conflict(self, prompt: WorkerDirectoryPrompt) -> None:
        try:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("间接节点目录已存在")
            dialog.setText(
                f"{prompt.path} 在 {len(prompt.aliases)} 个节点上已有同名项目。\n"
                f"冲突节点：{compact_node_aliases(prompt.aliases)}"
            )
            dialog.setInformativeText(
                "其他目标节点仍会创建原名目录。对于冲突节点，请选择跳过，"
                "或分别创建名称末尾带序号的新目录（例如 名称 (2)）。"
            )
            skip_button = dialog.addButton("跳过已有项目", QMessageBox.ButtonRole.AcceptRole)
            number_button = dialog.addButton("新建带序号的目录", QMessageBox.ButtonRole.ActionRole)
            cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.setDefaultButton(cancel_button)
            dialog.exec()
            if dialog.clickedButton() is skip_button:
                prompt.choice = "skip"
            elif dialog.clickedButton() is number_button:
                prompt.choice = "number"
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
        if password:
            self._log_secrets.add(password)

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
            self._sync_terminal_nodes()
            self.jump_title.setText(f"{'直连节点' if jump is None else '当前站点'} · {config.host}")
            self._show_connection_fields(config)
            self.tree.clear()
            self._show_listing(listing)
            route = f"（经 {jump.config.host} 中转）" if jump is not None else ""
            self.status.setText(f"已连接到 {config.host}{route}，当前目录：{listing.path}")
            if jump is None:
                self._jump_password = password
                self.worker_browser.path_edit.setText(listing.path)
                self._pending_worker_discovery = True

        description = "正在通过当前站点连接目标…" if jump is not None else "正在连接并读取目录…"
        self._run(
            work, success, description,
            log_action=f"SSH/SFTP 连接（{config.host}）"
            if jump is None else f"SSH/SFTP 中转连接（{jump.config.host} → {config.host}）",
            log_category="connection",
            log_context={"node": config.host, "port": config.port,
                         "via": jump.config.host if jump is not None else None},
        )

    def _disconnect(self) -> None:
        if self._copy_job is not None:
            self.status.setText("请等待后台复制完成后再断开连接。")
            return
        session = self._session
        if session is None:
            return
        self._cancel_auto_hash()
        self._probe_generation += 1
        self._probe_cancel.set()
        self._pending_worker_probe = False

        workers = list(self._workers.values())
        helpers = list(self._jump_helpers)
        terminal_nodes = [f"primary:{id(session)}"]
        if not self._parents:
            terminal_nodes.extend(
                f"worker:{alias}" for alias in set(self._workers) | set(self.worker_browser.known_aliases())
            )
        self._terminal_manager.close_nodes(terminal_nodes)

        def work():
            errors = []
            for worker in workers:
                try:
                    worker.close()
                except Exception as exc:
                    errors.append(str(exc))
            for helper in helpers:
                try:
                    helper.close()
                except Exception as exc:
                    errors.append(str(exc))
            try:
                session.close()
            except Exception as exc:
                errors.append(str(exc))
            return errors

        def success(errors):
            self._workers.clear()
            self._worker_jumps.clear()
            self._jump_helpers.clear()
            self.worker_browser.clear_connections(preserve_aliases=bool(self._parents))
            if self._parents:
                parent, listing = self._parents.pop()
                self._session = parent
                self.jump_title.setText(f"当前站点 · {parent.config.host}")
                self._show_connection_fields(parent.config)
                self.tree.clear()
                self._show_listing(listing)
                self.status.setText(f"已返回 {parent.config.host}，当前目录：{listing.path}")
            else:
                self._session = None
                self._jump_password = None
                self.jump_title.setText("直连节点 · 尚未连接")
                self._current_listing = None
                self._path = ""
                self.path_edit.clear()
                self.tree.clear()
                self.table.setRowCount(0)
                self.status.setText("已断开" if not errors else f"已断开；关闭时有 {len(errors)} 个错误。")
            self._sync_terminal_nodes()

        self._run(
            work, success, "正在断开…",
            log_action=f"SSH/SFTP 断开（{session.config.host}）",
            log_category="connection", log_context={"node": session.config.host},
            log_result=lambda errors: (
                "成功" if not errors else "部分完成",
                "；".join(errors),
            ),
        )

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

    def _worker_context_menu(self, point) -> None:
        if self._session is None or self._parents or self._job is not None:
            return
        table = self.worker_browser.table
        item = table.itemAt(point)
        if item is None:
            table.clearSelection()
        elif item.row() not in [index.row() for index in table.selectionModel().selectedRows(0)]:
            table.selectRow(item.row())
        menu = QMenu(table)
        if self.worker_browser.selected_names():
            menu.addAction("删除\tDelete", self._delete_worker_selection)
            menu.addSeparator()
        menu.addAction("新建目录", self._create_worker_directory).setEnabled(bool(self._workers))
        menu.addAction("刷新", lambda: self._browse_workers(self.worker_browser.rendered_path))
        menu.exec(table.viewport().mapToGlobal(point))

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
        if not paths or session is None or self._job is not None or self._copy_job is not None:
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

        self._run(
            work, success, "正在删除远程项目…",
            log_action=f"SFTP 删除（{self._session_label(session)}）：{self._log_paths(paths)}",
            log_context={"sources": paths, "node": self._session_label(session)},
        )

    def _delete_local_selection(self) -> None:
        paths = self._local_selected_paths()
        if not paths or self._job is not None or self._copy_job is not None:
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

        self._run(
            lambda: trash_local(paths), success, "正在移入回收站…",
            log_action=f"移入本地回收站：{self._log_paths(paths)}",
            log_context={"sources": paths},
        )

    def _rename_remote_selection(self) -> None:
        selected = self.table.selectionModel().selectedRows(0)
        if (len(selected) != 1 or self._session is None or self._job is not None
                or self._copy_job is not None):
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.table.edit(selected[0])

    def _commit_remote_rename(self, index: QModelIndex, name: str) -> None:
        session = self._session
        source = self._rename_source(self.table, index)
        if (session is None or self._job is not None or self._copy_job is not None
                or not source or name == posixpath.basename(source)):
            return

        def work():
            target = rename_remote(session, source, name)
            return target, session.list_directory(self._path)

        def success(result):
            self.tree.clear()
            self._show_listing(result[1])
            self._notify_done(f"已重命名为 {posixpath.basename(result[0])}。")

        self._run(
            work, success, "正在重命名远程项目…",
            log_action=f"SFTP 重命名（{self._session_label(session)}）：{source} → {name}",
            log_context={"sources": [source], "destination": posixpath.join(posixpath.dirname(source), name), "node": self._session_label(session)},
        )

    def _rename_local_selection(self) -> None:
        selected = self.local_table.selectionModel().selectedRows(0)
        if len(selected) != 1 or self._job is not None or self._copy_job is not None:
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.local_table.edit(selected[0])

    def _commit_local_rename(self, index: QModelIndex, name: str) -> None:
        source = self._rename_source(self.local_table, index)
        if self._job is not None or self._copy_job is not None or not source or name == Path(source).name:
            return

        def success(target):
            self.local.refresh()
            self._notify_done(f"已重命名为 {target.name}。")

        self._run(
            lambda: rename_local(source, name), success, "正在重命名本地项目…",
            log_action=f"本地重命名：{source} → {name}",
            log_context={"sources": [source], "destination": str(Path(source).with_name(name))},
        )

    def _create_remote_directory(self, enter: bool) -> None:
        session = self._session
        if session is None or self._job is not None or self._copy_job is not None:
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

        self._run(
            work, success, "正在创建远程目录…",
            log_action=f"SFTP 创建目录（{self._session_label(session)}）：{posixpath.join(parent, name)}",
            log_context={"destination": posixpath.join(parent, name), "node": self._session_label(session)},
        )

    def _create_local_directory(self, enter: bool) -> None:
        if self._job is not None or self._copy_job is not None:
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

        self._run(
            lambda: create_local_directory(parent, name), success, "正在创建本地目录…",
            log_action=f"本地创建目录：{Path(parent) / name}",
            log_context={"destination": str(Path(parent) / name)},
        )

    def _copy_local_to_local(self, paths: list[str], destination: str) -> None:
        if self._job is not None or self._copy_job is not None:
            return
        self._apply_conflict_choice = None

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在复制：{path}")
            return copy_local_to_local(
                paths, destination,
                progress,
                self._resolve_conflict,
            )

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("复制", result))

        self._run(
            work, success, "正在复制本地文件…",
            log_action=f"本地复制：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination},
            log_context_result=self._copy_log_context,
            background_copy=True,
        )

    def _copy_remote_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._job is not None or self._copy_job is not None:
            return
        self._apply_conflict_choice = None
        browsed_path = self._path

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在复制：{path}")
            peer = session.open_sftp_peer()
            try:
                result = copy_remote_to_remote(
                    peer, paths, destination, progress, self._resolve_conflict,
                )
                return result, peer.list_directory(browsed_path)
            finally:
                peer.close()

        def success(value):
            result, listing = value
            if self._session is session and self._path == browsed_path:
                self._show_listing(listing)
            self._notify_done(self._copy_status("复制", result))

        self._run(
            work, success, "正在复制远程文件…",
            log_action=f"SFTP 复制（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
        )

    def _copy_local_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._job is not None or self._copy_job is not None:
            return
        self._apply_conflict_choice = None
        browsed_path = self._path

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在上传：{path}")
            peer = session.open_sftp_peer()
            try:
                result = copy_local_to_remote(
                    peer, paths, destination, progress, self._resolve_conflict,
                )
                return result, peer.list_directory(browsed_path)
            finally:
                peer.close()

        def success(value):
            result, listing = value
            if self._session is session and self._path == browsed_path:
                self._show_listing(listing)
            self._notify_done(self._copy_status("上传", result))

        self._run(
            work, success, "正在检查并上传文件…",
            log_action=f"SFTP 上传（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
        )

    def _copy_remote_to_local(
        self, token: str, paths: list[str], destination: str, cached_paths: list[str]
    ) -> None:
        if token == self.worker_browser.drag_token:
            self._collect_worker_files(token, paths, destination, to_jump=False)
            return
        session = self._session
        if (session is None or self._job is not None or self._copy_job is not None
                or token != str(id(session))):
            return
        self._apply_conflict_choice = None

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在下载：{path}")
            if len(cached_paths) == len(paths) and all(Path(path).exists() for path in cached_paths):
                labels = {Path(cached).name: remote for cached, remote in zip(cached_paths, paths)}
                return copy_local_to_local(
                    cached_paths, destination, progress, self._resolve_conflict, labels
                )
            peer = session.open_sftp_peer()
            try:
                return copy_remote_to_local(peer, paths, destination, progress, self._resolve_conflict)
            finally:
                peer.close()

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("下载", result))

        self._run(
            work, success, "正在检查并下载文件…",
            log_action=f"SFTP 下载（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
        )

    def _prepare_drag_export(self, paths: list[str]) -> list[str]:
        session = self._session
        if session is None or self._job is not None or self._copy_job is not None:
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
            progress = lambda path: self.transferProgress.emit(f"正在准备拖出：{path}")
            peer = session.open_sftp_peer()
            try:
                copy_remote_to_local(peer, paths, temporary.name, progress)
            finally:
                peer.close()
            return [str(Path(temporary.name) / posixpath.basename(path)) for path in paths]

        def success(result):
            exported.extend(result)
            self._drag_cache[key] = (time.monotonic(), temporary, exported)
            self.status.setText("文件已准备好，可以拖到本地目录或资源管理器。")

        self._run(
            work, success, "正在准备拖出的文件…", loop.quit,
            log_action=f"SFTP 准备拖出缓存（{self._session_label(session)}）：{self._log_paths(paths)}",
            log_context={"sources": paths, "node": self._session_label(session), "note": "Windows 资源管理器最终复制结果不可见"},
            background_copy=True,
        )
        loop.exec()
        if not exported:
            temporary.cleanup()
        return exported

    def closeEvent(self, event) -> None:
        if self._copy_job is not None:
            event.ignore()
            self.status.setText("请等待后台复制完成后再关闭。")
            return
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
        if self._hash_job is not None or self._probe_job is not None:
            event.ignore()
            self._close_when_background_idle = True
            self._cancel_auto_hash()
            self._probe_cancel.set()
            self.status.setText("正在结束后台校验和节点探测…")
            return
        for _created, temporary, _paths in self._drag_cache.values():
            temporary.cleanup()
        self._drag_cache.clear()
        self._terminal_manager.close_all()
        event.accept()

    def _finish_background_close(self) -> None:
        if (self._close_when_background_idle and self._job is None
                and self._copy_job is None and self._hash_job is None and self._probe_job is None
                and self._session is None):
            QTimer.singleShot(0, self.close)
