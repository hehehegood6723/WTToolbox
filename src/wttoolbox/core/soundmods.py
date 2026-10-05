"""Sound-directory mod management with full rollback.

War Thunder's voice packs and sound mods are distributed as **file replacement
sets** - a zip or folder of ``.bank``/``.assets.bank`` files that are dropped
into the game's ``sound`` folder.  Because that overwrites shipped game data,
this module never writes a byte without first copying the original aside, and
records a manifest so the whole operation can be undone exactly.

Honest scope: the toolkit installs and can fully revert a file set.  Whether a
given third-party pack *sounds* right in game depends on that pack, so the UI
does not claim to verify it - it only guarantees the filesystem is recoverable.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import appdirs
from .applog import log
from .library import IGNORED_NAMES
from .winutil import delete_tree, human_size, walk_size

__all__ = [
    "SoundSet",
    "SoundInstallOutcome",
    "backup_root",
    "sound_inventory",
    "files_under",
    "install_sound_set",
    "list_sets",
    "restore_set",
    "delete_set",
    "snapshot",
    "diff_snapshots",
]

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


def backup_root() -> str:
    """Where sound-mod rollback sets live.  Never raises."""
    root = os.path.join(appdirs.mods_dir(), "sound")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass
    return root


# --------------------------------------------------------------------------- #
#  Inventory / snapshots
# --------------------------------------------------------------------------- #
def files_under(folder: str) -> list[tuple[str, int, float]]:
    """``[(relative_path, size, mtime)]`` for every file under *folder*.

    Relative paths always use ``/`` so snapshots compare identically regardless
    of platform separators.
    """
    rows: list[tuple[str, int, float]] = []
    if not folder or not os.path.isdir(folder):
        return rows
    for root, _dirs, names in os.walk(folder):
        for name in names:
            full = os.path.join(root, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, folder).replace("\\", "/")
            rows.append((rel, stat.st_size, stat.st_mtime))
    rows.sort()
    return rows


def snapshot(folder: str) -> dict[str, tuple[int, float]]:
    """Map of relative path -> (size, mtime), used for before/after checks."""
    return {rel: (size, mtime) for rel, size, mtime in files_under(folder)}


def diff_snapshots(
    before: dict[str, tuple[int, float]], after: dict[str, tuple[int, float]]
) -> dict[str, list[str]]:
    """Compare two snapshots.  ``changed`` ignores mtime-only differences."""
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = sorted(
        key for key in (before_keys & after_keys) if before[key][0] != after[key][0]
    )
    return {"added": added, "removed": removed, "changed": changed}


@dataclass
class SoundInventoryRow:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    file_count: int = 0
    kind: str = ""


def sound_inventory(
    sound_dir: str,
    *,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> list[SoundInventoryRow]:
    """Top-level view of the ``sound`` folder."""
    rows: list[SoundInventoryRow] = []
    if not sound_dir or not os.path.isdir(sound_dir):
        return rows
    try:
        entries = sorted(os.scandir(sound_dir), key=lambda e: e.name.lower())
    except OSError:
        return rows

    for index, entry in enumerate(entries):
        if should_cancel and should_cancel():
            break
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.is_dir(follow_symlinks=False):
            size, count = walk_size(entry.path, should_cancel=should_cancel)
            rows.append(
                SoundInventoryRow(entry.name, entry.path, True, size, count, "folder")
            )
        elif entry.is_file(follow_symlinks=False):
            lowered = entry.name.lower()
            kind = "bank" if lowered.endswith(".bank") else ("text" if lowered.endswith((".blk", ".txt", ".json")) else "file")
            rows.append(
                SoundInventoryRow(entry.name, entry.path, False, stat.st_size, 1, kind)
            )
        if on_progress:
            on_progress(index + 1, len(entries), entry.name)
    rows.sort(key=lambda r: (not r.is_dir, -r.size))
    return rows


def describe_sound_dir(sound_dir: str) -> dict:
    size, count = walk_size(sound_dir)
    return {
        "path": sound_dir,
        "exists": os.path.isdir(sound_dir),
        "bytes": size,
        "files": count,
        "text": human_size(size),
    }


# --------------------------------------------------------------------------- #
#  Install
# --------------------------------------------------------------------------- #
@dataclass
class SoundSet:
    ident: str
    label: str
    created_at: float
    restored_at: float | None
    sound_dir: str
    target_root: str
    entry_count: int
    source: str = ""
    folder: str = ""
    replacements: int = 0
    additions: int = 0
    error: str = ""

    @property
    def created_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at))

    @property
    def restored_text(self) -> str:
        if not self.restored_at:
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.restored_at))

    @property
    def is_restored(self) -> bool:
        return bool(self.restored_at)

    @property
    def size(self) -> int:
        if not self.folder or not os.path.isdir(self.folder):
            return 0
        return walk_size(self.folder)[0]

    @property
    def size_text(self) -> str:
        return human_size(self.size)


@dataclass
class SoundInstallOutcome:
    ok: bool
    set_ident: str = ""
    copied: int = 0
    replaced: int = 0
    added: int = 0
    bytes_copied: int = 0
    target_root: str = ""
    error: str = ""
    cancelled: bool = False


def _stage_source(source: str) -> tuple[str, str | None]:
    """Return ``(staging_dir, temp_dir_to_cleanup)``."""
    if os.path.isdir(source):
        return source, None
    if not zipfile.is_zipfile(source):
        raise ValueError("不是有效的文件夹或 zip 压缩包")
    temp = tempfile.mkdtemp(prefix="tk-sound-")
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts):
                continue
            if any(p.lower() in IGNORED_NAMES for p in parts):
                continue
            destination = os.path.join(temp, *parts)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(info) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 256)
    return temp, temp


def _strip_sound_prefix(staging: str) -> tuple[str, list[str]]:
    """If the archive wraps everything in ``sound/``, drop that level."""
    try:
        entries = [e for e in os.scandir(staging)]
    except OSError:
        return staging, []
    if len(entries) == 1 and entries[0].is_dir() and entries[0].name.lower() == "sound":
        return entries[0].path, []
    return staging, []


def _collect_files(root: str) -> list[str]:
    out: list[str] = []
    for current, _dirs, names in os.walk(root):
        for name in names:
            if name.lower() in IGNORED_NAMES:
                continue
            full = os.path.join(current, name)
            out.append(os.path.relpath(full, root))
    out.sort()
    return out


def install_sound_set(
    source: str,
    sound_dir: str,
    *,
    dest_subdir: str = "",
    label: str = "",
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> SoundInstallOutcome:
    """Copy a sound mod into the game, backing up everything it overwrites."""
    if not source or not os.path.exists(source):
        return SoundInstallOutcome(ok=False, error="来源不存在")
    if not os.path.isdir(sound_dir):
        return SoundInstallOutcome(ok=False, error=f"音效目录不存在：{sound_dir}")

    staging_root = ""
    cleanup: str | None = None
    try:
        staging_root, cleanup = _stage_source(source)
        staging, _ = _strip_sound_prefix(staging_root)
        files = _collect_files(staging)
        if not files:
            return SoundInstallOutcome(ok=False, error="没有找到可安装的文件")

        target_root = (
            os.path.normpath(os.path.join(sound_dir, dest_subdir))
            if dest_subdir
            else os.path.normpath(sound_dir)
        )
        # Never write outside the sound folder.
        if os.path.commonpath([os.path.abspath(target_root), os.path.abspath(sound_dir)]) != os.path.abspath(sound_dir):
            return SoundInstallOutcome(ok=False, error="目标路径越出了音效目录，已拒绝")

        ident = time.strftime("%Y%m%d-%H%M%S")
        set_dir = os.path.join(backup_root(), ident)
        suffix = 1
        while os.path.exists(set_dir):
            set_dir = os.path.join(backup_root(), f"{ident}-{suffix}")
            suffix += 1
        os.makedirs(os.path.join(set_dir, "files"), exist_ok=True)

        label = label or os.path.basename(source.rstrip("\\/")) or "音效模组"
        manifest = {
            "id": os.path.basename(set_dir),
            "label": label,
            "created_at": time.time(),
            "restored_at": None,
            "source": os.path.abspath(source),
            "sound_dir": os.path.abspath(sound_dir),
            "target_root": target_root,
            "dest_subdir": dest_subdir,
            "entries": [],
        }

        outcome = SoundInstallOutcome(ok=True, set_ident=manifest["id"], target_root=target_root)
        total = len(files)
        freed_slots = 0

        for index, relative in enumerate(files):
            if should_cancel and should_cancel():
                outcome.cancelled = True
                break
            src_path = os.path.join(staging, relative)
            dest_path = os.path.join(target_root, relative)
            if os.path.commonpath([os.path.abspath(dest_path), os.path.abspath(sound_dir)]) != os.path.abspath(sound_dir):
                continue
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            existed = os.path.isfile(dest_path)
            backup_rel = ""
            if existed:
                backup_rel = f"files/{freed_slots:05d}{os.path.splitext(dest_path)[1]}"
                freed_slots += 1
                shutil.copy2(dest_path, os.path.join(set_dir, backup_rel))
            try:
                shutil.copy2(src_path, dest_path)
            except OSError as exc:
                outcome.ok = False
                outcome.error = f"写入失败 {relative}：{exc}"
                break

            try:
                size = os.path.getsize(src_path)
            except OSError:
                size = 0
            manifest["entries"].append(
                {
                    "rel": relative.replace("\\", "/"),
                    "dest": dest_path,
                    "existed": existed,
                    "backup": backup_rel,
                    "size": size,
                }
            )
            outcome.copied += 1
            outcome.bytes_copied += size
            if existed:
                outcome.replaced += 1
            else:
                outcome.added += 1
            if on_progress and (index % 10 == 0 or index == total - 1):
                on_progress(index + 1, total, relative)

        manifest["replaced"] = outcome.replaced
        manifest["added"] = outcome.added
        with open(os.path.join(set_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        if outcome.copied and outcome.ok:
            log.ok(
                f"音效模组已安装：{label}（覆盖 {outcome.replaced} 个 / 新增 {outcome.added} 个，"
                f"{human_size(outcome.bytes_copied)}），可随时完整还原",
                "音效",
            )
        elif not outcome.ok:
            log.error(f"音效模组安装失败：{outcome.error}", "音效")
        return outcome
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        log.error(f"音效模组安装异常：{exc}", "音效")
        return SoundInstallOutcome(ok=False, error=str(exc))
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Rollback
# --------------------------------------------------------------------------- #
def list_sets() -> list[SoundSet]:
    root = backup_root()
    sets: list[SoundSet] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return sets
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        manifest_path = os.path.join(entry.path, "manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        sets.append(
            SoundSet(
                ident=data.get("id", entry.name),
                label=data.get("label", entry.name),
                created_at=float(data.get("created_at") or 0),
                restored_at=data.get("restored_at"),
                sound_dir=data.get("sound_dir", ""),
                target_root=data.get("target_root", ""),
                entry_count=len(data.get("entries", [])),
                source=data.get("source", ""),
                folder=entry.path,
                replacements=int(data.get("replaced") or 0),
                additions=int(data.get("added") or 0),
            )
        )
    sets.sort(key=lambda s: s.created_at, reverse=True)
    return sets


def _read_manifest(set_dir: str) -> dict:
    with open(os.path.join(set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def restore_set(
    sound_set: SoundSet,
    *,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str]:
    """Undo an installation: put originals back and remove files we added."""
    if not sound_set.folder or not os.path.isdir(sound_set.folder):
        return False, "备份记录已丢失"
    try:
        manifest = _read_manifest(sound_set.folder)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"无法读取备份清单：{exc}"

    sound_dir = manifest.get("sound_dir") or sound_set.sound_dir
    entries = manifest.get("entries", [])
    if not entries:
        return False, "备份清单为空"

    restored = 0
    removed = 0
    errors: list[str] = []
    total = len(entries)
    for index, entry in enumerate(entries):
        if should_cancel and should_cancel():
            break
        dest = entry.get("dest", "")
        if not dest:
            continue
        # Safety: only ever touch paths inside the recorded sound folder.
        try:
            if sound_dir and os.path.commonpath(
                [os.path.abspath(dest), os.path.abspath(sound_dir)]
            ) != os.path.abspath(sound_dir):
                errors.append(f"跳过越界路径 {dest}")
                continue
        except ValueError:
            continue

        backup = entry.get("backup") or ""
        if entry.get("existed") and backup:
            source = os.path.join(sound_set.folder, backup)
            if os.path.isfile(source):
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(source, dest)
                    restored += 1
                except OSError as exc:
                    errors.append(f"{os.path.basename(dest)}: {exc}")
            else:
                errors.append(f"缺少备份文件：{backup}")
        elif not entry.get("existed"):
            if os.path.isfile(dest):
                try:
                    os.remove(dest)
                    removed += 1
                except OSError as exc:
                    errors.append(f"{os.path.basename(dest)}: {exc}")
        if on_progress and (index % 10 == 0 or index == total - 1):
            on_progress(index + 1, total, os.path.basename(dest))

    manifest["restored_at"] = time.time()
    try:
        with open(os.path.join(sound_set.folder, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    if errors:
        log.warn(f"还原时有 {len(errors)} 个问题：{errors[0]}", "音效")
        return False, f"部分还原失败（{len(errors)} 个问题）：{errors[0]}"
    log.ok(f"已还原音效模组：{sound_set.label}（恢复 {restored} 个，移除 {removed} 个）", "音效")
    return True, f"已还原 {restored} 个文件，移除 {removed} 个新增文件"


def delete_set(sound_set: SoundSet) -> tuple[bool, str]:
    """Drop a backup set (the installed files stay as they are)."""
    if not sound_set.folder:
        return False, "无效的记录"
    ok, error = delete_tree(sound_set.folder)
    if ok:
        log.info(f"已删除备份记录：{sound_set.label}", "音效")
        return True, "已删除备份记录"
    return False, error
