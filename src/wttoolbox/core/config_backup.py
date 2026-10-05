"""Timestamped backups of ``config.blk``.

The game rewrites ``config.blk`` on exit, so any edit made while the game is
running can be silently lost.  Every write this toolkit performs is therefore
preceded by a copy into ``%APPDATA%\\WTToolbox\\backups\\config`` and can be
restored from the config editor.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass

from . import appdirs
from .applog import log
from .winutil import human_size

__all__ = [
    "ConfigBackup",
    "backup_dir",
    "backup_config",
    "list_backups",
    "restore_backup",
    "delete_backup",
    "prune_backups",
]

_PREFIX = "config-"


def backup_dir() -> str:
    """Where config snapshots live.  Never raises, even if it cannot be made."""
    path = os.path.join(appdirs.backups_dir(), "config")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


@dataclass
class ConfigBackup:
    path: str
    stamp: float
    size: int
    label: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.stamp))

    @property
    def size_text(self) -> str:
        return human_size(self.size)


def backup_config(config_path: str, *, keep: int = 20, label: str = "") -> str | None:
    """Copy the current config aside.  Returns the backup path, or ``None``."""
    if not config_path or not os.path.isfile(config_path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = os.path.join(backup_dir(), f"{_PREFIX}{stamp}.blk")
    suffix = 1
    while os.path.exists(target):
        target = os.path.join(backup_dir(), f"{_PREFIX}{stamp}-{suffix}.blk")
        suffix += 1
    try:
        shutil.copy2(config_path, target)
    except OSError as exc:
        log.warn(f"备份 config.blk 失败：{exc}", "配置")
        return None
    if label:
        try:
            with open(f"{target}.note", "w", encoding="utf-8") as handle:
                handle.write(label)
        except OSError:
            pass
    log.info(f"已备份 config.blk → {os.path.basename(target)}", "配置")
    prune_backups(keep)
    return target


def list_backups() -> list[ConfigBackup]:
    folder = backup_dir()
    out: list[ConfigBackup] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        if not entry.name.startswith(_PREFIX) or not entry.name.endswith(".blk"):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        # The mtime is the backup moment; the filename encodes it too.
        stamp = stat.st_mtime
        label = ""
        note = f"{entry.path}.note"
        if os.path.isfile(note):
            try:
                with open(note, "r", encoding="utf-8") as handle:
                    label = handle.read().strip()
            except OSError:
                label = ""
        out.append(ConfigBackup(path=entry.path, stamp=stamp, size=stat.st_size, label=label))
    out.sort(key=lambda b: b.stamp, reverse=True)
    return out


def restore_backup(backup: ConfigBackup, config_path: str) -> tuple[bool, str]:
    """Restore a backup over the live config, keeping a safety copy first."""
    if not backup or not os.path.isfile(backup.path):
        return False, "备份文件不存在"
    if not os.path.isfile(config_path):
        return False, "当前 config.blk 不存在"

    safety = backup_config(config_path, label="还原前自动备份")
    try:
        shutil.copy2(backup.path, config_path)
    except OSError as exc:
        return False, f"还原失败：{exc}"
    log.ok(
        f"已从备份还原 config.blk（{backup.text}）"
        + (f"，还原前副本：{os.path.basename(safety)}" if safety else ""),
        "配置",
    )
    return True, "已还原"


def delete_backup(backup: ConfigBackup) -> bool:
    removed = False
    for path in (backup.path, f"{backup.path}.note"):
        if os.path.exists(path):
            try:
                os.remove(path)
                removed = True
            except OSError:
                pass
    return removed


def prune_backups(keep: int = 20) -> int:
    """Keep the newest *keep* backups."""
    backups = list_backups()
    removed = 0
    for backup in backups[max(1, keep):]:
        if delete_backup(backup):
            removed += 1
    return removed
