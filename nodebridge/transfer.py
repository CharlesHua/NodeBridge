"""Copy files and directories among local paths and an SFTP session."""

from __future__ import annotations

import errno
import ntpath
import os
import posixpath
import stat
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from nodebridge.remote import RemoteSession


CHUNK_SIZE = 1024 * 1024
Progress = Callable[[str], None]


class ConflictAction(Enum):
    OVERWRITE = "overwrite"
    SKIP = "skip"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ConflictInfo:
    source_path: str
    source_size: int | None
    source_modified: float | None
    target_path: str
    target_size: int | None
    target_modified: float | None


ResolveConflict = Callable[[ConflictInfo], ConflictAction]


@dataclass(frozen=True)
class CopyItem:
    source: str | Path
    parts: tuple[str, ...]
    is_dir: bool
    modified: float | None = None


@dataclass(frozen=True)
class CopyResult:
    files: int
    directories: int
    bytes_copied: int
    skipped: int = 0
    cancelled: bool = False


def _valid_name(name: str) -> None:
    if not name or name in {".", ".."} or any(c in name for c in '/\\<>:"|?*'):
        raise ValueError(f"无法安全复制文件名：{name!r}")
    if name != name.rstrip(" .") or ntpath.basename(name).upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    } or any(ord(c) < 32 for c in name):
        raise ValueError(f"Windows 不支持此文件名：{name!r}")


def _remote_stat(sftp, path: str):
    try:
        return sftp.lstat(path)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT:
            return None
        raise


def _local_stat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _decision(info: ConflictInfo, resolver: ResolveConflict | None) -> ConflictAction:
    if resolver is None:
        raise FileExistsError(f"目标文件已存在：{info.target_path}")
    action = resolver(info)
    if not isinstance(action, ConflictAction):
        raise ValueError("无效的同名文件处理操作。")
    return action


def _copy_stream(reader, writer) -> int:
    count = 0
    while chunk := reader.read(CHUNK_SIZE):
        writer.write(chunk)
        count += len(chunk)
    return count


def _same_version(current, approved) -> bool:
    if current is None or approved is None:
        return False
    timestamp = "st_mtime_ns" if hasattr(current, "st_mtime_ns") and hasattr(approved, "st_mtime_ns") else "st_mtime"
    return (
        current.st_size == approved.st_size
        and getattr(current, timestamp) == getattr(approved, timestamp)
        and stat.S_IFMT(current.st_mode or 0) == stat.S_IFMT(approved.st_mode or 0)
    )


def _upload_file(sftp, source: Path, target: str, approved_target=None) -> int:
    overwrite = approved_target is not None
    output = (
        posixpath.join(posixpath.dirname(target), f".nodebridge-{uuid.uuid4().hex}.tmp")
        if overwrite else target
    )
    created = False
    try:
        with source.open("rb") as reader, sftp.open(output, "wbx") as writer:
            created = True
            count = _copy_stream(reader, writer)
        if overwrite:
            if not _same_version(_remote_stat(sftp, target), approved_target):
                raise FileExistsError(f"目标文件在确认后发生变化，未覆盖：{target}")
            sftp.posix_rename(output, target)
        return count
    except Exception:
        if created:
            with suppress(OSError):
                sftp.remove(output)
        raise


def _copy_remote_file(sftp, source: str, target: str, approved_target=None) -> int:
    overwrite = approved_target is not None
    output = (
        posixpath.join(posixpath.dirname(target), f".nodebridge-{uuid.uuid4().hex}.tmp")
        if overwrite else target
    )
    created = False
    try:
        with sftp.open(source, "rb") as reader, sftp.open(output, "wbx") as writer:
            created = True
            count = _copy_stream(reader, writer)
        if overwrite:
            if not _same_version(_remote_stat(sftp, target), approved_target):
                raise FileExistsError(f"目标文件在确认后发生变化，未覆盖：{target}")
            sftp.posix_rename(output, target)
        return count
    except Exception:
        if created:
            with suppress(OSError):
                sftp.remove(output)
        raise


def _copy_between_remote_files(source_sftp, target_sftp, source: str, target: str, approved_target=None) -> int:
    overwrite = approved_target is not None
    output = (
        posixpath.join(posixpath.dirname(target), f".nodebridge-{uuid.uuid4().hex}.tmp")
        if overwrite else target
    )
    created = False
    try:
        with source_sftp.open(source, "rb") as reader, target_sftp.open(output, "wbx") as writer:
            created = True
            count = _copy_stream(reader, writer)
        if overwrite:
            if not _same_version(_remote_stat(target_sftp, target), approved_target):
                raise FileExistsError(f"目标文件在确认后发生变化，未覆盖：{target}")
            target_sftp.posix_rename(output, target)
        return count
    except Exception:
        if created:
            with suppress(OSError):
                target_sftp.remove(output)
        raise


def _download_file(source, target: Path, approved_target=None) -> int:
    overwrite = approved_target is not None
    temporary = None
    created = False
    try:
        with source as reader:
            if overwrite:
                fd, name = tempfile.mkstemp(prefix=".nodebridge-", dir=target.parent)
                temporary = Path(name)
                writer = os.fdopen(fd, "wb")
            else:
                writer = target.open("xb")
                created = True
            with writer:
                count = _copy_stream(reader, writer)
        if overwrite:
            if not _same_version(_local_stat(target), approved_target):
                raise FileExistsError(f"目标文件在确认后发生变化，未覆盖：{target}")
            os.replace(temporary, target)
        return count
    except Exception:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
        elif created:
            with suppress(OSError):
                target.unlink()
        raise


def _scan_local(path: Path, parts: tuple[str, ...]) -> list[CopyItem]:
    _valid_name(parts[-1])
    source_stat = path.lstat()
    mode = source_stat.st_mode
    if stat.S_ISLNK(mode):
        raise ValueError(f"暂不复制符号链接：{path}")
    if stat.S_ISREG(mode):
        return [CopyItem(path, parts, False, source_stat.st_mtime)]
    if not stat.S_ISDIR(mode):
        raise ValueError(f"暂不复制特殊文件：{path}")
    result = [CopyItem(path, parts, True)]
    for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        result.extend(_scan_local(child, (*parts, child.name)))
    return result


def _scan_remote(sftp, path: str, parts: tuple[str, ...]) -> list[CopyItem]:
    _valid_name(parts[-1])
    source_stat = sftp.lstat(path)
    mode = source_stat.st_mode or 0
    if stat.S_ISLNK(mode):
        raise ValueError(f"暂不复制符号链接：{path}")
    if stat.S_ISREG(mode):
        return [CopyItem(path, parts, False, source_stat.st_mtime)]
    if not stat.S_ISDIR(mode):
        raise ValueError(f"暂不复制特殊文件：{path}")
    result = [CopyItem(path, parts, True)]
    children = sorted(sftp.listdir_attr(path), key=lambda item: item.filename.casefold())
    if len({entry.filename.casefold() for entry in children}) != len(children):
        raise FileExistsError(f"目录中存在仅大小写不同的同名文件，无法安全复制到 Windows：{path}")
    for entry in children:
        child = entry.filename
        _valid_name(child)
        result.extend(_scan_remote(sftp, posixpath.join(path, child), (*parts, child)))
    return result


def _roots(sources: Iterable[str], source_remote: bool, destination_local: bool) -> list[str]:
    roots = list(dict.fromkeys(sources))
    if not roots:
        raise ValueError("没有选中需要复制的文件。")
    names = [posixpath.basename(path.rstrip("/")) if source_remote else Path(path).name for path in roots]
    compare = [name.casefold() if destination_local else name for name in names]
    if len(set(compare)) != len(compare):
        raise FileExistsError("所选文件中存在同名项，无法安全复制。")
    for name in names:
        _valid_name(name)
    return roots


def copy_local_to_remote(
    session: RemoteSession, sources: Iterable[str], destination: str,
    progress: Progress = lambda _path: None,
    resolve_conflict: ResolveConflict | None = None,
) -> CopyResult:
    sftp = session.sftp
    roots = _roots(sources, source_remote=False, destination_local=False)
    if not stat.S_ISDIR(sftp.stat(destination).st_mode or 0):
        raise NotADirectoryError(destination)
    items: list[CopyItem] = []
    for source in roots:
        path = Path(source).absolute()
        items.extend(_scan_local(path, (path.name,)))
    files = directories = bytes_copied = skipped = 0
    for item in items:
        target = posixpath.join(destination, *item.parts)
        progress(str(item.source))
        existing = _remote_stat(sftp, target)
        if item.is_dir:
            if existing is None:
                sftp.mkdir(target)
                directories += 1
            elif not stat.S_ISDIR(existing.st_mode or 0):
                raise FileExistsError(f"目标已有同名文件，无法合并文件夹：{target}")
        else:
            approved_target = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode or 0):
                    raise FileExistsError(f"目标不是普通文件，无法覆盖：{target}")
                source_stat = Path(item.source).lstat()
                action = _decision(ConflictInfo(
                    str(item.source), source_stat.st_size, source_stat.st_mtime,
                    target, existing.st_size, existing.st_mtime,
                ), resolve_conflict)
                if action is ConflictAction.SKIP:
                    skipped += 1
                    continue
                if action is ConflictAction.CANCEL:
                    return CopyResult(files, directories, bytes_copied, skipped, True)
                approved_target = existing
            bytes_copied += _upload_file(sftp, Path(item.source), target, approved_target)
            files += 1
    return CopyResult(files, directories, bytes_copied, skipped)


def copy_remote_to_local(
    session: RemoteSession, sources: Iterable[str], destination: str | Path,
    progress: Progress = lambda _path: None,
    resolve_conflict: ResolveConflict | None = None,
) -> CopyResult:
    sftp = session.sftp
    target_root = Path(destination)
    if not target_root.is_dir():
        raise NotADirectoryError(target_root)
    roots = _roots(sources, source_remote=True, destination_local=True)
    items: list[CopyItem] = []
    for source in roots:
        name = posixpath.basename(source.rstrip("/"))
        items.extend(_scan_remote(sftp, source, (name,)))
    files = directories = bytes_copied = skipped = 0
    for item in items:
        target = target_root.joinpath(*item.parts)
        progress(str(item.source))
        existing = _local_stat(target)
        if item.is_dir:
            if existing is None:
                target.mkdir()
                directories += 1
            elif not stat.S_ISDIR(existing.st_mode):
                raise FileExistsError(f"目标已有同名文件，无法合并文件夹：{target}")
        else:
            approved_target = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise FileExistsError(f"目标不是普通文件，无法覆盖：{target}")
                source_stat = sftp.lstat(str(item.source))
                action = _decision(ConflictInfo(
                    str(item.source), source_stat.st_size, source_stat.st_mtime,
                    str(target), existing.st_size, existing.st_mtime,
                ), resolve_conflict)
                if action is ConflictAction.SKIP:
                    skipped += 1
                    continue
                if action is ConflictAction.CANCEL:
                    return CopyResult(files, directories, bytes_copied, skipped, True)
                approved_target = existing
            bytes_copied += _download_file(sftp.open(str(item.source), "rb"), target, approved_target)
            if item.modified is not None:
                with suppress(OSError, OverflowError, ValueError):
                    os.utime(target, (item.modified, item.modified))
            files += 1
    return CopyResult(files, directories, bytes_copied, skipped)


def copy_remote_to_remote(
    session: RemoteSession, sources: Iterable[str], destination: str,
    progress: Progress = lambda _path: None,
    resolve_conflict: ResolveConflict | None = None,
) -> CopyResult:
    sftp = session.sftp
    if not stat.S_ISDIR(sftp.stat(destination).st_mode or 0):
        raise NotADirectoryError(destination)
    roots = _roots(sources, source_remote=True, destination_local=False)
    items: list[CopyItem] = []
    for source in roots:
        name = posixpath.basename(source.rstrip("/"))
        if destination == source or destination.startswith(source.rstrip("/") + "/"):
            raise ValueError(f"不能把目录复制到自身或其子目录：{source}")
        if posixpath.join(destination, name) == source:
            raise ValueError(f"源文件和目标文件相同：{source}")
        items.extend(_scan_remote(sftp, source, (name,)))
    files = directories = bytes_copied = skipped = 0
    for item in items:
        target = posixpath.join(destination, *item.parts)
        progress(str(item.source))
        existing = _remote_stat(sftp, target)
        if item.is_dir:
            if existing is None:
                sftp.mkdir(target)
                directories += 1
            elif not stat.S_ISDIR(existing.st_mode or 0):
                raise FileExistsError(f"目标已有同名文件，无法合并文件夹：{target}")
        else:
            approved_target = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode or 0):
                    raise FileExistsError(f"目标不是普通文件，无法覆盖：{target}")
                source_stat = sftp.lstat(str(item.source))
                action = _decision(ConflictInfo(
                    str(item.source), source_stat.st_size, source_stat.st_mtime,
                    target, existing.st_size, existing.st_mtime,
                ), resolve_conflict)
                if action is ConflictAction.SKIP:
                    skipped += 1
                    continue
                if action is ConflictAction.CANCEL:
                    return CopyResult(files, directories, bytes_copied, skipped, True)
                approved_target = existing
            bytes_copied += _copy_remote_file(sftp, str(item.source), target, approved_target)
            files += 1
    return CopyResult(files, directories, bytes_copied, skipped)


def copy_remote_between_sessions(
    source_session: RemoteSession, target_session: RemoteSession,
    sources: Iterable[str], destination: str,
    progress: Progress = lambda _path: None,
    resolve_conflict: ResolveConflict | None = None,
) -> CopyResult:
    """Stream a remote tree between two independent SFTP sessions."""
    if source_session is target_session:
        return copy_remote_to_remote(source_session, sources, destination, progress, resolve_conflict)
    source_sftp, target_sftp = source_session.sftp, target_session.sftp
    if not stat.S_ISDIR(target_sftp.stat(destination).st_mode or 0):
        raise NotADirectoryError(destination)
    roots = _roots(sources, source_remote=True, destination_local=False)
    items: list[CopyItem] = []
    for source in roots:
        name = posixpath.basename(source.rstrip("/"))
        items.extend(_scan_remote(source_sftp, source, (name,)))
    files = directories = bytes_copied = skipped = 0
    for item in items:
        target = posixpath.join(destination, *item.parts)
        progress(str(item.source))
        existing = _remote_stat(target_sftp, target)
        if item.is_dir:
            if existing is None:
                target_sftp.mkdir(target)
                directories += 1
            elif not stat.S_ISDIR(existing.st_mode or 0):
                raise FileExistsError(f"目标已有同名文件，无法合并文件夹：{target}")
            continue
        approved_target = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode or 0):
                raise FileExistsError(f"目标不是普通文件，无法覆盖：{target}")
            source_stat = source_sftp.lstat(str(item.source))
            action = _decision(ConflictInfo(
                str(item.source), source_stat.st_size, source_stat.st_mtime,
                target, existing.st_size, existing.st_mtime,
            ), resolve_conflict)
            if action is ConflictAction.SKIP:
                skipped += 1
                continue
            if action is ConflictAction.CANCEL:
                return CopyResult(files, directories, bytes_copied, skipped, True)
            approved_target = existing
        bytes_copied += _copy_between_remote_files(
            source_sftp, target_sftp, str(item.source), target, approved_target
        )
        files += 1
    return CopyResult(files, directories, bytes_copied, skipped)


def copy_local_to_local(
    sources: Iterable[str], destination: str | Path,
    progress: Progress = lambda _path: None,
    resolve_conflict: ResolveConflict | None = None,
    source_labels: dict[str, str] | None = None,
) -> CopyResult:
    target_root = Path(destination)
    if not target_root.is_dir():
        raise NotADirectoryError(target_root)
    roots = _roots(sources, source_remote=False, destination_local=True)
    items: list[CopyItem] = []
    for source in roots:
        path = Path(source)
        target = target_root / path.name
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(target)):
            raise ValueError(f"源文件和目标文件相同：{path}")
        if path.is_dir() and os.path.normcase(os.path.abspath(target_root)).startswith(
            os.path.normcase(os.path.abspath(path)) + os.sep
        ):
            raise ValueError(f"不能把目录复制到自身或其子目录：{path}")
        items.extend(_scan_local(path, (path.name,)))
    files = directories = bytes_copied = skipped = 0
    for item in items:
        target = target_root.joinpath(*item.parts)
        progress(str(item.source))
        existing = _local_stat(target)
        if item.is_dir:
            if existing is None:
                target.mkdir()
                directories += 1
            elif not stat.S_ISDIR(existing.st_mode):
                raise FileExistsError(f"目标已有同名文件，无法合并文件夹：{target}")
        else:
            approved_target = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise FileExistsError(f"目标不是普通文件，无法覆盖：{target}")
                source_stat = Path(item.source).lstat()
                label = str(item.source)
                if source_labels and item.parts[0] in source_labels:
                    label = posixpath.join(source_labels[item.parts[0]], *item.parts[1:])
                action = _decision(ConflictInfo(
                    label, source_stat.st_size, source_stat.st_mtime,
                    str(target), existing.st_size, existing.st_mtime,
                ), resolve_conflict)
                if action is ConflictAction.SKIP:
                    skipped += 1
                    continue
                if action is ConflictAction.CANCEL:
                    return CopyResult(files, directories, bytes_copied, skipped, True)
                approved_target = existing
            bytes_copied += _download_file(Path(item.source).open("rb"), target, approved_target)
            if item.modified is not None:
                with suppress(OSError, OverflowError, ValueError):
                    os.utime(target, (item.modified, item.modified))
            files += 1
    return CopyResult(files, directories, bytes_copied, skipped)
