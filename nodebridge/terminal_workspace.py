"""Terminal workspace UI for SSH shell output and per-session command input."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QInputDialog, QMenu, QMessageBox, QPushButton, QSizePolicy, QSplitter, QTabBar, QTabWidget,
    QToolBar, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from nodebridge.node_check_tree import NodeCheckTree
from nodebridge.remote_terminal_widget import RemoteTerminalWidget


ITEM_ROLE = Qt.ItemDataRole.UserRole
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
PLAIN_PROMPT = re.compile(r"^(?:\([^()\r\n]+\)[ \t]+)*([^\s@:]+@[^\s@:]+:)([^\s$#]*)([$#])")


def _node_order(label: str) -> tuple[tuple[int, str | int], ...]:
    """Sort cft2 before cft10, independently of SSH reply timing."""
    return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold())
                 for part in re.split(r"(\d+)", label) if part)


@dataclass(frozen=True)
class TerminalRecord:
    session_id: str
    node_id: str
    title: str
    group_id: str


@dataclass
class TerminalGroup:
    group_id: str
    name: str
    node_ids: list[str]
    members: list[str]
    selected: set[str]
    ignore_conda_mismatch: bool = False


@dataclass
class CombinedResult:
    session_id: str
    node_id: str
    text: str = ""
    complete: bool = False


@dataclass
class CombinedRound:
    command: str
    results: list[CombinedResult]


class TerminalPane(QWidget):
    """One SSH shell, rendered only by its local xterm.js frontend."""

    sendRequested = Signal(bytes)
    resizeRequested = Signal(int, int)

    def __init__(self, node_label: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminalPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.output = RemoteTerminalWidget()
        self.output.inputRequested.connect(self.sendRequested)
        self.output.resizeRequested.connect(self.resizeRequested)
        self.output.bellRequested.connect(QApplication.beep)
        layout.addWidget(self.output)
        self._recent_output = ""

    def terminal_size(self) -> tuple[int, int]:
        return self.output.terminal_size

    def append_output(self, text: str) -> None:
        self._recent_output = (self._recent_output + text)[-8192:]
        self.output.write(text)

    def prompt_line(self) -> str:
        # This small text probe is only for deciding whether a broadcast cwd check
        # may be sent. Screen/cursor/ANSI state belongs solely to xterm.js.
        plain = ANSI_ESCAPE.sub("", self._recent_output)
        return re.split(r"\r\n|\n|\r", plain)[-1]

    def raw_prompt_line(self) -> str:
        return re.split(r"\r\n|\n|\r", self._recent_output)[-1]


class CombinedTerminalPane(QWidget):
    """Read-only xterm.js view of one group's broadcast replies in node order."""

    MAX_NODE_CHARS = 20000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.output = RemoteTerminalWidget(read_only=True)
        self.output.setObjectName("combinedTerminalOutput")
        layout.addWidget(self.output)
        self._rendered = ""
        self.render([])

    def render(self, rounds: list[tuple[list[str], bool]]) -> None:
        text = ""
        if not rounds:
            text = "广播命令的终端输出将在这里按节点顺序显示。\r\n"
        for results, complete in rounds:
            for reply in results:
                if not reply:
                    continue
                text += reply
                if not reply.endswith(("\r", "\n")):
                    text += "\r\n"
            if complete and any(results):
                text += "\r\n"
        if text.startswith(self._rendered):
            self.output.write(text[len(self._rendered):])
        else:
            self.output.reset_terminal()
            self.output.write(text)
        self._rendered = text


class BoundedTerminalTabBar(QTabBar):
    """Use the available tab row without letting all tab titles widen the window."""

    def sizeHint(self):
        hint = super().sizeHint()
        tabs = self.parentWidget()
        available = tabs.width() if tabs is not None else 0
        if isinstance(tabs, QTabWidget):
            menu = tabs.cornerWidget(Qt.Corner.TopRightCorner)
            if menu is not None:
                available -= menu.width()
        hint.setWidth(min(hint.width(), max(640, available)))
        return hint


class BroadcastCommandInput(QLineEdit):
    inputFocused = Signal()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.inputFocused.emit()


class TerminalWorkspace(QWidget):
    """Show shell sessions without owning their SSH channels."""

    connectRequested = Signal(str)
    disconnectRequested = Signal(str)
    connectManyRequested = Signal(list)
    disconnectManyRequested = Signal(list)
    disconnectGroupRequested = Signal(str)
    inputRequested = Signal(str, bytes)
    resizeRequested = Signal(str, int, int)
    broadcastRequested = Signal(str, str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: dict[str, str] = {}
        self._sessions: dict[str, TerminalRecord] = {}
        self._groups: dict[str, TerminalGroup] = {}
        self._next_group = 1
        self._closed_sessions: set[str] = set()
        self._views: dict[str, TerminalPane] = {}
        self._combined_views: dict[str, CombinedTerminalPane] = {}
        self._combined_rounds: dict[str, list[CombinedRound]] = {}
        self._combined_timer = QTimer(self)
        self._combined_timer.setSingleShot(True)
        self._combined_timer.setInterval(120)
        self._combined_timer.timeout.connect(self._refresh_combined_views)
        self._next_session = 1
        self._busy = False
        self._broadcast_busy = False
        self._command_target: tuple[str, str | None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        notice = QLabel("点击黑色终端区域直接输入；广播命令只发送给当前终端组内勾选的终端。")
        notice.setObjectName("terminalNotice")
        notice.setStyleSheet("QLabel#terminalNotice { background: #fff4d6; padding: 7px; }")
        layout.addWidget(notice)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setObjectName("terminalSidebar")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("节点与终端"))
        self.node_tree = NodeCheckTree(check_column=2)
        self.node_tree.setObjectName("terminalNodes")
        self.node_tree.setAlternatingRowColors(True)
        self.node_tree.setHeaderLabels(["节点", "状态", "批量选择"])
        self.node_tree.setColumnWidth(0, 125)
        self.node_tree.setColumnWidth(1, 75)
        self.node_tree.setColumnWidth(2, 80)
        self.node_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.node_tree.itemClicked.connect(self._item_clicked)
        self.node_tree.itemChanged.connect(self._item_changed)
        self.node_tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        left_layout.addWidget(self.node_tree, 1)
        left_layout.addWidget(QLabel("当前终端组（广播严格限于本组）"))
        group_row = QHBoxLayout()
        self.group_selector = QComboBox()
        self.group_selector.setObjectName("terminalGroupSelector")
        self.group_selector.currentIndexChanged.connect(self._group_changed)
        group_row.addWidget(self.group_selector, 1)
        self.rename_group_button = QPushButton("重命名")
        self.rename_group_button.setEnabled(False)
        self.rename_group_button.clicked.connect(self._rename_group)
        group_row.addWidget(self.rename_group_button)
        self.disconnect_group_button = QPushButton("断开当前组")
        self.disconnect_group_button.setEnabled(False)
        self.disconnect_group_button.clicked.connect(self._request_disconnect_group)
        group_row.addWidget(self.disconnect_group_button)
        left_layout.addLayout(group_row)
        self.group_tree = QTreeWidget()
        self.group_tree.setObjectName("terminalGroupMembers")
        self.group_tree.setHeaderLabels(["本组广播目标", "终端状态"])
        self.group_tree.setMinimumHeight(115)
        self.group_tree.itemChanged.connect(self._group_member_changed)
        left_layout.addWidget(self.group_tree, 0)
        batch_row = QHBoxLayout()
        self.connect_checked_button = QPushButton("连接勾选节点")
        self.disconnect_checked_button = QPushButton("断开勾选节点终端")
        self.connect_checked_button.setObjectName("terminalBatchAction")
        self.disconnect_checked_button.setObjectName("terminalBatchAction")
        self.connect_checked_button.clicked.connect(self._connect_checked)
        self.disconnect_checked_button.clicked.connect(self._disconnect_checked)
        batch_row.addWidget(self.connect_checked_button)
        batch_row.addWidget(self.disconnect_checked_button)
        left_layout.addLayout(batch_row)
        self.new_terminal_button = QPushButton("新建终端")
        self.new_terminal_button.setObjectName("terminalAction")
        self.new_terminal_button.clicked.connect(self._new_terminal)
        left_layout.addWidget(self.new_terminal_button)
        self.disconnect_terminal_button = QPushButton("断开终端")
        self.disconnect_terminal_button.setObjectName("terminalAction")
        self.disconnect_terminal_button.clicked.connect(self._disconnect_terminal)
        left_layout.addWidget(self.disconnect_terminal_button)
        splitter.addWidget(left)

        right = QWidget()
        right.setObjectName("terminalMain")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("终端标签  ·  关闭标签只隐藏视图"))
        self.terminal_tabs = QTabWidget()
        self.terminal_tabs.setTabBar(BoundedTerminalTabBar(self.terminal_tabs))
        self.terminal_tabs.setObjectName("terminalTabs")
        self.terminal_tabs.setTabsClosable(True)
        self.terminal_tabs.tabBar().setUsesScrollButtons(True)
        self.terminal_tabs.tabCloseRequested.connect(self._hide_tab)
        self.terminal_tabs.currentChanged.connect(self._tab_changed)
        self.tab_list_menu = QMenu(self.terminal_tabs)
        self.tab_list_menu.setObjectName("terminalTabListMenu")
        self.tab_list_menu.aboutToShow.connect(self._populate_tab_list)
        self.tab_list_button = QToolButton(self.terminal_tabs)
        self.tab_list_button.setObjectName("terminalTabListButton")
        self.tab_list_button.setArrowType(Qt.ArrowType.DownArrow)
        self.tab_list_button.setAutoRaise(True)
        self.tab_list_button.setFixedSize(22, 22)
        self.tab_list_button.setToolTip("列出所有终端标签")
        self.tab_list_button.setMenu(self.tab_list_menu)
        self.tab_list_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.terminal_tabs.setCornerWidget(self.tab_list_button, Qt.Corner.TopRightCorner)
        right_layout.addWidget(self.terminal_tabs, 1)

        broadcast = QWidget()
        broadcast.setObjectName("broadcastPanel")
        broadcast.setStyleSheet(
            "QWidget#broadcastPanel { background: #f9e8e6; border: 1px solid #d9aaa5; }"
        )
        broadcast_layout = QVBoxLayout(broadcast)
        broadcast_layout.setContentsMargins(8, 6, 8, 6)
        header = QHBoxLayout()
        header.addWidget(QLabel("广播命令"))
        self.broadcast_summary = QLabel()
        self.broadcast_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.broadcast_summary, 1)
        broadcast_layout.addLayout(header)
        command_row = QHBoxLayout()
        self.broadcast_input = BroadcastCommandInput()
        self.broadcast_input.setObjectName("terminalBroadcastInput")
        self.broadcast_input.setPlaceholderText("仅向当前组勾选的终端发送；发送前检查工作目录")
        self.broadcast_input.setEnabled(False)
        self.broadcast_input.textChanged.connect(self._update_broadcast_summary)
        self.broadcast_input.returnPressed.connect(self._request_broadcast)
        self.broadcast_input.inputFocused.connect(lambda: self._set_command_target("broadcast"))
        self.send_button = QPushButton("发送命令")
        self.send_button.setObjectName("terminalAction")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._request_broadcast)
        command_row.addWidget(self.broadcast_input, 1)
        command_row.addWidget(self.send_button)
        broadcast_layout.addLayout(command_row)
        self.command_toolbar = QToolBar()
        self.command_toolbar.setObjectName("terminalCommandHelpers")
        self.command_toolbar.setMovable(False)
        self.command_toolbar.setFloatable(False)
        self.command_toolbar.addWidget(QLabel("常用命令："))
        self.command_toolbar.setToolTip("替换当前输入，不会立即执行")
        self.command_helpers: list[QPushButton] = []
        for label, snippet in (
            ("cd（进入目录）", "cd "),
            ("mkdir（新建目录）", "mkdir "),
            ("rm -r（删除目录及子目录）", "rm -r "),
            ("cd ..（退回上级）", "cd .."),
            ("ls（列出文件）", "ls"),
            ("ls -a（含隐藏）", "ls -a"),
            ("ls -lh（详细列表）", "ls -lh"),
            ("pwd（当前路径）", "pwd"),
        ):
            button = QPushButton(label)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, value=snippet: self._insert_command_snippet(value))
            self.command_helpers.append(button)
            self.command_toolbar.addWidget(button)
        right_layout.addWidget(broadcast)
        right_layout.addWidget(self.command_toolbar)
        splitter.addWidget(right)
        splitter.setSizes([300, 900])
        layout.addWidget(splitter, 1)
        self._rebuild_tree()

    def set_nodes(self, nodes: list[tuple[str, str]]) -> None:
        """Replace available nodes; node IDs are stable and labels are user facing."""
        new_nodes = dict(nodes)
        for session_id, record in list(self._sessions.items()):
            if record.node_id not in new_nodes:
                self._remove_session(session_id)
        self._nodes = new_nodes
        self._refresh_groups()
        self._rebuild_tree()

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def create_group(self, node_ids: list[str]) -> str:
        """Create one stable group for a connection batch."""
        group_id = f"group-{self._next_group}"
        number = self._next_group
        self._next_group += 1
        self._groups[group_id] = TerminalGroup(
            group_id, f"终端组{number}", list(dict.fromkeys(node_ids)), [], set()
        )
        self._refresh_groups(select=group_id)
        return group_id

    def active_group_id(self) -> str | None:
        return self.group_selector.currentData()

    def group_name(self, group_id: str) -> str:
        group = self._groups.get(group_id)
        if group is None:
            return group_id
        labels = [self._nodes.get(node_id, node_id) for node_id in group.node_ids]
        if len(labels) > 1:
            parts = [re.fullmatch(r"(.*?)(\d+)", label) for label in labels]
            if all(parts) and len({part.group(1) for part in parts}) == 1:
                numbers = [int(part.group(2)) for part in parts]
                if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
                    labels = [f"{labels[0]}..{parts[-1].group(2)}"]
            if len(labels) > 3:
                labels = [f"{labels[0]}..{labels[-1]} · {len(labels)}节点"]
        return f"{group.name}({', '.join(labels)})"

    def _show_combined(self) -> None:
        group_id = self.active_group_id()
        if group_id not in self._groups:
            return
        pane = self._combined_views.get(group_id)
        if pane is None:
            pane = CombinedTerminalPane()
            self._combined_views[group_id] = pane
        index = self.terminal_tabs.indexOf(pane)
        if index < 0:
            index = self.terminal_tabs.addTab(pane, f"联合 · {self._groups[group_id].name}")
        self.terminal_tabs.setCurrentIndex(index)
        self._render_combined(group_id)

    def show_group_combined(self, group_id: str) -> None:
        index = self.group_selector.findData(group_id)
        if index >= 0:
            self.group_selector.setCurrentIndex(index)
            self._show_combined()

    def group_sessions(self, group_id: str) -> list[str]:
        group = self._groups.get(group_id)
        return list(group.members) if group is not None else []

    def session_node_label(self, session_id: str) -> str:
        record = self._sessions.get(session_id)
        return self.node_label(record.node_id) if record is not None else "未知节点"

    def _request_disconnect_group(self) -> None:
        group_id = self.active_group_id()
        if group_id is not None and self.group_sessions(group_id):
            self.disconnectGroupRequested.emit(group_id)

    def _update_combined_title(self, group_id: str) -> None:
        pane = self._combined_views.get(group_id)
        if pane is not None:
            index = self.terminal_tabs.indexOf(pane)
            if index >= 0:
                self.terminal_tabs.setTabText(index, f"联合 · {self._groups[group_id].name}")
            self._render_combined(group_id)

    def _render_combined(self, group_id: str) -> None:
        pane = self._combined_views.get(group_id)
        if group_id not in self._groups or pane is None or self.terminal_tabs.indexOf(pane) < 0:
            return
        rounds = []
        for round_ in self._combined_rounds.get(group_id, []):
            results = []
            for result in sorted(round_.results, key=lambda item: _node_order(self.node_label(item.node_id))):
                results.append(result.text)
            rounds.append((results, all(result.complete for result in round_.results)))
        pane.render(rounds)

    def begin_broadcast_round(self, group_id: str, command: str, targets: dict[str, str]) -> None:
        """Start a display round only after the command is approved for delivery."""
        rounds = self._combined_rounds.setdefault(group_id, [])
        for old in rounds:
            for result in old.results:
                result.complete = True
        results = []
        for node_id, session_id in targets.items():
            view = self._views.get(session_id)
            if view is not None:
                prompt = view.raw_prompt_line() if self.shell_ready(session_id) else ""
                results.append(CombinedResult(session_id, node_id, prompt))
        if results:
            rounds.append(CombinedRound(command, results))
            if len(rounds) > 100:
                del rounds[:-100]
            self._combined_timer.start()

    def mark_broadcast_send_failed(self, group_id: str, session_id: str) -> None:
        rounds = self._combined_rounds.get(group_id)
        if rounds:
            rounds[-1].results = [result for result in rounds[-1].results
                                  if result.session_id != session_id]
            if not rounds[-1].results:
                rounds.pop()
            self._combined_timer.start()

    def _refresh_combined_views(self) -> None:
        for group_id in list(self._combined_views):
            self._render_combined(group_id)

    def _rename_group(self) -> None:
        group = self._groups.get(self.active_group_id())
        if group is None or self._broadcast_busy:
            return
        name, accepted = QInputDialog.getText(self, "重命名终端组", "组名：", text=group.name)
        if not accepted:
            return
        name = name.strip()
        if not name or len(name) > 24:
            QMessageBox.warning(self, "组名无效", "组名须为 1–24 个字符。")
            return
        group.name = name
        self._refresh_groups(select=group.group_id)
        self._rebuild_tree()
        self._update_combined_title(group.group_id)
        for session_id in group.members:
            view = self._views.get(session_id)
            index = self.terminal_tabs.indexOf(view) if view is not None else -1
            if index >= 0:
                record = self._sessions[session_id]
                self.terminal_tabs.setTabText(index, self._terminal_tab_title(record))
                self.terminal_tabs.setTabToolTip(index, self.group_name(group.group_id))

    def add_terminal(self, node_id: str, title: str | None = None,
                     group_id: str | None = None, *, activate: bool = True) -> str:
        """Register a connected shell after its channel has opened."""
        if node_id not in self._nodes:
            raise ValueError(f"节点未连接：{node_id}")
        if group_id is None:
            group_id = self.create_group([node_id])
        if group_id not in self._groups:
            raise ValueError(f"终端组不存在：{group_id}")
        number = 1 + sum(record.node_id == node_id for record in self._sessions.values())
        session_id = f"terminal-{self._next_session}"
        self._next_session += 1
        record = TerminalRecord(session_id, node_id, title or f"终端{number}", group_id)
        self._sessions[session_id] = record
        group = self._groups[group_id]
        group.members.append(session_id)
        if node_id not in group.node_ids:
            group.node_ids.append(node_id)
        if not any(self._sessions[sid].node_id == node_id for sid in group.selected):
            group.selected.add(session_id)
        self._refresh_groups(select=group_id)
        self._rebuild_tree(session_id=session_id)
        self._show_tab(session_id, activate=activate)
        self._refresh_node_tab_titles(node_id)
        return session_id

    def remove_terminal(self, session_id: str) -> None:
        record = self._sessions.get(session_id)
        if record is None:
            return
        self._remove_session(session_id)
        self._closed_sessions.discard(session_id)
        self._refresh_groups()
        self._rebuild_tree()

    def mark_terminal_closed(self, session_id: str) -> None:
        if session_id not in self._sessions:
            return
        self._closed_sessions.add(session_id)
        self._groups[self._sessions[session_id].group_id].selected.discard(session_id)
        view = self._views.get(session_id)
        if view is not None:
            view.output.set_input_enabled(False)
            view.output.write("\r\n[SSH Shell 已关闭；输出保留供查看]\r\n")
        self._rebuild_tree(session_id=session_id)
        self._refresh_groups()
        self._update_command_helpers()

    def append_output(self, session_id: str, text: str) -> None:
        view = self._views.get(session_id)
        if view is None:
            return
        view.append_output(text)
        group_id = self._sessions[session_id].group_id
        for round_ in reversed(self._combined_rounds.get(group_id, [])):
            result = next((item for item in round_.results
                           if item.session_id == session_id and not item.complete), None)
            if result is not None:
                result.text = (result.text + text)[-CombinedTerminalPane.MAX_NODE_CHARS:]
                if self.shell_ready(session_id):
                    result.complete = True
                break
        combined = self._combined_views.get(group_id)
        if combined is not None and self.terminal_tabs.indexOf(combined) >= 0:
            self._combined_timer.start()
        if self._command_target == ("terminal", session_id):
            self._update_command_helpers()

    def terminal_size(self, session_id: str) -> tuple[int, int] | None:
        view = self._views.get(session_id)
        return view.terminal_size() if view is not None else None

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._selection_changed()
        self._update_broadcast_summary()

    def set_broadcast_busy(self, busy: bool) -> None:
        self._broadcast_busy = busy
        self.group_selector.setEnabled(not busy and self.group_selector.count() > 0)
        self.group_tree.setEnabled(not busy)
        self.rename_group_button.setEnabled(not busy and self.group_selector.count() > 0)
        self.disconnect_group_button.setEnabled(not busy and self.group_selector.count() > 0)
        self._update_broadcast_summary()

    def set_terminal_input_enabled(self, session_id: str, enabled: bool) -> None:
        view = self._views.get(session_id)
        if view is not None:
            view.output.set_input_enabled(enabled and session_id not in self._closed_sessions)
            if self._command_target == ("terminal", session_id):
                self._update_command_helpers()

    def shell_ready(self, session_id: str) -> bool:
        """Only inject a cwd probe when a recognizable Bash prompt is idle."""
        view = self._views.get(session_id)
        if view is None or session_id in self._closed_sessions:
            return False
        line = view.prompt_line()
        match = PLAIN_PROMPT.match(line)
        return match is not None and not line[match.end():].strip()

    def shell_prompt(self, session_id: str) -> str:
        view = self._views.get(session_id)
        return view.prompt_line() if view is not None else ""

    def conda_prefixes(self, session_id: str) -> tuple[str, ...] | None:
        """Read visible environment prefixes from an idle, recognizable prompt."""
        if not self.shell_ready(session_id):
            return None
        line = self.shell_prompt(session_id)
        match = PLAIN_PROMPT.match(line)
        if match is None:
            return None
        return tuple(re.findall(r"\(([^()\r\n]+)\)[ \t]+", line[:match.start(1)]))

    def ignores_conda_mismatch(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        return bool(group and group.ignore_conda_mismatch)

    def ignore_conda_mismatch(self, group_id: str) -> None:
        group = self._groups.get(group_id)
        if group is not None:
            group.ignore_conda_mismatch = True

    def node_label(self, node_id: str) -> str:
        return self._nodes.get(node_id, node_id)

    def broadcast_targets(self) -> dict[str, str]:
        """Only selected, live members of the active group; at most one per node."""
        group = self._groups.get(self.active_group_id())
        if group is None:
            return {}
        targets = {}
        for session_id in group.members:
            record = self._sessions.get(session_id)
            if (record is not None and session_id in group.selected
                    and session_id not in self._closed_sessions):
                targets.setdefault(record.node_id, session_id)
        return targets

    def valid_broadcast(self, group_id: str, targets: dict[str, str]) -> bool:
        """Recheck the group boundary before and after the cwd probe."""
        group = self._groups.get(group_id)
        return bool(group and group_id == self.active_group_id() and targets
                    and all(sid in group.members and sid in group.selected
                            and sid not in self._closed_sessions
                            and self._sessions.get(sid, None) is not None
                            and self._sessions[sid].node_id == node_id
                            for node_id, sid in targets.items())
                    and len(set(targets.values())) == len(targets))

    def _refresh_groups(self, select: str | None = None) -> None:
        previous = self.active_group_id()
        with QSignalBlocker(self.group_selector):
            self.group_selector.clear()
            for group in self._groups.values():
                self.group_selector.addItem(self.group_name(group.group_id), group.group_id)
            index = self.group_selector.findData(select or previous)
            if index >= 0:
                self.group_selector.setCurrentIndex(index)
        if self.active_group_id() != previous:
            self._clear_broadcast_draft()
        self._rebuild_group_members()
        self.group_selector.setEnabled(not self._broadcast_busy and self.group_selector.count() > 0)
        self.rename_group_button.setEnabled(not self._broadcast_busy and self.group_selector.count() > 0)
        self.disconnect_group_button.setEnabled(not self._broadcast_busy and self.group_selector.count() > 0)

    def _clear_broadcast_draft(self) -> None:
        self.broadcast_input.clear()

    def _group_changed(self, _index: int) -> None:
        self._clear_broadcast_draft()
        self._rebuild_group_members()
        group = self._groups.get(self.active_group_id())
        if group is not None and len(group.members) == 1:
            self._show_tab(group.members[0])
        elif group is not None:
            self._show_combined()

    def _rebuild_group_members(self) -> None:
        group = self._groups.get(self.active_group_id())
        with QSignalBlocker(self.group_tree):
            self.group_tree.clear()
            if group is not None:
                for session_id in group.members:
                    record = self._sessions.get(session_id)
                    if record is None:
                        continue
                    live = session_id not in self._closed_sessions
                    item = QTreeWidgetItem(self.group_tree, [
                        f"{self.node_label(record.node_id)} · {record.title}",
                        "已连接" if live else "已断开",
                    ])
                    item.setData(0, ITEM_ROLE, session_id)
                    if live:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(0, Qt.CheckState.Checked if session_id in group.selected
                                           else Qt.CheckState.Unchecked)
        self._update_broadcast_summary()

    def _group_member_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        group = self._groups.get(self.active_group_id())
        if group is None:
            return
        session_id = item.data(0, ITEM_ROLE)
        record = self._sessions.get(session_id)
        if record is None or record.group_id != group.group_id:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            for other in list(group.selected):
                if other != session_id and self._sessions[other].node_id == record.node_id:
                    group.selected.discard(other)
            group.selected.add(session_id)
            self._rebuild_group_members()
        else:
            group.selected.discard(session_id)
        self._update_broadcast_summary()

    def _selected_node(self) -> str | None:
        item = self.node_tree.currentItem()
        if item is None:
            return None
        kind, value = item.data(0, ITEM_ROLE)
        return value if kind == "node" else self._sessions[value].node_id

    def _selected_session(self) -> str | None:
        item = self.node_tree.currentItem()
        if item is not None:
            kind, value = item.data(0, ITEM_ROLE)
            if kind == "terminal":
                return value
        current = self.terminal_tabs.currentWidget()
        return next((session_id for session_id, view in self._views.items() if view is current), None)

    def _new_terminal(self) -> None:
        node_id = self._selected_node()
        if node_id is not None and not self._busy:
            self.connectRequested.emit(node_id)

    def _disconnect_terminal(self) -> None:
        session_id = self._selected_session()
        if session_id is not None:
            self.disconnectRequested.emit(session_id)

    def checked_nodes(self) -> list[str]:
        return [
            self.node_tree.topLevelItem(index).data(0, ITEM_ROLE)[1]
            for index in range(self.node_tree.topLevelItemCount())
            if self.node_tree.topLevelItem(index).checkState(2) == Qt.CheckState.Checked
        ]

    def clear_checked_nodes(self) -> None:
        with QSignalBlocker(self.node_tree):
            for index in range(self.node_tree.topLevelItemCount()):
                self.node_tree.topLevelItem(index).setCheckState(2, Qt.CheckState.Unchecked)
        self._selection_changed()

    def _connect_checked(self) -> None:
        nodes = self.checked_nodes()
        if nodes and not self._busy:
            self.clear_checked_nodes()
            self.connectManyRequested.emit(nodes)

    def _disconnect_checked(self) -> None:
        nodes = self.checked_nodes()
        if nodes:
            self.clear_checked_nodes()
            self.disconnectManyRequested.emit(nodes)

    def _request_broadcast(self) -> None:
        command = self.broadcast_input.text()
        targets = self.broadcast_targets()
        group_id = self.active_group_id()
        if (not self._broadcast_busy and not self._busy
                and command.strip() and group_id is not None
                and self.valid_broadcast(group_id, targets)):
            self.broadcastRequested.emit(group_id, command, targets)

    def _insert_command_snippet(self, snippet: str) -> None:
        target = self._active_command_target()
        if target is None:
            return
        kind, session_id = target
        if kind == "broadcast":
            self.broadcast_input.setText(snippet)
            self.broadcast_input.setFocus()
            return
        view = self._views[session_id]
        self.inputRequested.emit(session_id, b"\x05\x15" + snippet.encode("utf-8"))
        view.output.focus_terminal()

    def _set_command_target(self, kind: str, session_id: str | None = None) -> None:
        self._command_target = (kind, session_id)
        self._update_command_helpers()

    def _active_command_target(self) -> tuple[str, str | None] | None:
        target = self._command_target
        if target is not None and target[0] == "broadcast" and self.broadcast_input.isEnabled():
            return target
        if target is not None and target[0] == "terminal":
            session_id = target[1]
            view = self._views.get(session_id)
            if (view is not None and self.terminal_tabs.currentWidget() is view
                    and session_id not in self._closed_sessions and view.output._input_enabled
                    and PLAIN_PROMPT.match(view.prompt_line())):
                return target
        if self.broadcast_input.isEnabled():
            return ("broadcast", None)
        return None

    def _update_command_helpers(self) -> None:
        enabled = self._active_command_target() is not None
        for button in self.command_helpers:
            button.setEnabled(enabled)

    def _rebuild_tree(self, session_id: str | None = None) -> None:
        checked_nodes = set(self.checked_nodes())
        selected = session_id
        if selected is None and self.node_tree.currentItem() is not None:
            selected = self.node_tree.currentItem().data(0, ITEM_ROLE)[1]
        with QSignalBlocker(self.node_tree):
            self.node_tree.clear()
            selected_item = None
            for node_id, label in sorted(self._nodes.items(), key=lambda pair: pair[1].casefold()):
                sessions = [record for record in self._sessions.values() if record.node_id == node_id]
                node = QTreeWidgetItem(self.node_tree, [label, f"{len(sessions)} 个终端", ""])
                node.setData(0, ITEM_ROLE, ("node", node_id))
                node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                node.setCheckState(2, Qt.CheckState.Checked if node_id in checked_nodes else Qt.CheckState.Unchecked)
                if selected == node_id:
                    selected_item = node
                for record in sessions:
                    state = "已断开" if record.session_id in self._closed_sessions else "已连接"
                    child = QTreeWidgetItem(node, [f"{record.title} · {self._groups[record.group_id].name}", state])
                    child.setData(0, ITEM_ROLE, ("terminal", record.session_id))
                    if selected == record.session_id:
                        selected_item = child
                node.setExpanded(True)
            if selected_item is None and self.node_tree.topLevelItemCount():
                selected_item = self.node_tree.topLevelItem(0)
            if selected_item is not None:
                self.node_tree.setCurrentItem(selected_item)
        self._selection_changed()
        self._update_broadcast_summary()

    def _selection_changed(self) -> None:
        node_id = self._selected_node()
        self.new_terminal_button.setEnabled(node_id is not None and not self._busy)
        self.disconnect_terminal_button.setEnabled(self._selected_session() is not None)
        self.connect_checked_button.setEnabled(bool(self.checked_nodes()) and not self._busy)
        self.disconnect_checked_button.setEnabled(bool(self.checked_nodes()) and not self._busy)

    def _item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        kind, value = item.data(0, ITEM_ROLE)
        if kind == "terminal":
            self._show_tab(value)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 2:
            self._selection_changed()

    def _show_tab(self, session_id: str, *, activate: bool = True) -> None:
        record = self._sessions[session_id]
        view = self._views.get(session_id)
        if view is None:
            view = TerminalPane(self._nodes[record.node_id], record.title)
            view.sendRequested.connect(lambda data, sid=session_id: self.inputRequested.emit(sid, data))
            view.output.inputFocused.connect(
                lambda sid=session_id: self._set_command_target("terminal", sid)
            )
            view.resizeRequested.connect(
                lambda columns, rows, sid=session_id: self.resizeRequested.emit(sid, columns, rows)
            )
            self._views[session_id] = view
        index = self.terminal_tabs.indexOf(view)
        if index < 0:
            index = self.terminal_tabs.addTab(view, self._terminal_tab_title(record))
            self.terminal_tabs.setTabToolTip(index, self.group_name(record.group_id))
        if not activate:
            return
        self.terminal_tabs.setCurrentIndex(index)
        self._set_command_target("terminal", session_id)
        view.output.focus_terminal()
        self._selection_changed()

    def _terminal_tab_title(self, record: TerminalRecord) -> str:
        title = f"{self._groups[record.group_id].name} · {self.node_label(record.node_id)}"
        if sum(item.node_id == record.node_id for item in self._sessions.values()) > 1:
            title += f" （{record.title}）"
        return title

    def _refresh_node_tab_titles(self, node_id: str) -> None:
        for record in self._sessions.values():
            if record.node_id != node_id:
                continue
            view = self._views.get(record.session_id)
            index = self.terminal_tabs.indexOf(view) if view is not None else -1
            if index >= 0:
                self.terminal_tabs.setTabText(index, self._terminal_tab_title(record))

    def _populate_tab_list(self) -> None:
        self.tab_list_menu.clear()
        current = self.terminal_tabs.currentWidget()
        for index in range(self.terminal_tabs.count()):
            widget = self.terminal_tabs.widget(index)
            action = self.tab_list_menu.addAction(self.terminal_tabs.tabText(index))
            action.setCheckable(True)
            action.setChecked(widget is current)
            action.triggered.connect(
                lambda _checked=False, target=widget: self._select_tab_from_list(target)
            )

    def _select_tab_from_list(self, widget: QWidget) -> None:
        index = self.terminal_tabs.indexOf(widget)
        if index >= 0:
            self.terminal_tabs.setCurrentIndex(index)

    def _hide_tab(self, index: int) -> None:
        if self.terminal_tabs.widget(index) in self._combined_views.values():
            return
        self.terminal_tabs.removeTab(index)
        self._selection_changed()

    def _tab_changed(self, _index: int) -> None:
        if self._broadcast_busy:
            return
        current = self.terminal_tabs.currentWidget()
        combined_group = next((group_id for group_id, pane in self._combined_views.items()
                               if pane is current), None)
        if combined_group is not None:
            index = self.group_selector.findData(combined_group)
            if index >= 0 and combined_group != self.active_group_id():
                with QSignalBlocker(self.group_selector):
                    self.group_selector.setCurrentIndex(index)
                self._clear_broadcast_draft()
                self._rebuild_group_members()
            self._update_command_helpers()
            return
        session_id = next((sid for sid, view in self._views.items() if view is current), None)
        record = self._sessions.get(session_id)
        if record is not None and record.group_id != self.active_group_id():
            index = self.group_selector.findData(record.group_id)
            if index >= 0:
                with QSignalBlocker(self.group_selector):
                    self.group_selector.setCurrentIndex(index)
                self._clear_broadcast_draft()
                self._rebuild_group_members()
        self._update_command_helpers()

    def _remove_session(self, session_id: str) -> None:
        if self._command_target == ("terminal", session_id):
            self._command_target = None
        for rounds in self._combined_rounds.values():
            for round_ in rounds:
                for result in round_.results:
                    if result.session_id == session_id:
                        result.complete = True
        view = self._views.pop(session_id, None)
        if view is not None:
            index = self.terminal_tabs.indexOf(view)
            if index >= 0:
                self.terminal_tabs.removeTab(index)
            view.deleteLater()
        terminal = self._sessions.pop(session_id, None)
        self._closed_sessions.discard(session_id)
        group = self._groups.get(terminal.group_id) if terminal is not None else None
        if group is not None:
            group.members.remove(session_id)
            group.selected.discard(session_id)
            if (terminal.node_id in group.node_ids
                    and not any(self._sessions[sid].node_id == terminal.node_id for sid in group.members)):
                group.node_ids.remove(terminal.node_id)
            if not group.members:
                self._groups.pop(group.group_id)
                self._combined_rounds.pop(group.group_id, None)
                combined = self._combined_views.pop(group.group_id, None)
                if combined is not None:
                    index = self.terminal_tabs.indexOf(combined)
                    if index >= 0:
                        self.terminal_tabs.removeTab(index)
                    combined.deleteLater()
            else:
                self._combined_timer.start()
        if terminal is not None:
            self._refresh_node_tab_titles(terminal.node_id)
        self._update_command_helpers()

    def _update_broadcast_summary(self, _checked: bool = False) -> None:
        targets = self.broadcast_targets()
        group_id = self.active_group_id()
        group_name = self.group_name(group_id) if group_id else "无终端组"
        enabled = group_id is not None and not self._broadcast_busy and not self._busy
        self.broadcast_input.setEnabled(enabled)
        self._update_command_helpers()
        self.send_button.setEnabled(enabled and bool(targets) and bool(self.broadcast_input.text().strip()))
        if self._broadcast_busy:
            self.broadcast_summary.setText(f"{group_name} · 正在检查各终端工作目录…")
            self.broadcast_summary.setToolTip("")
        elif targets:
            labels = [self._nodes[node_id] for node_id in targets]
            preview = ", ".join(labels[:3]) + (" 等" if len(labels) > 3 else "")
            self.broadcast_summary.setText(
                f"{group_name} · {len(targets)} 个目标：{preview} · 严格组内广播"
            )
            self.broadcast_summary.setToolTip("广播目标：" + ", ".join(labels))
        else:
            self.broadcast_summary.setText(f"{group_name} · 在本组成员列表勾选目标")
            self.broadcast_summary.setToolTip("")
