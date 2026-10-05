"""Persistent application settings (JSON, atomic writes)."""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from . import appdirs

__all__ = ["Settings", "DEFAULTS"]

DEFAULTS: dict[str, Any] = {
    # --- game ---------------------------------------------------------------
    "game_path": "",
    "game_path_dev": "",
    "known_paths": [],
    "known_paths_dev": [],
    "active_channel": "stable",
    "auto_detect_on_start": True,
    # --- appearance ---------------------------------------------------------
    "theme": "light",
    "accent": "#FF9E1B",
    "font_scale": 1.0,
    "start_page": "home",
    # --- features -----------------------------------------------------------
    "vehicle_compare": [],       # slugs of the last compared pair
    "wt_nickname": "",
    "stats_disclaimer_accepted": False,
    "stats_disclaimer_at": 0.0,
    # --- behaviour ----------------------------------------------------------
    "confirm_delete": True,
    "use_trash": True,
    "minimize_to_tray": True,
    "close_to_tray": False,
    "tray_notifications": True,
    "start_with_windows": False,
    "start_minimized": False,
    # --- data ---------------------------------------------------------------
    "news_locale": "zh",
    "news_cache_minutes": 30,
    "news_enabled": True,
    "log_keep_days": 14,
    "backup_keep": 20,
    # --- window -------------------------------------------------------------
    "window": {"w": 1180, "h": 760, "x": None, "y": None, "maximized": False},
    # --- meta ---------------------------------------------------------------
    "first_run": True,
    "stats": {"launches": 0, "last_launch": None, "play_seconds": 0},
}


class Settings:
    """Thread-safe settings store backed by ``%APPDATA%\\WTToolbox\\settings.json``."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or appdirs.settings_file()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._listeners: list = []
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                if isinstance(stored, dict):
                    self._merge(stored)
            except FileNotFoundError:
                pass
            except (OSError, json.JSONDecodeError):
                # Corrupt settings must never block startup.
                pass

    def _merge(self, stored: dict) -> None:
        for key, value in stored.items():
            if key == "window" and isinstance(value, dict):
                merged = dict(DEFAULTS["window"])
                merged.update({k: v for k, v in value.items() if k in merged})
                self._data["window"] = merged
            elif key == "stats" and isinstance(value, dict):
                merged = dict(DEFAULTS["stats"])
                merged.update(value)
                self._data["stats"] = merged
            else:
                self._data[key] = value

    def save(self) -> bool:
        with self._lock:
            data = copy.deepcopy(self._data)
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------- accessors
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
            if default is not None:
                return default
            return copy.deepcopy(DEFAULTS.get(key))

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
        if save:
            self.save()
        self._notify(key, value)

    def update(self, values: dict[str, Any], *, save: bool = True) -> None:
        with self._lock:
            changed = {k: v for k, v in values.items() if self._data.get(k) != v}
            self._data.update(values)
        if save and changed:
            self.save()
        for key, value in changed.items():
            self._notify(key, value)

    def bump_stat(self, key: str, delta: float = 1) -> None:
        with self._lock:
            stats = dict(self._data.get("stats") or {})
            stats[key] = (stats.get(key) or 0) + delta
            self._data["stats"] = stats
        self.save()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------- listeners
    def on_change(self, callback) -> None:
        """Register ``callback(key, value)`` for programmatic updates."""
        self._listeners.append(callback)

    def _notify(self, key: str, value: Any) -> None:
        for cb in list(self._listeners):
            try:
                cb(key, value)
            except Exception:
                pass

    # ------------------------------------------------------------ game paths
    def remember_game_path(self, path: str, channel: str = "stable") -> None:
        """Record a path for one channel.  The two channels never overwrite
        each other: the dev client lives in its own folder."""
        dev = str(channel).lower() == "dev"
        path_key = "game_path_dev" if dev else "game_path"
        known_key = "known_paths_dev" if dev else "known_paths"
        with self._lock:
            known = list(self._data.get(known_key) or [])
            norm = os.path.normcase(os.path.normpath(path)) if path else ""
            known = [k for k in known if os.path.normcase(os.path.normpath(k)) != norm]
            if path:
                known.insert(0, path)
            self._data[known_key] = known[:8]
            if path:
                self._data[path_key] = path
        self.save()

    def game_path_for(self, channel: str = "stable") -> str:
        key = "game_path_dev" if str(channel).lower() == "dev" else "game_path"
        return str(self._data.get(key) or "")

    def known_game_paths(self, channel: str = "stable") -> list[str]:
        key = "known_paths_dev" if str(channel).lower() == "dev" else "known_paths"
        return [str(item) for item in (self._data.get(key) or []) if item]

    def forget_game_path(self, channel: str = "stable") -> None:
        dev = str(channel).lower() == "dev"
        with self._lock:
            self._data["game_path_dev" if dev else "game_path"] = ""
        self.save()

    # -------------------------------------------------------------- reset
    def reset(self) -> None:
        with self._lock:
            window = self._data.get("window")
            self._data = copy.deepcopy(DEFAULTS)
            if window:
                self._data["window"] = window
        self.save()

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)
