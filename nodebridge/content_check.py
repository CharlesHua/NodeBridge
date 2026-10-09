"""Compare selected regular files at the same absolute path on several nodes."""

from __future__ import annotations

import errno
import posixpath
import stat
import threading
from concurrent.futures import CancelledError
from concurrent.futures import ThreadPoolExecutor, as_completed


def hash_remote_file(
    session, path: str, channel_gate: threading.Semaphore | None = None,
    command_client=None, cancel_event: threading.Event | None = None,
) -> tuple[str, str]:
    """Return the digest computed on the remote node, or an error state."""
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        info = session.sftp.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return "unsupported", "不是普通文件"
        def calculate():
            if command_client is None:
                return session.sha256_file(path)
            if cancel_event is not None:
                return session.sha256_file(
                    path, command_client=command_client, cancel_event=cancel_event,
                )
            return session.sha256_file(path, command_client=command_client)

        if channel_gate is None:
            digest = calculate()
        else:
            with channel_gate:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()
                digest = calculate()
        after = session.sftp.lstat(path)
        if any(getattr(info, key, None) != getattr(after, key, None)
               for key in ("st_size", "st_mtime")):
            return "changed", "读取期间文件大小或修改时间发生变化"
        return "ok", digest
    except CancelledError:
        raise
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT:
            return "missing", "文件不存在"
        return "error", f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {exc}"


def compare_remote_files(
    sessions: dict, directory: str, names: list[str], *, command_client=None,
) -> list[dict]:
    """Hash selected names on every connected node and return a JSON-ready result list."""
    if not directory.startswith("/"):
        raise ValueError("必须提供绝对目录路径。")
    if not sessions:
        raise ValueError("没有可检查的节点。")
    names = list(dict.fromkeys(names))
    if any(not name or name in {".", ".."} or "/" in name for name in names):
        raise ValueError("包含无效文件名。")
    hashes: dict[str, dict[str, tuple[str, str]]] = {name: {} for name in names}
    gates = {id(command_client or getattr(session, "_root_client", session)): threading.Semaphore(1)
             for session in sessions.values()}

    def check_node(session):
        gate = gates[id(command_client or getattr(session, "_root_client", session))]
        return {name: hash_remote_file(session, posixpath.join(directory, name), gate,
                                       command_client)
                for name in names}

    with ThreadPoolExecutor(max_workers=min(4, len(sessions))) as pool:
        futures = {pool.submit(check_node, session): alias for alias, session in sessions.items()}
        for future in as_completed(futures):
            alias = futures[future]
            for name, value in future.result().items():
                hashes[name][alias] = value
    return _summarize(hashes, names, sessions)


def _summarize(hashes: dict, names: list[str], aliases) -> list[dict]:
    results = []
    for name in names:
        nodes = {alias: {"state": state, "detail": detail}
                 for alias in sorted(aliases, key=str.casefold)
                 for state, detail in [hashes[name][alias]]}
        states = [item["state"] for item in nodes.values()]
        values = [item["detail"] for item in nodes.values() if item["state"] == "ok"]
        if any(state == "error" for state in states):
            result = "error"
        elif any(state == "changed" for state in states):
            result = "changed"
        elif any(state == "missing" for state in states):
            result = "missing"
        elif any(state == "unsupported" for state in states):
            result = "unsupported"
        else:
            result = "same" if len(set(values)) == 1 else "different"
        results.append({"name": name, "result": result, "nodes": nodes})
    return results


def compare_remote_files_isolated(
    jump, aliases: list[str], directory: str, names: list[str], *,
    cancel_event: threading.Event | None = None, progress=None,
    snapshots: dict | None = None,
) -> list[dict]:
    """Check files through short-lived SFTP sessions, separate from browsing sessions."""
    from nodebridge.remote import RemoteSession

    if not directory.startswith("/"):
        raise ValueError("必须提供绝对目录路径。")
    if any(not name or name in {".", ".."} or "/" in name for name in names):
        raise ValueError("包含无效文件名。")
    aliases = list(dict.fromkeys(aliases))
    names = list(dict.fromkeys(names))
    if not aliases:
        return []
    hashes: dict[str, dict[str, tuple[str, str]]] = {name: {} for name in names}
    gate = threading.Semaphore(2)  # Dedicated jump transport; never exceed two hash channels.

    def check_node(alias):
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        try:
            session = RemoteSession.connect_alias(alias, jump)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return {name: ("error", detail) for name in names}
        try:
            values = {}
            if snapshots is not None:
                before = snapshots[alias]
                for offset in range(0, len(names), 32):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError()
                    group = names[offset:offset + 32]
                    paths = [posixpath.join(directory, name) for name in group]
                    try:
                        with gate:
                            digests = session.sha256_files(
                                paths, command_client=jump._client,
                                cancel_event=cancel_event,
                            )
                        values.update({name: ("ok", digests[path])
                                       for name, path in zip(group, paths)})
                    except CancelledError:
                        raise
                    except Exception as exc:
                        detail = f"{type(exc).__name__}: {exc}"
                        values.update({name: ("error", detail) for name in group})
                if any(state == "ok" for state, _detail in values.values()):
                    try:
                        if len(names) == 1:
                            name = names[0]
                            info = session.sftp.lstat(posixpath.join(directory, name))
                            old = before[name]
                            if (not stat.S_ISREG(info.st_mode)
                                    or info.st_size != old.size
                                    or info.st_mtime != old.modified):
                                values[name] = ("changed", "校验期间文件类型、大小或修改时间发生变化")
                            return values
                        after = {entry.name: entry
                                 for entry in session.list_directory(directory).entries}
                    except FileNotFoundError:
                        return {name: ("changed", "校验后文件已消失") for name in names}
                    except Exception as exc:
                        detail = f"{type(exc).__name__}: {exc}"
                        return {name: ("error", detail) for name in names}
                    for name in names:
                        if values[name][0] != "ok":
                            continue
                        old, current = before[name], after.get(name)
                        if current is None:
                            values[name] = ("changed", "校验后文件已消失")
                        elif (current.is_dir or current.is_symlink
                              or current.size != old.size
                              or current.modified != old.modified):
                            values[name] = ("changed", "校验期间文件类型、大小或修改时间发生变化")
                return values
            for name in names:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()
                values[name] = hash_remote_file(
                    session, posixpath.join(directory, name), gate,
                    command_client=jump._client, cancel_event=cancel_event,
                )
            return values
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=min(2, len(aliases))) as pool:
        futures = {pool.submit(check_node, alias): alias for alias in aliases}
        completed = 0
        for future in as_completed(futures):
            alias = futures[future]
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError()
            try:
                values = future.result()
            except CancelledError:
                raise
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                values = {name: ("error", detail) for name in names}
            for name, value in values.items():
                hashes[name][alias] = value
            completed += 1
            if progress is not None:
                progress(completed, len(aliases))
    return _summarize(hashes, names, aliases)
