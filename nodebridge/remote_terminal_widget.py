"""Local xterm.js frontend for one SSH PTY channel."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication


class _TerminalPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source) -> None:
        if level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel:
            logging.getLogger(__name__).warning("Terminal JS %s:%s: %s", source, line, message)


class _TerminalBridge(QObject):
    output = Signal(str)
    reset = Signal()
    inputEnabled = Signal(bool)
    fitRequested = Signal()
    focusRequested = Signal()

    inputReceived = Signal(str)
    sizeReceived = Signal(int, int)
    frontendReady = Signal(int, int)
    inputFocused = Signal()
    bellReceived = Signal()

    @Slot(str)
    def input(self, data: str) -> None:
        self.inputReceived.emit(data)

    @Slot(int, int)
    def resize(self, columns: int, rows: int) -> None:
        self.sizeReceived.emit(columns, rows)

    @Slot(int, int)
    def ready(self, columns: int, rows: int) -> None:
        self.frontendReady.emit(columns, rows)

    @Slot()
    def focused(self) -> None:
        self.inputFocused.emit()

    @Slot()
    def bell(self) -> None:
        self.bellReceived.emit()

    @Slot(str)
    def copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    @Slot(result=str)
    def paste(self) -> str:
        return QApplication.clipboard().text()


class RemoteTerminalWidget(QWebEngineView):
    """Only xterm.js owns cursor, cells, ANSI state, selection and keyboard mapping."""

    inputRequested = Signal(bytes)
    resizeRequested = Signal(int, int)
    inputFocused = Signal()
    bellRequested = Signal()

    def __init__(self, *, read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._input_enabled = not read_only
        self._read_only = read_only
        self._ready = False
        self._load_started = False
        self._focus_when_ready = False
        self._pending_output: list[str] = []
        self._size = (100, 30)
        self.setPage(_TerminalPage(self))
        self._bridge = _TerminalBridge(self)
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("terminalBridge", self._bridge)
        self.page().setWebChannel(self._channel)
        self._bridge.inputReceived.connect(self._send_input)
        self._bridge.sizeReceived.connect(self._set_size)
        self._bridge.frontendReady.connect(self._frontend_ready)
        self._bridge.inputFocused.connect(self.inputFocused)
        self._bridge.bellReceived.connect(self.bellRequested)
        self.setObjectName("terminalOutput")

    @property
    def terminal_size(self) -> tuple[int, int]:
        return self._size

    def write(self, text: str) -> None:
        if not text:
            return
        if self._ready:
            self._bridge.output.emit(text)
        else:
            self._pending_output.append(text)

    def reset_terminal(self) -> None:
        self._pending_output.clear()
        if self._ready:
            self._bridge.reset.emit()

    def set_input_enabled(self, enabled: bool) -> None:
        self._input_enabled = enabled and not self._read_only
        if self._ready:
            self._bridge.inputEnabled.emit(self._input_enabled)

    def focus_terminal(self) -> None:
        if self._ready:
            self._bridge.focusRequested.emit()
        else:
            self._focus_when_ready = True
            self.setFocus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._load_started:
            self._load_started = True
            self.load(QUrl.fromLocalFile(str(Path(__file__).with_name("assets") / "terminal.html")))
        if self._ready:
            self._bridge.fitRequested.emit()
            if self._focus_when_ready:
                self._focus_when_ready = False
                self._bridge.focusRequested.emit()

    def _send_input(self, data: str) -> None:
        if self._input_enabled:
            self.inputRequested.emit(data.encode("utf-8"))

    def _set_size(self, columns: int, rows: int) -> None:
        if columns < 2 or rows < 2:
            return
        size = (columns, rows)
        if size != self._size:
            self._size = size
            self.resizeRequested.emit(*size)

    def _frontend_ready(self, columns: int, rows: int) -> None:
        self._ready = True
        self._bridge.inputEnabled.emit(self._input_enabled)
        self._set_size(columns, rows)
        for text in self._pending_output:
            self._bridge.output.emit(text)
        self._pending_output.clear()
        self._bridge.fitRequested.emit()
        if self._focus_when_ready and self.isVisible():
            self._focus_when_ready = False
            self._bridge.focusRequested.emit()
