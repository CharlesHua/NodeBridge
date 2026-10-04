"""Own live SSH shell channels independently of terminal widgets."""

from __future__ import annotations

import codecs
from concurrent.futures import Future, ThreadPoolExecutor

import paramiko
from PySide6.QtCore import QObject, QTimer, Signal


class TerminalManager(QObject):
    outputReady = Signal(str, str)
    terminalClosed = Signal(str, bool)
    inputFailed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._channels: dict[str, tuple[str, paramiko.Channel]] = {}
        self._decoders: dict[str, codecs.IncrementalDecoder] = {}
        self._send_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nodebridge-pty")
        self._pending_send: dict[str, Future] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)

    def add(self, session_id: str, node_id: str, channel: paramiko.Channel) -> None:
        self._channels[session_id] = (node_id, channel)
        self._decoders[session_id] = codecs.getincrementaldecoder("utf-8")("replace")
        if not self._timer.isActive():
            self._timer.start()

    def has_session(self, session_id: str) -> bool:
        return session_id in self._channels

    def send(self, session_id: str, data: bytes) -> Future:
        owned = self._channels.get(session_id)
        if owned is None:
            raise ConnectionError("终端已断开。")
        channel = owned[1]
        previous = self._pending_send.get(session_id)

        def write() -> None:
            if previous is not None:
                try:
                    previous.result()
                except Exception:
                    pass
            channel.sendall(data)

        future = self._send_pool.submit(write)
        self._pending_send[session_id] = future

        def report(result: Future) -> None:
            try:
                result.result()
            except Exception as exc:
                if self._channels.get(session_id, (None, None))[1] is channel:
                    self.inputFailed.emit(session_id, f"{type(exc).__name__}: {exc}")

        future.add_done_callback(report)
        return future

    def resize(self, session_id: str, columns: int, rows: int) -> Future | None:
        owned = self._channels.get(session_id)
        if owned is None:
            return None
        channel = owned[1]
        previous = self._pending_send.get(session_id)

        def resize_pty() -> None:
            if previous is not None:
                try:
                    previous.result()
                except Exception:
                    pass
            channel.resize_pty(width=columns, height=rows)

        future = self._send_pool.submit(resize_pty)
        self._pending_send[session_id] = future
        return future

    def close(self, session_id: str, unexpected: bool = False) -> None:
        owned = self._channels.pop(session_id, None)
        self._pending_send.pop(session_id, None)
        decoder = self._decoders.pop(session_id, None)
        if owned is None:
            return
        if decoder is not None:
            tail = decoder.decode(b"", final=True)
            if tail:
                self.outputReady.emit(session_id, tail)
        try:
            owned[1].close()
        except Exception:
            pass
        finally:
            self.terminalClosed.emit(session_id, unexpected)
            if not self._channels:
                self._timer.stop()

    def close_nodes(self, node_ids: list[str]) -> int:
        selected = set(node_ids)
        closed = 0
        for session_id, (node_id, _channel) in list(self._channels.items()):
            if node_id in selected:
                self.close(session_id)
                closed += 1
        return closed

    def retain_nodes(self, node_ids: set[str]) -> None:
        for session_id, (node_id, _channel) in list(self._channels.items()):
            if node_id not in node_ids:
                self.close(session_id)

    def close_all(self) -> None:
        for session_id in list(self._channels):
            self.close(session_id)

    def _poll(self) -> None:
        for session_id, (_node_id, channel) in list(self._channels.items()):
            try:
                if channel.recv_ready():
                    data = channel.recv(16384)
                    if data:
                        text = self._decoders[session_id].decode(data)
                        if text:
                            self.outputReady.emit(session_id, text)
                    else:
                        self.close(session_id, unexpected=True)
                        continue
                if channel.closed or channel.exit_status_ready():
                    self.close(session_id, unexpected=True)
            except (OSError, EOFError, paramiko.SSHException):
                self.close(session_id, unexpected=True)
