"""Bounded parallel execution and per-node results for work-node operations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import posixpath
import stat
from typing import Callable, Mapping, TypeVar

from nodebridge.transfer import CopyResult
from nodebridge.remote import RemoteSession


T = TypeVar("T")


@dataclass(frozen=True)
class BatchResult:
    alias: str
    copied: CopyResult | None = None
    deleted: int = 0
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None and (
            self.copied is None or not self.copied.cancelled and self.copied.skipped == 0
        )


def run_parallel(
    nodes: Mapping[str, T],
    operation: Callable[[str, T], BatchResult],
    on_result: Callable[[BatchResult], None] = lambda _result: None,
    max_workers: int = 4,
) -> list[BatchResult]:
    """Run independent node operations, retaining errors instead of hiding peers' results."""
    if max_workers < 1:
        raise ValueError("并发数至少为 1。")
    if not nodes:
        raise ValueError("没有选择已连接的工作节点。")
    results: dict[str, BatchResult] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(nodes))) as pool:
        futures = {pool.submit(operation, alias, node): alias for alias, node in nodes.items()}
        for future in as_completed(futures):
            alias = futures[future]
            try:
                result = future.result()
                if result.alias != alias:
                    raise ValueError(f"任务结果节点不匹配：{alias}")
            except Exception as exc:
                result = BatchResult(alias, error=f"{type(exc).__name__}: {exc}")
            results[alias] = result
            on_result(result)
    return [results[alias] for alias in nodes]


def source_snapshot(session: RemoteSession | None, paths: list[str]) -> dict[str, tuple[int, int, float]]:
    """Record a move source tree so changed or added entries prevent deletion."""
    result = {}

    def visit(path: str) -> None:
        info = session.sftp.lstat(path) if session is not None else Path(path).lstat()
        mode = info.st_mode or 0
        result[path] = (mode, info.st_size or 0, info.st_mtime or 0)
        if stat.S_ISDIR(mode):
            names = (
                [entry.filename for entry in session.sftp.listdir_attr(path)]
                if session is not None else [child.name for child in Path(path).iterdir()]
            )
            for name in names:
                if name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError(f"目录返回了不安全的名称：{name!r}")
                visit(posixpath.join(path, name) if session is not None else str(Path(path) / name))

    for path in paths:
        visit(path)
    return result
