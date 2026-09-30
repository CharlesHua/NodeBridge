"""Explicit local and SFTP file management operations."""

from __future__ import annotations

import errno
import posixpath
import re
import stat
from pathlib import Path

from PySide6.QtCore import QFile

from nodebridge.remote import RemoteSession


def _name(name: str, *, local: bool) -> str:
    name = name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("请输入不含路径分隔符的名称。")
    if local:
        stem = name.split(".", 1)[0].upper()
        if (any(character in name for character in '<>:"|?*')
                or name.endswith((" ", "."))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", stem)):
            raise ValueError("文件名不符合 Windows 命名规则。")
    return name


def _remote_lstat(sftp, path: str):
    try:
        return sftp.lstat(path)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT:
            return None
        raise


def create_remote_directory(session: RemoteSession, parent: str, name: str) -> str:
    target = posixpath.join(parent, _name(name, local=False))
    session.sftp.mkdir(target)
    return target


def create_local_directory(parent: str, name: str) -> Path:
    target = Path(parent) / _name(name, local=True)
    target.mkdir()
    return target


def rename_remote(session: RemoteSession, source: str, name: str) -> str:
    target = posixpath.join(posixpath.dirname(source), _name(name, local=False))
    if target == source:
        return target
    if _remote_lstat(session.sftp, target) is not None:
        raise FileExistsError(f"远程目标已存在：{target}")
    session.sftp.rename(source, target)
    return target


def rename_local(source: str, name: str) -> Path:
    path = Path(source)
    target = path.with_name(_name(name, local=True))
    if target == path:
        return target
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"本地目标已存在：{target}")
    return path.rename(target)


def delete_remote(session: RemoteSession, paths: list[str]) -> int:
    sftp = session.sftp
    count = 0

    def delete_one(path: str) -> None:
        nonlocal count
        mode = sftp.lstat(path).st_mode or 0
        if stat.S_ISDIR(mode):
            for item in sftp.listdir_attr(path):
                name = item.filename
                if name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError(f"远程目录返回了不安全的名称：{name!r}")
                delete_one(posixpath.join(path, name))
            sftp.rmdir(path)
        else:
            sftp.remove(path)
        count += 1

    for path in paths:
        if path == "/":
            raise ValueError("不能删除远程根目录。")
        delete_one(path)
    return count


def trash_local(paths: list[str]) -> int:
    for path in paths:
        if not QFile.moveToTrash(path)[0]:
            raise OSError(f"无法移入回收站：{path}")
    return len(paths)
