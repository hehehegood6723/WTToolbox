"""Disk usage analysis and safe cleanup of War Thunder's throwaway folders.

Targets are split by risk so nothing destructive is ever the default:

``safe``      regenerated automatically by the game (shader cache, leftovers)
``logs``      diagnostic logs; old ones are usually worthless
``cache``     re-downloadable caches - bigger win, slightly slower next launch
``userdata``  replays / screenshots / user content - OFF by default
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .applog import log
from .winutil import delete_tree, human_size, move_to_trash, walk_size

__all__ = [
    "CleanTarget",
    "CleanReport",
    "build_targets",
    "measure_target",
    "measure_all",
    "clean_target",
    "CATEGORY_LABELS",
]

CATEGORY_LABELS = {
    "safe": "安全清理",
    "logs": "日志文件",
    "cache": "缓存数据",
    "userdata": "个人数据",
}

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class CleanTarget:
    key: str
    label: str
    description: str
    folder: str
    category: str = "safe"
    risk: str = ""
    pattern: str | None = None
    """Only touch files whose lowercased name ends with this suffix."""
    keep_within_days: float | None = None
    """Only delete files older than this many days."""
    delete_folder_itself: bool = False
    enabled_by_default: bool = True
    requires_game_closed: bool = True
    optional_dirs: tuple[str, ...] = ()
    """Extra directories merged into this target (e.g. per-version caches)."""

    size: int = 0
    file_count: int = 0
    missing: bool = False
    measured: bool = False

    @property
    def target_dirs(self) -> list[str]:
        dirs = [self.folder] if self.folder else []
        dirs.extend(self.optional_dirs)
        return dirs

    @property
    def exists(self) -> bool:
        return any(os.path.exists(d) for d in self.target_dirs)

    @property
    def size_text(self) -> str:
        return human_size(self.size)


@dataclass
class CleanReport:
    freed_bytes: int = 0
    deleted_files: int = 0
    failures: list[str] = field(default_factory=list)
    cancelled: bool = False
    trashed: list[str] = field(default_factory=list)

    @property
    def freed_text(self) -> str:
        return human_size(self.freed_bytes)

    @property
    def ok(self) -> bool:
        return not self.failures


# --------------------------------------------------------------------------- #
#  Target construction
# --------------------------------------------------------------------------- #
def build_targets(
    install,
    *,
    log_keep_days: float = 14,
    keep_recent_replays_days: float | None = 30,
) -> list[CleanTarget]:
    """Describe everything WTToolbox knows how to clean for this install."""
    targets = [
        CleanTarget(
            key="shaders",
            label="已编译着色器缓存",
            description="游戏会自动重建；清理后首次进入战斗会有短暂卡顿。",
            folder=install.compiled_shaders,
            category="safe",
            risk="下次启动会重新编译着色器",
        ),
        CleanTarget(
            key="obsolete",
            label="旧版本残留 (.obsolete)",
            description="更新后遗留的过期文件，可以安全删除。",
            folder=install.sub(".obsolete"),
            category="safe",
            risk="无风险",
        ),
        CleanTarget(
            key="browser_cache",
            label="内置浏览器缓存",
            description="启动器内核（CEF）的网页缓存。",
            folder=install.sub("cache", "browser"),
            category="safe",
            risk="下次打开启动器页面会重新加载",
        ),
        CleanTarget(
            key="game_logs",
            label=f"游戏运行日志 (.clog) 超过 {int(log_keep_days)} 天",
            description="战斗诊断日志，体积增长很快，旧文件基本无用。",
            folder=install.game_logs,
            category="logs",
            pattern=".clog",
            keep_within_days=log_keep_days,
            risk="清理后无法再向客服提交这段时间的日志",
        ),
        CleanTarget(
            key="launcher_logs",
            label=f"启动器日志 超过 {int(log_keep_days)} 天",
            description="launcher.exe 输出的纯文本日志。",
            folder=install.launcher_logs,
            category="logs",
            pattern=".txt",
            keep_within_days=log_keep_days,
            risk="无风险",
        ),
        CleanTarget(
            key="startapp_logs",
            label=f"更新器日志 超过 {int(log_keep_days)} 天",
            description="更新器 stub 的文本日志。",
            folder=install.startapp_logs,
            category="logs",
            pattern=".txt",
            keep_within_days=log_keep_days,
            risk="无风险",
        ),
        CleanTarget(
            key="cache_content",
            label="内容缓存 (cache/content)",
            description="更新时下载的临时内容缓存，可以重新下载。",
            folder=install.sub("cache", "content"),
            category="cache",
            risk="下次更新可能需要重新下载部分内容",
        ),
        CleanTarget(
            key="cache_ugc",
            label="用户内容缓存 (cache/contentUGC)",
            description="自定义涂装/任务等 UGC 的本地缓存，会自动重建。",
            folder=install.sub("cache", "contentUGC"),
            category="cache",
            risk="游戏会重新生成",
        ),
        CleanTarget(
            key="replays",
            label=(
                "回放文件"
                if not keep_recent_replays_days
                else f"回放文件（保留最近 {int(keep_recent_replays_days)} 天）"
            ),
            description="你的录像。删除后不可恢复（除非使用本工具的回收站）。",
            folder=install.replays,
            category="userdata",
            pattern=".wrpl",
            keep_within_days=keep_recent_replays_days,
            enabled_by_default=False,
            risk="个人数据，删除前请确认",
        ),
        CleanTarget(
            key="screenshots",
            label="游戏截图",
            description="你的截图。默认不勾选。",
            folder=install.screenshots,
            category="userdata",
            enabled_by_default=False,
            risk="个人数据，删除前请确认",
        ),
    ]
    return targets


# --------------------------------------------------------------------------- #
#  Measurement
# --------------------------------------------------------------------------- #
def _iter_files(target: CleanTarget):
    """Yield the files a target would remove."""
    cutoff = None
    if target.keep_within_days is not None:
        cutoff = time.time() - target.keep_within_days * 86400
    suffix = target.pattern.lower() if target.pattern else None

    for folder in target.target_dirs:
        if not folder or not os.path.isdir(folder):
            continue
        if target.delete_folder_itself:
            yield folder
            continue
        stack = [folder]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if suffix and not entry.name.lower().endswith(suffix):
                        continue
                    if cutoff is not None and entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                        continue
                except OSError:
                    continue
                yield entry.path


def measure_target(
    target: CleanTarget,
    *,
    on_progress: Callable[[int, str], None] | None = None,
    should_cancel: CancelFn | None = None,
) -> CleanTarget:
    total = 0
    count = 0
    target.missing = not target.exists
    for path in _iter_files(target):
        if should_cancel and should_cancel():
            break
        try:
            total += os.path.getsize(path)
            count += 1
        except OSError:
            continue
        if on_progress and count % 200 == 0:
            on_progress(total, path)
    target.size = total
    target.file_count = count
    target.measured = True
    return target


def measure_all(
    targets: Iterable[CleanTarget],
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: CancelFn | None = None,
) -> list[CleanTarget]:
    targets = list(targets)
    for index, target in enumerate(targets):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(index, len(targets), target.label)

        def inner(_bytes: int, path: str) -> None:
            if on_progress:
                on_progress(index, len(targets), target.label)

        measure_target(target, on_progress=inner, should_cancel=should_cancel)
    if on_progress:
        on_progress(len(targets), len(targets), "")
    return targets


# --------------------------------------------------------------------------- #
#  Cleaning
# --------------------------------------------------------------------------- #
def clean_target(
    target: CleanTarget,
    *,
    use_trash: bool = False,
    trash_root: str = "",
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> CleanReport:
    """Remove one target's files.  Never raises; collects failures instead."""
    report = CleanReport()
    if not target.exists:
        return report

    if target.delete_folder_itself:
        folders = [d for d in target.target_dirs if os.path.isdir(d)]
        total = len(folders)
        for index, folder in enumerate(folders):
            if should_cancel and should_cancel():
                report.cancelled = True
                break
            size, count = walk_size(folder)
            if use_trash and trash_root:
                ok, dest = move_to_trash(folder, trash_root)
                if ok:
                    report.trashed.append(dest)
                else:
                    report.failures.append(f"{os.path.basename(folder)}: {dest}")
                    continue
            else:
                ok, error = delete_tree(folder, should_cancel=should_cancel)
                if not ok:
                    report.failures.append(f"{os.path.basename(folder)}: {error}")
                    continue
            report.freed_bytes += size
            report.deleted_files += count
            if on_progress:
                on_progress(index + 1, total, os.path.basename(folder))
        return report

    files = list(_iter_files(target))
    total = len(files)
    for index, path in enumerate(files):
        if should_cancel and should_cancel():
            report.cancelled = True
            break
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if use_trash and trash_root:
            ok, dest = move_to_trash(path, trash_root)
            if ok:
                report.trashed.append(dest)
            else:
                report.failures.append(f"{os.path.basename(path)}: {dest}")
                continue
        else:
            ok, error = delete_tree(path)
            if not ok:
                report.failures.append(f"{os.path.basename(path)}: {error}")
                continue
        report.freed_bytes += size
        report.deleted_files += 1
        if on_progress and (index % 25 == 0 or index == total - 1):
            on_progress(index + 1, total, os.path.basename(path))

    _prune_empty_dirs(target.target_dirs)
    return report


def _prune_empty_dirs(roots: Iterable[str]) -> None:
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for current, dirs, files in os.walk(root, topdown=False):
            if current == root:
                continue
            if not dirs and not files:
                try:
                    os.rmdir(current)
                except OSError:
                    pass


def clean_many(
    targets: Iterable[CleanTarget],
    *,
    use_trash: bool = False,
    trash_root: str = "",
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: CancelFn | None = None,
) -> CleanReport:
    targets = list(targets)
    total_report = CleanReport()
    for index, target in enumerate(targets):
        if should_cancel and should_cancel():
            total_report.cancelled = True
            break
        if on_progress:
            on_progress(index, len(targets), target.label)

        def inner(done: int, count: int, name: str) -> None:
            if on_progress:
                on_progress(index, len(targets), f"{target.label} · {name}")

        part = clean_target(
            target,
            use_trash=use_trash,
            trash_root=trash_root,
            on_progress=inner,
            should_cancel=should_cancel,
        )
        total_report.freed_bytes += part.freed_bytes
        total_report.deleted_files += part.deleted_files
        total_report.failures.extend(part.failures)
        total_report.trashed.extend(part.trashed)
        if part.cancelled:
            total_report.cancelled = True
            break

    if on_progress:
        on_progress(len(targets), len(targets), "")
    if total_report.freed_bytes:
        log.ok(
            f"清理完成：释放 {total_report.freed_text}，删除 {total_report.deleted_files} 个文件",
            "清理",
        )
    if total_report.failures:
        log.warn(f"{len(total_report.failures)} 个文件无法删除（可能被占用）", "清理")
    return total_report


# --------------------------------------------------------------------------- #
#  Folder inventory
# --------------------------------------------------------------------------- #
INVENTORY_FOLDERS = (
    ("game_logs", ".game_logs", "游戏日志"),
    ("launcher_logs", ".launcher_log", "启动器日志"),
    ("startapp_logs", ".start_app_logs", "更新器日志"),
    ("compiled_shaders", "compiledShaders", "着色器缓存"),
    ("cache", "cache", "缓存目录"),
    ("replays", "Replays", "回放"),
    ("screenshots", "Screenshots", "截图"),
    ("user_skins", "UserSkins", "自定义涂装"),
    ("user_sights", "UserSights", "自定义瞄具"),
    ("user_missions", "UserMissions", "自定义任务"),
    ("sound", "sound", "音效资源"),
)


def inventory(
    install,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: CancelFn | None = None,
) -> list[dict]:
    """Size up each folder the toolkit cares about (runs off the GUI thread)."""
    rows: list[dict] = []
    total = len(INVENTORY_FOLDERS)
    for index, (key, rel, label) in enumerate(INVENTORY_FOLDERS):
        if should_cancel and should_cancel():
            break
        path = install.sub(rel)
        if on_progress:
            on_progress(index, total, label)
        if not os.path.isdir(path):
            rows.append({"key": key, "label": label, "path": path, "size": 0,
                         "files": 0, "exists": False})
            continue
        size, count = walk_size(path, should_cancel=should_cancel)
        rows.append({"key": key, "label": label, "path": path, "size": size,
                     "files": count, "exists": True})
    if on_progress:
        on_progress(total, total, "")
    return rows


def empty_trash(trash_root: str) -> CleanReport:
    """Permanently remove everything WTToolbox moved to its trash."""
    report = CleanReport()
    if not trash_root or not os.path.isdir(trash_root):
        return report
    for entry in os.scandir(trash_root):
        if not entry.is_dir(follow_symlinks=False):
            continue
        size, count = walk_size(entry.path)
        ok, error = delete_tree(entry.path)
        if ok:
            report.freed_bytes += size
            report.deleted_files += count
        else:
            report.failures.append(f"{entry.name}: {error}")
    if report.freed_bytes:
        log.ok(f"已清空回收站，释放 {report.freed_text}", "清理")
    return report


def trash_size(trash_root: str) -> tuple[int, int]:
    if not trash_root or not os.path.isdir(trash_root):
        return 0, 0
    return walk_size(trash_root)
