"""SSH connection and remote filesystem session."""

from __future__ import annotations

import base64
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
        sftp: paramiko.SFTPClient,
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
            sftp = client.open_sftp()
            return cls(config, client, sftp, tunnel)
        except Exception:
            client.close()
            if tunnel is not None:
                tunnel.close()
            raise

    def home(self) -> str:
        return self._sftp.normalize(".")

    @property
    def sftp(self) -> paramiko.SFTPClient:
        return self._sftp

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

    def close(self) -> None:
        try:
            self._sftp.close()
        finally:
            try:
                if self._client is not None:
                    self._client.close()
            finally:
                if self._tunnel is not None:
                    self._tunnel.close()
