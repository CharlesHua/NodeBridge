"""SSH connection and remote filesystem session."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import posixpath
import re
import shlex
import stat
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
