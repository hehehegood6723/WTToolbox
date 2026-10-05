"""In-app activity log.

This backs the "运行日志" panel: a bounded in-memory ring buffer plus a rotating
file on disk, with listener callbacks so the GUI can stream new records.

It deliberately does **not** import Qt - the UI wraps it in a ``QObject`` that
re-emits records as a signal.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Iterable

from . import appdirs

__all__ = ["LogRecord", "AppLog", "log", "LEVELS"]

LEVELS = ("DEBUG", "INFO", "OK", "WARN", "ERROR")

_LEVEL_ORDER = {name: i for i, name in enumerate(LEVELS)}


@dataclass(frozen=True, slots=True)
class LogRecord:
    ts: float
    level: str
    source: str
    message: str

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts))

    def format_line(self) -> str:
        tag = f"[{self.level}]"
        src = f"[{self.source}] " if self.source else ""
        return f"[{self.clock}] {tag} {src}{self.message}"

    def as_file_line(self) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.ts))
        src = f"[{self.source}] " if self.source else ""
        return f"{stamp} [{self.level}] {src}{self.message}"


class AppLog:
    """Bounded, thread-safe, listener-driven log."""

    def __init__(self, *, capacity: int = 800, filename: str = "wttoolbox.log") -> None:
        self._buffer: Deque[LogRecord] = deque(maxlen=capacity)
        self._listeners: list[Callable[[LogRecord], None]] = []
        self._lock = threading.RLock()
        self._path = os.path.join(appdirs.logs_dir(), filename)
        self._max_bytes = 2 * 1024 * 1024
        self._keep = 3
        self._to_file = True
        self._min_level = "DEBUG"
        self._last_prune = 0.0

    # ------------------------------------------------------------- plumbing
    def subscribe(self, callback: Callable[[LogRecord], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def set_file_logging(self, enabled: bool) -> None:
        self._to_file = enabled

    def set_min_level(self, level: str) -> None:
        if level in _LEVEL_ORDER:
            self._min_level = level

    @property
    def path(self) -> str:
        return self._path

    def records(self, *, min_level: str = "DEBUG", limit: int | None = None) -> list[LogRecord]:
        threshold = _LEVEL_ORDER.get(min_level, 0)
        with self._lock:
            out = [r for r in self._buffer if _LEVEL_ORDER.get(r.level, 0) >= threshold]
        if limit is not None:
            out = out[-limit:]
        return out

    def text(self, **kwargs) -> str:
        return "\n".join(r.format_line() for r in self.records(**kwargs))

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    # --------------------------------------------------------------- writing
    def emit(self, level: str, message: str, source: str = "") -> LogRecord:
        level = level.upper()
        if level not in _LEVEL_ORDER:
            level = "INFO"
        record = LogRecord(ts=time.time(), level=level, source=source, message=str(message))

        if _LEVEL_ORDER[level] < _LEVEL_ORDER.get(self._min_level, 0):
            return record

        with self._lock:
            self._buffer.append(record)
            listeners = list(self._listeners)

        for cb in listeners:
            try:
                cb(record)
            except Exception:
                # A broken listener must never take down the app.
                pass

        if self._to_file:
            self._write_file(record)
        return record

    def debug(self, message: str, source: str = "") -> LogRecord:
        return self.emit("DEBUG", message, source)

    def info(self, message: str, source: str = "") -> LogRecord:
        return self.emit("INFO", message, source)

    def ok(self, message: str, source: str = "") -> LogRecord:
        return self.emit("OK", message, source)

    def warn(self, message: str, source: str = "") -> LogRecord:
        return self.emit("WARN", message, source)

    def error(self, message: str, source: str = "") -> LogRecord:
        return self.emit("ERROR", message, source)

    # ------------------------------------------------------------------ file
    def _write_file(self, record: LogRecord) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(record.as_file_line() + "\n")
        except OSError:
            return
        now = time.monotonic()
        if now - self._last_prune > 30:
            self._last_prune = now
            self._prune()

    def _prune(self) -> None:
        try:
            if os.path.getsize(self._path) < self._max_bytes:
                return
            for index in range(self._keep - 1, 0, -1):
                src = f"{self._path}.{index}"
                dst = f"{self._path}.{index + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            os.replace(self._path, f"{self._path}.1")
        except OSError:
            pass


log = AppLog()
