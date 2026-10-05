"""Locating and validating a War Thunder installation.

Detection sources, in order of trust:

1. ``HKCU\\Software\\Gaijin\\WarThunder`` - written by the official launcher and
   by far the fastest, most reliable signal.
2. Steam library metadata (app id 236390) - for Steam installs.
3. A running ``aces.exe``/``launcher.exe`` process image path.
4. A bounded filesystem scan of fixed drives.

A directory only counts as a valid install when the marker files the game
actually ships are present.  Nothing is guessed.
"""

from __future__ import annotations

import os
import re
import string
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .applog import log

__all__ = [
    "GameInstall",
    "Validation",
    "is_game_root",
    "detect_all",
    "detect_registry",
    "detect_steam",
    "detect_running",
    "detect_scan",
    "fixed_drives",
    "channel_of",
    "dev_markers",
    "circuit",
    "detect_dev_hints",
    "detect_netagent",
    "CHANNEL_STABLE",
    "CHANNEL_DEV",
    "CHANNEL_UNKNOWN",
    "CHANNELS",
    "CHANNEL_LABELS",
]

# --------------------------------------------------------------------------- #
#  Release channels
# --------------------------------------------------------------------------- #
#: The live/production client (what "War Thunder" normally means).
CHANNEL_STABLE = "stable"
#: The DEV-server client - a separate installation that tests upcoming updates.
CHANNEL_DEV = "dev"
CHANNEL_UNKNOWN = "unknown"
CHANNELS = (CHANNEL_STABLE, CHANNEL_DEV)
CHANNEL_LABELS = {CHANNEL_STABLE: "正式版", CHANNEL_DEV: "测试版"}

# How a dev-server client is recognised, taken from the official wiki page
# "DEV-server" (https://wiki.warthunder.com/mechanics/dev_server):
#   * the dev client is installed by a separate launcher, ``wt_dev_launcher.exe``,
#     into a folder of its own;
#   * the manual route is to copy the game folder, put an empty file named
#     ``matchingdevmode`` in it, and set ``yunetwork { curCircuit:t="dev" }``
#     in ``config.blk``.
DEV_MARKER_FILE = "matchingdevmode"
DEV_LAUNCHER_NAMES = ("wt_dev_launcher.exe", "launcher_dev.exe", "wtdevlauncher.exe")
DEV_CIRCUIT_VALUE = "dev"
PROD_CIRCUIT_VALUE = "production"
# Folder names the dev client is commonly installed to.  These are only
# *candidates*: every one of them is still verified with the markers above.
DEV_DIR_HINTS = (
    "WarThunderDev",
    "WarThunderDevServer",
    "WarThunder_Dev",
    "WarThunderDevClient",
    "WarThunderTest",
    "WarThunder Test",
    "WTDev",
    "WT_Dev",
)

_CIRCUIT_RE = re.compile(r'curCircuit\s*:\s*[a-z]\s*=\s*"([^"]*)"', re.IGNORECASE)
_NETAGENT_FOLDER_RE = re.compile(r'folder\s*:\s*t\s*=\s*"([^"]+)"', re.IGNORECASE)

# Files that must exist for us to trust a directory.
REQUIRED_MARKERS = ("config.blk",)
# Any one of these proves the game binaries are present.
BINARY_MARKERS = (
    os.path.join("win64", "aces.exe"),
    os.path.join("win32", "aces.exe"),
    "aces.vromfs.bin",
    "launcher.exe",
)
# Folders the scanner must never descend into.
SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "winsxs",
    "windowsapps",
    "programdata",
    "recovery",
    "perflogs",
    "$windows.~bt",
    "$windows.~ws",
    "node_modules",
    ".git",
    ".svn",
    "__pycache__",
    "appdata",
    "msocache",
    "intel",
    "amd",
    "nvidia",
    "drivers",
    "installer",
    "assembly",
    "servicing",
    "temp",
    "tmp",
    "cache",
}
NAME_HINTS = ("warthunder", "warthunderlauncher")
STEAM_APP_ID = "236390"


def fixed_drives() -> list[str]:
    """Existing fixed/removable drive roots that are readable."""
    drives: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def is_game_root(path: str) -> bool:
    """Cheap structural check used by the scanner."""
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, "config.blk")):
        return False
    return any(
        os.path.exists(os.path.join(path, marker)) for marker in BINARY_MARKERS
    )


@dataclass
class Validation:
    ok: bool
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok and not self.missing:
            return "文件完整" if not self.warnings else "；".join(self.warnings)
        if self.missing:
            return "缺少：" + "、".join(self.missing[:4])
        return "校验未通过"


@dataclass
class GameInstall:
    """A concrete War Thunder directory."""

    root: str
    source: str = "manual"
    confidence: str = "high"
    #: ``stable``, ``dev`` or ``""`` (derive it from the folder on demand).
    channel: str = ""

    # ----------------------------------------------------------------- channel
    def resolve_channel(self) -> str:
        """The channel this folder really is, decided by its own files."""
        return self.channel or channel_of(self.root)

    @property
    def is_dev(self) -> bool:
        return self.resolve_channel() == CHANNEL_DEV

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS.get(self.resolve_channel(), "未知版本")

    @property
    def circuit(self) -> str:
        """``yunetwork.curCircuit`` as written in config.blk."""
        return circuit(self.root)

    @property
    def dev_evidence(self) -> list[str]:
        return dev_markers(self.root)

    # ------------------------------------------------------------------ paths
    @property
    def launcher(self) -> str:
        return os.path.join(self.root, "launcher.exe")

    @property
    def dev_launcher(self) -> str:
        """The separate dev-server launcher, when the install ships one."""
        for name in DEV_LAUNCHER_NAMES:
            candidate = os.path.join(self.root, name)
            if os.path.isfile(candidate):
                return candidate
        return os.path.join(self.root, DEV_LAUNCHER_NAMES[0])

    @property
    def aces64(self) -> str:
        return os.path.join(self.root, "win64", "aces.exe")

    @property
    def aces32(self) -> str:
        return os.path.join(self.root, "win32", "aces.exe")

    @property
    def aces(self) -> str:
        """Preferred game executable (64-bit when available)."""
        return self.aces64 if os.path.isfile(self.aces64) else self.aces32

    @property
    def arch(self) -> str:
        return "64-bit" if os.path.isfile(self.aces64) else ("32-bit" if os.path.isfile(self.aces32) else "未知")

    @property
    def config(self) -> str:
        return os.path.join(self.root, "config.blk")

    def sub(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def game_logs(self) -> str:
        return self.sub(".game_logs")

    @property
    def launcher_logs(self) -> str:
        return self.sub(".launcher_log")

    @property
    def startapp_logs(self) -> str:
        return self.sub(".start_app_logs")

    @property
    def replays(self) -> str:
        return self.sub("Replays")

    @property
    def screenshots(self) -> str:
        return self.sub("Screenshots")

    @property
    def user_skins(self) -> str:
        return self.sub("UserSkins")

    @property
    def user_sights(self) -> str:
        return self.sub("UserSights")

    @property
    def user_missions(self) -> str:
        return self.sub("UserMissions")

    @property
    def sound(self) -> str:
        return self.sub("sound")

    @property
    def cache(self) -> str:
        return self.sub("cache")

    @property
    def compiled_shaders(self) -> str:
        return self.sub("compiledShaders")

    # -------------------------------------------------------------- metadata
    @property
    def exists(self) -> bool:
        return bool(self.root) and os.path.isdir(self.root)

    @property
    def display_name(self) -> str:
        return os.path.basename(self.root.rstrip("\\/")) or self.root

    def validate(self) -> Validation:
        result = Validation(ok=True)
        if not self.exists:
            return Validation(ok=False, missing=["游戏目录不存在"])

        for marker in REQUIRED_MARKERS:
            if os.path.isfile(os.path.join(self.root, marker)):
                result.present.append(marker)
            else:
                result.missing.append(marker)
                result.ok = False

        if not any(os.path.exists(os.path.join(self.root, m)) for m in BINARY_MARKERS):
            result.missing.append("win64/aces.exe")
            result.ok = False
        else:
            result.present.append("win64/aces.exe" if os.path.isfile(self.aces64) else "win32/aces.exe")

        if not os.path.isfile(self.launcher):
            result.warnings.append("未找到 launcher.exe，将只能直接启动游戏")

        if not os.path.isdir(self.sub("levels")):
            result.warnings.append("缺少 levels 目录，可能是未下载完整")

        for label, path in (
            ("涂装目录", self.user_skins),
            ("瞄具目录", self.user_sights),
            ("回放目录", self.replays),
        ):
            if not os.path.isdir(path):
                result.warnings.append(f"{label}不存在")
        return result

    def version(self) -> str | None:
        """Game client version read from the executable's version resource."""
        from .winutil import file_version

        for candidate in (self.aces64, self.aces32, self.launcher):
            version = file_version(candidate)
            if version and version != "0.0.0.0":
                return version
        return None

    def launcher_version(self) -> str | None:
        from .winutil import file_version

        return file_version(self.launcher)

    def is_running(self) -> bool:
        from .winutil import find_processes

        procs = find_processes({"aces", "aces-min-cpu"})
        for proc in procs:
            if proc.path and os.path.normcase(os.path.dirname(proc.path)).startswith(
                os.path.normcase(self.root)
            ):
                return True
        return bool(procs) and any(not p.path for p in procs)

    def running_processes(self) -> list:
        from .winutil import find_processes

        procs = find_processes({"aces", "aces-min-cpu"})
        root = os.path.normcase(os.path.normpath(self.root))
        matched = [p for p in procs if p.path and os.path.normcase(p.path).startswith(root)]
        if not matched:
            # Processes we cannot read the path of are still suspicious.
            matched = [p for p in procs if not p.path]
        return matched

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"<GameInstall {self.root} [{self.source}]>"


# --------------------------------------------------------------------------- #
#  Detection sources
# --------------------------------------------------------------------------- #
def _clean_registry_path(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().strip('"')
    if not value:
        return None
    # The launcher stores either the directory or a full exe path.
    if value.lower().endswith(".exe"):
        value = os.path.dirname(value)
    return value


def detect_registry(channel: str = "") -> list[str]:
    """Read the Gaijin launcher registry keys.

    Every subkey under ``Software\\Gaijin`` is inspected, not just the
    ``WarThunder`` one, so a dev-server launcher that registers itself under a
    name of its own is picked up as well; the folder is then classified by its
    own files rather than by the key name.
    """
    try:
        import winreg
    except ImportError:
        return []

    found: list[str] = []
    subkeys: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for base in (r"Software\Gaijin", r"SOFTWARE\WOW6432Node\Gaijin"):
            try:
                with winreg.OpenKey(hive, base, 0, winreg.KEY_READ) as parent:
                    count = winreg.QueryInfoKey(parent)[0]
                    for index in range(count):
                        try:
                            name = winreg.EnumKey(parent, index)
                        except OSError:
                            continue
                        prefix = "Software" if base.startswith("Software") else r"SOFTWARE\WOW6432Node"
                        subkeys.append(f"{prefix}\\Gaijin\\{name}")
            except OSError:
                continue
    # The well-known key first, then anything else the launchers registered.
    subkeys.sort(key=lambda k: ("warthunder" not in k.lower(), k))

    value_names = ("InstallDir", "InstallPath", "Dir", "Path")
    for subkey in _dedupe(subkeys):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                    for name in value_names:
                        try:
                            value, _ = winreg.QueryValueEx(key, name)
                        except OSError:
                            continue
                        cleaned = _clean_registry_path(value)
                        if cleaned and os.path.isdir(cleaned) and _matches_channel(cleaned, channel):
                            found.append(cleaned)
            except OSError:
                continue

    # Uninstall entries, as a secondary source.
    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in uninstall_roots:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as root_key:
                count = winreg.QueryInfoKey(root_key)[0]
                for index in range(count):
                    try:
                        name = winreg.EnumKey(root_key, index)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(root_key, name, 0, winreg.KEY_READ) as entry:
                            try:
                                display, _ = winreg.QueryValueEx(entry, "DisplayName")
                            except OSError:
                                continue
                            if "war thunder" not in str(display).lower():
                                continue
                            for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    value, _ = winreg.QueryValueEx(entry, value_name)
                                except OSError:
                                    continue
                                cleaned = _clean_registry_path(value)
                                if cleaned and os.path.isdir(cleaned) and _matches_channel(cleaned, channel):
                                    found.append(cleaned)
                    except OSError:
                        continue
        except OSError:
            continue
    return _dedupe(found)


def _steam_roots() -> list[str]:
    roots = []
    try:
        import winreg

        for hive, subkey, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value:
                        roots.append(str(value))
            except OSError:
                continue
    except ImportError:
        pass
    roots.extend(
        [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
            r"D:\Steam",
            r"D:\SteamLibrary",
            r"E:\Steam",
            r"E:\SteamLibrary",
        ]
    )
    return _dedupe([r for r in roots if os.path.isdir(r)])


def detect_steam(channel: str = "") -> list[str]:
    """Parse Steam library folders for a War Thunder install."""
    found: list[str] = []
    vdf_paths: list[str] = []
    for root in _steam_roots():
        for rel in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                vdf_paths.append(candidate)
        direct = os.path.join(root, "steamapps", "common", "War Thunder")
        if os.path.isdir(direct) and _matches_channel(direct, channel):
            found.append(direct)

    libraries: list[str] = []
    for vdf in vdf_paths:
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        libraries.extend(re.findall(r'"path"\s+"([^"]+)"', text))
        libraries.extend(re.findall(r'"\d+"\s+"([A-Za-z]:\\\\[^"]+)"', text))

    for library in libraries:
        library = library.replace("\\\\", "\\")
        for rel in (
            os.path.join("steamapps", "common", "War Thunder"),
            os.path.join("common", "War Thunder"),
        ):
            candidate = os.path.join(library, rel)
            if os.path.isdir(candidate) and _matches_channel(candidate, channel):
                found.append(candidate)
    return _dedupe(found)


def detect_running(channel: str = "") -> list[str]:
    """Derive the install root from a running game/launcher process."""
    from .winutil import find_processes

    found: list[str] = []
    for proc in find_processes({"aces", "aces-min-cpu", "launcher", "beac_wt_mlauncher"}):
        if not proc.path:
            continue
        parent = os.path.dirname(proc.path)
        candidates = [parent, os.path.dirname(parent)]
        for candidate in candidates:
            if is_game_root(candidate) and _matches_channel(candidate, channel):
                found.append(candidate)
    return _dedupe(found)


def detect_scan(
    *,
    drives: Iterable[str] | None = None,
    max_depth: int = 4,
    on_progress: Callable[[str, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    budget_seconds: float = 90.0,
    channel: str = "",
) -> list[str]:
    """Bounded breadth-first scan of the fixed drives.

    ``on_progress(current_path, dirs_visited)`` fires often enough to drive a
    GUI indicator.  Honours a wall-clock budget so it can never hang forever.
    """
    roots = list(drives) if drives else fixed_drives()
    found: list[str] = []
    started = time.monotonic()
    visited = 0

    for root in roots:
        if should_cancel and should_cancel():
            break
        queue: list[tuple[str, int]] = [(root, 0)]
        while queue:
            if should_cancel and should_cancel():
                break
            if time.monotonic() - started > budget_seconds:
                log.warn("自动搜索达到时间上限，已停止扫描", "路径检测")
                queue.clear()
                break
            current, depth = queue.pop(0)
            visited += 1
            if on_progress and visited % 8 == 0:
                on_progress(current, visited)
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue

            for entry in entries:
                if should_cancel and should_cancel():
                    break
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                name = entry.name
                lowered = name.lower()
                if lowered in SKIP_DIR_NAMES or name.startswith((".", "$")):
                    continue
                # Strong signal: a folder whose name looks like the game.
                if any(hint in lowered.replace(" ", "").replace("_", "") for hint in NAME_HINTS):
                    if is_game_root(entry.path) and _matches_channel(entry.path, channel):
                        found.append(entry.path)
                        continue
                if depth + 1 <= max_depth:
                    queue.append((entry.path, depth + 1))
    if on_progress:
        on_progress("", visited)
    return _dedupe(found)


def _dedupe(paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if not path:
            continue
        try:
            norm = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        except (OSError, ValueError):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(os.path.normpath(os.path.abspath(path)))
    return out


# --------------------------------------------------------------------------- #
#  Channel classification (stable vs dev server)
# --------------------------------------------------------------------------- #
def circuit(root: str) -> str:
    """Value of ``yunetwork.curCircuit`` in ``config.blk`` (``""`` if absent).

    The live client ships ``curCircuit:t="production"`` and the dev client
    ``curCircuit:t="dev"`` - this is the clearest in-file discriminator there is.
    """
    if not root:
        return ""
    path = os.path.join(root, "config.blk")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(200_000)
    except OSError:
        return ""
    match = _CIRCUIT_RE.search(head)
    return match.group(1).strip() if match else ""


def dev_markers(root: str) -> list[str]:
    """Human-readable evidence that *root* is a DEV-server client."""
    if not root or not os.path.isdir(root):
        return []
    found: list[str] = []
    marker = os.path.join(root, DEV_MARKER_FILE)
    if os.path.isfile(marker):
        found.append(DEV_MARKER_FILE)
    for name in DEV_LAUNCHER_NAMES:
        if os.path.isfile(os.path.join(root, name)):
            found.append(name)
    value = circuit(root)
    if value and value.lower() == DEV_CIRCUIT_VALUE:
        found.append(f'config.blk: yunetwork.curCircuit="{value}"')
    return found


def channel_of(root: str) -> str:
    """Classify a directory as ``dev`` / ``stable`` / ``unknown`` from evidence.

    A dev marker wins outright, so a dev copy is never mistaken for the live
    client even though it contains the same binaries.
    """
    if not root or not os.path.isdir(root):
        return CHANNEL_UNKNOWN
    if dev_markers(root):
        return CHANNEL_DEV
    value = circuit(root)
    if value:
        return CHANNEL_STABLE
    if is_game_root(root):
        return CHANNEL_STABLE
    return CHANNEL_UNKNOWN


def _matches_channel(root: str, channel: str) -> bool:
    if not channel:
        return True
    return channel_of(root) == channel


def detect_netagent(channel: str = "") -> list[str]:
    """Install folders recorded by Gaijin's NetAgent updater.

    ``%LOCALAPPDATA%\\Gaijin\\NetAgent\\targets\\*.blk`` holds one small block per
    launcher-known install (``folder:t=``/``project:t=``/``launcher:t=``), so it
    lists the dev client too if its launcher ever ran.
    """
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Gaijin", "NetAgent", "targets")
    if not base or not os.path.isdir(base):
        return []
    found: list[str] = []
    try:
        entries = [e for e in os.scandir(base) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    for entry in entries:
        try:
            with open(entry.path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(8192)
        except OSError:
            continue
        for match in _NETAGENT_FOLDER_RE.finditer(text):
            candidate = match.group(1).strip().replace("\\\\", "\\")
            if os.path.isdir(candidate) and _matches_channel(candidate, channel):
                found.append(candidate)
    return _dedupe(found)


def detect_dev_hints(channel: str = CHANNEL_DEV) -> list[str]:
    """Probe the folder names the dev client is commonly installed to."""
    found: list[str] = []
    parents: list[str] = []
    for drive in fixed_drives():
        parents.append(drive)
    for name in ("Games", "Program Files", "Program Files (x86)", "SteamLibrary", "Steam"):
        for drive in fixed_drives():
            parent = os.path.join(drive, name)
            if os.path.isdir(parent):
                parents.append(parent)
    for parent in _dedupe(parents):
        for hint in DEV_DIR_HINTS:
            candidate = os.path.join(parent, hint)
            if os.path.isdir(candidate) and _matches_channel(candidate, channel):
                found.append(candidate)
    return _dedupe(found)


def detect_all(
    *,
    channel: str = CHANNEL_STABLE,
    include_scan: bool = True,
    on_progress: Callable[[str, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    extra_candidates: Iterable[str] | None = None,
) -> list[GameInstall]:
    """Run every detector and return validated candidates, best first.

    ``channel`` selects which client we are looking for: the live client and the
    DEV-server client are separate installations, so a folder that belongs to
    one is never offered for the other.
    """
    ordered: list[tuple[str, str, str]] = []  # (root, source, confidence)

    for path in detect_registry(channel):
        ordered.append((path, "registry", "high"))
    for path in detect_netagent(channel):
        ordered.append((path, "netagent", "high"))
    for path in detect_steam(channel):
        ordered.append((path, "steam", "high"))
    for path in detect_running(channel):
        ordered.append((path, "process", "high"))
    for path in detect_dev_hints(channel):
        ordered.append((path, "dev-hint", "medium"))
    for path in _dedupe(list(extra_candidates or [])):
        ordered.append((path, "remembered", "medium"))
    if include_scan:
        for path in detect_scan(on_progress=on_progress, should_cancel=should_cancel, channel=channel):
            ordered.append((path, "scan", "medium"))

    results: list[GameInstall] = []
    seen: set[str] = set()
    for root, source, confidence in ordered:
        norm = os.path.normcase(os.path.normpath(root))
        if norm in seen:
            continue
        if not os.path.isdir(root):
            continue
        if not _matches_channel(root, channel):
            continue
        seen.add(norm)
        install = GameInstall(
            root=root, source=source, confidence=confidence, channel=channel_of(root)
        )
        results.append(install)

    # Valid installs float to the top.
    results.sort(key=lambda i: (not i.validate().ok, i.source == "scan"))
    return results
