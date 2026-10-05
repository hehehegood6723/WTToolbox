"""Application data directories and bundled-resource resolution."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

APP_DIRNAME = "WTToolbox"
#: The tool used to be called ThunderKit; its data directory is migrated once
#: so settings, caches, backups and the mod rollback sets are not lost.
LEGACY_APP_DIRNAME = "ThunderKit"

_migration_checked = False


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> str:
    """Locate a bundled resource (works both from source and from the exe)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # .../src/wttoolbox/core/appdirs.py -> .../src/wttoolbox
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def _roaming_base() -> str:
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.expanduser("~")
    return base


def migrate_legacy_dir() -> str:
    """Move ``%APPDATA%\\ThunderKit`` to ``%APPDATA%\\WTToolbox``.

    Idempotent and never destructive:

    * does nothing once the new directory already holds a ``settings.json``;
    * prefers a plain rename (instant, same volume), but only when the
      destination does not exist - Windows refuses to rename onto an existing
      directory with WinError 183, which is exactly how an earlier version of
      this function silently lost the user's settings;
    * otherwise copies the legacy files in **one by one and never overwriting**
      anything the new build already wrote.

    Never raises.
    """
    global _migration_checked
    if _migration_checked:
        return ""
    _migration_checked = True

    base = _roaming_base()
    new = os.path.join(base, APP_DIRNAME)
    old = os.path.join(base, LEGACY_APP_DIRNAME)
    if not os.path.isdir(old) or os.path.normcase(old) == os.path.normcase(new):
        return ""
    if os.path.isfile(os.path.join(new, "settings.json")):
        return ""  # already migrated

    if not os.path.isdir(new):
        try:
            os.rename(old, new)
            return f"moved {old} -> {new}"
        except OSError:
            pass  # fall through to the copy

    copied = 0
    for current, _dirs, names in os.walk(old):
        relative = os.path.relpath(current, old)
        target = new if relative == "." else os.path.join(new, relative)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            continue
        for name in names:
            source = os.path.join(current, name)
            destination = os.path.join(target, name)
            if os.path.exists(destination):
                continue  # never clobber newer data
            try:
                shutil.copy2(source, destination)
                copied += 1
            except OSError:
                continue
    return f"copied {copied} file(s) from {old}" if copied else ""


def appdata_dir(*parts: str) -> str:
    """``%APPDATA%\\WTToolbox`` plus optional sub-parts (created on demand).

    Directory creation is best-effort: a locked-down or redirected ``%APPDATA%``
    must degrade to "feature unavailable", never to a crash on startup.
    """
    migrate_legacy_dir()
    path = os.path.join(_roaming_base(), APP_DIRNAME, *parts)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def logs_dir() -> str:
    return appdata_dir("logs")


def cache_dir() -> str:
    return appdata_dir("cache")


def backups_dir() -> str:
    return appdata_dir("backups")


def mods_dir() -> str:
    return appdata_dir("mods")


def trash_dir() -> str:
    return appdata_dir("trash")


def settings_file() -> str:
    return os.path.join(appdata_dir(), "settings.json")


def temp_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), APP_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return path


def ensure_all() -> None:
    """Create every directory the app uses.  Never raises."""
    for fn in (logs_dir, cache_dir, backups_dir, mods_dir, trash_dir):
        try:
            fn()
        except OSError:
            continue
