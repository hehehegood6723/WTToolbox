"""War Thunder replay (``.wrpl``) inspection and management.

Format notes (determined empirically against this machine's replays)
--------------------------------------------------------------------
The header is a fixed-layout binary record followed by the mission blk text.
Two fields are stable across every file observed and are enough to identify a
battle:

* offset 8     - NUL-terminated ASCII ``levels/<level>.bin``
* offset 136   - NUL-terminated ASCII ``gamedata/missions/.../<mission>.blk``

Some later fields shift between game versions, so rather than trusting a fixed
offset for everything, this module:

1. validates the two known offsets, and
2. falls back to scanning the first few KiB for NUL-terminated printable runs.

There is **no readable result blob at the end of the file** (verified: the tail
is incompressible binary), so kills/outcome are not available and are not
fabricated.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from . import mapnames
from .applog import log

__all__ = [
    "ReplayInfo",
    "LEVEL_OFFSET",
    "MISSION_OFFSET",
    "parse_replay_header",
    "list_replays",
    "replay_size",
    "safe_replay_name",
]

LEVEL_OFFSET = 8
MISSION_OFFSET = 136
HEADER_READ = 8192

_LEVEL_RE = re.compile(r"^levels/(.+)\.bin$", re.IGNORECASE)
_MISSION_RE = re.compile(r"^gamedata/missions/(.+)\.blk$", re.IGNORECASE)

# (path, mtime, size) -> ReplayInfo, so re-listing a 100-replay folder is cheap.
_header_cache: dict[tuple[str, float, int], "ReplayInfo"] = {}


@dataclass
class ReplayInfo:
    path: str
    size: int
    mtime: float
    level_path: str = ""
    mission_path: str = ""
    header_verified: bool = False
    parse_note: str = ""

    # ------------------------------------------------------------- derived
    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def map_code(self) -> str:
        return mapnames.map_code_from_level(self.level_path)

    @property
    def map_name(self) -> str:
        return mapnames.display_name(self.map_code)

    @property
    def map_name_en(self) -> str:
        return mapnames.prettify(self.map_code)

    @property
    def is_air(self) -> bool:
        return os.path.basename(self.level_path).lower().startswith("air_")

    @property
    def mission_file(self) -> str:
        if not self.mission_path:
            return ""
        return self.mission_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]

    @property
    def mission_parts(self) -> list[str]:
        if not self.mission_path:
            return []
        parts = self.mission_path.replace("\\", "/").split("/")
        # drop 'gamedata', 'missions'
        return [p for p in parts[2:] if p]

    @property
    def branch(self) -> str:
        """``tanks`` / ``aircraft`` / ``ships`` etc. when discoverable."""
        parts = self.mission_parts
        for token in parts[:-1]:
            lowered = token.lower()
            if lowered in {"tanks", "aircraft", "ships", "helicopters", "boats"}:
                return lowered
        return ""

    @property
    def mode_en(self) -> str:
        return mapnames.mode_label(self.mission_file)[0]

    @property
    def mode_zh(self) -> str:
        return mapnames.mode_label(self.mission_file)[1]

    @property
    def mode_display(self) -> str:
        en, zh = mapnames.mode_label(self.mission_file)
        return zh or en or "未知模式"

    @property
    def datetime(self):
        return time.localtime(self.mtime)

    @property
    def is_meta_file(self) -> bool:
        return self.filename.lower().endswith(".wdb")

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.lower()
        haystack = " ".join(
            [self.filename, self.level_path, self.mission_path,
             self.map_name, self.map_name_en, self.map_code, self.mode_display]
        ).lower()
        return needle in haystack

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"<Replay {self.filename} {self.map_name} {self.mode_display}>"


def _printable_runs(buf: bytes, minlen: int = 4) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    start = -1
    for index, byte in enumerate(buf):
        if 32 <= byte < 127:
            if start < 0:
                start = index
        else:
            if start >= 0 and index - start >= minlen:
                out.append((start, buf[start:index].decode("ascii", "replace")))
            start = -1
    if start >= 0 and len(buf) - start >= minlen:
        out.append((start, buf[start:].decode("ascii", "replace")))
    return out


def _read_cstring(buf: bytes, offset: int, limit: int = 512) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    end = offset
    while end < len(buf) and end - offset < limit and buf[end] != 0:
        if not (32 <= buf[end] < 127):
            return ""
        end += 1
    if end >= len(buf):
        return ""
    return buf[offset:end].decode("ascii", "replace")


def parse_replay_header(path: str) -> ReplayInfo:
    """Extract map + mission from a replay file.  Never raises."""
    try:
        stat = os.stat(path)
    except OSError as exc:
        return ReplayInfo(path=path, size=0, mtime=0.0, parse_note=f"无法读取: {exc}")

    key = (os.path.normcase(path), stat.st_mtime, stat.st_size)
    cached = _header_cache.get(key)
    if cached is not None:
        return cached

    info = ReplayInfo(path=path, size=stat.st_size, mtime=stat.st_mtime)

    try:
        with open(path, "rb") as fh:
            head = fh.read(HEADER_READ)
    except OSError as exc:
        info.parse_note = f"打开失败: {exc}"
        return info

    if stat.st_size < 64:
        info.parse_note = "文件过小，可能已损坏"
        return info

    # --- preferred: the two validated fixed offsets --------------------------
    level = _read_cstring(head, LEVEL_OFFSET)
    mission = _read_cstring(head, MISSION_OFFSET)
    if _LEVEL_RE.match(level) and _MISSION_RE.match(mission):
        info.level_path = level
        info.mission_path = mission
        info.header_verified = True

    # --- fallback: scan printable runs --------------------------------------
    if not info.level_path or not info.mission_path:
        for _offset, text in _printable_runs(head):
            if not info.level_path and _LEVEL_RE.match(text):
                info.level_path = text
            elif not info.mission_path and _MISSION_RE.match(text):
                info.mission_path = text
            if info.level_path and info.mission_path:
                break
        if info.level_path or info.mission_path:
            info.parse_note = "通过扫描识别（非标准头）"

    if not info.level_path and not info.mission_path:
        info.parse_note = "无法识别地图信息"

    _header_cache[key] = info
    return info


def list_replays(
    folder: str,
    *,
    include_meta: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[ReplayInfo]:
    """List ``.wrpl`` files in *folder*, newest first.

    ``on_progress(done, total, name)`` matches the shape the UI's task runner
    expects, so it can be passed straight through.
    """
    if not folder or not os.path.isdir(folder):
        return []
    try:
        names = [
            entry.name
            for entry in os.scandir(folder)
            if entry.is_file(follow_symlinks=False)
            and (entry.name.lower().endswith(".wrpl")
                 or (include_meta and entry.name.lower().endswith(".wdb")))
        ]
    except OSError as exc:
        log.warn(f"读取回放目录失败：{exc}", "回放")
        return []

    infos: list[ReplayInfo] = []
    total = len(names)
    for index, name in enumerate(names):
        if should_cancel and should_cancel():
            break
        infos.append(parse_replay_header(os.path.join(folder, name)))
        if on_progress and (index % 5 == 0 or index == total - 1):
            on_progress(index + 1, total, name)

    infos.sort(key=lambda i: i.mtime, reverse=True)
    return infos


def replay_size(infos: Iterable[ReplayInfo]) -> int:
    return sum(i.size for i in infos)


def safe_replay_name(name: str) -> str:
    """Strip characters Windows will not accept in a filename."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not cleaned:
        cleaned = "replay"
    if not cleaned.lower().endswith(".wrpl"):
        cleaned += ".wrpl"
    return cleaned[:180]


def prune_header_cache(max_entries: int = 4000) -> None:
    """Bound the header cache so long sessions cannot grow without limit."""
    if len(_header_cache) <= max_entries:
        return
    for key in list(_header_cache.keys())[: len(_header_cache) - max_entries]:
        _header_cache.pop(key, None)
