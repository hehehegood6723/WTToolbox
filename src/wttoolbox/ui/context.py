"""Shared application context handed to every page.

Keeps page constructors uniform - ``Page(ctx, parent=None)`` - and centralises
"is a game selected yet?" handling so each page does not reinvent it.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QObject, Signal

from .. import APP_NAME, APP_NAME_ZH, __version__
from ..core import appdirs, gamepath
from ..core.applog import AppLog, log as global_log
from ..core.gamepath import GameInstall
from ..core.settings import Settings
from . import theme

__all__ = ["AppContext"]

PAGE_ORDER = ("home", "sound", "tools", "library", "vehicles", "stats", "settings")
PAGE_LABELS = {
    "home": "主页",
    "sound": "音效模组",
    "tools": "工具箱",
    "library": "信息库",
    "vehicles": "载具对比",
    "stats": "战绩",
    "settings": "设置",
}
PAGE_ICONS = {
    "home": "home",
    "sound": "sound",
    "tools": "grid",
    "library": "layers",
    "vehicles": "crosshair",
    "stats": "shield",
    "settings": "gear",
}
PAGE_SHORT = {
    "home": "主页",
    "sound": "音效",
    "tools": "工具",
    "library": "信息",
    "vehicles": "载具",
    "stats": "战绩",
    "settings": "设置",
}


class AppContext(QObject):
    """Session-wide state: settings, current install, logging, navigation.

    War Thunder ships as two *separate* installations - the live client and the
    DEV-server client - so the context keeps one install per channel and a
    pointer to the channel the tools currently act on.  The two never mix: each
    is detected, validated and stored independently.
    """

    installChanged = Signal(object)
    channelChanged = Signal(str)
    logRecord = Signal(object)
    themeChanged = Signal(str)
    requestPage = Signal(str)
    installsFound = Signal(object)

    def __init__(self, settings: Settings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings or Settings()
        self.log: AppLog = global_log
        self._installs: dict[str, GameInstall | None] = {
            gamepath.CHANNEL_STABLE: None,
            gamepath.CHANNEL_DEV: None,
        }
        self._channel = str(self.settings.get("active_channel") or gamepath.CHANNEL_STABLE)
        if self._channel not in gamepath.CHANNELS:
            self._channel = gamepath.CHANNEL_STABLE
        self.version = __version__
        self.name = APP_NAME
        self.name_zh = APP_NAME_ZH
        self.palette = theme.current()
        self.known_installs: list[GameInstall] = []

    # ---------------------------------------------------------------- install
    @property
    def active_channel(self) -> str:
        return self._channel

    def install_for(self, channel: str) -> GameInstall | None:
        return self._installs.get(channel)

    def has_install_for(self, channel: str) -> bool:
        install = self._installs.get(channel)
        return install is not None and install.exists

    @property
    def install(self) -> GameInstall | None:
        """The install the tools act on (the active channel's)."""
        return self._installs.get(self._channel)

    @property
    def stable_install(self) -> GameInstall | None:
        return self._installs.get(gamepath.CHANNEL_STABLE)

    @property
    def dev_install(self) -> GameInstall | None:
        return self._installs.get(gamepath.CHANNEL_DEV)

    @property
    def has_install(self) -> bool:
        install = self.install
        return install is not None and install.exists

    @property
    def has_dev_install(self) -> bool:
        return self.has_install_for(gamepath.CHANNEL_DEV)

    def set_active_channel(self, channel: str) -> None:
        """Point the tools at the other client."""
        if channel not in gamepath.CHANNELS or channel == self._channel:
            return
        self._channel = channel
        self.settings.set("active_channel", channel)
        self.channelChanged.emit(channel)
        self.installChanged.emit(self.install)

    def set_install(
        self,
        install: GameInstall | None,
        *,
        remember: bool = True,
        channel: str | None = None,
    ) -> None:
        target = channel or (install.channel if install is not None and install.channel else self._channel)
        if target not in gamepath.CHANNELS:
            target = gamepath.CHANNEL_STABLE
        if install is not None and not install.exists:
            self.log.warn(f"游戏目录不存在：{install.root}", "路径")
            install = None
        if install is not None and not install.channel:
            install.channel = gamepath.channel_of(install.root)
        # A folder that belongs to the other channel is refused outright - and
        # refusing must leave whatever was already stored untouched, otherwise
        # one mistyped path would silently wipe a working configuration.
        if install is not None and install.channel != target:
            wanted = gamepath.CHANNEL_LABELS[target]
            self.log.warn(
                f"目录版本不符，已忽略：{install.root} 是{install.channel_label}，"
                f"不能作为{wanted}客户端",
                "路径",
            )
            return
        self._installs[target] = install
        if install is not None and remember:
            self.settings.remember_game_path(install.root, target)
        self.installChanged.emit(self.install)

    def require_install(self, parent=None, *, reason: str = "此功能") -> GameInstall | None:
        """Return the active install, or complain and route the user home."""
        if self.has_install:
            return self.install
        from .widgets import notify

        if parent is not None:
            label = gamepath.CHANNEL_LABELS.get(self._channel, "游戏")
            notify(parent, f"{reason}需要先设置{label}目录", "warn")
        self.requestPage.emit("home")
        return None

    # -------------------------------------------------------------- utilities
    @property
    def app_dir(self) -> str:
        return appdirs.appdata_dir()

    @property
    def log_file(self) -> str:
        return self.log.path

    def notify(self, widget, text: str, kind: str = "info", duration: int = 2600) -> None:
        from .widgets import notify

        notify(widget, text, kind, duration)

    def set_theme(self, name: str) -> None:
        """Switch the palette.

        The single source of truth is :func:`theme.current` - the palette that
        is actually applied.  ``self.palette`` is only a cached copy for callers
        that want it, and it is refreshed here; an earlier version compared
        against that stale copy, so once the theme had changed the guard either
        blocked the switch back or re-emitted the same theme forever.
        """
        name = "dark" if str(name).lower() == "dark" else "light"
        self.palette = theme.palette_for(name)
        self.settings.set("theme", name)
        if theme.current().name == name:
            return
        self.themeChanged.emit(name)

    def toggle_theme(self) -> None:
        # Derive the next theme from what is really on screen, not from a
        # cached value: the missed "back to light" bug came from exactly that.
        self.set_theme("light" if theme.current().name == "dark" else "dark")

    def page_label(self, key: str) -> str:
        return PAGE_LABELS.get(key, key)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        stable = self._installs.get(gamepath.CHANNEL_STABLE)
        dev = self._installs.get(gamepath.CHANNEL_DEV)
        return (
            f"<AppContext theme={self.palette.name} channel={self._channel} "
            f"stable={stable.root if stable else '—'} dev={dev.root if dev else '—'}>"
        )
