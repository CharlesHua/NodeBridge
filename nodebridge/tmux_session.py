"""Remote tmux sessions and a control-mode channel exposing raw pane bytes."""
from __future__ import annotations

import logging
import re
import shlex
import time
import uuid
from concurrent.futures import CancelledError
from dataclasses import asdict, dataclass


NAME = re.compile(r"nodebridge-[a-zA-Z0-9_-]{1,80}\Z")
PANE = re.compile(r"%[0-9]+\Z")
TMUX = "tmux -L nodebridge -f /dev/null"
FORMAT = "\t".join("#{" + value + "}" for value in (
    "session_name", "pane_id", "pane_current_path", "pane_current_command",
    "session_attached", "session_created", "@nodebridge_group", "@nodebridge_label",
    "window_active", "pane_active", "pane_dead"))


@dataclass(frozen=True)
class RemoteTask:
    name: str
    pane: str
    path: str = ""
    command: str = ""
    attached: int = 0
    created: int = 0
    group: str = ""
    label: str = ""
    dead: bool = False


def endpoint_key(config, route=()):
    """Stable identity without passwords, display labels, or local session IDs."""
    return (config.host, config.port, config.username, tuple(route))


def _target(name):
    if not NAME.fullmatch(name):
        raise ValueError("只能管理 nodebridge- 开头的有效 tmux 会话。")
    return "=" + name


class TmuxBackend:
    def __init__(self, host, alias=None, cancel=None):
        self.host = host
        self.route = (*host._alias_route, *((alias,) if alias else ()))
        self.cancel = cancel

    def _open(self, script):
        command = "sh -lc " + shlex.quote(script)
        for hop in reversed(self.route):
            command = ("ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
                       f"-o ConnectTimeout=10 -- {shlex.quote(hop)} {shlex.quote(command)}")
        transport = self.host._root_client.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("直连节点已断开。")
        channel = transport.open_session(timeout=10)
        try:
            channel.settimeout(0.2)
            channel.exec_command(command)
            return channel
        except Exception:
            channel.close()
            raise

    def run(self, script, timeout=20):
        if self.cancel is not None and self.cancel.is_set():
            raise CancelledError("tmux 查询已取消")
        channel = self._open(script)
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.cancel is not None and self.cancel.is_set():
                    raise CancelledError("tmux 查询已取消")
                if channel.recv_ready():
                    output.extend(channel.recv(16384))
                if channel.recv_stderr_ready():
                    errors.extend(channel.recv_stderr(4096))
                if len(output) + len(errors) > 2 * 1024**2:
                    raise OSError("tmux 查询结果过长。")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("tmux 查询超时。")
                time.sleep(0.01)
            code = channel.recv_exit_status()
            text = output.decode("utf-8", "replace")
            if code:
                detail = errors.decode("utf-8", "replace").strip() or text.strip()
                if code == 77:
                    raise OSError("目标节点未安装 tmux；请安装 tmux 3.1 或更新版本，或取消勾选持久终端后重试。")
                raise OSError(f"tmux 操作失败（{code}）：{detail}")
            return text
        finally:
            channel.close()

    def check(self):
        text = self.run("command -v tmux >/dev/null 2>&1 || exit 77; tmux -V")
        version = re.search(r"tmux (\d+)\.(\d+)", text)
        if version is None or tuple(map(int, version.groups())) < (3, 1):
            raise OSError("持久终端需要 tmux 3.1 或更新版本；不会自动安装或修改服务器配置。")

    def create(self, group, label):
        self.check()
        name = "nodebridge-" + uuid.uuid4().hex
        target = _target(name)
        # Options apply only to this new session, never the global tmux server.
        text = self.run(
            f"{TMUX} new-session -d -s {name} -x 100 -y 30 && "
            f"{TMUX} set-option -t {name} destroy-unattached off && "
            f"{TMUX} set-option -t {name} @nodebridge_group {shlex.quote(group)} && "
            f"{TMUX} set-option -t {name} @nodebridge_label {shlex.quote(label)} && "
            f"{TMUX} list-panes -s -t {target} -F {shlex.quote(FORMAT)}"
        )
        tasks = self.parse_tasks(text)
        task = next((task for task in tasks if task.name == name), None)
        if task is None:
            raise OSError(f"tmux 会话已创建但无法读取，名称：{name}；可在任务页扫描恢复。")
        return task

    @staticmethod
    def parse_tasks(text):
        tasks = []
        for line in text.splitlines():
            fields = line.split("\t")
            if line.startswith("nodebridge-") and len(fields) != 11:
                raise OSError("tmux 会话信息格式无效，目录或标签可能含制表符／换行；未据此判断会话是否已结束。")
            if len(fields) != 11 or not NAME.fullmatch(fields[0]) or not PANE.fullmatch(fields[1]):
                continue
            if fields[8:10] != ["1", "1"]:
                continue
            try:
                tasks.append(RemoteTask(*fields[:4], int(fields[4]), int(fields[5]),
                                        fields[6], fields[7], fields[10] == "1"))
            except ValueError:
                continue
        return tasks

    def scan(self):
        self.check()
        try:
            return self.parse_tasks(self.run(TMUX + " list-panes -a -F " + shlex.quote(FORMAT)))
        except OSError as exc:
            if any(message in str(exc).lower() for message in ("no server running", "no sessions", "no such file or directory")):
                return []
            raise

    def kill(self, name):
        self.run(TMUX + " kill-session -t " + _target(name))

    def attach(self, task):
        if task.dead:
            raise OSError("此 tmux 窗格中的进程已退出，无法继续交互。")
        channel = self._open("exec " + TMUX + " -C attach-session -t " + _target(task.name))
        adapter = TmuxChannel(channel, task.pane)
        try:
            adapter.prime()
            return adapter
        except Exception:
            channel.close()
            raise


class TmuxChannel:
    """The TerminalManager channel contract, backed by tmux control protocol.

    Notifications contain original pane output, not tmux's rendered terminal.
    Keyboard bytes are hexadecimal arguments, so no input becomes tmux syntax.
    """
    def __init__(self, channel, pane):
        if not PANE.fullmatch(pane):
            raise ValueError("无效 tmux 窗格。")
        self.raw, self.pane = channel, pane
        self._wire = bytearray()
        self._output = bytearray()
        self._block = None
        self._lines = []
        self._blocks = []
        self._priming = True
        self._captured = False
        self._capture_next = False
        self._exited = False
        self.initial_screen = b""
        self.initial_prompt = ""

    @staticmethod
    def unescape(data):
        return re.sub(rb"\\([0-3][0-7]{2})", lambda m: bytes([int(m[1], 8)]), data)

    def _line(self, line):
        if self._block is not None:
            if line in (b"%end " + self._block, b"%error " + self._block):
                if line.startswith(b"%error"):
                    message = b"\n".join(self._lines).decode("utf-8", "replace")
                    logging.getLogger(__name__).error("tmux 控制命令失败：%s", message)
                    raise OSError("tmux 控制命令失败：" + message)
                self._blocks.append(b"\n".join(self._lines))
                if self._capture_next:
                    self._captured = True
                    self._capture_next = False
                self._block, self._lines = None, []
            else:
                self._lines.append(line)
            return
        if line.startswith(b"%begin "):
            self._block = line[7:]
            self._lines = []
        elif line.startswith(b"%output "):
            fields = line.split(b" ", 2)
            if len(fields) == 3 and fields[1].decode() == self.pane and (not self._priming or self._captured):
                self._output.extend(self.unescape(fields[2]))
        elif line.startswith(b"%extended-output "):
            prefix, separator, value = line.partition(b" : ")
            if separator and prefix.split()[1].decode() == self.pane and (not self._priming or self._captured):
                self._output.extend(self.unescape(value))
        elif line.startswith(b"%exit"):
            self._exited = True

    def _pump(self):
        if self.raw.recv_ready():
            self._wire.extend(self.raw.recv(65536))
        while b"\n" in self._wire:
            line, _, rest = self._wire.partition(b"\n")
            self._wire = bytearray(rest)
            self._line(bytes(line).rstrip(b"\r"))
        if len(self._wire) + len(self._output) > 4 * 1024**2:
            raise OSError("tmux 终端输出积压过多；可重新连接持久会话。")
        if not self._priming:
            self._blocks.clear()

    def _wait_block(self, timeout=15):
        deadline = time.monotonic() + timeout
        while not self._blocks:
            self._pump()
            if self.closed or self.raw.exit_status_ready():
                raise OSError("tmux 控制连接提前关闭。")
            if time.monotonic() >= deadline:
                raise TimeoutError("tmux 控制连接超时。")
            time.sleep(0.01)
        return self._blocks.pop(0)

    def prime(self):
        self._wait_block()  # attach-session acknowledgement
        self._capture_next = True
        self.raw.sendall(f"capture-pane -p -e -S -2000 -t {self.pane}\n".encode())
        snapshot = self._wait_block()
        self.raw.sendall(f'display-message -p -t {self.pane} "#{{cursor_x}},#{{cursor_y}},#{{alternate_on}},#{{pane_height}}"\n'.encode())
        cursor = self._wait_block().decode().strip().split(",")
        lines = snapshot.split(b"\n")
        if len(cursor) != 4 or not all(value.isdecimal() for value in cursor):
            raise OSError("tmux 返回了无效的光标信息。")
        x, y, alternate, height = map(int, cursor)
        prompt_index = max(0, len(lines) - height) + y
        if prompt_index < len(lines):
            self.initial_prompt = lines[prompt_index].decode("utf-8", "replace")
        live = bytes(self._output)
        self.initial_screen = b"\x1b[2J\x1b[H" + b"\r\n".join(lines)
        if alternate:
            self.initial_screen = b"\x1b[?1049h" + self.initial_screen
        self.initial_screen += f"\x1b[{y + 1};{x + 1}H".encode()
        self._output = bytearray(self.initial_screen + live)
        self._priming = False

    def take_initial_screen(self):
        if self._output.startswith(self.initial_screen):
            del self._output[:len(self.initial_screen)]
        return self.initial_screen.decode("utf-8", "replace"), self.initial_prompt

    @property
    def closed(self):
        return self._exited or self.raw.closed

    def recv_ready(self):
        self._pump()
        return bool(self._output)

    def recv(self, size):
        data = bytes(self._output[:size])
        del self._output[:size]
        return data

    def sendall(self, data):
        for start in range(0, len(data), 256):
            keys = " ".join(f"{value:02x}" for value in data[start:start + 256])
            self.raw.sendall(f"send-keys -t {self.pane} -H {keys}\n".encode())

    def resize_pty(self, width, height):
        width, height = max(2, int(width)), max(2, int(height))
        self.raw.sendall(f"refresh-client -C {width}x{height}\n".encode())

    def exit_status_ready(self):
        return self._exited or self.raw.exit_status_ready()

    def close(self):
        self.raw.close()  # Detach only: never send exit, Ctrl+C, or kill-session.


def task_dict(task):
    return asdict(task)
