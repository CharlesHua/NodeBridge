"""Bounded, read-only Linux resource sampling on an independent SSH transport."""
from __future__ import annotations

import logging
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal
from nodebridge.remote import RemoteSession


USERS_MARKER = "__NODEBRIDGE_PROCESS_USERS__"
USERS_ERROR = "__NODEBRIDGE_USERS_UNAVAILABLE__"
# Query effective process owners, not just interactive login sessions. UID_MIN
# follows the host's ordinary-account convention; ps runs in the same channel.
COMMAND = (
    "LC_ALL=C; export LC_ALL; "
    "head -n 1 /proc/stat && cat /proc/meminfo && cat /proc/sys/kernel/random/boot_id; "
    "resource_status=$?; [ \"$resource_status\" -eq 0 ] || exit \"$resource_status\"; "
    f"printf '\\n{USERS_MARKER}\\n'; "
    "user_min=$(awk '$1 == \"UID_MIN\" { print $2; exit }' /etc/login.defs 2>/dev/null); "
    "case $user_min in ''|*[!0-9]*) user_min=1000;; esac; "
    "if process_users=$(ps -e -o uid= -o user:256= 2>/dev/null); then "
    "printf '%s\\n' \"$process_users\" | "
    "awk -v minimum=\"$user_min\" '$1 >= minimum && $1 != 65534 { print $2 }' | sort -u; "
    f"else printf '{USERS_ERROR}\\n'; fi"
)


@dataclass(frozen=True)
class Sample:
    total: int
    idle: int
    memory_total: int
    memory_used: int
    boot: str
    users: tuple[str, ...] | None = None


def parse_sample(text: str) -> Sample:
    resources, marker, users_text = text.partition(USERS_MARKER)
    lines = resources.strip().splitlines()
    cpu_line = next(line for line in lines if line.startswith("cpu "))
    cpu = [int(value) for value in cpu_line.split()[1:9]]
    memory = {}
    for line in lines:
        if line.startswith(("MemTotal:", "MemAvailable:")):
            key, value, unit = line.split()
            if unit != "kB":
                raise ValueError("未知内存单位")
            memory[key] = int(value) * 1024
    total, available = memory["MemTotal:"], memory["MemAvailable:"]
    if len(cpu) != 8 or min(cpu) < 0 or total <= 0 or not 0 <= available <= total:
        raise ValueError("无效的 Linux 资源数据")
    users = None
    if marker and USERS_ERROR not in users_text:
        users = tuple(sorted(set(line.strip() for line in users_text.splitlines() if line.strip())))
    return Sample(sum(cpu), cpu[3] + cpu[4], total, total - available, lines[-1], users)


def cpu_percent(previous: Sample | None, current: Sample) -> float | None:
    if previous is None or previous.boot != current.boot:
        return None
    elapsed, idle = current.total - previous.total, current.idle - previous.idle
    if elapsed <= 0 or idle < 0 or idle > elapsed:
        return None
    return 100 * (elapsed - idle) / elapsed


def query_sample(host, alias: str | None, cancel: threading.Event, timeout=8) -> Sample:
    if cancel.is_set():
        raise CancelledError()
    command = COMMAND
    if alias is not None:
        command = ("ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
                   f"-o ConnectTimeout=5 -- {shlex.quote(alias)} {shlex.quote(command)}")
    transport = host._client.get_transport()
    channel = transport.open_session(timeout=timeout)
    channel.settimeout(timeout)
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        channel.exec_command(command)
        while True:
            if cancel.is_set():
                raise CancelledError()
            if channel.recv_ready():
                output.extend(channel.recv(4096))
            if channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(4096))
            if len(output) + len(errors) > 65536:
                raise ValueError("资源查询输出过长")
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() > deadline:
                raise TimeoutError("资源查询超时")
            cancel.wait(0.03)
        if channel.recv_exit_status() != 0:
            raise OSError(errors.decode("utf-8", "replace").strip() or "资源查询失败")
        return parse_sample(output.decode("utf-8", "replace"))
    finally:
        channel.close()


class ResourceMonitor(QThread):
    result = Signal(object)

    def __init__(self, config, password, parent=None):
        super().__init__(parent)
        self.config, self.password = config, password
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.targets = {}

    def set_targets(self, targets):
        with self.lock:
            self.targets = dict(targets)

    def run(self):
        host = None
        previous, retry_after = {}, {}
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                while not self.cancel.is_set():
                    with self.lock:
                        targets = dict(self.targets)
                    previous = {key: value for key, value in previous.items() if key in targets}
                    try:
                        if targets and host is None:
                            host = RemoteSession.connect_shell_host(self.config, self.password)
                        futures = {pool.submit(query_sample, host, alias, self.cancel): key
                                   for key, alias in targets.items()
                                   if time.monotonic() >= retry_after.get(key, 0)}
                        for future in as_completed(futures):
                            if self.cancel.is_set():
                                break
                            key = futures[future]
                            try:
                                sample = future.result()
                                cpu = cpu_percent(previous.get(key), sample)
                                previous[key] = sample
                                self.result.emit((key, cpu, sample, ""))
                            except Exception as exc:
                                previous.pop(key, None)
                                retry_after[key] = time.monotonic() + 30
                                self.result.emit((key, None, None, str(exc)))
                                logging.getLogger(__name__).warning("节点 %s 资源查询失败：%s", key, exc)
                        if host is not None and not host._client.get_transport().is_active():
                            host.close()
                            host = None
                    except Exception as exc:
                        if not self.cancel.is_set():
                            for key in targets:
                                self.result.emit((key, None, None, str(exc)))
                            logging.getLogger(__name__).warning("资源监控连接失败：%s", exc)
                        if host is not None:
                            host.close()
                            host = None
                    self.cancel.wait(5)
        finally:
            if host is not None:
                host.close()
            self.password = None
