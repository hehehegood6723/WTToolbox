"""WTToolbox's own recoverable trash.

Deleting something should be undoable, and Windows' Recycle Bin API is awkward
to drive reliably from Python without extra dependencies.  Instead, anything the
toolkit removes is *moved* into ``%APPDATA%\\WTToolbox\\trash\\<timestamp>`` and
can be restored to its original location from the 设置 / 工具箱 pages.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass

from . import appdirs
from .applog import log
from .winutil import delete_tree, human_size, walk_size

__all__ = ["TrashEntry", "list_entries", "restore_entry", "delete_entry", "totals", "empty"]

_META = ".tk-trash.json"


@dataclass
class TrashEntry:
    ident: str
    path: str
    name: str
    origin: str
    created_at: float
    size: int = 0
    file_count: int = 0

    @property
    def created_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at))

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    @property
    def origin_text(self) -> str:
        return self.origin or "（未知原始位置）"


def _root() -> str:
    return appdirs.trash_dir()


def list_entries() -> list[TrashEntry]:
    root = _root()
    entries: list[TrashEntry] = []
    if not os.path.isdir(root):
        return entries
    try:
        candidates = list(os.scandir(root))
    except OSError:
        return entries

    for folder in candidates:
        if not folder.is_dir(follow_symlinks=False):
            continue
        try:
            children = [
                e for e in os.scandir(folder.path) if e.name != _META
            ]
        except OSError:
            continue
        origin = ""
        created = None
        meta_path = os.path.join(folder.path, _META)
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                origin = meta.get("origin", "")
                created = meta.get("created_at")
            except (OSError, json.JSONDecodeError):
                pass

        for child in children:
            size = 0
            count = 0
            try:
                if child.is_dir(follow_symlinks=False):
                    size, count = walk_size(child.path)
                else:
                    size = child.stat(follow_symlinks=False).st_size
                    count = 1
                    created_stat = child.stat(follow_symlinks=False).st_mtime
                    created = created or created_stat
            except OSError:
                continue
            entries.append(
                TrashEntry(
                    ident=folder.name,
                    path=child.path,
                    name=child.name,
                    origin=origin,
                    created_at=float(created or time.time()),
                    size=size,
                    file_count=count,
                )
            )
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries


def totals() -> tuple[int, int, int]:
    """``(bytes, file_count, entry_count)`` currently held in the trash."""
    entries = list_entries()
    return (
        sum(e.size for e in entries),
        sum(e.file_count for e in entries),
        len(entries),
    )


def restore_entry(entry: TrashEntry, target_dir: str | None = None) -> tuple[bool, str]:
    """Move an entry back, either to its recorded origin or a chosen folder."""
    if not os.path.exists(entry.path):
        return False, "回收站中的文件已不存在"

    destination_dir = target_dir or entry.origin or ""
    if not destination_dir:
        return False, "没有记录原始位置，请选择还原到的文件夹"
    try:
        os.makedirs(destination_dir, exist_ok=True)
    except OSError as exc:
        return False, f"无法创建目标文件夹：{exc}"

    target = os.path.join(destination_dir, entry.name)
    counter = 1
    while os.path.exists(target):
        stem, ext = os.path.splitext(entry.name)
        target = os.path.join(destination_dir, f"{stem} (还原{counter}){ext}")
        counter += 1
    try:
        shutil.move(entry.path, target)
    except OSError as exc:
        return False, f"还原失败：{exc}"

    _prune_empty(entry.ident)
    log.ok(f"已还原 {entry.name} → {destination_dir}", "回收站")
    return True, target


def delete_entry(entry: TrashEntry) -> tuple[bool, str]:
    ok, error = delete_tree(entry.path)
    if ok:
        _prune_empty(entry.ident)
        log.info(f"已从回收站永久删除 {entry.name}", "回收站")
        return True, "已永久删除"
    return False, error


def empty() -> tuple[int, int, list[str]]:
    """Permanently delete everything in the trash."""
    entries = list_entries()
    freed = 0
    removed = 0
    errors: list[str] = []
    for entry in entries:
        ok, error = delete_tree(entry.path)
        if ok:
            freed += entry.size
            removed += 1
        else:
            errors.append(f"{entry.name}: {error}")
    _prune_empty(None)
    if removed:
        log.ok(f"已清空回收站，释放 {human_size(freed)}", "回收站")
    return freed, removed, errors


def _prune_empty(ident: str | None) -> None:
    root = _root()
    if not os.path.isdir(root):
        return
    try:
        folders = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return
    for folder in folders:
        if ident and folder.name != ident:
            continue
        try:
            remaining = [e for e in os.scandir(folder.path) if e.name != _META]
        except OSError:
            continue
        if remaining:
            continue
        try:
            shutil.rmtree(folder.path, ignore_errors=True)
        except OSError:
            pass
