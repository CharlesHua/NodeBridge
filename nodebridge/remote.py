"""SSH connection and remote filesystem session."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import posixpath
import re
import shlex
import stat
import threading
import time
from concurrent.futures import CancelledError
from dataclasses import dataclass

import paramiko


@dataclass(frozen=True)
class NodeConfig:
    host: str
    port: int
    username: str
    key_file: str | None = None


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    is_symlink: bool
    size: int | None
    modified: int | None


@dataclass(frozen=True)
class DirectoryListing:
    path: str
    entries: tuple[RemoteEntry, ...]


@dataclass(frozen=True)
class NodeDiscovery:
    aliases: tuple[str, ...]
    ssh_config_aliases: tuple[str, ...]
    numbered_host_aliases: tuple[str, ...]
    hostname: str | None


class UnknownHostKeyError(Exception):
    pass


class VerifyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        digest = hashlib.sha256(key.asbytes()).digest()
        fingerprint = base64.b64encode(digest).decode("ascii").rstrip("=")
        raise UnknownHostKeyError(
            f"Unknown SSH host key for {hostname} ({key.get_name()}, SHA256:{fingerprint}). "
            "Verify it independently and add it to your OpenSSH known_hosts before connecting."
        )


class RemoteSession:
    def __init__(
        self,
        config: NodeConfig,
        client: paramiko.SSHClient | None,
        sftp: paramiko.SFTPClient | None,
        tunnel: paramiko.Channel | None = None,
        alias_route: tuple[str, ...] = (),
        root_client: paramiko.SSHClient | None = None,
    ):
        self.config = config
        self._client = client
        self._sftp = sftp
        self._tunnel = tunnel
        self._alias_route = alias_route
        self._root_client = root_client or client

    @classmethod
    def connect(cls, config: NodeConfig, password: str | None = None) -> "RemoteSession":
        return cls._connect(config, password)

    @classmethod
    def connect_shell_host(
        cls, config: NodeConfig, password: str | None = None
    ) -> "RemoteSession":
        """Authenticate a jump transport without consuming an SFTP session slot."""
        return cls._connect(config, password, open_sftp=False)

    @classmethod
    def connect_via(
        cls, config: NodeConfig, jump: "RemoteSession", password: str | None = None
    ) -> "RemoteSession":
        if jump._client is None:
            raise ConnectionError("此站点通过 SSH 别名连接；请继续使用别名中转。")
        transport = jump._client.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH jump host is no longer connected")
        tunnel = transport.open_channel(
            "direct-tcpip", (config.host, config.port), ("127.0.0.1", 0), timeout=10
        )
        return cls._connect(config, password, tunnel)

    @classmethod
    def connect_alias(cls, alias: str, jump: "RemoteSession") -> "RemoteSession":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
            raise ValueError("请输入 SSH 目标别名，例如 cft02。")
        root_client = jump._root_client
        transport = root_client.get_transport() if root_client is not None else None
        if transport is None or not transport.is_active():
            raise ConnectionError("当前 SSH 站点已断开。")
        route = (*jump._alias_route, alias)
        command = "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -s "
        command += f"{shlex.quote(route[-1])} sftp"
        for hop in reversed(route[:-1]):
            command = (
                "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "
                f"{shlex.quote(hop)} {shlex.quote(command)}"
            )
        channel = transport.open_session(timeout=10)
        try:
            channel.settimeout(30)
            channel.exec_command(command)
            sftp = paramiko.SFTPClient(channel)
            return cls(NodeConfig(alias, 22, ""), None, sftp, channel, route, root_client)
        except Exception as exc:
            detail = (
                channel.recv_stderr(4096).decode("utf-8", "replace").strip()
                if channel.recv_stderr_ready() else ""
            )
            channel.close()
            if detail:
                raise ConnectionError(f"SSH 别名 {alias} 连接失败：{detail}") from exc
            raise

    @classmethod
    def _connect(
        cls,
        config: NodeConfig,
        password: str | None,
        tunnel: paramiko.Channel | None = None,
        *,
        open_sftp: bool = True,
    ) -> "RemoteSession":
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(VerifyHostKey())
        try:
            client.connect(
                hostname=config.host,
                port=config.port,
                username=config.username,
                password=password or None,
                key_filename=config.key_file or None,
                look_for_keys=True,
                allow_agent=True,
                timeout=10,
                auth_timeout=15,
                banner_timeout=15,
                sock=tunnel,
            )
            sftp = client.open_sftp() if open_sftp else None
            return cls(config, client, sftp, tunnel)
        except Exception:
            client.close()
            if tunnel is not None:
                tunnel.close()
            raise

    def home(self) -> str:
        if self._sftp is None:
            raise ConnectionError("This SSH connection is reserved for terminal channels")
        return self._sftp.normalize(".")

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise ConnectionError("This SSH connection is reserved for terminal channels")
        return self._sftp

    @classmethod
    def open_alias_shell(cls, alias: str, jump: "RemoteSession", width: int = 100, height: int = 30) -> paramiko.Channel:
        """Open a Shell through the jump transport without creating a worker SFTP session."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
            raise ValueError("请输入 SSH 目标别名，例如 cft02。")
        root_client = jump._root_client
        transport = root_client.get_transport() if root_client is not None else None
        if transport is None or not transport.is_active():
            raise ConnectionError("跳板 SSH 连接已断开，无法打开终端。")
        route = (*jump._alias_route, alias)
        command = cls._alias_shell_command(route)
        channel = transport.open_session(timeout=10)
        try:
            channel.settimeout(20)
            channel.get_pty(term="xterm", width=width, height=height)
            channel.exec_command(command)
            channel.settimeout(0.2)
            return channel
        except Exception:
            channel.close()
            raise

    @staticmethod
    def _alias_shell_command(route: tuple[str, ...]) -> str:
        command = (
            "ssh -tt -o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o ConnectTimeout=10 {shlex.quote(route[-1])}"
        )
        for hop in reversed(route[:-1]):
            command = (
                "ssh -tt -o BatchMode=yes -o StrictHostKeyChecking=yes "
                f"-o ConnectTimeout=10 {shlex.quote(hop)} {shlex.quote(command)}"
            )
        return command

    def open_shell(self, width: int = 100, height: int = 30) -> paramiko.Channel:
        """Open a separate interactive PTY channel beside the SFTP channel."""
        root_client = self._root_client
        transport = root_client.get_transport() if root_client is not None else None
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH 连接已断开，无法打开终端。")
        channel = transport.open_session(timeout=10)
        try:
            channel.settimeout(20)
            channel.get_pty(term="xterm", width=width, height=height)
            if self._alias_route:
                channel.exec_command(self._alias_shell_command(self._alias_route))
            else:
                channel.invoke_shell()
            channel.settimeout(0.2)
            return channel
        except Exception:
            channel.close()
            raise

    def authentication_banner(self) -> str:
        """Return the server's pre-authentication notice, if one was sent."""
        transport = self._root_client.get_transport() if self._root_client is not None else None
        if transport is None:
            return ""
        banner = transport.get_banner()
        return banner if isinstance(banner, str) else ""

    def list_directory(self, path: str) -> DirectoryListing:
        normalized = self._sftp.normalize(path)
        entries = []
        for item in self._sftp.listdir_attr(normalized):
            mode = item.st_mode or 0
            entries.append(RemoteEntry(
                name=item.filename,
                path=posixpath.join(normalized, item.filename),
                is_dir=stat.S_ISDIR(mode),
                is_symlink=stat.S_ISLNK(mode),
                size=item.st_size,
                modified=item.st_mtime,
            ))
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        return DirectoryListing(normalized, tuple(entries))

    def discover_ssh_aliases(self) -> list[str]:
        """List explicit Host aliases in this server's user SSH configuration.

        The result is a list of candidates, not a reachability or trust check.
        Wildcard Host patterns cannot be enumerated.
        """
        ssh_dir = posixpath.join(self.home(), ".ssh")
        seen_files: set[str] = set()
        aliases: set[str] = set()

        def visit(path: str, base_dir: str) -> None:
            if path in seen_files or len(seen_files) >= 64:
                return
            seen_files.add(path)
            try:
                with self._sftp.open(path, "r") as stream:
                    raw = stream.read(1024 * 1024)
            except OSError:
                return
            lines = raw.decode("utf-8", "replace").splitlines() if isinstance(raw, bytes) else raw.splitlines()
            for line in lines:
                try:
                    words = shlex.split(line, comments=True)
                except ValueError:
                    continue
                if not words:
                    continue
                if "=" in words[0]:
                    keyword, value = words[0].split("=", 1)
                    words = [keyword, value, *words[1:]]
                else:
                    keyword = words[0]
                if keyword.casefold() == "host":
                    for alias in words[1:]:
                        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
                            aliases.add(alias)
                elif keyword.casefold() == "include":
                    for pattern in words[1:]:
                        if pattern.startswith("~/"):
                            pattern = posixpath.join(self.home(), pattern[2:])
                        elif not pattern.startswith("/"):
                            pattern = posixpath.join(base_dir, pattern)
                        folder, name = posixpath.split(pattern)
                        try:
                            filenames = self._sftp.listdir(folder)
                        except OSError:
                            continue
                        for filename in sorted(filenames):
                            if fnmatch.fnmatchcase(filename, name):
                                visit(posixpath.join(folder, filename), base_dir)

        visit(posixpath.join(ssh_dir, "config"), ssh_dir)
        visit("/etc/ssh/ssh_config", "/etc/ssh")
        return sorted(aliases, key=str.casefold)

    def _exec_text(self, command: str, timeout: int = 10) -> str:
        if self._client is None:
            return ""
        try:
            _stdin, stdout, _stderr = self._client.exec_command(command, timeout=timeout)
            raw = stdout.read(65536)
            stdout.close()
        except (OSError, paramiko.SSHException):
            return ""
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw

    def discover_work_nodes(self) -> NodeDiscovery:
        """Find explicit SSH aliases and numbered hosts resolvable on this jump host.

        DNS/hosts matches are candidates only; the SSH connection verifies each
        target's host key and authentication separately.
        """
        config_aliases = self.discover_ssh_aliases()
        hostname = self._exec_text("hostname -s").strip().splitlines()
        short_name = hostname[0].strip() if hostname else None
        numbered: set[str] = set()
        try:
            with self._sftp.open("/etc/hosts", "r") as stream:
                raw = stream.read(1024 * 1024)
            hosts_text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        except OSError:
            hosts_text = ""
        for line in hosts_text.splitlines():
            fields = line.partition("#")[0].split()
            if len(fields) < 2 or fields[0].startswith(("127.", "::1")):
                continue
            numbered.update(
                name for name in fields[1:]
                if name != short_name and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*[0-9]{1,3}", name)
            )

        # If the hosts file has no numbered inventory, try names sharing the
        # jump host's prefix. A resolver cannot enumerate arbitrary DNS names.
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9._-]*?)([0-9]{1,3})", short_name or "")
        if not numbered and match:
            prefix, digits = match.groups()
            width = len(digits)
            maximum = min(99, max(40, int(digits) + 40))
            candidate_names = {
                f"{prefix}{number:0{width}d}"
                for number in range(1, maximum + 1)
                if number != int(digits)
            }
            to_probe = sorted(candidate_names - numbered, key=lambda name: int(name[len(prefix):]))
            if to_probe:
                script = "for name in " + " ".join(map(shlex.quote, to_probe))
                script += '; do if getent hosts "$name" >/dev/null 2>&1; then printf "%s\\n" "$name"; fi; done'
                output = self._exec_text(f"timeout 20s sh -c {shlex.quote(script)}", timeout=25)
                numbered.update(name for name in output.splitlines() if name in candidate_names)
        aliases = sorted(set(config_aliases).union(numbered), key=str.casefold)
        return NodeDiscovery(tuple(aliases), tuple(config_aliases), tuple(sorted(numbered, key=str.casefold)), short_name)

    def probe_alias(self, alias: str, *, timeout: float = 8.0) -> tuple[str, str]:
        """Test passwordless SSH from the jump host without trusting new keys."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", alias):
            raise ValueError("无效的 SSH 节点别名。")
        if self._client is None:
            raise ConnectionError("跳板节点不可用。")
        command = (
            "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
            "-o ConnectTimeout=4 -o ConnectionAttempts=1 "
            f"{shlex.quote(alias)} true"
        )
        _stdin, stdout, _stderr = self._client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        deadline = time.monotonic() + timeout
        error_output = bytearray()
        try:
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    channel.recv(4096)
                if channel.recv_stderr_ready() and len(error_output) < 2048:
                    error_output.extend(channel.recv_stderr(2048 - len(error_output)))
                if time.monotonic() >= deadline:
                    return "unreachable", "SSH 探测超时"
                time.sleep(0.05)
            while channel.recv_stderr_ready() and len(error_output) < 2048:
                error_output.extend(channel.recv_stderr(2048 - len(error_output)))
            code = channel.recv_exit_status()
            if code == 0:
                return "reachable", "SSH 免密连接成功"
            detail = error_output.decode("utf-8", "replace").strip() or f"SSH 返回状态 {code}"
            lowered = detail.casefold()
            if "host key verification failed" in lowered or "unknown host key" in lowered:
                return "untrusted", detail
            if "permission denied" in lowered:
                return "auth", detail
            return "unreachable", detail
        finally:
            channel.close()

    def sha256_file(
        self, path: str, *, timeout: float = 3600.0,
        command_client: paramiko.SSHClient | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Calculate a file's SHA-256 on its SSH host; return only the digest."""
        if not path.startswith("/"):
            raise ValueError("SHA-256 校验要求绝对路径。")
        output, errors, code = self._sha256_command_output(
            f"LC_ALL=C sha256sum -- {shlex.quote(path)}", timeout=timeout,
            command_client=command_client, cancel_event=cancel_event, max_output=8192,
        )
        if code != 0:
            detail = errors.decode("utf-8", "replace").strip() or f"退出状态 {code}"
            if "No such file or directory" in detail:
                raise FileNotFoundError(path)
            raise OSError(f"远程 sha256sum 失败：{detail}")
        match = re.search(rb"(?:^|\n)\\?([0-9a-fA-F]{64}) [ *]", output)
        if match is None:
            raise OSError("远程 sha256sum 未返回有效的 SHA-256。")
        return match.group(1).decode("ascii").lower()

    def sha256_files(
        self, paths: list[str], *, timeout: float = 3600.0,
        command_client: paramiko.SSHClient | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, str]:
        """Hash a bounded group in one remote command, with NUL-delimited names."""
        if not paths:
            return {}
        if any(not path.startswith("/") for path in paths):
            raise ValueError("SHA-256 校验要求绝对路径。")
        command = "LC_ALL=C sha256sum -z -- " + " ".join(shlex.quote(path) for path in paths)
        max_output = max(8192, sum(len(path.encode("utf-8")) + 68 for path in paths) + 1024)
        output, errors, code = self._sha256_command_output(
            command, timeout=timeout, command_client=command_client,
            cancel_event=cancel_event, max_output=max_output,
        )
        if code != 0:
            detail = errors.decode("utf-8", "replace").strip() or f"退出状态 {code}"
            raise OSError(f"远程 sha256sum 失败：{detail}")
        records = output.split(b"\0")
        if len(records) != len(paths) + 1 or records[-1]:
            raise OSError("远程 sha256sum 返回的文件数量与请求不符。")
        result = {}
        for index, (path, record) in enumerate(zip(paths, records[:-1])):
            if index == 0:
                start = re.search(rb"(?:^|\n)(?=[0-9a-fA-F]{64} [ *])", record)
                if start is not None:
                    record = record[start.end():]
            match = re.fullmatch(rb"([0-9a-fA-F]{64}) [ *](.*)", record, re.DOTALL)
            if match is None or match.group(2) != path.encode("utf-8"):
                raise OSError("远程 sha256sum 返回了无法对应的文件名或摘要。")
            result[path] = match.group(1).decode("ascii").lower()
        return result

    def _sha256_command_output(
        self, command: str, *, timeout: float,
        command_client: paramiko.SSHClient | None,
        cancel_event: threading.Event | None, max_output: int,
    ) -> tuple[bytes, bytes, int]:
        client = command_client or self._root_client
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        transport = client.get_transport() if client is not None else None
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH 连接已断开，无法计算远程 SHA-256。")
        for alias in reversed(self._alias_route):
            command = (
                "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
                f"-o ConnectTimeout=10 {shlex.quote(alias)} {shlex.quote(command)}"
            )
        try:
            channel = transport.open_session(timeout=10)
        except paramiko.ChannelException as exc:
            raise OSError(f"跳板 SSH 拒绝打开核对通道，可能达到会话上限：{exc}") from exc
        output = bytearray()
        errors = bytearray()
        deadline = time.monotonic() + timeout
        try:
            channel.exec_command(command)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()
                if channel.recv_ready():
                    chunk = channel.recv(4096)
                    output.extend(chunk)
                    if len(output) > max_output:
                        raise OSError("远程 sha256sum 输出超过预期长度。")
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(4096)
                    errors.extend(chunk[:max(0, 8192 - len(errors))])
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"远程 SHA-256 计算超时：{path}")
                time.sleep(0.05)
            code = channel.recv_exit_status()
            return bytes(output), bytes(errors), code
        finally:
            channel.close()

    def close(self) -> None:
        try:
            if self._sftp is not None:
                self._sftp.close()
        finally:
            try:
                if self._client is not None:
                    self._client.close()
            finally:
                if self._tunnel is not None:
                    self._tunnel.close()

    def open_sftp_peer(self) -> "RemoteSession":
        """Open an independently owned SFTP channel for a file transfer."""
        root = self._root_client
        transport = root.get_transport() if root is not None else None
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH 连接已断开，无法开始文件传输。")
        if not self._alias_route:
            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:
                raise ConnectionError("无法打开独立的 SFTP 传输通道。")
            return RemoteSession(self.config, None, sftp)
        command = (
            "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o ConnectTimeout=10 -s {shlex.quote(self._alias_route[-1])} sftp"
        )
        for hop in reversed(self._alias_route[:-1]):
            command = (
                "ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "
                f"-o ConnectTimeout=10 {shlex.quote(hop)} {shlex.quote(command)}"
            )
        channel = transport.open_session(timeout=10)
        try:
            channel.settimeout(30)
            channel.exec_command(command)
            sftp = paramiko.SFTPClient(channel)
            return RemoteSession(self.config, None, sftp, channel)
        except Exception:
            channel.close()
            raise
