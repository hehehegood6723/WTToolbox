"""Reading the logs War Thunder and its launcher leave behind.

Three log families live next to the game:

``.launcher_log/*.txt``    plain UTF-8 text from ``launcher.exe``
``.start_app_logs/*.txt``  plain UTF-8 text from the updater stub
``.game_logs/*.clog``      the game client's own log

The ``.clog`` files are **not** plain text - they use a Gaijin-proprietary
compressed container (a fixed 16-byte header, verified to be neither zlib,
raw-deflate nor gzip, containing no readable ASCII runs).  This module therefore
treats them as opaque artefacts: it lists them, sizes them and cleans them up,
and never pretends to decode them.
"""

from __future__ import annotations

import codecs
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence

from .applog import log

__all__ = [
    "LogFile",
    "LogKind",
    "list_logs",
    "read_tail",
    "read_text",
    "decode_preview",
    "TextTailer",
    "log_summary",
    "CLOG_NOTE",
    "LEVEL_RE",
]

CLOG_NOTE = (
    "游戏端 .clog 日志使用 Gaijin 私有压缩格式（固定 16 字节文件头），"
    "并非纯文本，无法直接解码显示。此处提供文件清单、占用统计与清理功能。"
)

LEVEL_RE = re.compile(r"\[\s*([DIWE])\s*\]")


class LogKind:
    LAUNCHER = "launcher"
    STARTAPP = "startapp"
    GAME = "game"

    LABELS = {
        LAUNCHER: "启动器日志",
        STARTAPP: "更新器日志",
        GAME: "游戏运行日志",
    }
    ORDER = (LAUNCHER, STARTAPP, GAME)


@dataclass
class LogFile:
    path: str
    kind: str
    size: int
    mtime: float
    is_text: bool = True

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def datetime(self):
        return time.localtime(self.mtime)

    @property
    def label(self) -> str:
        return LogKind.LABELS.get(self.kind, self.kind)

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"<LogFile {self.kind} {self.name} {self.size}B>"


def _scan(folder: str, kind: str, suffixes: tuple[str, ...]) -> list[LogFile]:
    out: list[LogFile] = []
    if not folder or not os.path.isdir(folder):
        return out
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        log.warn(f"读取日志目录失败 {folder}：{exc}", "日志")
        return out
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
        except OSError:
            continue
        lowered = entry.name.lower()
        if not lowered.endswith(suffixes):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        out.append(
            LogFile(
                path=entry.path,
                kind=kind,
                size=stat.st_size,
                mtime=stat.st_mtime,
                is_text=kind != LogKind.GAME,
            )
        )
    out.sort(key=lambda f: f.mtime, reverse=True)
    return out


def list_logs(install) -> dict[str, list[LogFile]]:
    """Every log file, grouped by :class:`LogKind`."""
    return {
        LogKind.LAUNCHER: _scan(install.launcher_logs, LogKind.LAUNCHER, (".txt", ".log")),
        LogKind.STARTAPP: _scan(install.startapp_logs, LogKind.STARTAPP, (".txt", ".log")),
        LogKind.GAME: _scan(install.game_logs, LogKind.GAME, (".clog",)),
    }


def newest_text_log(install) -> LogFile | None:
    """The most recently written readable log (launcher or updater)."""
    candidates: list[LogFile] = []
    candidates.extend(_scan(install.launcher_logs, LogKind.LAUNCHER, (".txt", ".log")))
    candidates.extend(_scan(install.startapp_logs, LogKind.STARTAPP, (".txt", ".log")))
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.mtime)


def newest_game_log(install) -> LogFile | None:
    files = _scan(install.game_logs, LogKind.GAME, (".clog",))
    return files[0] if files else None


# --------------------------------------------------------------------------- #
#  Text access
# --------------------------------------------------------------------------- #
def _decode(data: bytes) -> str:
    """Launcher logs are UTF-8; older builds may emit the ANSI codepage."""
    for encoding in ("utf-8", "cp936", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_text(path: str, *, max_bytes: int | None = None) -> str:
    """Read a whole text log, optionally only its last *max_bytes*."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if max_bytes is not None and size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            data = fh.read()
    except OSError as exc:
        return f"[读取失败] {exc}"
    return _decode(data)


def read_tail(path: str, *, max_bytes: int = 256 * 1024) -> str:
    return read_text(path, max_bytes=max_bytes)


def decode_preview(log_file: LogFile, max_bytes: int = 256 * 1024) -> str:
    """Human-readable content for a log, or an explanation why there is none."""
    if not log_file.is_text:
        return CLOG_NOTE
    text = read_tail(log_file.path, max_bytes=max_bytes)
    return text or "（日志为空）"


# --------------------------------------------------------------------------- #
#  Incremental tailing
# --------------------------------------------------------------------------- #
class TextTailer:
    """Incrementally read a growing text file without re-reading it all.

    Uses an incremental UTF-8 decoder so a multi-byte character split across two
    reads is never mangled.
    """

    def __init__(self, path: str, *, from_end_bytes: int = 96 * 1024) -> None:
        self.path = path
        self._offset = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._primed = False
        self._from_end = from_end_bytes
        self._truncation_guard = 0

    def reset(self, path: str | None = None) -> None:
        if path:
            self.path = path
        self._offset = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._primed = False

    def read_new(self, *, first_read_tail: bool = True) -> str:
        """Return any text appended since the previous call."""
        if not self.path or not os.path.isfile(self.path):
            return ""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return ""

        if size < self._offset:
            # File was rotated or truncated - start over.
            self._offset = 0
            self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        start = self._offset
        if not self._primed and first_read_tail and size > self._from_end:
            start = size - self._from_end
        try:
            with open(self.path, "rb") as fh:
                fh.seek(start)
                data = fh.read()
        except OSError:
            return ""

        self._primed = True
        self._offset = start + len(data)
        return self._decoder.decode(data)


# --------------------------------------------------------------------------- #
#  Analysis
# --------------------------------------------------------------------------- #
def log_summary(files: Iterable[LogFile]) -> dict:
    files = list(files)
    total = sum(f.size for f in files)
    return {
        "count": len(files),
        "bytes": total,
        "oldest": min((f.mtime for f in files), default=0.0),
        "newest": max((f.mtime for f in files), default=0.0),
    }


def scan_levels(text: str) -> dict[str, int]:
    """Count ``[D] [I] [W] [E]`` style level tags in a log body."""
    counts = {"D": 0, "I": 0, "W": 0, "E": 0}
    for match in LEVEL_RE.finditer(text):
        counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def find_problems(text: str, *, limit: int = 300) -> list[str]:
    """Lines that look like errors or warnings, for a quick triage view."""
    hits: list[str] = []
    for line in text.splitlines():
        match = LEVEL_RE.search(line)
        if match and match.group(1) in ("E", "W"):
            hits.append(line.rstrip())
            if len(hits) >= limit:
                break
    return hits


def clog_inventory(install) -> dict:
    """Sizes for the game-log folder - the main target of the log cleaner."""
    files = _scan(install.game_logs, LogKind.GAME, (".clog",))
    return {"files": files, **log_summary(files)}
