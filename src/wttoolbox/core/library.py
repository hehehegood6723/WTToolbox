"""Managing user content: custom skins, sights, missions and screenshots.

Everything here works on a generic "content root" so the same code serves
``UserSkins``, ``UserSights`` and ``UserMissions``.  Installations from ``.zip``
archives are validated, zip-slip protected, and reversible via WTToolbox's own
trash folder.
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .applog import log
from .winutil import delete_tree, human_size, move_to_trash, walk_size

__all__ = [
    "LibraryItem",
    "InstallOutcome",
    "list_content",
    "list_sights",
    "list_screenshots",
    "content_stats",
    "install_from_path",
    "inspect_archive",
    "delete_items",
    "IMAGE_SUFFIXES",
    "IGNORED_NAMES",
]

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".tga", ".dds")
IGNORED_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini", ".git"}

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class LibraryItem:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    file_count: int = 0
    mtime: float = 0.0
    kind: str = ""
    detail: str = ""
    has_blk: bool = False
    images: int = 0
    preview: str = ""
    """A representative image file inside the item, when one exists."""

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    @property
    def datetime_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.mtime))

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        return needle.lower() in self.name.lower()


def _summarise_folder(path: str) -> tuple[int, int, bool, int, str]:
    """Return ``(bytes, files, has_blk, image_count, preview_image)``."""
    total = 0
    count = 0
    has_blk = False
    images = 0
    preview = ""
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            try:
                total += os.path.getsize(full)
                count += 1
            except OSError:
                continue
            lowered = name.lower()
            if lowered.endswith(".blk"):
                has_blk = True
            if lowered.endswith(IMAGE_SUFFIXES):
                images += 1
                if not preview and lowered.endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    preview = full
    return total, count, has_blk, images, preview


def list_content(
    root: str,
    *,
    kind: str = "",
    depth: int = 1,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
    skip_names: Iterable[str] = (),
) -> list[LibraryItem]:
    """Enumerate immediate children (or *depth* levels) of a content root."""
    if not root or not os.path.isdir(root):
        return []
    skip = {n.lower() for n in skip_names} | IGNORED_NAMES

    items: list[LibraryItem] = []
    seen: set[str] = set()
    counter = 0

    def walk(folder: str, level: int, prefix: str) -> None:
        nonlocal counter
        if should_cancel and should_cancel():
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.lower() in skip:
                continue
            counter += 1
            if on_progress and counter % 20 == 0:
                # No known total while walking: (0, 0) keeps the UI indeterminate.
                on_progress(0, 0, entry.name)
            norm = os.path.normcase(entry.path)
            if norm in seen:
                continue
            seen.add(norm)
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            display = f"{prefix}{entry.name}"
            if entry.is_dir(follow_symlinks=False):
                if level >= depth:
                    total, count, has_blk, images, preview = _summarise_folder(entry.path)
                    detail = f"{count} 个文件 · {human_size(total)}"
                    if has_blk:
                        detail += " · 含配置文件"
                    items.append(
                        LibraryItem(
                            path=entry.path, name=display, is_dir=True, size=total,
                            file_count=count, mtime=stat.st_mtime, kind=kind,
                            detail=detail, has_blk=has_blk, images=images, preview=preview,
                        )
                    )
                else:
                    walk(entry.path, level + 1, f"{display} / ")
            else:
                items.append(
                    LibraryItem(
                        path=entry.path, name=display, is_dir=False, size=stat.st_size,
                        file_count=1, mtime=stat.st_mtime, kind=kind,
                        detail=human_size(stat.st_size),
                        images=1 if entry.name.lower().endswith(IMAGE_SUFFIXES) else 0,
                        preview=entry.path
                        if entry.name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))
                        else "",
                    )
                )

    walk(root, 1, "")
    items.sort(key=lambda i: (not i.is_dir, -i.mtime))
    return items


def list_sights(
    root: str,
    *,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> list[LibraryItem]:
    """``UserSights`` is nested ``<nation>/<vehicle>/*.blk`` - flatten it."""
    return list_content(
        root, kind="sight", depth=2, on_progress=on_progress, should_cancel=should_cancel
    )


def list_screenshots(install) -> list[LibraryItem]:
    folder = install.screenshots
    if not os.path.isdir(folder):
        return []
    items: list[LibraryItem] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        if not entry.name.lower().endswith(IMAGE_SUFFIXES):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        items.append(
            LibraryItem(
                path=entry.path, name=entry.name, is_dir=False, size=stat.st_size,
                file_count=1, mtime=stat.st_mtime, kind="screenshot",
                detail=human_size(stat.st_size), images=1, preview=entry.path,
            )
        )
    items.sort(key=lambda i: i.mtime, reverse=True)
    return items


def content_stats(root: str) -> tuple[int, int]:
    if not root or not os.path.isdir(root):
        return 0, 0
    return walk_size(root)


# --------------------------------------------------------------------------- #
#  Archives
# --------------------------------------------------------------------------- #
@dataclass
class ArchivePreview:
    path: str
    ok: bool
    entries: int = 0
    total_size: int = 0
    suggested_name: str = ""
    top_level: list[str] = field(default_factory=list)
    error: str = ""


def _normalise_member(name: str) -> list[str] | None:
    """Split an archive entry into safe path parts, or ``None`` to skip it."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        return None  # zip-slip guard
    if any(p.lower() in IGNORED_NAMES for p in parts):
        return None
    return parts


def _safe_names(path: str) -> list[str]:
    """Every installable entry name in an archive, normalised and filtered."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = _normalise_member(info.filename)
                if parts:
                    names.append("/".join(parts))
            return names
    except (zipfile.BadZipFile, OSError):
        return []


def _archive_prefix(names: Iterable[str]) -> str:
    """The single wrapping folder of an archive, or ``""`` when there is none.

    Metadata folders such as ``__MACOSX`` are ignored, so a normal skin archive
    that also carries macOS junk still resolves to its real top-level folder.
    """
    names = list(names)
    if not names:
        return ""
    if not all("/" in name for name in names):
        return ""
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) == 1:
        return next(iter(tops))
    return ""


def inspect_archive(path: str) -> ArchivePreview:
    """Work out what an archive would install as, without touching the game."""
    preview = ArchivePreview(path=path, ok=False)
    if not os.path.isfile(path):
        preview.error = "文件不存在"
        return preview
    if not zipfile.is_zipfile(path):
        preview.error = "不是有效的 zip 压缩包"
        return preview
    try:
        names = _safe_names(path)
        with zipfile.ZipFile(path) as archive:
            preview.total_size = sum(
                info.file_size
                for info in archive.infolist()
                if _normalise_member(info.filename) and not info.is_dir()
            )
        preview.entries = len(names)
        preview.top_level = sorted({n.split("/", 1)[0] for n in names})[:12]
        prefix = _archive_prefix(names)
        preview.suggested_name = prefix or os.path.splitext(os.path.basename(path))[0]
        preview.ok = bool(names)
        if not names:
            preview.error = "压缩包内没有可安装的文件"
    except (zipfile.BadZipFile, OSError) as exc:
        preview.error = f"读取失败：{exc}"
    return preview


def _archive_members(
    archive: zipfile.ZipFile, prefix: str
) -> list[tuple[zipfile.ZipInfo, list[str]]]:
    """``[(ZipInfo, relative_parts)]`` with the wrapper folder removed."""
    prefix_parts = prefix.split("/") if prefix else []
    out: list[tuple[zipfile.ZipInfo, list[str]]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = _normalise_member(info.filename)
        if not parts:
            continue
        if prefix_parts:
            if parts[: len(prefix_parts)] != prefix_parts:
                continue
            parts = parts[len(prefix_parts):]
            if not parts:
                continue
        out.append((info, parts))
    return out


@dataclass
class InstallOutcome:
    ok: bool
    installed: int = 0
    bytes_copied: int = 0
    target: str = ""
    error: str = ""
    cancelled: bool = False
    replaced: bool = False


def _safe_members(archive: zipfile.ZipFile, root_prefix: str) -> list[zipfile.ZipInfo]:
    return [info for info, _parts in _archive_members(archive, root_prefix)]


def install_from_path(
    source: str,
    dest_root: str,
    *,
    name: str | None = None,
    overwrite: bool = False,
    use_trash: bool = True,
    trash_root: str = "",
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> InstallOutcome:
    """Install a folder or zip into ``dest_root``.

    The archive's single top-level folder (if any) becomes the installed folder
    name, which is what War Thunder's content loaders expect.
    """
    if not source or not os.path.exists(source):
        return InstallOutcome(ok=False, error="来源不存在")
    if not os.path.isdir(dest_root):
        try:
            os.makedirs(dest_root, exist_ok=True)
        except OSError as exc:
            return InstallOutcome(ok=False, error=f"无法创建目标目录：{exc}")

    is_zip = os.path.isfile(source) and zipfile.is_zipfile(source)
    root_prefix = ""
    suggested = name or ""

    if is_zip:
        preview = inspect_archive(source)
        if not preview.ok:
            return InstallOutcome(ok=False, error=preview.error)
        suggested = suggested or preview.suggested_name
        root_prefix = _archive_prefix(_safe_names(source))
    else:
        suggested = suggested or os.path.basename(source.rstrip("\\/"))

    target = os.path.join(dest_root, suggested or f"import_{int(time.time())}")

    replaced = False
    if os.path.exists(target):
        if not overwrite:
            return InstallOutcome(
                ok=False, target=target,
                error=f"目标已存在：{os.path.basename(target)}",
            )
        replaced = True
        if use_trash and trash_root:
            ok, dest = move_to_trash(target, trash_root)
            if not ok:
                return InstallOutcome(ok=False, target=target, error=f"无法移除旧目录：{dest}")
        else:
            ok, error = delete_tree(target)
            if not ok:
                return InstallOutcome(ok=False, target=target, error=f"无法删除旧目录：{error}")

    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return InstallOutcome(ok=False, target=target, error=f"创建目标失败：{exc}")

    outcome = InstallOutcome(ok=True, target=target, replaced=replaced)
    try:
        if is_zip:
            with zipfile.ZipFile(source) as archive:
                members = _archive_members(archive, root_prefix)
                total = len(members)
                for index, (info, parts) in enumerate(members):
                    if should_cancel and should_cancel():
                        outcome.cancelled = True
                        break
                    destination = os.path.join(target, *parts)
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with archive.open(info) as src, open(destination, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 256)
                    outcome.installed += 1
                    outcome.bytes_copied += info.file_size
                    if on_progress and (index % 10 == 0 or index == total - 1):
                        on_progress(index + 1, total, "/".join(parts))
        else:
            files: list[tuple[str, str]] = []
            for root, _dirs, names in os.walk(source):
                for filename in names:
                    if filename.lower() in IGNORED_NAMES:
                        continue
                    full = os.path.join(root, filename)
                    rel = os.path.relpath(full, source)
                    files.append((full, rel))
            total = len(files)
            for index, (full, rel) in enumerate(files):
                if should_cancel and should_cancel():
                    outcome.cancelled = True
                    break
                destination = os.path.join(target, rel)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(full, destination)
                try:
                    outcome.bytes_copied += os.path.getsize(full)
                except OSError:
                    pass
                outcome.installed += 1
                if on_progress and (index % 10 == 0 or index == total - 1):
                    on_progress(index + 1, total, rel)
    except (OSError, zipfile.BadZipFile) as exc:
        outcome.ok = False
        outcome.error = f"解包失败：{exc}"
        log.error(f"安装失败：{exc}", "内容")
        return outcome

    if outcome.installed == 0 and not outcome.cancelled:
        outcome.ok = False
        outcome.error = "压缩包内没有可安装的文件"
    elif outcome.ok:
        log.ok(
            f"已安装 {outcome.installed} 个文件到 {os.path.basename(target)}"
            f"（{human_size(outcome.bytes_copied)}）",
            "内容",
        )
    return outcome


def delete_items(
    items: Iterable[LibraryItem],
    *,
    use_trash: bool = True,
    trash_root: str = "",
    should_cancel: CancelFn | None = None,
) -> tuple[int, int, list[str]]:
    """Delete library items.  Returns ``(deleted, freed_bytes, errors)``."""
    deleted = 0
    freed = 0
    errors: list[str] = []
    for item in items:
        if should_cancel and should_cancel():
            break
        path = item.path
        if not os.path.exists(path):
            continue
        size = item.size
        if not size:
            size, _count = walk_size(path)
        if use_trash and trash_root:
            ok, dest = move_to_trash(path, trash_root)
            if not ok:
                errors.append(f"{item.name}: {dest}")
                continue
        else:
            ok, error = delete_tree(path)
            if not ok:
                errors.append(f"{item.name}: {error}")
                continue
        deleted += 1
        freed += size
    if deleted:
        log.ok(f"已移除 {deleted} 个项目（{human_size(freed)}）", "内容")
    if errors:
        log.warn(f"{len(errors)} 个项目未能移除", "内容")
    return deleted, freed, errors
