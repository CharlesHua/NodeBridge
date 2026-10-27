"""Local and remote file manager UI. All SSH/SFTP calls run outside the GUI thread."""

from __future__ import annotations

from nodebridge.i18n import tr, ui

import json
import errno
import logging
import posixpath
import re
import secrets
import stat
import tempfile
import time
import threading
import uuid
from contextlib import closing
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
from nodebridge.layout_settings import LayoutSettings
from nodebridge.qt_rendering import prepare_terminal_rendering
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
from nodebridge.command_dialog import CommandManagerDialog, load_commands
from nodebridge.remote import DirectoryListing, NodeConfig, NodeDiscovery, RemoteSession, TransferSessionPool
from nodebridge.site_dialog import SiteManagerDialog
from nodebridge.sites import LockedSiteStore, SiteStore
from nodebridge.resource_monitor import ResourceMonitor
from nodebridge.transfer import (
    ensure_remote_destination,
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
from nodebridge.tmux_session import TmuxBackend, TmuxChannel, endpoint_key, task_dict
from nodebridge.tasks_workspace import TasksWorkspace, load_tasks, save_tasks
from nodebridge.worker_browser import WorkerBrowser


PATH_ROLE = Qt.ItemDataRole.UserRole
DESTRUCTIVE_SHELL_COMMAND = re.compile(
    r"(?:^\s*|[;&|]\s*)(?:sudo\s+)?(?:rm|rmdir|mv|dd|mkfs(?:\.[\w-]+)?|"
    r"shutdown|reboot|poweroff|chmod|chown)\b",
    re.IGNORECASE | re.MULTILINE,
)
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class ConflictPrompt:
    info: ConflictInfo
    done: threading.Event = field(default_factory=threading.Event)
    action: ConflictAction = ConflictAction.CANCEL
    choices: dict | None = None


@dataclass
class WorkerDirectoryPrompt:
    path: str
    aliases: list[str]
    done: threading.Event = field(default_factory=threading.Event)
    choice: str = "cancel"


@dataclass
class MissingDestinationPrompt:
    alias: str
    path: str
    missing: list[str]
    done: threading.Event = field(default_factory=threading.Event)
    create: bool = False


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
        ui(editor.setText, name)
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
    nodeProbeResult = Signal(object)
    missingDestinationRequested = Signal(object)

    def __init__(self, settings: QSettings | None = None, *, log_path: Path | None = None):
        super().__init__()
        self._terminal_render_surface = prepare_terminal_rendering(self)
        ui(self.setWindowTitle, tr('NodeBridge — 多节点文件浏览'))
        self.resize(1550, 850)
        self._settings = settings if settings is not None else default_settings()
        self._session: RemoteSession | None = None
        self._job: JobThread | None = None
        self._copy_job: JobThread | None = None
        self._file_tasks: dict[JobThread, frozenset[str]] = {}
        self._file_copy_tasks: set[JobThread] = set()
        self._remote_browse_job: JobThread | None = None
        self._worker_browse_job: JobThread | None = None
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
        self._tmux_job: JobThread | None = None
        self._tmux_scan_job: JobThread | None = None
        self._tmux_restore_queue: list[str] = []
        self._tmux_cancel = threading.Event()
        self._tmux_records = load_tasks(self._settings)
        self._persistent_sessions = {}
        self._tmux_group_links = {}
        self._terminal_helper_reservations: set[int] = set()
        self._jump_password: str | None = None
        self._pending_worker_discovery = False
        self._pending_worker_refresh: tuple[str, bool] | None = None
        self._pending_worker_probe = False
        self._probe_job: JobThread | None = None
        self._resource_monitor = None
        self._resource_timer = QTimer(self)
        self._resource_timer.setInterval(1000)
        self._resource_timer.timeout.connect(self._refresh_resource_monitor)
        self._worker_connect_job: JobThread | None = None
        self._node_verified_revision: dict[str, int] = {}
        self._node_status_revision = 0
        self._hash_cache: dict[tuple, tuple[float, dict]] = {}
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

        self.file_menu = ui(QMenu, tr('文件'), self.menuBar())
        ui(self.menuBar().addMenu, self.file_menu)
        ui(self.file_menu.addAction, tr('退出'), self.close)
        self.view_menu = ui(QMenu, tr('查看'), self.menuBar())
        ui(self.menuBar().addMenu, self.view_menu)
        from nodebridge.i18n import add_language_menu
        self.language_menu = add_language_menu(self, self.view_menu, self._settings)
        self.show_local_action = ui(self.view_menu.addAction, tr('查看本地站点'))
        self.show_local_action.setCheckable(True)
        self.show_local_action.setChecked(
            self._settings.value("view/show_local_site", True, type=bool)
        )
        self.show_local_action.toggled.connect(self._set_local_visible)
        self.show_workers_action = ui(self.view_menu.addAction, tr('查看间接节点'))
        self.show_workers_action.setCheckable(True)
        self.show_workers_action.setChecked(
            self._settings.value("view/show_indirect_nodes", True, type=bool)
        )
        self.show_workers_action.toggled.connect(self._set_workers_visible)
        self.show_log_action = ui(self.view_menu.addAction, tr('查看日志'))
        self.show_log_action.setCheckable(True)
        self.show_log_action.setChecked(
            self._settings.value("view/show_operation_log", True, type=bool)
        )
        self.show_log_action.toggled.connect(self._set_operation_log_visible)
        ui(self.view_menu.addAction, tr('打开日志文件'), self._open_log_file)
        self.tools_menu = ui(self.menuBar().addMenu, tr('工具'))
        ui(self.tools_menu.addAction, tr('常用命令管理'), self._manage_commands)
        self.help_menu = ui(QMenu, tr('帮助'), self.menuBar())
        ui(self.menuBar().addMenu, self.help_menu)
        ui(self.help_menu.addAction, tr('使用指南'), self._show_user_guide)
        ui(self.help_menu.addAction, tr('关于 NodeBridge'), self._show_about)

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        self.setCentralWidget(body)

        quick = QHBoxLayout()
        self.site_button = ui(QPushButton, tr('站点管理器…'))
        self.site_button.clicked.connect(self._open_site_manager)
        quick.addWidget(self.site_button)
        self.host = QLineEdit()
        ui(self.host.setPlaceholderText, tr('主机'))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.user = QLineEdit()
        ui(self.user.setPlaceholderText, tr('用户名'))
        self.key_file = QLineEdit()
        ui(self.key_file.setPlaceholderText, tr('可选：私钥文件路径'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        ui(self.password.setPlaceholderText, tr('密码（可选）'))
        quick.addWidget(ui(QLabel, tr('主机:')))
        quick.addWidget(self.host, 3)
        quick.addWidget(ui(QLabel, tr('用户名:')))
        quick.addWidget(self.user, 2)
        quick.addWidget(ui(QLabel, tr('密码:')))
        quick.addWidget(self.password, 2)
        quick.addWidget(ui(QLabel, tr('端口:')))
        quick.addWidget(self.port)
        self.connect_button = ui(QPushButton, tr('连接'))
        self.jump_button = ui(QPushButton, tr('添加间接节点…'))
        self.disconnect_button = ui(QPushButton, tr('断开'))
        self.connect_button.clicked.connect(self._connect)
        self.jump_button.clicked.connect(self._add_worker_alias)
        self.disconnect_button.clicked.connect(self._disconnect)
        quick.addWidget(self.connect_button)
        quick.addWidget(self.jump_button)
        quick.addWidget(self.disconnect_button)
        layout.addLayout(quick)

        key_row = QHBoxLayout()
        key_row.addWidget(ui(QLabel, tr('私钥路径:')))
        key_row.addWidget(self.key_file, 1)
        layout.addLayout(key_row)

        self.status = ui(QLabel, tr('未连接。主机密钥须已存在于 OpenSSH known_hosts。'))
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
        ui(self.workspace_tabs.addTab, self.file_view, tr('文件'))
        self.terminal_workspace = TerminalWorkspace()
        self.terminal_workspace.set_commands(load_commands(self._settings))
        self.terminal_workspace.connectRequested.connect(self._connect_terminal)
        self.terminal_workspace.disconnectRequested.connect(self._request_close_terminal)
        self.terminal_workspace.connectManyRequested.connect(self._connect_terminals)
        self.terminal_workspace.disconnectManyRequested.connect(self._close_terminals)
        self.terminal_workspace.disconnectGroupRequested.connect(self._close_terminal_group)
        self.terminal_workspace.inputRequested.connect(self._send_terminal_input)
        self.terminal_workspace.resizeRequested.connect(self._resize_terminal)
        self.terminal_workspace.broadcastRequested.connect(self._start_broadcast)
        self.terminal_workspace.interruptRequested.connect(self._interrupt_broadcast)
        self._terminal_manager.outputReady.connect(self._terminal_output)
        self._terminal_manager.terminalClosed.connect(self._terminal_closed)
        self._terminal_manager.inputFailed.connect(self._terminal_input_failed)
        ui(self.workspace_tabs.addTab, self.terminal_workspace, tr('终端'))
        self.terminal_workspace.persistent_mode.setChecked(
            self._settings.value("terminal/persistent_mode", False, type=bool))
        self.terminal_workspace.persistent_mode.toggled.connect(
            lambda checked: self._settings.setValue("terminal/persistent_mode", checked))
        self.terminal_workspace.groupRenamed.connect(self._rename_persistent_group)
        self.terminal_workspace.recoverPersistentRequested.connect(self._show_persistent_tasks)
        self.tasks_workspace = TasksWorkspace()
        self.tasks_workspace.scanRequested.connect(self._scan_tmux_tasks)
        self.tasks_workspace.restoreRequested.connect(self._restore_tmux_task)
        self.tasks_workspace.detachRequested.connect(self._detach_tmux_task)
        self.tasks_workspace.terminateRequested.connect(self._terminate_tmux_task)
        self.tasks_workspace.restoreManyRequested.connect(self._restore_tmux_tasks)
        self.tasks_workspace.detachManyRequested.connect(self._detach_tmux_tasks)
        self.tasks_workspace.terminateManyRequested.connect(self._terminate_tmux_tasks)
        ui(self.workspace_tabs.addTab, self.tasks_workspace, tr('任务'))
        self._refresh_tmux_tasks()
        self.workspace_tabs.currentChanged.connect(self._workspace_changed)
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.workspace_tabs)
        self.log_panel = QWidget()
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(3)
        log_layout.addWidget(ui(QLabel, tr('操作日志（界面仅本次运行；文件会保存）')))
        self.operation_log = QPlainTextEdit()
        self.operation_log.setReadOnly(True)
        self.operation_log.setMinimumHeight(45)
        ui(self.operation_log.setPlaceholderText, tr('文件操作、连接和广播的结果会显示在这里。'))
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
        self.jump_title = ui(QLabel, tr('直连节点 · 尚未连接'))
        self.jump_title.setFixedHeight(28)
        remote_layout.addWidget(self.jump_title)

        navigation = QHBoxLayout()
        navigation.addWidget(ui(QLabel, tr('直连节点:')))
        self.path_edit = QLineEdit()
        ui(self.path_edit.setPlaceholderText, tr('远程 POSIX 路径'))
        self.up_button = ui(QPushButton, tr('上一级'))
        self.refresh_button = ui(QPushButton, tr('刷新'))
        self.up_button.clicked.connect(self._up)
        self.refresh_button.clicked.connect(self._refresh)
        self.path_edit.returnPressed.connect(self._open_path)
        navigation.addWidget(self.path_edit, 1)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.refresh_button)
        remote_layout.addLayout(navigation)

        self.remote_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        ui(self.tree.setHeaderLabel, tr("直连目录"))
        self.tree.itemExpanded.connect(self._expand_tree)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.table = RemoteFileTable()
        self.table.localPathsDropped.connect(self._copy_local_to_remote)
        self.table.workerPathsDropped.connect(self._collect_workers_to_jump)
        self.table.preparedDragCancelled.connect(
            lambda: ui(self.status.setText, tr('拖出文件已准备好；如果刚才松开了鼠标，请再拖一次。'))
        )
        self.table.prepare_export = self._prepare_drag_export
        ui(self.table.setHorizontalHeaderLabels, [tr('文件名'), tr('文件大小'), tr('文件类型'), tr('最后修改')])
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
        self.worker_browser.nodeSwitchRequested.connect(
            lambda path: self._browse_workers(path, parent_fallback=True)
        )
        self.worker_browser.createDirectoryRequested.connect(self._create_worker_directory)
        self.worker_browser.table.localPathsDropped.connect(self._drop_local_on_workers)
        self.worker_browser.table.remotePathsDropped.connect(self._drop_jump_on_workers)
        panes.addWidget(self.worker_browser)
        panes.setSizes([350, 510, 690])
        file_layout.addWidget(panes, 1)
        self.local.setVisible(self.show_local_action.isChecked())
        self.worker_browser.setVisible(self.show_workers_action.isChecked())

        self.batch_results = QTableWidget(0, 4)
        ui(self.batch_results.setHorizontalHeaderLabels, [tr('节点'), tr('操作'), tr('结果'), tr('详情')])
        self.batch_results.setShowGrid(False)
        self.batch_results.verticalHeader().hide()
        self.batch_results.verticalHeader().setMinimumSectionSize(18)
        self.batch_results.verticalHeader().setDefaultSectionSize(20)
        self.batch_results.setWordWrap(False)
        self.batch_results.setFixedHeight(135)
        self.batch_results.setColumnWidth(0, 95)
        self.batch_results.setColumnWidth(1, 150)
        self.batch_results.setColumnWidth(2, 100)
        self.batch_results.horizontalHeader().setStretchLastSection(True)
        file_layout.addWidget(self.batch_results)
        self.batchResultReady.connect(self._show_batch_result)
        self.nodeProbeResult.connect(self._show_node_probe_result)
        self.missingDestinationRequested.connect(self._show_missing_destination)

        self._install_file_actions()
        self.completion_toast = ui(QLabel, self)
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
        self._layout_settings = LayoutSettings(self, self._settings, {
            "workspace": self.workspace_splitter,
            "files": panes,
            "local": self.local.content_splitter,
            "remote": self.remote_splitter,
            "workers": self.worker_browser.findChild(QSplitter),
            "terminal": self.terminal_workspace.findChild(QSplitter),
        }, {
            "local_columns": self.local_table.horizontalHeader(),
            "remote_columns": self.table.horizontalHeader(),
            "worker_columns": self.worker_browser.table.horizontalHeader(),
            "worker_nodes": self.worker_browser.nodes.header(),
            "terminal_nodes": self.terminal_workspace.node_tree.header(),
            "terminal_groups": self.terminal_workspace.group_tree.header(),
            "task_columns": self.tasks_workspace.table.horizontalHeader(),
            "batch_columns": self.batch_results.horizontalHeader(),
        })

    def _terminal_sources(self) -> dict[str, tuple[str, RemoteSession]]:
        sources = {}
        def display_name(session, fallback):
            name = getattr(session, "display_hostname", None)
            return name if isinstance(name, str) and name else fallback

        for session, _listing in self._parents:
            sources[f"primary:{id(session)}"] = (display_name(session, session.config.host), session)
        if self._session is not None:
            host = getattr(getattr(self._session, "config", None), "host", None)
            sources[f"primary:{id(self._session)}"] = (
                display_name(self._session, host or self.host.text().strip() or "当前站点"), self._session,
            )
        sources.update((f"worker:{alias}", (alias, session)) for alias, session in self._workers.items())
        root = self._parents[0][0] if self._parents else self._session
        if root is not None:
            for record in self._tmux_records.values():
                if (record.get("local_session") and len(record["route"]) > 1
                        and (record["host"], record["port"], record["user"]) == endpoint_key(root.config)[:3]):
                    sources[self._tmux_route_node(record["route"])] = (record["node"], root)
        return sources

    @staticmethod
    def _tmux_route_node(route):
        return "tmux-route:" + uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(list(route))).hex

    def _workspace_changed(self, _index):
        if self.workspace_tabs.currentWidget() is self.terminal_workspace:
            self.enable_resource_monitoring()
        elif self.workspace_tabs.currentWidget() is self.tasks_workspace and self._session is not None:
            self._scan_tmux_tasks()

    def enable_resource_monitoring(self):
        if not self._resource_timer.isActive():
            self._resource_timer.start()

    def _refresh_resource_monitor(self):
        jump = self._session
        monitor = self._resource_monitor
        if monitor is not None and getattr(monitor, "owner_session", None) is not jump:
            monitor.cancel.set()
        if jump is None or self._parents or self._close_when_background_idle:
            if monitor is not None:
                monitor.cancel.set()
            return
        targets = {f"primary:{id(jump)}": None}
        for alias, (state, _detail) in self.worker_browser._probe_status.items():
            if state == "reachable":
                targets[f"worker:{alias}"] = alias
        targets.update((f"worker:{alias}", alias) for alias in self._workers)
        if monitor is None:
            if self._job is not None:
                return
            monitor = ResourceMonitor(jump.config, self._jump_password, self)
            monitor.owner_session = jump
            self._resource_monitor = monitor
            monitor.result.connect(lambda result: self._resource_result(monitor, jump, result))
            monitor.finished.connect(lambda: self._resource_finished(monitor))
            monitor.finished.connect(monitor.deleteLater)
            monitor.set_targets(targets)
            monitor.start()
        elif not monitor.cancel.is_set():
            monitor.set_targets(targets)
        for node_id in list(self.terminal_workspace._resource_values):
            if node_id not in targets:
                self.terminal_workspace.set_resource_usage(node_id, None, None, "节点当前不可连接")

    def _resource_result(self, monitor, jump, result):
        if (self._resource_monitor is monitor and self._session is jump
                and not self._parents and not monitor.cancel.is_set()):
            node_id = result[0]
            if node_id.startswith("worker:"):
                alias = node_id[len("worker:"):]
                if alias not in self._workers and self.worker_browser._probe_status.get(alias, ("", ""))[0] != "reachable":
                    return
            self.terminal_workspace.set_resource_usage(*result)

    def _resource_finished(self, monitor):
        if self._resource_monitor is monitor:
            self._resource_monitor = None
        self._finish_background_close()

    def _sync_terminal_nodes(self) -> None:
        sources = self._terminal_sources()
        nodes = {node_id: label for node_id, (label, _session) in sources.items()}
        if self._session is not None:
            nodes.update((f"worker:{alias}", alias) for alias in self.worker_browser.known_aliases())
        self._terminal_manager.retain_nodes(set(nodes))
        self.terminal_workspace.set_nodes(list(nodes.items()))
        self.terminal_workspace.set_node_addresses({
            node_id: address for node_id, (_label, session) in sources.items()
            if node_id.startswith("primary:")
            and isinstance(address := getattr(getattr(session, "config", None), "host", None), str)
        })
        self.terminal_workspace.set_node_statuses({
            f"worker:{alias}": status for alias, status in self.worker_browser._probe_status.items()
        })

    def _manage_commands(self):
        dialog = CommandManagerDialog(load_commands(self._settings), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            commands = dialog.commands()
            self._settings.setValue("terminal/commands", json.dumps(commands, ensure_ascii=False))
            self._settings.sync()
            self.terminal_workspace.set_commands(commands)

    def _set_node_probe_statuses(self, statuses, observed_revision=None):
        statuses = {alias: status for alias, status in statuses.items()
                    if observed_revision is None or self._node_verified_revision.get(alias, 0) <= observed_revision}
        self.worker_browser.set_probe_statuses(statuses)
        self.terminal_workspace.set_node_statuses({f"worker:{alias}": value for alias, value in statuses.items()})

    def _node_connected(self, alias):
        self._node_status_revision += 1
        self._node_verified_revision[alias] = self._node_status_revision
        self._set_node_probe_statuses({alias: ("reachable", "已实际连接成功")})

    def _connect_terminal(self, node_id: str) -> None:
        self._connect_terminals([node_id])

    def _refresh_tmux_tasks(self, save=False):
        if save:
            save_tasks(self._settings, self._tmux_records.values())
        self.tasks_workspace.set_records(list(self._tmux_records.values()))
        self.tasks_workspace.set_busy(self._job is not None or self._tmux_job is not None,
                                      self._session is not None)
        self.tasks_workspace.set_scanning(self._tmux_scan_job is not None)

    def _forget_tmux_record(self, key):
        # Only called after successful termination or a successful absence check.
        record = self._tmux_records.get(key)
        if record is not None:
            if record.get("local_session"):
                self._close_terminal(record["local_session"])
            self._tmux_records.pop(key, None)

    def _remember_tmux_task(self, config, route, task, node):
        identity = json.dumps([config.host, config.port, config.username, list(route), task.name])
        key = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        previous = self._tmux_records.get(key, {})
        local = previous.get("local_session", "")
        if local and not self._terminal_manager.has_session(local):
            local = ""
        record = dict(task_dict(task), key=key, host=config.host, port=config.port,
                      user=config.username, route=list(route), node=node, local_session=local,
                      label=previous.get("label") or task.label,
                      state="窗格已退出" if task.dead else "本机已连接" if local else
                      "远端有其他客户端" if task.attached else "可恢复", detail="已查询远程 tmux 会话。")
        previous.update(record)
        self._tmux_records[key] = previous
        return previous

    def _show_persistent_tasks(self):
        self.workspace_tabs.setCurrentWidget(self.tasks_workspace)
        if self._session is not None:
            self._scan_tmux_tasks()

    def _rename_persistent_group(self, group_id, name):
        for session_id in self.terminal_workspace.group_sessions(group_id):
            record = self._tmux_records.get(self._persistent_sessions.get(session_id))
            if record is not None:
                for other in self._tmux_records.values():
                    if (other is record or record.get("group") and other.get("group") == record.get("group")
                            and other["host"] == record["host"]):
                        other["label"] = name
        self._refresh_tmux_tasks(save=True)

    def _tmux_root(self, record=None):
        root = self._parents[0][0] if self._parents else self._session
        if root is None:
            raise ValueError("请先连接对应的直连站点。")
        if record and endpoint_key(root.config)[:3] != (record["host"], record["port"], record["user"]):
            root = next((source for _label, source in self._terminal_sources().values()
                         if endpoint_key(source.config)[:3] == (record["host"], record["port"], record["user"])), None)
            if root is None:
                raise ValueError(f"请先连接站点 {record['user']}@{record['host']}:{record['port']}。")
        return root

    def _scan_tmux_tasks(self):
        if self._tmux_scan_job is not None:
            return
        try:
            root = self._tmux_root()
        except ValueError as exc:
            QMessageBox.information(self, tr('持久终端'), str(exc))
            return
        config, password = root.config, self._jump_password
        cancel = threading.Event()
        self._tmux_cancel = cancel
        snapshot = {key: dict(record) for key, record in self._tmux_records.items()}
        routes = {()}
        routes.update((alias,) for alias in self.worker_browser.known_aliases()
                      if alias in self._workers or self.worker_browser._probe_status.get(alias, ("", ""))[0] == "reachable")
        routes.update(tuple(record["route"]) for record in self._tmux_records.values()
                      if (record["host"], record["port"], record["user"]) == endpoint_key(config)[:3])
        def work():
            if cancel.is_set():
                return []
            helper = RemoteSession.connect_shell_host(config, password)
            try:
                def one(route):
                    if cancel.is_set():
                        return route, [], "查询已取消"
                    backend = TmuxBackend(helper, cancel=cancel)
                    backend.route = route
                    try:
                        return route, backend.scan(), ""
                    except Exception as exc:
                        return route, [], f"{type(exc).__name__}: {exc}"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    return list(pool.map(one, sorted(routes)))
            finally:
                helper.close()
        def success(results):
            if cancel.is_set():
                return
            if self._session is not root and (not self._parents or self._parents[0][0] is not root):
                return
            failures = []
            for route, tasks, error in results:
                relevant = [r for r in self._tmux_records.values()
                            if (r["host"], r["port"], r["user"]) == endpoint_key(config)[:3]
                            and tuple(r["route"]) == route]
                # Attach/detach/termination may have changed a record while this
                # read-only scan was in flight. Never overwrite those newer states.
                changed_names = {r["name"] for r in relevant if r != snapshot.get(r["key"])}
                changed_names.update(r["name"] for key, r in snapshot.items()
                    if key not in self._tmux_records and tuple(r["route"]) == route
                    and (r["host"], r["port"], r["user"]) == endpoint_key(config)[:3])
                relevant = [r for r in relevant if r["name"] not in changed_names]
                if error:
                    for record in relevant:
                        record.update(state="查询失败", detail=error)
                    failures.append(f"{' → '.join(route) or config.host}：{error}")
                    continue
                names = {task.name for task in tasks}
                for record in relevant:
                    if record["name"] not in names:
                        self._forget_tmux_record(record["key"])
                for task in tasks:
                    if task.name in changed_names:
                        continue
                    root_name = getattr(root, "display_hostname", None)
                    root_name = root_name if isinstance(root_name, str) and root_name else config.host
                    self._remember_tmux_task(config, route, task,
                                             " → ".join(route) or root_name)
            self._refresh_tmux_tasks(save=True)
            ui(self.tasks_workspace.summary.setText, tr('扫描完成：{0}/{1} 个节点查询成功。', f'{len(results) - len(failures)}', f'{len(results)}'))
            ui(self.tasks_workspace.summary.setToolTip, "")
            if failures:
                logging.getLogger(__name__).warning("tmux 节点查询失败：%s", "；".join(failures))
                ui(self.tasks_workspace.summary.setToolTip, "\n".join(failures))
        ui(self.tasks_workspace.summary.setText, tr('正在后台扫描持久终端，可继续其他操作…'))
        self._run(work, success, tr('正在后台扫描持久终端…'), background_slot="_tmux_scan_job",
                  log_action="扫描 tmux 持久终端", log_category="terminal",
                  log_result=lambda value: ("部分完成" if any(row[2] for row in value) else "成功",
                                            "；".join(f"{' → '.join(route) or config.host}: {error}" for route, _, error in value if error)))

    def _restore_tmux_task(self, key):
        record = self._tmux_records.get(key)
        if record is None or self._job is not None or self._tmux_job is not None:
            return
        local = record.get("local_session")
        if local and self._terminal_manager.has_session(local):
            self.workspace_tabs.setCurrentWidget(self.terminal_workspace)
            self.terminal_workspace._show_tab(local)
            return
        try:
            root = self._tmux_root(record)
            if record.get("attached", 0) > 0:
                choice = QMessageBox.question(self, tr('会话已有客户端'),
                    tr('这个远程会话已有客户端连接，继续连接后输入会共享。\n是否继续？不会断开其他客户端。'),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if choice != QMessageBox.StandardButton.Yes:
                    return
            route = record["route"]
            if len(route) > 1:
                node_id = self._tmux_route_node(route)
            elif route:
                node_id = f"worker:{route[0]}"
                if route[0] not in self.worker_browser.known_aliases():
                    self.worker_browser.set_aliases([*self.worker_browser.known_aliases(), route[0]])
            else:
                node_id = f"primary:{id(root)}"
            self._sync_terminal_nodes()
            if len(route) > 1:
                self.terminal_workspace.set_nodes([*self.terminal_workspace._nodes.items(), (node_id, record["node"])])
            self.workspace_tabs.setCurrentWidget(self.terminal_workspace)
            self._connect_terminals([node_id], persistent=True, restore_tasks={node_id: record})
        except ValueError as exc:
            QMessageBox.warning(self, tr('恢复持久终端'), str(exc))

    def _detach_tmux_task(self, key):
        record = self._tmux_records.get(key)
        if record and record.get("local_session"):
            self._close_terminal(record["local_session"])

    def _restore_tmux_tasks(self, keys):
        if self._job is not None or self._tmux_job is not None or self._tmux_restore_queue:
            return
        self._tmux_restore_queue = list(dict.fromkeys(keys))
        self._continue_tmux_restore()

    def _continue_tmux_restore(self):
        if self._job is not None or self._tmux_job is not None:
            self._tmux_restore_queue.clear()
            return
        while self._tmux_restore_queue:
            self._restore_tmux_task(self._tmux_restore_queue.pop(0))
            if self._job is not None:
                self._job.finished.connect(lambda: QTimer.singleShot(0, self._continue_tmux_restore))
                return

    def _detach_tmux_tasks(self, keys):
        for key in list(dict.fromkeys(keys)):
            self._detach_tmux_task(key)

    def _terminate_tmux_task(self, key):
        self._terminate_tmux_tasks([key])

    def _terminate_tmux_tasks(self, keys):
        if self._job is not None or self._tmux_job is not None:
            return
        records = [self._tmux_records[key] for key in dict.fromkeys(keys) if key in self._tmux_records]
        if not records:
            return
        try:
            targets = [(r, self._tmux_root(r), tuple(r["route"]), r["name"]) for r in records]
        except ValueError as exc:
            QMessageBox.warning(self, tr('结束远程会话'), str(exc))
            return
        names = "\n".join(f"{r['node']} / {r['name']}" for r in records)
        answer = QMessageBox.question(self, tr('结束远程 tmux 会话'),
            tr('永久结束所选 {0} 个远程会话？\n{1}\n这可能终止其中的程序，之后无法恢复。', f'{len(records)}', f'{names}'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        def work():
            ended, errors = [], []
            for record, root, route, name in targets:
                try:
                    backend = TmuxBackend(root)
                    backend.route = route
                    backend.kill(name)
                    ended.append(record["key"])
                except Exception as exc:
                    errors.append(f"{record['node']} / {name}: {type(exc).__name__}: {exc}")
            return ended, errors
        def success(result):
            ended, errors = result
            for record in records:
                if record["key"] in ended:
                    self._forget_tmux_record(record["key"])
            self._refresh_tmux_tasks(save=True)
            if errors:
                QMessageBox.warning(self, tr('部分会话未能结束'), "\n".join(errors))
        self._run(work, success, tr('正在结束远程 tmux 会话…'), background_slot="_tmux_job",
                  log_action=f"结束 tmux 会话（{len(records)} 个）", log_category="terminal",
                  log_result=lambda result: ("部分完成" if result[1] and result[0] else "失败" if result[1] else "成功",
                      f"{len(result[0])}/{len(records)} 个会话已结束" + ("；" + "；".join(result[1]) if result[1] else "")))

    def _connect_terminals(self, node_ids: list[str], *, persistent=None, restore_tasks=None, recovery_group=None) -> None:
        if self._job is not None or not node_ids:
            return
        sources = self._terminal_sources()
        for node_id, record in (restore_tasks or {}).items():
            if node_id.startswith("tmux-route:"):
                sources[node_id] = (record["node"], self._parents[0][0] if self._parents else self._session)
        available = set(sources) | {f"worker:{alias}" for alias in self.worker_browser.known_aliases()}
        node_ids = list(dict.fromkeys(node_id for node_id in node_ids if node_id in available))
        if not node_ids:
            return
        persistent = self.terminal_workspace.persistent_mode.isChecked() if persistent is None else persistent
        restore_tasks = restore_tasks or {}
        remote_group = (next(iter(restore_tasks.values())).get("group") or next(iter(restore_tasks.values()))["name"]) if restore_tasks else uuid.uuid4().hex
        remote_label = (next(iter(restore_tasks.values())).get("label", "") if restore_tasks
                        else f"终端组{self.terminal_workspace._next_group}")
        jump = self._parents[0][0] if self._parents else self._session
        terminal_hosts = list(self._terminal_hosts.values())
        worker_hosts = list(self._worker_jumps.values())
        existing_helpers = list(self._terminal_jump_helpers)
        self._terminal_helper_reservations.update(id(host) for host in existing_helpers)
        prepared_tasks = {}

        def open_one(node_id: str, host: RemoteSession | None):
            if persistent:
                shell_host = None
                if node_id.startswith("worker:"):
                    if host is None:
                        raise ConnectionError("请先连接直连节点。")
                    backend = TmuxBackend(host, node_id.removeprefix("worker:"))
                else:
                    source = sources[node_id][1]
                    if source is jump and isinstance(source, RemoteSession) and not source._alias_route:
                        shell_host = RemoteSession.connect_shell_host(source.config, self._jump_password)
                    backend = TmuxBackend(shell_host or source)
                if node_id in restore_tasks:
                    backend.route = tuple(restore_tasks[node_id]["route"])
                try:
                    if node_id in restore_tasks:
                        name = restore_tasks[node_id]["name"]
                        task = next((task for task in backend.scan() if task.name == name), None)
                        if task is None:
                            raise OSError("远程会话已不存在；恢复不会重新创建或重新执行任务。")
                    elif node_id in prepared_tasks:
                        task = prepared_tasks[node_id]
                    else:
                        task = backend.create(remote_group, remote_label)
                        prepared_tasks[node_id] = task
                    channel = backend.attach(task)
                    channel.tmux_task = task
                    channel.tmux_route = backend.route
                    return channel, shell_host, ""
                except Exception:
                    if shell_host is not None:
                        shell_host.close()
                    raise
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
            link = (jump.config.host, jump.config.port, jump.config.username, remote_group) if persistent else None
            group_id = self._tmux_group_links.get(link) if link else None
            if recovery_group in self.terminal_workspace._groups:
                group_id = recovery_group
            if group_id not in self.terminal_workspace._groups:
                group_id = self.terminal_workspace.create_group(ready) if ready else None
                if persistent and group_id:
                    self._tmux_group_links[link] = group_id
                    if restore_tasks and remote_label:
                        self.terminal_workspace._groups[group_id].name = remote_label[:24]
                        self.terminal_workspace._refresh_groups(select=group_id)
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
                    node_id, group_id=group_id, activate=len(ready) == 1, persistent=persistent
                )
                if node_id in banners:
                    notice = banners[node_id].replace("\r\n", "\n").replace("\n", "\r\n")
                    self.terminal_workspace.append_output(session_id, notice.rstrip("\r\n") + "\r\n")
                if isinstance(channel, TmuxChannel):
                    screen, prompt = channel.take_initial_screen()
                    view = self.terminal_workspace._views[session_id]
                    view.output.write(screen)
                    view._recent_output = prompt
                self._terminal_manager.add(session_id, node_id, channel)
                if persistent:
                    task = channel.tmux_task
                    route = channel.tmux_route
                    source = sources.get(node_id, ("", jump))[1]
                    config = jump.config if route else source.config
                    record = self._remember_tmux_task(config, route, task,
                                                     self.terminal_workspace.node_label(node_id))
                    record["local_session"] = session_id
                    record["state"] = "本机已连接"
                    self._persistent_sessions[session_id] = record["key"]
                if node_id.startswith("worker:"):
                    self._node_connected(node_id.removeprefix("worker:"))
                if assigned[node_id] is not None:
                    self._terminal_hosts[session_id] = assigned[node_id]
                size = self.terminal_workspace.terminal_size(session_id)
                if size is not None:
                    self._terminal_manager.resize(session_id, *size)
            if recovery_group and group_id and len(self.terminal_workspace._groups[group_id].members) > 1:
                self.terminal_workspace.show_group_combined(group_id)
            if persistent:
                for node_id, error in errors.items():
                    if node_id in restore_tasks:
                        restore_tasks[node_id].update(state="恢复失败", detail=error)
                if group_id and len(self.terminal_workspace._groups[group_id].members) > 1:
                    self.terminal_workspace.show_group_combined(group_id)
                self._refresh_tmux_tasks(save=True)
            ui(self.status.setText,
                tr('SSH Shell：连接 {0} 个，失败 {1} 个；Enter 可发送命令。', f'{len(channels)}', f'{len(errors)}')
            )
            if errors:
                lines = [f"{self.terminal_workspace.node_label(node_id)}: {message}"
                         for node_id, message in errors.items()]
                omitted = f"\n另有 {len(lines) - 12} 个节点失败。" if len(lines) > 12 else ""
                QMessageBox.warning(
                    self, tr('终端连接结果'),
                    "以下节点连接失败：\n" + "\n".join(lines[:12]) + omitted,
                )

        def finished():
            self._terminal_helper_reservations.difference_update(id(host) for host in existing_helpers)
            self._release_unused_terminal_helpers()

        labels = [self.terminal_workspace.node_label(node_id) for node_id in node_ids]
        self._run(
            work, success, tr('正在连接 {0} 个 SSH Shell…', f'{len(node_ids)}'),
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
        persistent = session_id in self._persistent_sessions
        self._terminal_manager.close(session_id)
        self.terminal_workspace.remove_terminal(session_id)
        ui(self.status.setText, (tr('持久终端已分离；远程会话保留，可在任务页检查和恢复。') if persistent else tr('终端连接已关闭。')))

    def _terminal_close_choice(self, title: str, count: int) -> str:
        box = QMessageBox(self)
        ui(box.setWindowTitle, title)
        box.setIcon(QMessageBox.Icon.Warning)
        ui(box.setText, tr('所选 {0} 个终端包含 tmux 持久会话，请选择处理方式。', f'{count}'))
        ui(box.setInformativeText, tr('永久结束会销毁远程 tmux 会话，可能终止其中的程序，之后无法恢复。\n分离连接会关闭本地连接，保留远程 tmux 会话，可在任务页恢复。\n若包含普通终端，其连接也会关闭，但不支持恢复。'))
        end = ui(box.addButton, tr('永久结束会话'), QMessageBox.ButtonRole.DestructiveRole)
        detach = ui(box.addButton, tr('分离连接'), QMessageBox.ButtonRole.ActionRole)
        cancel = ui(box.addButton, QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return "end" if box.clickedButton() is end else "detach" if box.clickedButton() is detach else "cancel"

    def _request_close_terminal(self, session_id: str) -> None:
        if session_id not in self._persistent_sessions:
            self._close_terminal(session_id)
            return
        choice = self._terminal_close_choice(tr("断开终端"), 1)
        if choice == "detach":
            self._close_terminal(session_id)
        elif choice == "end":
            self._end_terminal_sessions([session_id])

    def _end_terminal_sessions(self, sessions: list[str]) -> None:
        # Resolve all routes before closing anything; failed kills retain their local views.
        if self._job is not None or self._tmux_job is not None:
            QMessageBox.information(self, tr('结束终端'), tr('请等待当前连接或会话查询完成后重试。'))
            return
        targets = []
        ordinary = []
        try:
            for sid in sessions:
                key = self._persistent_sessions.get(sid)
                if key is None:
                    ordinary.append(sid)
                    continue
                record = self._tmux_records[key]
                targets.append((sid, record, self._tmux_root(record), tuple(record["route"]), record["name"]))
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, tr('结束终端'), str(exc))
            return
        if not targets:
            for sid in ordinary:
                self._close_terminal(sid)
            return
        def work():
            ended, errors = [], []
            for sid, record, root, route, name in targets:
                try:
                    backend = TmuxBackend(root)
                    backend.route = route
                    backend.kill(name)
                    ended.append(sid)
                except Exception as exc:
                    errors.append(f"{record['node']} / {name}: {type(exc).__name__}: {exc}")
            return ended, errors
        def success(result):
            ended, errors = result
            for sid in ordinary:
                self._close_terminal(sid)
            for sid, record, *_rest in targets:
                if sid in ended:
                    # PTY EOF may already have cleared record['local_session'].
                    # The requested session ID still identifies its retained tab.
                    self._close_terminal(sid)
                    self._forget_tmux_record(record["key"])
            self._refresh_tmux_tasks(save=True)
            ui(self.status.setText, tr('已永久结束 {0} 个 tmux 会话，关闭 {1} 个普通终端。', f'{len(ended)}', f'{len(ordinary)}'))
            if errors:
                QMessageBox.warning(self, tr('部分终端未能结束'), "失败的终端连接保留，可重试。\n" + "\n".join(errors))
        self._run(work, success, tr('正在永久结束远程会话…'), background_slot="_tmux_job",
                  log_action=f"永久结束终端（{len(targets)} 个 tmux 会话）", log_category="terminal",
                  log_result=lambda result: ("部分完成" if result[1] and result[0] else "失败" if result[1] else "成功",
                      f"{len(result[0])}/{len(targets)} 个会话已结束" + ("；" + "；".join(result[1]) if result[1] else "")))

    def _close_terminal_group(self, group_id: str) -> None:
        sessions = self.terminal_workspace.group_sessions(group_id)
        if not sessions:
            return
        name = self.terminal_workspace.group_name(group_id)
        if any(sid in self._persistent_sessions for sid in sessions):
            choice = self._terminal_close_choice(tr("断开终端组：{0}", name), len(sessions))
            if choice == "detach":
                for sid in sessions:
                    self._close_terminal(sid)
            elif choice == "end":
                self._end_terminal_sessions(sessions)
            return
        choice = QMessageBox.question(
            self, tr('断开终端组'),
            tr('断开 {0} 的 {1} 个终端连接？', f'{name}', f'{len(sessions)}'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        for session_id in sessions:
            self._terminal_manager.close(session_id)
            self.terminal_workspace.remove_terminal(session_id)
        ui(self.status.setText, tr('已断开 {0} 的 {1} 个终端连接。', f'{name}', f'{len(sessions)}'))

    def _send_terminal_input(self, session_id: str, data: bytes) -> None:
        try:
            self._terminal_manager.send(session_id, data)
        except ConnectionError as exc:
            ui(self.status.setText, str(exc))

    def _interrupt_broadcast(self, group_id: str, targets: dict[str, str]) -> None:
        workspace = self.terminal_workspace
        targets = dict(targets)
        if self._job is not None or self._broadcast_check is not None:
            return
        if not workspace.valid_broadcast(group_id, targets):
            return
        labels = [workspace.node_label(node_id) for node_id in targets]
        group_name = workspace.group_name(group_id)
        answer = QMessageBox.question(
            self, tr('确认广播中断'),
            tr('向 {0} 中勾选的 {1} 个节点发送 Ctrl+C？\n目标：{2}\n\n这会请求中断各终端当前的前台程序，可能使正在进行的工作提前结束。每个节点仅发送一次，程序是否停止取决于远端程序是否响应。', f'{group_name}', f'{len(targets)}', f'{compact_node_aliases(labels)}'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if (self._job is not None or self._broadcast_check is not None
                or not workspace.valid_broadcast(group_id, targets)
                or workspace.broadcast_targets() != targets):
            QMessageBox.warning(self, tr('中断未发送'), tr('终端组或目标状态已变化，请重新选择后再试。'))
            return
        sent, errors = [], []
        for node_id, session_id in targets.items():
            try:
                self._terminal_manager.send(session_id, b"\x03")
                sent.append(workspace.node_label(node_id))
            except ConnectionError as exc:
                errors.append(f"{workspace.node_label(node_id)}：{exc}")
        detail = f"Ctrl+C 已提交发送至 {len(sent)}/{len(targets)} 个终端；实际中断结果请查看终端输出。"
        ui(self.status.setText, detail)
        self._record_activity(
            "broadcast", f"广播中断（{group_name}）",
            "部分发送" if errors and sent else "发送失败" if errors else "已提交发送",
            detail, context={"targets": labels, "sent_nodes": sent, "errors": errors, "control": "Ctrl+C"},
        )
        if errors:
            QMessageBox.warning(self, tr('中断发送失败'), "\n".join(errors))
        workspace.broadcast_input.setFocus()

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
            QMessageBox.warning(self, tr('广播未发送'), tr('终端组或目标已变化，请重新选择当前组的终端。'))
            self._record_activity("broadcast", "终端广播", "未发送", "终端组或目标已变化")
            return
        if any(not self._terminal_manager.has_session(sid) for sid in targets.values()):
            QMessageBox.warning(self, tr('广播未发送'), tr('有目标终端已断开，请重新选择广播目标。'))
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
            QMessageBox.warning(self, tr('广播未发送'), tr('检查期间有目标终端断开。请重新选择后再试。'))
            self.terminal_workspace.set_broadcast_busy(False)
            self._record_broadcast(state, "未发送", "检查期间有目标终端断开")
            return
        directories = [state.directories.get(sid) for sid in state.targets.values()]
        same_directory = all(directories) and len(set(directories)) == 1
        if not same_directory:
            details = [
                f"{self.terminal_workspace.node_label(node_id)}："
                f"{state.directories.get(sid) or tr('目录未知或终端不在可识别的 Shell 提示符')}"
                for node_id, sid in state.targets.items()
            ]
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            ui(dialog.setWindowTitle, tr('广播前确认'))
            ui(dialog.setText, tr('目标终端的工作目录不同或无法核实。仍向全部目标发送命令？'))
            ui(dialog.setInformativeText, "\n".join(details[:12]) +
                                      (tr("\n其余 {0} 个节点见详情。", len(details) - 12) if len(details) > 12 else ""))
            dialog.setDetailedText("\n".join(details))
            send_button = ui(dialog.addButton, tr('仍然发送'), QMessageBox.ButtonRole.AcceptRole)
            cancel_button = ui(dialog.addButton, tr('取消'), QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(cancel_button)
            dialog.exec()
            if dialog.clickedButton() is not send_button:
                ui(self.status.setText, tr('广播已取消；命令保留在广播输入框中。'))
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "工作目录不同或无法核实")
                return

        if not self.terminal_workspace.valid_broadcast(state.group_id, state.targets):
            ui(self.status.setText, tr('广播未发送：终端组或目标已变化。'))
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
                   if state.conda_prefixes[sid] else tr("未激活 Conda 环境"))
                for node_id, sid in state.targets.items()
            ]
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            ui(dialog.setWindowTitle, tr('广播前确认 Conda 环境'))
            ui(dialog.setText, tr('目标终端显示的 Conda 环境前缀不同。是否继续广播？'))
            ui(dialog.setInformativeText, "\n".join(details[:12]) +
                                      (tr("\n其余 {0} 个节点见详情。", len(details) - 12)
                                       if len(details) > 12 else ""))
            dialog.setDetailedText("\n".join(details))
            cancel_button = ui(dialog.addButton, tr('不发送'), QMessageBox.ButtonRole.RejectRole)
            send_button = ui(dialog.addButton, tr('此次仍要发送'), QMessageBox.ButtonRole.AcceptRole)
            ignore_button = ui(dialog.addButton,
                tr('该终端组不再提示'), QMessageBox.ButtonRole.AcceptRole
            )
            dialog.setDefaultButton(cancel_button)
            dialog.exec()
            if dialog.clickedButton() is ignore_button:
                self.terminal_workspace.ignore_conda_mismatch(state.group_id)
            elif dialog.clickedButton() is not send_button:
                ui(self.status.setText, tr('广播已取消：Conda 环境前缀不同；命令保留在输入框中。'))
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "Conda 环境前缀不同")
                return

        if DESTRUCTIVE_SHELL_COMMAND.search(state.command):
            group_name = self.terminal_workspace.group_name(state.group_id)
            response = QMessageBox.warning(
                self, tr('确认高风险广播'),
                tr('{0} 将向 {1} 个终端广播可能修改或删除数据的命令。\n请核对组和目标后决定是否继续。', f'{group_name}', f'{len(state.targets)}'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response != QMessageBox.StandardButton.Yes:
                ui(self.status.setText, tr('高风险广播已取消；命令保留在输入框中。'))
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "取消", "高风险命令确认未通过")
                return
            if not self.terminal_workspace.valid_broadcast(state.group_id, state.targets):
                ui(self.status.setText, tr('广播未发送：终端组或目标已变化。'))
                self.terminal_workspace.set_broadcast_busy(False)
                self._record_broadcast(state, "未发送", "终端组或目标已变化")
                return

        sent = 0
        errors = []
        sent_nodes = []
        self.terminal_workspace.begin_broadcast_round(state.group_id, state.command, state.targets)
        for node_id, session_id in state.targets.items():
            try:
                command = state.command.replace("\r\n", "\n").replace("\r", "\n")
                self._terminal_manager.send(session_id, (command.replace("\n", "\r") + "\r").encode("utf-8"))
                sent += 1
                sent_nodes.append(self.terminal_workspace.node_label(node_id))
            except ConnectionError as exc:
                self.terminal_workspace.mark_broadcast_send_failed(state.group_id, session_id)
                errors.append(f"{self.terminal_workspace.node_label(node_id)}：{exc}")
        if sent:
            self.terminal_workspace.broadcast_input.clear()
        ui(self.status.setText,
            tr('{0}：命令已发送至 {1}/{2} 个终端；可在联合显示或各终端查看输出。', f'{self.terminal_workspace.group_name(state.group_id)}', f'{sent}', f'{len(state.targets)}')
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
            QMessageBox.warning(self, tr('广播发送失败'), "\n".join(errors))

    def _resize_terminal(self, session_id: str, columns: int, rows: int) -> None:
        self._terminal_manager.resize(session_id, columns, rows)

    def _terminal_input_failed(self, _session_id: str, error: str) -> None:
        state = self._broadcast_check
        if state is not None and _session_id in state.pending:
            state.pending.pop(_session_id, None)
            state.buffers.pop(_session_id, None)
            if not state.pending:
                QTimer.singleShot(0, self._finish_broadcast_check)
        ui(self.status.setText, tr('终端输入失败：{0}', f'{error}'))
        self._record_activity("terminal", "SSH 终端输入", "发送失败", error)

    def _close_terminals(self, node_ids: list[str]) -> None:
        sessions = [sid for sid, record in self.terminal_workspace._sessions.items()
                    if record.node_id in node_ids]
        if any(sid in self._persistent_sessions for sid in sessions):
            choice = self._terminal_close_choice(tr("断开勾选节点终端"), len(sessions))
            if choice == "detach":
                for sid in sessions:
                    self._close_terminal(sid)
            elif choice == "end":
                self._end_terminal_sessions(sessions)
            return
        closed = self._terminal_manager.close_nodes(node_ids)
        ui(self.status.setText, tr('已关闭 {0} 个终端连接。', f'{closed}'))

    def _terminal_closed(self, session_id: str, unexpected: bool) -> None:
        task_key = self._persistent_sessions.pop(session_id, None)
        record = self._tmux_records.get(task_key)
        if record is not None:
            record.update(local_session="", state="待检查（已断开）" if unexpected else "已分离（待检查）",
                          attached=max(0, record.get("attached", 0) - 1),
                          detail="本机连接已关闭；需扫描服务器确认会话仍存在。")
            self._refresh_tmux_tasks(save=True)
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
            ui(self.status.setText, (tr('持久终端连接已断开；请在任务页检查和恢复远程会话。') if record is not None else tr('SSH Shell 已关闭；终端输出仍可查看。')))
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

    def _set_workers_visible(self, visible: bool) -> None:
        self.worker_browser.setVisible(visible)
        self._settings.setValue("view/show_indirect_nodes", visible)
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
            QMessageBox.warning(self, tr('打开日志文件'), tr('无法创建日志文件：{0}', f'{exc}'))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, tr('打开日志文件'), tr('无法打开日志文件：{0}', f'{path}'))

    def _show_about(self) -> None:
        QMessageBox.about(self, tr("关于 NodeBridge"), tr("NodeBridge — SSH/SFTP 文件管理器"))

    def _show_user_guide(self) -> None:
        if self._help_dialog is None:
            path = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
            try:
                guide = path.read_text(encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, tr('使用指南'), tr('无法读取使用指南：{0}', f'{exc}'))
                return
            dialog = QDialog(self)
            ui(dialog.setWindowTitle, tr('NodeBridge 使用指南'))
            dialog.resize(860, 650)
            layout = QVBoxLayout(dialog)
            browser = QTextBrowser(dialog)
            browser.setObjectName("userGuideBrowser")
            browser.setMarkdown(guide)
            browser.setOpenExternalLinks(True)
            layout.addWidget(browser)
            close_button = ui(QPushButton, tr('关闭'), dialog)
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            self._help_dialog = dialog
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()

    def _file_busy(self, *scopes: str) -> bool:
        requested = set(scopes)
        return (bool(self._job is not None and requested != {"local"})
                or bool("workers" in requested and self._worker_connect_job is not None)
                or any(requested.intersection(active) for active in self._file_tasks.values()))

    def _remote_files_active(self) -> bool:
        return (any(active - {"local"} for active in self._file_tasks.values())
                or self._remote_browse_job is not None or self._worker_browse_job is not None
                or self._worker_connect_job is not None)

    def refresh_language(self) -> None:
        """Repaint translated model data without replacing live views or connections."""
        self.local.file_model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 3)
        self.local.table.viewport().update()
        self.terminal_workspace.refresh_language()

    def _update_controls(self) -> None:
        if getattr(self, "_recovery", None) is not None:
            self._recovery.checkpoint()
        busy = self._job is not None
        copying = self._remote_files_active()
        connecting_workers = self._worker_connect_job is not None
        connected = self._session is not None
        self.connect_button.setEnabled(not busy and not copying and not connected)
        self.jump_button.setEnabled(not busy and not copying and connected)
        self.disconnect_button.setEnabled(not busy and not copying and connected)
        ui(self.disconnect_button.setText, (tr('返回上一站点') if self._parents else tr('断开')))
        self.site_button.setEnabled(not busy and not copying)
        for widget in (self.host, self.port, self.user, self.key_file, self.password):
            widget.setEnabled(not busy and not connected)
        for widget in (self.up_button, self.refresh_button, self.path_edit, self.tree, self.table):
            widget.setEnabled(not busy and self._remote_browse_job is None and connected)
        self.local_tree.setEnabled(True)
        self.local_table.setEnabled(True)
        self.worker_browser.set_busy(
            busy or self._worker_browse_job is not None or connecting_workers,
            connected and not self._parents,
        )
        if self._file_busy("workers"):
            self.worker_browser.connect_button.setEnabled(False)
            self.worker_browser.disconnect_button.setEnabled(False)
            self.worker_browser.add_button.setEnabled(False)
            self.worker_browser.create_directory_button.setEnabled(False)
        self.terminal_workspace.set_busy(busy)
        self.terminal_workspace.persistent_mode.setEnabled(not busy)
        self.tasks_workspace.set_busy(busy or self._tmux_job is not None, connected)
        self.tasks_workspace.set_scanning(self._tmux_scan_job is not None)
        if self._tmux_job is not None:
            self.disconnect_button.setEnabled(False)
        if connecting_workers:
            for widget in (self.jump_button, self.disconnect_button, self.site_button,
                           self.worker_browser.connect_button, self.worker_browser.disconnect_button,
                           self.worker_browser.add_button):
                widget.setEnabled(False)

    def connect_startup_site(self) -> None:
        if self._session is not None or self._job is not None:
            return
        try:
            if not self._site_store.has_startup_site():
                return
            try:
                sites = self._site_store.load()
            except LockedSiteStore:
                while True:
                    password, accepted = QInputDialog.getText(
                        self, tr('启动连接：解锁站点密码'), tr('请输入 NodeBridge 主密码：'),
                        QLineEdit.EchoMode.Password,
                    )
                    if not accepted:
                        return
                    try:
                        self._site_store.unlock(password)
                        sites = self._site_store.load()
                        break
                    except ValueError as exc:
                        QMessageBox.warning(self, tr('解锁失败'), str(exc))
            selected = [site for site in sites if site.auto_connect]
            if len(selected) != 1:
                raise ValueError("请在站点管理器中仅选择一个自动连接站点。")
            site = selected[0]
            if not site.host or not site.username:
                raise ValueError("自动连接站点缺少主机或用户名，请在站点管理器中补全。")
            password = site.password or None
            if self._site_store.mode == "none" and not password and not site.key_file:
                password, accepted = QInputDialog.getText(
                    self, tr('启动连接：站点密码'), tr('请输入“{0}”的连接密码（免密可留空）：', f'{site.name}'),
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    return
            self._start_connection(
                NodeConfig(site.host, site.port, site.username, site.key_file or None, site.discovery), password or None, None,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, tr('启动连接'), tr('无法自动连接站点：{0}', f'{exc}'))

    def _open_site_manager(self) -> None:
        try:
            dialog = SiteManagerDialog(
                self._site_store, self,
                jump_available=self._session is not None and getattr(self._session, "_client", None) is not None,
                connect_available=self._session is None or getattr(self._session, "_client", None) is not None,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, tr('站点管理器'), tr('无法读取站点资料：{0}', f'{exc}'))
            return
        if dialog.exec() != SiteManagerDialog.DialogCode.Accepted:
            return
        site = dialog.selected_site
        if site is None:
            return
        if self._session is not None and getattr(self._session, "_client", None) is None:
            QMessageBox.warning(self, "NodeBridge", tr('当前站点通过 SSH 别名连接；请用“SSH 别名中转”继续跳转。'))
            return
        config = NodeConfig(site.host, site.port, site.username, site.key_file or None, site.discovery)
        self._start_connection(config, dialog.connection_password, self._session)

    def _prompt_alias(self) -> None:
        if self._session is None:
            return
        alias, accepted = QInputDialog.getText(
            self, tr('SSH 别名中转'), tr('目标节点别名（例如 cft02）：')
        )
        if accepted and alias.strip():
            self._start_alias_connection(alias.strip())

    def _start_alias_connection(self, alias: str) -> None:
        jump = self._session
        if (jump is None or self._job is not None or self._remote_files_active()
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
            ui(self.jump_title.setText, tr('当前站点 · {0}', f'{alias}'))
            self._show_connection_fields(self._session.config)
            self.tree.clear()
            self._show_listing(listing)
            ui(self.status.setText, tr('已通过 {0} 的 SSH 别名连接到 {1}，当前目录：{2}', f'{jump.config.host}', f'{alias}', f'{listing.path}'))

        self._run(
            work, success, tr('正在通过当前站点连接 SSH 别名 {0}…', f'{alias}'),
            log_action=f"SSH 连接（{jump.config.host} → {alias}）",
            log_category="connection", log_context={"node": alias, "via": jump.config.host},
        )

    def _discover_worker_aliases(self, manual: bool = False) -> None:
        jump = self._session
        if jump is None:
            if manual:
                QMessageBox.information(self, tr('发现间接节点'), tr('请先连接直连节点。'))
            return
        if self._parents:
            if manual:
                QMessageBox.information(self, tr('发现间接节点'), tr('请先返回直连节点，再发现间接节点。'))
            return
        if self._job is not None:
            if manual:
                ui(self.status.setText, tr('请等待当前任务结束后再发现节点。'))
            return

        ui(self.worker_browser.summary.setText, tr('正在按当前站点的发现设置读取远程节点名单…'))

        def success(result: NodeDiscovery) -> None:
            if result.hostname:
                jump.display_hostname = result.hostname
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
                ui(self.worker_browser.summary.setText, message)
            ui(self.status.setText, message)
            if manual:
                QMessageBox.information(self, tr('发现间接节点'), message)

        self._run(jump.discover_work_nodes, success, tr('正在发现直连节点可解析的工作主机…'))

    def _show_node_probe_result(self, result) -> None:
        generation, jump, cancel, revision, alias, status = result
        if (generation != self._probe_generation or self._session is not jump
                or self._parents or cancel.is_set()):
            return
        self._set_node_probe_statuses({alias: status}, revision)

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
        observed_revision = self._node_status_revision
        self._set_node_probe_statuses({
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
                        self.nodeProbeResult.emit((
                            generation, jump, cancel, observed_revision, alias, statuses[alias],
                        ))
            finally:
                helper.close()
            return statuses

        def success(statuses):
            if (generation != self._probe_generation or self._session is not jump
                    or self._parents or cancel.is_set()):
                return
            reachable = sum(state == "reachable" for state, _ in statuses.values())
            ui(self.status.setText,
                tr('SSH 检测完成：{0}/{1} 个候选节点可连接。 灰色节点仍可勾选并重新尝试连接。', f'{reachable}', f'{len(statuses)}')
            )

        job = JobThread(work, self)
        self._probe_job = job
        job.succeeded.connect(success)
        def failed(message):
            if generation != self._probe_generation or cancel.is_set():
                return
            self._set_node_probe_statuses({
                alias: ("error", message) for alias in aliases
            }, observed_revision)
            ui(self.status.setText, tr('SSH 后台探测失败：{0}', f'{message}'))
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
        if (self._session is None or self._parents or self._file_busy("workers")):
            return
        alias, accepted = QInputDialog.getText(self, tr('添加间接节点'), tr('直连节点上的 SSH 别名：'))
        alias = alias.strip()
        if not accepted or not alias:
            return
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
            QMessageBox.warning(self, tr('间接节点'), tr('别名只能包含字母、数字、点、下划线、@ 和连字符。'))
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
        if jump is None or self._parents or self._file_busy("workers") or self._worker_browse_job is not None or self._worker_connect_job is not None:
            return
        aliases = [alias for alias in self.worker_browser.target_aliases() if alias not in self._workers]
        self.worker_browser._check_all(False)
        if not aliases:
            ui(self.status.setText, tr('请勾选尚未连接的间接节点，或通过“添加别名…”输入节点。'))
            return
        self._cancel_auto_hash()
        path = self.worker_browser.path_edit.text().strip()
        connection_revision = self._node_status_revision

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
                # A browser session stays open per worker. Reserve room on each
                # jump transport for separate transfer channels and other work.
                host = next((root for root in roots if counts[id(root)] < 4), None)
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
                self._node_connected(alias)
            for alias, message in errors.items():
                self._set_node_probe_statuses({alias: ("error", message)}, connection_revision)
            self._sync_terminal_nodes()
            self._schedule_auto_hash()
            ui(self.status.setText, tr('间接节点：新连接 {0} 个，失败 {1} 个。', f'{len(results)}', f'{len(errors)}'))
            if errors:
                lines = [f"{alias}: {message}" for alias, message in list(errors.items())[:8]]
                QMessageBox.warning(self, tr('间接节点连接结果'), "以下节点连接失败：\n" + "\n".join(lines))

        self._run(
            work, success, tr('正在经 {0} 连接 {1} 个间接节点…', f'{jump.config.host}', f'{len(aliases)}'),
            log_action=f"SFTP 并行连接（{compact_node_aliases(aliases)}）",
            background_slot="_worker_connect_job",
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
        if self._file_busy("workers") or self._worker_browse_job is not None or self._worker_connect_job is not None:
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
            ui(self.status.setText, tr('已断开 {0} 个间接节点；关闭错误 {1} 个。', f'{len(aliases)}', f'{len(errors)}'))

        self._run(
            work, success, tr('正在断开 {0} 个间接节点…', f'{len(aliases)}'),
            background_slot="_worker_connect_job",
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

    def _browse_workers(self, path: str, parent_fallback: bool = False) -> None:
        if path.startswith("/"):
            self._cancel_auto_hash()
        if (self._job is not None or self._worker_browse_job is not None
                or self._worker_connect_job is not None):
            if path.startswith("/"):
                self._pending_worker_refresh = (path, parent_fallback)
            return
        if not path.startswith("/"):
            ui(self.status.setText, tr('间接节点路径必须是绝对路径。'))
            return
        if self.worker_browser.mode.currentIndex() == 1:
            alias = self.worker_browser.focused_alias
            names = [alias] if alias in self._workers else []
        else:
            names = sorted(self._workers)
        if not names:
            ui(self.status.setText, tr('请先连接间接节点。'))
            return
        sessions = {name: self._workers[name] for name in names}
        requested_view = (self.worker_browser.mode.currentIndex(), self.worker_browser.focused_alias)
        parent_fallback = parent_fallback and requested_view[0] == 1

        def read_directory(session):
            candidate = path
            while True:
                try:
                    return session.list_directory(candidate)
                except OSError as exc:
                    missing = isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT
                    if not parent_fallback or not missing or candidate == "/":
                        raise
                    candidate = posixpath.dirname(candidate.rstrip("/")) or "/"

        def work():
            results = {}
            errors = {}
            with ThreadPoolExecutor(max_workers=min(4, len(sessions))) as pool:
                futures = {pool.submit(read_directory, session): alias for alias, session in sessions.items()}
                for future in as_completed(futures):
                    alias = futures[future]
                    try:
                        results[alias] = future.result()
                    except Exception as exc:
                        errors[alias] = str(exc)
            return results, errors

        def success(result):
            results, errors = result
            current_view = (self.worker_browser.mode.currentIndex(), self.worker_browser.focused_alias)
            if current_view == requested_view and self._pending_worker_refresh is None:
                shown_path = results[names[0]].path if requested_view[0] == 1 and results else path
                ui(self.worker_browser.path_edit.setText, shown_path)
            self.worker_browser.set_listings(results, list(errors))
            self._schedule_auto_hash()
            ui(self.status.setText, tr('间接节点目录：已读取 {0} 个，失败 {1} 个。', f'{len(results)}', f'{len(errors)}'))
            if errors:
                lines = [f"{alias}: {message}" for alias, message in list(errors.items())[:8]]
                self._show_background_error("以下节点读取失败：\n" + "\n".join(lines))

        self._run(work, success, tr('正在读取 {0} 个间接节点的 {1}…', f'{len(names)}', f'{path}'),
                  self._finish_worker_browse, background_slot="_worker_browse_job")

    def _finish_worker_browse(self) -> None:
        if self._pending_worker_refresh is not None:
            path, fallback = self._pending_worker_refresh
            self._pending_worker_refresh = None
            self._browse_workers(path, fallback)

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
        snapshots = self.worker_browser.hash_listing_snapshots(path, aliases, names)
        cached_results = []
        remaining = []
        now = time.monotonic()
        for name in names:
            key = self._hash_cache_key(path, aliases, name, snapshots)
            cached = self._hash_cache.get(key) if key is not None else None
            if cached is not None and now - cached[0] < 60:
                cached_results.append({**cached[1], "cached": True})
            else:
                remaining.append(name)
        if cached_results:
            self.worker_browser.set_hash_results(path, cached_results)
        names = remaining
        if not names:
            return
        generation = self._hash_generation
        self._hash_pending = (path, aliases, names, generation)
        self.worker_browser.set_hash_pending(path, names)
        if self._hash_job is None:
            self._start_auto_hash()

    def _hash_cache_key(self, path, aliases, name, snapshots):
        entries = []
        for alias in sorted(aliases):
            entry = snapshots.get(alias, {}).get(name)
            if entry is None or entry.modified is None or entry.size is None:
                return None
            entries.append((alias, self._workers.get(alias), entry.size, entry.modified,
                            entry.is_dir, entry.is_symlink))
        return self._session, path, name, tuple(entries)

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
            now = time.monotonic()
            self._hash_cache = {key: value for key, value in self._hash_cache.items()
                                if now - value[0] < 60}
            for result in results:
                key = self._hash_cache_key(path, aliases, result["name"], snapshots)
                if key is not None and result["result"] in {"same", "different"}:
                    self._hash_cache[key] = (now, result)
            while len(self._hash_cache) > 2048:
                self._hash_cache.pop(next(iter(self._hash_cache)))
            differences = sum(result["result"] != "same" for result in results)
            ui(self.status.setText,
                tr('后台 SHA-256 校验完成：{0} 个文件，{1} 项差异或错误。', f'{len(results)}', f'{differences}')
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
            ui(self.status.setText, tr('后台 SHA-256 校验失败：{0}', f'{message}'))
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
            ui(self.status.setText, tr('后台 SHA-256 校验 {0}：已检查 {1}/{2} 个节点…', f'{path}', f'{done}', f'{total}'))

    def _hash_finished(self, job: JobThread) -> None:
        if self._hash_job is job:
            self._hash_job = None
        if self._hash_pending is not None:
            QTimer.singleShot(0, self._start_auto_hash)
        self._finish_background_close()

    def _choose_worker_scope(self, verb: str) -> list[str]:
        verb = tr(verb)
        connected = sorted(self._workers, key=str.casefold)
        current = self.worker_browser.focused_alias
        if current not in self._workers:
            current = connected[0] if connected else None
        if current is None:
            QMessageBox.warning(self, tr('间接节点'), tr('请先连接并选中一个间接节点。'))
            return []
        if len(connected) < 2:
            return [current]
        dialog = QMessageBox(self)
        ui(dialog.setWindowTitle, tr('{0}范围', f'{verb}'))
        ui(dialog.setText, tr('要在当前节点 {0} 上{1}，还是在已连接的 {2} 个节点上并行{3}？', f'{current}', f'{verb}', f'{len(connected)}', f'{verb}'))
        single = ui(dialog.addButton, tr('仅 {0}', f'{current}'), QMessageBox.ButtonRole.AcceptRole)
        multiple = ui(dialog.addButton, tr('已连接的 {0} 个节点', f'{len(connected)}'), QMessageBox.ButtonRole.ActionRole)
        ui(dialog.addButton, QMessageBox.StandardButton.Cancel)
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
            QMessageBox.warning(self, tr('复制文件'), tr('拖动来源不是当前直连节点。'))
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
        transfer_pool: TransferSessionPool | None = None,
    ) -> tuple[dict[str, list[str]], dict[str, BatchResult]]:
        mapped: dict[str, list[str]] = {}
        failures: dict[str, BatchResult] = {}

        def inspect_one(alias: str, session: RemoteSession) -> list[str]:
            if transfer_pool is not None:
                with transfer_pool.worker(alias) as peer:
                    return inspect_paths(alias, peer)
            return inspect_paths(alias, session)

        def inspect_paths(alias: str, session: RemoteSession) -> list[str]:
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
        scopes = frozenset({"workers", "remote" if to_jump else "local"})
        jump = self._session
        if (jump is None or self._parents or self._file_busy(*scopes)
                or token != self.worker_browser.drag_token):
            return
        if not paths or any(not path.startswith("/") for path in paths):
            return
        parents = {posixpath.dirname(path.rstrip("/")) for path in paths}
        if len(parents) != 1 or not next(iter(parents)).startswith("/"):
            QMessageBox.warning(self, tr('汇集文件'), tr('请从同一个间接节点目录选择文件或文件夹。'))
            return
        if to_jump and not destination.startswith("/"):
            QMessageBox.warning(self, tr('汇集文件'), tr('跳板目标必须是绝对路径。'))
            return
        if not to_jump and not Path(destination).is_dir():
            QMessageBox.warning(self, tr('汇集文件'), tr('本地目标目录不存在。'))
            return
        aliases = self._choose_worker_scope("汇集复制")
        if not aliases:
            return
        sessions = {alias: self._workers[alias] for alias in aliases}
        browsed_jump_path = self._path
        jump_version = self._listing_version
        resolve_conflict = self._new_conflict_resolver()
        self._batch_mode_label = "汇集到跳板" if to_jump else "汇集到本地"
        self.batch_results.setRowCount(0)

        def work():
            # Only active tasks hold transfer channels. The authenticated jump
            # connections are reused across preflight, copy, and refresh.
            with TransferSessionPool(jump.config, self._jump_password) as transfer_pool:
                mapped, failures = self._inspect_collection_roots(
                    paths, sessions, transfer_pool,
                )
                for result in failures.values():
                    self.batchResultReady.emit(result)
                good = {alias: session for alias, session in sessions.items() if alias in mapped}

                def copy_one(alias: str, _browse_session: RemoteSession) -> BatchResult:
                    progress = lambda path: self.transferProgress.emit(f"{alias}：正在汇集 {path}")
                    with transfer_pool.worker(alias) as source:
                        if not to_jump:
                            result = copy_remote_to_local(
                                source, paths, destination, progress, resolve_conflict,
                                root_suffix=alias,
                            )
                        else:
                            with transfer_pool.jump() as target:
                                result = copy_remote_between_sessions(
                                    source, target, paths, destination, progress,
                                    resolve_conflict, root_suffix=alias,
                                )
                    return BatchResult(alias, copied=result)

                copied = run_parallel(good, copy_one, self.batchResultReady.emit) if good else []
                results = {result.alias: result for result in [*failures.values(), *copied]}
                listing = None
                if to_jump:
                    try:
                        with transfer_pool.jump() as target:
                            listing = target.list_directory(browsed_jump_path)
                    except Exception:
                        pass
                return [results[alias] for alias in aliases], listing

        def success(value):
            results, listing = value
            if to_jump and listing is not None and (self._session is jump and self._path == browsed_jump_path
                    and self._listing_version == jump_version and self._remote_browse_job is None):
                self._show_listing(listing)
            elif not to_jump:
                self.local.refresh()
            complete = sum(result.complete for result in results)
            self._notify_done(tr('汇集完成：{0}/{1} 个节点成功；详情见下方结果表。', f'{complete}', f'{len(results)}'))

        self._run(
            work, success, tr('正在从 {0} 个间接节点汇集文件…', f'{len(aliases)}'),
            log_action=f"并行汇集（{compact_node_aliases(aliases)}）：{self._log_paths(paths)} → {destination}；顶层名称加节点后缀",
            log_result=self._batch_log_result,
            log_context={"sources": paths, "destination": destination, "nodes": aliases},
            log_context_result=self._batch_log_context,
            background_copy=True, file_scopes=scopes,
        )

    def _delete_worker_selection(self) -> None:
        if self._file_busy("workers"):
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
        if (self._session is None or self._parents or self._file_busy("workers")):
            return
        parent = self.worker_browser.rendered_path
        if not parent.startswith("/"):
            QMessageBox.warning(self, tr('新建目录'), tr('请先打开间接节点上的绝对目录。'))
            return
        name, accepted = QInputDialog.getText(self, tr('新建目录'), tr('目录名称：'))
        if not accepted:
            return
        try:
            name = validate_remote_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, tr('新建目录'), str(exc))
            return
        aliases = self._choose_worker_scope("新建目录")
        if not aliases:
            return
        nodes = {alias: self._workers[alias] for alias in aliases}
        target = posixpath.join(parent, name)
        self.batch_results.setRowCount(0)

        jump = self._session

        def work_on_pool(transfer_pool) -> WorkerDirectoryOutcome:
            conflicts: list[str] = []
            errors: dict[str, str] = {}

            def inspect(alias: str, session: RemoteSession) -> bool:
                with transfer_pool.worker(alias) as peer:
                    parent_info = peer.sftp.stat(parent)
                    if not stat.S_ISDIR(parent_info.st_mode or 0):
                        raise NotADirectoryError(f"目标不是目录：{parent}")
                    return remote_directory_conflict(peer, parent, name)

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

            def create_one(alias: str, _session: RemoteSession):
                try:
                    with transfer_pool.worker(alias) as peer:
                        path, created = create_remote_directory_with_policy(peer, parent, name, policy)
                        result = WorkerDirectoryResult(alias, path, created)
                        try:
                            listing = peer.list_directory(parent)
                        except Exception:
                            listing = None
                        return result, listing
                except Exception as exc:
                    return WorkerDirectoryResult(alias, error=f"{type(exc).__name__}: {exc}"), None

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

        def work():
            with TransferSessionPool(jump.config, self._jump_password) as transfer_pool:
                return work_on_pool(transfer_pool)

        def success(outcome: WorkerDirectoryOutcome) -> None:
            if outcome.cancelled:
                ui(self.status.setText, tr('新建目录已取消；没有创建任何目录。'))
                return
            if (outcome.listings and self.worker_browser.rendered_path == parent
                    and self._worker_browse_job is None):
                self.worker_browser.set_listings(outcome.listings)
                self._schedule_auto_hash()
            for result in outcome.results:
                row = self.batch_results.rowCount()
                self.batch_results.insertRow(row)
                state = "失败" if result.error else "已创建" if result.created else "已跳过"
                detail = result.error or result.path
                for column, value in enumerate((result.alias, "新建目录", state, detail)):
                    self.batch_results.setItem(row, column, ui(QTableWidgetItem, value))
            created = sum(result.created for result in outcome.results)
            skipped = sum(not result.created and not result.error for result in outcome.results)
            failed = sum(bool(result.error) for result in outcome.results)
            self._notify_done(
                tr('新建目录完成：{0} 个节点已创建，{1} 个跳过，{2} 个失败；详情见下方结果表。', f'{created}', f'{skipped}', f'{failed}')
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
            work, success, tr('正在 {0} 个间接节点上新建目录…', f'{len(aliases)}'),
            log_action=f"{'并行' if len(aliases) > 1 else ''}新建目录（{compact_node_aliases(aliases)}）：{target}",
            log_result=log_result,
            log_context={"destination": target, "nodes": aliases},
            log_context_result=lambda outcome: {"results": [
                {"node": result.alias, "path": result.path,
                 "created": result.created, "error": result.error}
                for result in outcome.results
            ]},
            file_scopes=frozenset(("workers",)),
        )

    def _batch_operation(self, action: BatchAction | None = None) -> None:
        jump = self._session
        if jump is None or self._parents or self._job is not None:
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
        scopes = frozenset({
            "local_to_workers": ("local", "workers"),
            "jump_to_workers": ("remote", "workers"),
            "worker_to_jump": ("workers", "remote"),
            "worker_to_local": ("workers", "local"),
            "worker_to_worker": ("workers",),
            "delete_workers": ("workers",),
            "local_to_jump": ("local", "remote"),
            "jump_to_local": ("remote", "local"),
        }[mode])
        if self._file_busy(*scopes):
            return
        uses_workers = mode.endswith("to_workers") or mode.startswith("worker_") or mode == "delete_workers"
        if uses_workers and (not aliases or any(alias not in self._workers for alias in aliases)):
            QMessageBox.warning(self, tr('批量操作'), tr('请先勾选并连接全部要操作的间接节点。'))
            return
        if mode in {"worker_to_jump", "worker_to_local", "worker_to_worker"} and len(aliases) != 1:
            QMessageBox.warning(self, tr('批量操作'), tr('从间接节点移动或复制时，请只勾选一个来源节点。'))
            return
        if mode == "worker_to_worker" and (not target_alias or target_alias not in self._workers or target_alias == aliases[0]):
            QMessageBox.warning(self, tr('批量操作'), tr('请选择另一个已连接的目标间接节点。'))
            return
        if moving and mode.endswith("to_workers") and len(aliases) > 1:
            QMessageBox.warning(self, tr('批量操作'), tr('向多个间接节点只能复制；请取消“移动”选项。'))
            return
        if not sources:
            QMessageBox.warning(self, tr('批量操作'), tr('请先在对应的文件列表中选中文件或文件夹。'))
            return
        if mode != "delete_workers" and not destination:
            QMessageBox.warning(self, tr('批量操作'), tr('请填写目标目录。'))
            return
        if mode not in {"worker_to_local", "jump_to_local", "delete_workers"} and not destination.startswith("/"):
            QMessageBox.warning(self, tr('批量操作'), tr('远程目标目录必须是绝对路径。'))
            return
        if mode in {"worker_to_jump", "worker_to_local", "worker_to_worker", "delete_workers"} and not worker_path.startswith("/"):
            QMessageBox.warning(self, tr('批量操作'), tr('间接节点当前路径必须是绝对路径。'))
            return
        if mode in {"worker_to_local", "jump_to_local"} and not Path(destination).is_dir():
            QMessageBox.warning(self, tr('批量操作'), tr('本地目标目录不存在。'))
            return
        if mode == "delete_workers":
            choice = QMessageBox.question(
                self, tr('确认批量删除'),
                tr('在 {0} 个间接节点上永久删除所选 {1} 个名称及其内容？\n此操作不能撤销；缺失项目将作为该节点的失败结果报告。', f'{len(aliases)}', f'{len(sources)}'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        elif moving:
            choice = QMessageBox.question(
                self, tr('确认批量移动'),
                tr('目标完整复制成功后删除来源？\n任何跳过或取消都不会删除对应来源；已完成的节点不会自动回滚。'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        nodes = ({alias: self._workers[alias] for alias in aliases} if uses_workers
                 else {"直连节点": jump})
        if uses_workers:
            self._cancel_auto_hash()
        resolve_conflict = self._new_conflict_resolver()
        self.batch_results.setRowCount(0)
        self._batch_mode_label = next(label for label, key in BatchDialog.MODES if key == mode)
        self._missing_destination_choice = None

        def prepare_destination(alias, session):
            def confirm(missing):
                with self._conflict_lock:
                    prompt = MissingDestinationPrompt(alias, destination, missing)
                    self.missingDestinationRequested.emit(prompt)
                    prompt.done.wait()
                    return prompt.create
            return ensure_remote_destination(session, destination, confirm)

        def work_one(
            alias: str, session: RemoteSession, transfer_pool: TransferSessionPool | None = None,
        ) -> BatchResult:
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
            if mode in {"local_to_workers", "jump_to_workers"}:
                if not prepare_destination(alias, session):
                    return BatchResult(alias, copied=CopyResult(0, 0, 0, skipped=len(sources)))
            if mode == "local_to_workers":
                result = copy_local_to_remote(session, sources, destination, progress, resolve_conflict)
            elif mode == "local_to_jump":
                result = copy_local_to_remote(session, sources, destination, progress, resolve_conflict)
            elif mode == "jump_to_local":
                result = copy_remote_to_local(session, sources, destination, progress, resolve_conflict)
            elif mode == "worker_to_local":
                result = copy_remote_to_local(session, worker_sources, destination, progress, resolve_conflict)
            elif mode == "worker_to_worker":
                if transfer_pool is not None:
                    with transfer_pool.worker(target_alias) as target_session:
                        result = copy_remote_between_sessions(
                            session, target_session, worker_sources, destination,
                            progress, resolve_conflict,
                        )
                else:
                    target_session = self._workers[target_alias].open_sftp_peer()
                    try:
                        result = copy_remote_between_sessions(
                            session, target_session, worker_sources, destination,
                            progress, resolve_conflict,
                        )
                    finally:
                        target_session.close()
            else:
                # Keep source and destination SFTP streams separate while copying.
                if transfer_pool is not None:
                    with transfer_pool.jump() as extra_jump:
                        if mode == "jump_to_workers":
                            result = copy_remote_between_sessions(
                                extra_jump, session, sources, destination,
                                progress, resolve_conflict,
                            )
                        else:
                            result = copy_remote_between_sessions(
                                session, extra_jump, worker_sources, destination,
                                progress, resolve_conflict,
                            )
                else:
                    extra_jump = RemoteSession.connect(jump.config, self._jump_password)
                    try:
                        if mode == "jump_to_workers":
                            result = copy_remote_between_sessions(
                                extra_jump, session, sources, destination,
                                progress, resolve_conflict,
                            )
                        else:
                            result = copy_remote_between_sessions(
                                session, extra_jump, worker_sources, destination,
                                progress, resolve_conflict,
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
        jump_version = self._listing_version
        def work():
            if moving:
                results = run_parallel(nodes, work_one, self.batchResultReady.emit, max_workers)
                return build_batch_result(results, nodes)
            with TransferSessionPool(jump.config, self._jump_password) as transfer_pool:
                refreshed = {}

                def copy_one(alias: str, _browse_session: RemoteSession) -> BatchResult:
                    channel = (transfer_pool.jump() if alias == "直连节点"
                               else transfer_pool.worker(alias))
                    with channel as peer:
                        try:
                            return work_one(alias, peer, transfer_pool)
                        finally:
                            if uses_workers and worker_path.startswith("/"):
                                try:
                                    refreshed[alias] = peer.list_directory(worker_path)
                                except Exception:
                                    pass

                results = run_parallel(nodes, copy_one, self.batchResultReady.emit, max_workers)
                return build_batch_result(results, nodes, refreshed, transfer_pool)

        def build_batch_result(results, active_nodes, refreshed=None, transfer_pool=None):
            jump_listing = None
            worker_listings = dict(refreshed or {})
            if mode in {"jump_to_workers", "worker_to_jump", "jump_to_local", "local_to_jump"}:
                try:
                    if transfer_pool is None:
                        jump_listing = jump.list_directory(browsed_jump_path)
                    else:
                        with transfer_pool.jump() as jump_peer:
                            jump_listing = jump_peer.list_directory(browsed_jump_path)
                except Exception:
                    pass
            if transfer_pool is None and uses_workers and worker_path.startswith("/"):
                for alias, session in active_nodes.items():
                    try:
                        worker_listings[alias] = session.list_directory(worker_path)
                    except Exception:
                        pass
            if mode == "worker_to_worker" and target_alias not in worker_listings:
                try:
                    if transfer_pool is None:
                        worker_listings[target_alias] = self._workers[target_alias].list_directory(worker_path)
                    else:
                        with transfer_pool.worker(target_alias) as target_peer:
                            worker_listings[target_alias] = target_peer.list_directory(worker_path)
                except Exception:
                    pass
            return results, jump_listing, worker_listings

        def success(value):
            results, jump_listing, worker_listings = value
            if jump_listing is not None and (self._session is jump and self._path == browsed_jump_path
                    and self._listing_version == jump_version and self._remote_browse_job is None):
                self._show_listing(jump_listing)
            if self.worker_browser.rendered_path == worker_path and self._worker_browse_job is None:
                for alias, listing in worker_listings.items():
                    self.worker_browser.set_listing(alias, listing)
            if (worker_listings and self.worker_browser.rendered_path == worker_path
                    and self._worker_browse_job is None):
                self._schedule_auto_hash()
            self.local.refresh()
            complete = sum(result.complete for result in results)
            self._notify_done(tr('批量操作完成：{0}/{1} 个节点成功；详情见下方结果表。', f'{complete}', f'{len(results)}'))

        targets = compact_node_aliases(list(nodes))
        action_name = "删除" if mode == "delete_workers" else "移动" if moving else "复制"
        source_text = self._log_paths(sources)
        target_text = "" if mode == "delete_workers" else f" → {destination}"
        self._run(
            work, success, tr('正在对 {0} 个间接节点执行批量操作…', f'{len(nodes)}'),
            log_action=f"并行{action_name}（{targets}）：{source_text}{target_text}",
            log_result=self._batch_log_result,
            log_context={"sources": sources, "destination": destination, "nodes": list(nodes)},
            log_context_result=self._batch_log_context,
            background_copy=not moving and mode != "delete_workers",
            file_scopes=scopes if not moving else None,
        )

    @Slot(object)
    def _show_batch_result(self, result: BatchResult) -> None:
        row = self.batch_results.rowCount()
        self.batch_results.insertRow(row)
        state = tr('失败') if result.error else tr('取消') if result.copied and result.copied.cancelled else (
            tr('已跳过') if result.copied and result.copied.skipped else tr('完成')
        )
        detail = result.error or (
            tr('{0} 文件，{1} 目录，跳过 {2}，删除来源 {3}', f'{result.copied.files}', f'{result.copied.directories}', f'{result.copied.skipped}', f'{result.deleted}')
            if result.copied else tr('已删除 {0} 项', f'{result.deleted}')
        )
        for column, value in enumerate((result.alias, tr(self._batch_mode_label), state, detail)):
            self.batch_results.setItem(row, column, ui(QTableWidgetItem, value))

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
            ui(self.status.setText, tr('日志文件写入失败：{0}', f'{exc}'))
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
        ui(self.host.setText, config.host)
        self.port.setValue(config.port)
        ui(self.user.setText, config.username)
        ui(self.key_file.setText, config.key_file or "")
        self.password.clear()

    def _run(
        self, work: Callable[[], object], on_success: Callable[[object], None],
        description: str, on_finished: Callable[[], None] | None = None,
        *, log_action: str | None = None,
        log_result: Callable[[object], tuple[str, str]] | None = None,
        log_category: str = "file", log_context: dict | None = None,
        log_context_result: Callable[[object], dict] | None = None,
        background_copy: bool = False,
        background_slot: str | None = None,
        file_scopes: frozenset[str] | None = None,
    ) -> None:
        if log_action and log_category == "file":
            self._hash_cache.clear()
        if file_scopes is not None or background_copy or background_slot:
            attribute = ("_file_task_" + "_".join(sorted(file_scopes))
                         if file_scopes is not None else background_slot or "_copy_job")
            self._run_background_task(
                work, on_success, description, log_action=log_action,
                log_result=log_result, log_category=log_category,
                log_context=log_context, log_context_result=log_context_result,
                on_finished=on_finished,
                job_attribute=attribute, file_scopes=file_scopes, is_copy=background_copy,
            )
            return
        if self._job is not None:
            return
        ui(self.status.setText, tr(description))
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

    def _run_background_task(
        self, work: Callable[[], object], on_success: Callable[[object], None],
        description: str, *, log_action: str | None,
        log_result: Callable[[object], tuple[str, str]] | None,
        log_category: str, log_context: dict | None,
        log_context_result: Callable[[object], dict] | None,
        on_finished: Callable[[], None] | None,
        job_attribute: str = "_copy_job",
        file_scopes: frozenset[str] | None = None,
        is_copy: bool = False,
    ) -> None:
        scanning = job_attribute == "_tmux_scan_job"
        if file_scopes is not None:
            if self._file_busy(*file_scopes):
                return
        elif self._job is not None and not scanning:
            return
        if getattr(self, job_attribute, None) is not None:
            return
        if not scanning:
            ui(self.status.setText, tr(description))
        event_id = uuid.uuid4().hex
        started = time.monotonic()
        index = self._append_operation_log(log_action, "进行中") if log_action else None
        if log_action:
            self._write_activity(event_id=event_id, category=log_category,
                                 action=log_action, state="进行中", context=log_context)
        job = JobThread(work, self)
        setattr(self, job_attribute, job)
        if file_scopes is not None:
            self._file_tasks[job] = file_scopes
            if is_copy:
                self._file_copy_tasks.add(job)
                self._copy_job = next(iter(self._file_copy_tasks))

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
            if log_category == "file":
                self._hash_cache.clear()
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
                "后台任务失败：%s", self._redact_log_value(message),
            )
            finish_log("失败", message)
            if scanning:
                if not self._tmux_cancel.is_set():
                    ui(self.tasks_workspace.summary.setText, tr('持久终端扫描失败，可稍后重试；其他操作不受影响。'))
                    ui(self.tasks_workspace.summary.setToolTip, self._redact_log_value(message))
            elif file_scopes is not None or job_attribute in {"_remote_browse_job", "_worker_browse_job"}:
                self._show_background_error(message)
            else:
                self._show_error(message)

        def finished() -> None:
            if getattr(self, job_attribute) is job:
                setattr(self, job_attribute, None)
            self._file_tasks.pop(job, None)
            self._file_copy_tasks.discard(job)
            if file_scopes is not None:
                self._copy_job = next(iter(self._file_copy_tasks), None)
            self._update_controls()
            self._finish_background_close()
            if job_attribute == "_worker_connect_job":
                self._finish_worker_browse()

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
        if self._active_log_category == "file" and self._active_log_action:
            self._hash_cache.clear()
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
            path, parent_fallback = self._pending_worker_refresh
            self._pending_worker_refresh = None
            QTimer.singleShot(0, lambda: self._browse_workers(path, parent_fallback))

    def _show_error(self, message: str) -> None:
        ui(self.status.setText, tr('操作失败'))
        QMessageBox.warning(self, "NodeBridge", tr(message))

    def _show_background_error(self, message: str) -> None:
        ui(self.status.setText, tr('文件任务失败；详情见操作日志。'))
        box = QMessageBox(QMessageBox.Icon.Warning, "NodeBridge", message,
                          QMessageBox.StandardButton.Ok, self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.show()

    def _notify_done(self, message: str) -> None:
        ui(self.status.setText, message)
        ui(self.completion_toast.setText, message)
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

    def _new_conflict_resolver(self):
        choices = {}
        lock = threading.Lock()

        def resolve(info):
            with lock:
                prompt = ConflictPrompt(info, choices=choices)
                self.conflictRequested.emit(prompt)
                prompt.done.wait()
                return prompt.action

        return resolve

    @Slot(object)
    def _show_missing_destination(self, prompt: MissingDestinationPrompt) -> None:
        from PySide6.QtWidgets import QCheckBox
        try:
            if self._missing_destination_choice is not None:
                prompt.create = self._missing_destination_choice
                return
            dialog = QMessageBox(self)
            ui(dialog.setWindowTitle, tr('目标目录不存在'))
            dialog.setIcon(QMessageBox.Icon.Question)
            ui(dialog.setText, tr('节点 {0} 的目标目录不存在：\n{1}', f'{prompt.alias}', f'{prompt.path}'))
            ui(dialog.setInformativeText, tr('是否创建缺少的各级目录后复制？\n跳过则不向此节点复制，其他节点继续。'))
            dialog.setDetailedText("需要创建：\n" + "\n".join(prompt.missing))
            create = ui(dialog.addButton, tr('新建路径并复制'), QMessageBox.ButtonRole.AcceptRole)
            skip = ui(dialog.addButton, tr('跳过此节点'), QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(skip)
            checkbox = ui(QCheckBox, tr('总是使用此操作（仅本次复制任务）'), dialog)
            dialog.setCheckBox(checkbox)
            dialog.exec()
            prompt.create = dialog.clickedButton() is create
            if checkbox.isChecked() and dialog.clickedButton() in (create, skip):
                self._missing_destination_choice = prompt.create
        finally:
            prompt.done.set()

    @Slot(object)
    def _show_conflict_prompt(self, prompt: ConflictPrompt) -> None:
        try:
            saved = (prompt.choices.get("action") if prompt.choices is not None
                     else self._apply_conflict_choice)
            if saved is not None:
                prompt.action = saved
                return
            choice, apply_all = ConflictDialog(prompt.info, self).result_choice()
            prompt.action = choice
            if apply_all and choice is not ConflictAction.CANCEL:
                if prompt.choices is not None:
                    prompt.choices["action"] = choice
                else:
                    self._apply_conflict_choice = choice
        finally:
            prompt.done.set()

    @Slot(object)
    def _show_worker_directory_conflict(self, prompt: WorkerDirectoryPrompt) -> None:
        try:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            ui(dialog.setWindowTitle, tr('间接节点目录已存在'))
            ui(dialog.setText,
                tr('{0} 在 {1} 个节点上已有同名项目。\n冲突节点：{2}', f'{prompt.path}', f'{len(prompt.aliases)}', f'{compact_node_aliases(prompt.aliases)}')
            )
            ui(dialog.setInformativeText,
                tr('其他目标节点仍会创建原名目录。对于冲突节点，请选择跳过，或分别创建名称末尾带序号的新目录（例如 名称 (2)）。')
            )
            skip_button = ui(dialog.addButton, tr('跳过已有项目'), QMessageBox.ButtonRole.AcceptRole)
            number_button = ui(dialog.addButton, tr('新建带序号的目录'), QMessageBox.ButtonRole.ActionRole)
            cancel_button = ui(dialog.addButton, QMessageBox.StandardButton.Cancel)
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
            QMessageBox.warning(self, "NodeBridge", tr('请输入主机和用户名。'))
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
        if self._job is not None or self._remote_files_active():
            return
        if jump is not None and self._current_listing is None:
            QMessageBox.warning(self, "NodeBridge", tr('当前站点目录尚未加载完成。'))
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
            ui(self.jump_title.setText, f"{tr('直连节点') if jump is None else tr('当前站点')} · {config.host}")
            self._show_connection_fields(config)
            self.tree.clear()
            self._show_listing(listing)
            route = f"（经 {jump.config.host} 中转）" if jump is not None else ""
            ui(self.status.setText, tr('已连接到 {0}{1}，当前目录：{2}', f'{config.host}', f'{route}', f'{listing.path}'))
            if jump is None:
                self._jump_password = password
                ui(self.worker_browser.path_edit.setText, listing.path)
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
        if self._tmux_job is not None:
            ui(self.status.setText, tr('请等待远程会话结束操作完成后再断开站点。'))
            return
        if self._worker_connect_job is not None:
            ui(self.status.setText, tr('请等待间接节点连接结束后再断开直连节点。'))
            return
        if (self._remote_files_active() or self._remote_browse_job is not None
                or self._worker_browse_job is not None):
            ui(self.status.setText, tr('请等待远程文件任务完成后再断开连接。'))
            return
        session = self._session
        if session is None:
            return
        self._tmux_cancel.set()
        self._tmux_restore_queue.clear()
        self._cancel_auto_hash()
        self._probe_generation += 1
        self._probe_cancel.set()
        self._pending_worker_probe = False
        if self._resource_monitor is not None:
            self._resource_monitor.cancel.set()

        workers = list(self._workers.values())
        helpers = list(self._jump_helpers)
        terminal_nodes = [f"primary:{id(session)}"]
        if not self._parents:
            terminal_nodes.extend(record.node_id for sid, record in self.terminal_workspace._sessions.items()
                                  if sid in self._persistent_sessions)
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
                ui(self.jump_title.setText, tr('当前站点 · {0}', f'{parent.config.host}'))
                self._show_connection_fields(parent.config)
                self.tree.clear()
                self._show_listing(listing)
                ui(self.status.setText, tr('已返回 {0}，当前目录：{1}', f'{parent.config.host}', f'{listing.path}'))
            else:
                self._session = None
                self._jump_password = None
                ui(self.jump_title.setText, tr('直连节点 · 尚未连接'))
                self._current_listing = None
                self._path = ""
                self.path_edit.clear()
                self.tree.clear()
                self.table.setRowCount(0)
                ui(self.status.setText, (tr('已断开') if not errors else tr('已断开；关闭时有 {0} 个错误。', f'{len(errors)}')))
            self._sync_terminal_nodes()

        self._run(
            work, success, tr('正在断开…'),
            log_action=f"SSH/SFTP 断开（{session.config.host}）",
            log_category="connection", log_context={"node": session.config.host},
            log_result=lambda errors: (
                "成功" if not errors else "部分完成",
                "；".join(errors),
            ),
        )

    def _make_directory_item(self, path: str) -> QTreeWidgetItem:
        item = ui(QTreeWidgetItem, [posixpath.basename(path.rstrip("/")) or "/"])
        item.setIcon(0, self._directory_icon)
        item.setData(0, PATH_ROLE, path)
        item.setData(0, LOADED_ROLE, False)
        item.addChild(ui(QTreeWidgetItem, ["…"]))
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
        ui(self.path_edit.setText, listing.path)
        self.table.current_path = listing.path
        self.table.session_token = str(id(self._session)) if self._session is not None else ""
        self.table.setRowCount(len(listing.entries))
        for row, entry in enumerate(listing.entries):
            name = ui(QTableWidgetItem, entry.name)
            name.setIcon(self._directory_icon if entry.is_dir else self._file_icons.for_filename(entry.name))
            name.setData(PATH_ROLE, entry.path)
            name.setData(LOADED_ROLE, entry.is_dir)
            size = "" if entry.is_dir or entry.size is None else f"{entry.size:,}"
            size_item = ui(QTableWidgetItem, size)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            modified = "" if entry.modified is None else datetime.fromtimestamp(entry.modified).strftime("%Y-%m-%d %H:%M")
            suffix = posixpath.splitext(entry.name)[1].lstrip(".").upper()
            file_type = tr('文件夹') if entry.is_dir else tr('符号链接') if entry.is_symlink else tr('{0} 文件', f'{suffix}') if suffix else tr('文件')
            for column, value in enumerate((
                name, size_item, ui(QTableWidgetItem, file_type), ui(QTableWidgetItem, modified)
            )):
                self.table.setItem(row, column, value)
        item = self._ensure_tree_item(listing.path)
        self._fill_tree(item, listing)
        self.tree.setCurrentItem(item)
        ui(self.status.setText, tr('{0} — {1} 项', f'{listing.path}', f'{len(listing.entries)}'))

    def _browse(self, path: str, tree_only: QTreeWidgetItem | None = None) -> None:
        session = self._session
        if session is None:
            return

        def success(result):
            if tree_only is None:
                self._show_listing(result)
            else:
                self._fill_tree(tree_only, result)
                ui(self.status.setText, tr('已展开 {0}', f'{result.path}'))

        self._run(lambda: session.list_directory(path), success, tr('正在读取 {0}…', f'{path}'),
                  background_slot="_remote_browse_job")

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
        menu = ui(QMenu, self.table)
        if paths:
            ui(menu.addAction, tr('复制\tCtrl+C'), self._copy_remote_selection)
            ui(menu.addAction, tr('下载到…'), self._download_selected_to)
            ui(menu.addAction, tr('重命名\tF2'), self._rename_remote_selection).setEnabled(len(paths) == 1)
            ui(menu.addAction, tr('删除\tDelete'), self._delete_remote_selection)
            menu.addSeparator()
        ui(menu.addAction, tr('粘贴\tCtrl+V'), lambda: self._paste_remote(destination))
        ui(menu.addAction, tr('创建目录'), lambda: self._create_remote_directory(False))
        ui(menu.addAction, tr('创建目录并进入'), lambda: self._create_remote_directory(True))
        menu.addSeparator()
        ui(menu.addAction, tr('刷新'), self._refresh)
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
        menu = ui(QMenu, self.local_table)
        if paths:
            ui(menu.addAction, tr('复制\tCtrl+C'), self._copy_local_selection)
            ui(menu.addAction, tr('重命名\tF2'), self._rename_local_selection).setEnabled(len(paths) == 1)
            ui(menu.addAction, tr('删除\tDelete'), self._delete_local_selection)
            menu.addSeparator()
        ui(menu.addAction, tr('粘贴\tCtrl+V'), lambda: self._paste_local(destination))
        ui(menu.addAction, tr('创建目录'), lambda: self._create_local_directory(False))
        ui(menu.addAction, tr('创建目录并进入'), lambda: self._create_local_directory(True))
        menu.addSeparator()
        ui(menu.addAction, tr('刷新'), self.local.refresh)
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
        menu = ui(QMenu, table)
        if self.worker_browser.selected_names():
            ui(menu.addAction, tr('删除\tDelete'), self._delete_worker_selection)
            menu.addSeparator()
        ui(menu.addAction, tr('新建目录'), self._create_worker_directory).setEnabled(bool(self._workers))
        ui(menu.addAction, tr('刷新'), lambda: self._browse_workers(self.worker_browser.rendered_path))
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
        ui(self.status.setText, tr('已复制 {0} 个远程项目；可在 NodeBridge 中粘贴。', f'{len(paths)}'))

    def _copy_local_selection(self) -> None:
        paths = self._local_selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        QApplication.clipboard().setMimeData(mime)
        ui(self.status.setText, tr('已复制 {0} 个本地项目。', f'{len(paths)}'))

    def _paste_remote(self, destination: str | None = None) -> None:
        if self._session is None or self._job is not None:
            return
        target = destination or self._path
        mime = QApplication.clipboard().mimeData()
        payload = remote_payload(mime)
        if payload is not None:
            if payload[0] != str(id(self._session)):
                QMessageBox.warning(self, "NodeBridge", tr('复制的远程文件不属于当前站点。'))
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
            self, tr('选择本地目标目录'), self.local.current_path
        )
        if destination:
            self._copy_remote_to_local(str(id(self._session)), paths, destination, [])

    def _delete_remote_selection(self) -> None:
        paths = self._remote_selected_paths()
        session = self._session
        if not paths or session is None or self._file_busy("remote"):
            return
        choice = QMessageBox.question(
            self, tr('确认删除'), tr('永久删除选中的 {0} 个远程项目及其内容？此操作不能撤销。', f'{len(paths)}'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        browsed_path = self._path
        version = self._listing_version

        def work():
            with closing(session.open_sftp_peer()) as peer:
                count = delete_remote(peer, paths)
                return count, peer.list_directory(browsed_path)

        def success(result):
            if (self._session is session and self._path == browsed_path
                    and self._listing_version == version and self._remote_browse_job is None):
                self._show_listing(result[1])
            self._notify_done(tr('已删除 {0} 个远程项目。', f'{result[0]}'))

        self._run(
            work, success, tr('正在删除远程项目…'),
            log_action=f"SFTP 删除（{self._session_label(session)}）：{self._log_paths(paths)}",
            log_context={"sources": paths, "node": self._session_label(session)},
            file_scopes=frozenset(("remote",)),
        )

    def _delete_local_selection(self) -> None:
        paths = self._local_selected_paths()
        if not paths or self._file_busy("local"):
            return
        choice = QMessageBox.question(
            self, tr('确认删除'), tr('将选中的 {0} 个本地项目移入回收站？', f'{len(paths)}'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return

        def success(count):
            self.local.refresh()
            self._notify_done(tr('已将 {0} 个本地项目移入回收站。', f'{count}'))

        self._run(
            lambda: trash_local(paths), success, tr('正在移入回收站…'),
            log_action=f"移入本地回收站：{self._log_paths(paths)}",
            log_context={"sources": paths},
            file_scopes=frozenset(("local",)),
        )

    def _rename_remote_selection(self) -> None:
        selected = self.table.selectionModel().selectedRows(0)
        if (len(selected) != 1 or self._session is None or self._file_busy("remote")):
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.table.edit(selected[0])

    def _commit_remote_rename(self, index: QModelIndex, name: str) -> None:
        session = self._session
        source = self._rename_source(self.table, index)
        if (session is None or self._file_busy("remote")
                or not source or name == posixpath.basename(source)):
            return

        browsed_path = self._path
        version = self._listing_version

        def work():
            with closing(session.open_sftp_peer()) as peer:
                target = rename_remote(peer, source, name)
                return target, peer.list_directory(browsed_path)

        def success(result):
            if (self._session is session and self._path == browsed_path
                    and self._listing_version == version and self._remote_browse_job is None):
                self._show_listing(result[1])
            self._notify_done(tr('已重命名为 {0}。', f'{posixpath.basename(result[0])}'))

        self._run(
            work, success, tr('正在重命名远程项目…'),
            log_action=f"SFTP 重命名（{self._session_label(session)}）：{source} → {name}",
            log_context={"sources": [source], "destination": posixpath.join(posixpath.dirname(source), name), "node": self._session_label(session)},
            file_scopes=frozenset(("remote",)),
        )

    def _rename_local_selection(self) -> None:
        selected = self.local_table.selectionModel().selectedRows(0)
        if len(selected) != 1 or self._file_busy("local"):
            return
        self._rename_timer.stop()
        self._pending_rename = None
        self.local_table.edit(selected[0])

    def _commit_local_rename(self, index: QModelIndex, name: str) -> None:
        source = self._rename_source(self.local_table, index)
        if self._file_busy("local") or not source or name == Path(source).name:
            return

        def success(target):
            self.local.refresh()
            self._notify_done(tr('已重命名为 {0}。', f'{target.name}'))

        self._run(
            lambda: rename_local(source, name), success, tr('正在重命名本地项目…'),
            log_action=f"本地重命名：{source} → {name}",
            log_context={"sources": [source], "destination": str(Path(source).with_name(name))},
            file_scopes=frozenset(("local",)),
        )

    def _create_remote_directory(self, enter: bool) -> None:
        session = self._session
        if session is None or self._file_busy("remote"):
            return
        name, accepted = QInputDialog.getText(self, tr('创建目录'), tr('目录名称：'))
        if not accepted:
            return
        parent = self._path
        version = self._listing_version

        def work():
            with closing(session.open_sftp_peer()) as peer:
                target = create_remote_directory(peer, parent, name)
                return target, peer.list_directory(target if enter else parent)

        def success(result):
            if (self._session is session and self._path == parent
                    and self._listing_version == version and self._remote_browse_job is None):
                self._show_listing(result[1])
            self._notify_done(tr('已创建目录 {0}。', f'{result[0]}'))

        self._run(
            work, success, tr('正在创建远程目录…'),
            log_action=f"SFTP 创建目录（{self._session_label(session)}）：{posixpath.join(parent, name)}",
            log_context={"destination": posixpath.join(parent, name), "node": self._session_label(session)},
            file_scopes=frozenset(("remote",)),
        )

    def _create_local_directory(self, enter: bool) -> None:
        if self._file_busy("local"):
            return
        name, accepted = QInputDialog.getText(self, tr('创建目录'), tr('目录名称：'))
        if not accepted:
            return
        parent = self.local.current_path

        def success(target):
            if enter and self.local.current_path == parent:
                self.local.navigate(str(target))
            else:
                self.local.refresh()
            self._notify_done(tr('已创建目录 {0}。', f'{target}'))

        self._run(
            lambda: create_local_directory(parent, name), success, tr('正在创建本地目录…'),
            log_action=f"本地创建目录：{Path(parent) / name}",
            log_context={"destination": str(Path(parent) / name)},
            file_scopes=frozenset(("local",)),
        )

    def _copy_local_to_local(self, paths: list[str], destination: str) -> None:
        if self._file_busy("local"):
            return
        resolve_conflict = self._new_conflict_resolver()

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在复制：{path}")
            return copy_local_to_local(
                paths, destination,
                progress,
                resolve_conflict,
            )

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("复制", result))

        self._run(
            work, success, tr('正在复制本地文件…'),
            log_action=f"本地复制：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination},
            log_context_result=self._copy_log_context,
            background_copy=True,
            file_scopes=frozenset(("local",)),
        )

    def _copy_remote_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._file_busy("remote"):
            return
        resolve_conflict = self._new_conflict_resolver()
        browsed_path = self._path
        version = self._listing_version

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在复制：{path}")
            peer = session.open_sftp_peer()
            try:
                result = copy_remote_to_remote(
                    peer, paths, destination, progress, resolve_conflict,
                )
                return result, peer.list_directory(browsed_path)
            finally:
                peer.close()

        def success(value):
            result, listing = value
            if (self._session is session and self._path == browsed_path
                    and self._listing_version == version and self._remote_browse_job is None):
                self._show_listing(listing)
            self._notify_done(self._copy_status("复制", result))

        self._run(
            work, success, tr('正在复制远程文件…'),
            log_action=f"SFTP 复制（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
            file_scopes=frozenset(("remote",)),
        )

    def _copy_local_to_remote(self, paths: list[str], destination: str) -> None:
        session = self._session
        if session is None or self._file_busy("local", "remote"):
            return
        resolve_conflict = self._new_conflict_resolver()
        browsed_path = self._path
        version = self._listing_version

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在上传：{path}")
            peer = session.open_sftp_peer()
            try:
                result = copy_local_to_remote(
                    peer, paths, destination, progress, resolve_conflict,
                )
                return result, peer.list_directory(browsed_path)
            finally:
                peer.close()

        def success(value):
            result, listing = value
            if (self._session is session and self._path == browsed_path
                    and self._listing_version == version and self._remote_browse_job is None):
                self._show_listing(listing)
            self._notify_done(self._copy_status("上传", result))

        self._run(
            work, success, tr('正在检查并上传文件…'),
            log_action=f"SFTP 上传（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
            file_scopes=frozenset(("local", "remote")),
        )

    def _copy_remote_to_local(
        self, token: str, paths: list[str], destination: str, cached_paths: list[str]
    ) -> None:
        if token == self.worker_browser.drag_token:
            self._collect_worker_files(token, paths, destination, to_jump=False)
            return
        session = self._session
        if (session is None or self._file_busy("local", "remote")
                or token != str(id(session))):
            return
        resolve_conflict = self._new_conflict_resolver()

        def work():
            progress = lambda path: self.transferProgress.emit(f"正在下载：{path}")
            if len(cached_paths) == len(paths) and all(Path(path).exists() for path in cached_paths):
                labels = {Path(cached).name: remote for cached, remote in zip(cached_paths, paths)}
                return copy_local_to_local(
                    cached_paths, destination, progress, resolve_conflict, labels
                )
            peer = session.open_sftp_peer()
            try:
                return copy_remote_to_local(peer, paths, destination, progress, resolve_conflict)
            finally:
                peer.close()

        def success(result):
            self.local.refresh()
            self._notify_done(self._copy_status("下载", result))

        self._run(
            work, success, tr('正在检查并下载文件…'),
            log_action=f"SFTP 下载（{self._session_label(session)}）：{self._log_paths(paths)} → {destination}",
            log_result=self._copy_log_result,
            log_context={"sources": paths, "destination": destination, "node": self._session_label(session)},
            log_context_result=self._copy_log_context,
            background_copy=True,
            file_scopes=frozenset(("local", "remote")),
        )

    def _prepare_drag_export(self, paths: list[str]) -> list[str]:
        session = self._session
        if session is None or self._file_busy("remote"):
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
            ui(self.status.setText, tr('文件已准备好，可以拖到本地目录或资源管理器。'))

        self._run(
            work, success, tr('正在准备拖出的文件…'), loop.quit,
            log_action=f"SFTP 准备拖出缓存（{self._session_label(session)}）：{self._log_paths(paths)}",
            log_context={"sources": paths, "node": self._session_label(session), "note": "Windows 资源管理器最终复制结果不可见"},
            background_copy=True,
            file_scopes=frozenset(("remote",)),
        )
        loop.exec()
        if not exported:
            temporary.cleanup()
        return exported

    def closeEvent(self, event) -> None:
        if getattr(self, "_recovery", None) is not None and not self._recovery.allow_close():
            event.ignore()
            return
        self._tmux_restore_queue.clear()
        if self._tmux_scan_job is not None:
            event.ignore()
            self._tmux_cancel.set()
            self._tmux_scan_job.finished.connect(self.close)
            ui(self.status.setText, tr('正在取消后台持久终端扫描…'))
            return
        if self._tmux_job is not None:
            event.ignore()
            self._tmux_cancel.set()
            self._tmux_job.finished.connect(self.close)
            ui(self.status.setText, tr('正在等待持久终端操作完成，请稍后关闭。'))
            return
        if self._worker_connect_job is not None:
            event.ignore()
            ui(self.status.setText, tr('请等待间接节点连接结束后再关闭。'))
            return
        if (self._file_tasks or self._remote_browse_job is not None
                or self._worker_browse_job is not None):
            event.ignore()
            ui(self.status.setText, tr('请等待后台文件任务完成后再关闭。'))
            return
        if self._job is not None:
            event.ignore()
            ui(self.status.setText, tr('请等待当前操作完成后再关闭。'))
            return
        if self._session is not None:
            event.ignore()
            if getattr(self, "_recovery", None) is not None:
                self._recovery.begin_disconnect()
            self._disconnect()
            if self._job is not None:
                self._job.finished.connect(self.close)
            return
        if self._hash_job is not None or self._probe_job is not None or self._resource_monitor is not None:
            event.ignore()
            self._close_when_background_idle = True
            self._cancel_auto_hash()
            self._probe_cancel.set()
            if self._resource_monitor is not None:
                self._resource_monitor.cancel.set()
            ui(self.status.setText, tr('正在结束后台校验和节点探测…'))
            return
        for _created, temporary, _paths in self._drag_cache.values():
            temporary.cleanup()
        self._drag_cache.clear()
        self._terminal_manager.close_all()
        self._layout_settings.save()
        event.accept()

    def _finish_background_close(self) -> None:
        if (self._close_when_background_idle and self._job is None
                and self._worker_connect_job is None
                and self._tmux_scan_job is None and self._tmux_job is None
                and not self._file_tasks and self._remote_browse_job is None
                and self._worker_browse_job is None and self._hash_job is None and self._probe_job is None
                and self._resource_monitor is None
                and self._session is None):
            QTimer.singleShot(0, self.close)
