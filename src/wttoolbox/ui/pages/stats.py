"""战绩 - the account page.

Honest scope, verified on this machine:

* ``warthunder.com/*/community/userinfo/`` answers **HTTP 403** with the body
  "Enable JavaScript and cookies to continue" - an anti-automation wall.  This
  page therefore does *not* scrape, guess or fabricate stats, and it does not
  touch the encrypted ``IntAuth*`` blobs the launcher keeps in the registry.
* What it does instead: opens the official profile in the user's browser, shows
  direct official links, and displays the **local** game record that can be
  measured truthfully (playtime tracked by WTToolbox, launch counts, replays,
  screenshots, install integrity).

Access requires accepting a disclaimer first; the acceptance is remembered.
"""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ...core import appdirs, gamelog, replays, winutil
from .. import icons, theme, widgets

__all__ = ["StatsPage"]

PROFILE_URL = "https://warthunder.com/zh/community/userinfo/?nick={nick}"
LIVE_URL = "https://live.warthunder.com/user/{nick}/"
REGISTRY_KEY = r"Software\Gaijin\WarThunder"

_NICK_PATTERNS = (
    re.compile(r'"nick(?:name)?"\s*:\s*"([A-Za-z0-9_\-\[\]]{3,24})"'),
    re.compile(r"\bnick(?:name)?\s*[=:]\s*([A-Za-z0-9_\-\[\]]{3,24})", re.I),
    re.compile(r'"userName"\s*:\s*"([A-Za-z0-9_\-\[\]]{3,24})"'),
)
_NICK_BLOCKLIST = {
    "null", "undefined", "true", "false", "user", "login", "player", "unknown",
    "nickname", "username", "guest", "default", "test",
}


def find_local_nickname(install) -> tuple[str, str]:
    """Best-effort local nickname search.  Returns ``(nick, where)``.

    Only plain-text logs and small config files are read; the launcher's
    encrypted credential blobs are deliberately never touched.
    """
    candidates: list[tuple[str, str]] = []
    folders = []
    if install is not None and install.exists:
        folders = [install.launcher_logs, install.startapp_logs]

    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            entries = sorted(
                (e for e in os.scandir(folder) if e.is_file(follow_symlinks=False)),
                key=lambda e: e.stat(follow_symlinks=False).st_mtime,
                reverse=True,
            )[:6]
        except OSError:
            continue
        for entry in entries:
            try:
                with open(entry.path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read(2_000_000)
            except OSError:
                continue
            for pattern in _NICK_PATTERNS:
                for match in pattern.finditer(text):
                    value = match.group(1)
                    if value.lower() in _NICK_BLOCKLIST:
                        continue
                    candidates.append((value, os.path.basename(entry.path)))
    if candidates:
        return candidates[0]
    return "", ""


class StatsPage(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(0)
        self._outer = outer

        self.scroll = widgets.ScrollColumn()
        outer.addWidget(self.scroll, 1)

        self.gate = None
        self._mount()

    # ------------------------------------------------------------------ mount
    _TRANSIENT = (
        "scroll", "nick_edit", "nick_status", "tile_playtime", "tile_launches",
        "tile_replays", "tile_shots", "local_rows",
    )

    def _mount(self) -> None:
        accepted = bool(self.ctx.settings.get("stats_disclaimer_accepted", False))
        while self._outer.count() > 0:
            item = self._outer.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        # Drop stale references: the widgets above are gone, so keeping the
        # attributes would leave the page pointing at deleted C++ objects.
        # (Check __dict__ - names like "scroll" also exist as QWidget methods.)
        for name in self._TRANSIENT:
            if name in self.__dict__:
                del self.__dict__[name]

        if not accepted:
            holder = widgets.Card()
            empty = widgets.EmptyState(
                "使用前需要先确认免责声明",
                "战绩功能会打开 War Thunder 官方网页，请先阅读并同意下方声明。",
                icon_name="shield",
            )
            empty.add_action(
                widgets.primary_button("阅读并同意免责声明", "shield", self._ask_disclaimer)
            )
            holder.add(empty)
            self._outer.addWidget(holder)
            return

        self.scroll = widgets.ScrollColumn()
        self._outer.addWidget(self.scroll, 1)
        self.scroll.add(self._build_disclaimer_card())
        self.scroll.add(self._build_profile_card())
        self.scroll.add(self._build_explain_card())
        self.scroll.add(self._build_local_card())
        self.scroll.body.addStretch(1)

    # ------------------------------------------------------------------ cards
    def _build_disclaimer_card(self) -> widgets.Card:
        accepted_at = self.ctx.settings.get("stats_disclaimer_at") or 0
        stamp = (
            __import__("time").strftime("%Y-%m-%d %H:%M", __import__("time").localtime(accepted_at))
            if accepted_at
            else "—"
        )
        card = widgets.Card("免责声明", f"已于 {stamp} 确认", icon_name="shield")
        card.add(
            widgets.make_label(
                "本工具是第三方非官方工具，与 Gaijin Entertainment 无任何关联。"
                "战绩页面仅会打开官方公开网页；本工具不绕过任何访问限制、不抓取账号数据、"
                "不读取或上传任何登录凭据。",
                "Muted", wrap=True,
            )
        )
        row = QHBoxLayout()
        row.setSpacing(9)
        row.addWidget(widgets.ghost_button("重新查看声明", "shield", self._ask_disclaimer))
        row.addWidget(
            widgets.ghost_button(
                "撤回同意", "x-circle", self._revoke, tooltip="撤回后将无法使用战绩页面"
            )
        )
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _build_profile_card(self) -> widgets.Card:
        card = widgets.Card("我的账号", "输入昵称后打开官方战绩页面", icon_name="home")

        row = QHBoxLayout()
        row.setSpacing(9)
        self.nick_edit = QLineEdit()
        self.nick_edit.setPlaceholderText("你的 War Thunder 游戏昵称（区分大小写）")
        self.nick_edit.setText(self.ctx.settings.get("wt_nickname", "") or "")
        self.nick_edit.setMinimumHeight(38)
        self.nick_edit.returnPressed.connect(self.open_profile)
        row.addWidget(self.nick_edit, 1)
        row.addWidget(
            widgets.subtle_button("尝试自动识别", "search", self._detect_nick,
                                  tooltip="在启动器日志中查找昵称（只读本地文本日志）")
        )
        card.add_layout(row)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(
            widgets.primary_button("打开官方战绩页面", "external", self.open_profile)
        )
        buttons.addWidget(widgets.subtle_button("打开我的社区主页", "link", self.open_live))
        buttons.addWidget(
            widgets.ghost_button("复制战绩页链接", "file", self._copy_link)
        )
        buttons.addStretch(1)
        card.add_layout(buttons)

        self.nick_status = widgets.make_label("", "Faint", wrap=True)
        card.add(self.nick_status)
        return card

    def _build_explain_card(self) -> widgets.Card:
        card = widgets.Card("为什么不能自动读取？", "已在本机实测的结论", icon_name="info")
        card.add(
            widgets.make_label(
                "本工具尝试过直接读取官方战绩接口，服务器返回的是 HTTP 403 反爬页面，"
                "页面正文写着 “Enable JavaScript and cookies to continue”。"
                "这属于站点的访问保护，本工具不会去绕过它。\n\n"
                "因此这里不会显示任何自动获取或推算出来的战绩数字——上面那个按钮会把"
                "官方页面交给你的浏览器打开（浏览器带着你自己的登录状态，可以正常显示）。",
                "Muted", wrap=True,
            )
        )
        row = QHBoxLayout()
        row.setSpacing(9)
        row.addWidget(
            widgets.ghost_button("打开官方战绩页（不带昵称）", "external",
                                 lambda: self._open("https://warthunder.com/zh/community/"))
        )
        row.addWidget(
            widgets.ghost_button("打开官方资讯", "external",
                                 lambda: self._open("https://warthunder.com/zh/news/"))
        )
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _build_local_card(self) -> widgets.Card:
        card = widgets.Card("本机游戏记录", "这些数据来自本机文件，可以真实统计", icon_name="drive")
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_playtime = widgets.StatTile("累计记录时长", "—", icon_name="clock")
        self.tile_launches = widgets.StatTile("记录启动次数", "—", icon_name="play")
        self.tile_replays = widgets.StatTile("回放", "—", icon_name="replay")
        self.tile_shots = widgets.StatTile("截图", "—", icon_name="image")
        for tile in (self.tile_playtime, self.tile_launches, self.tile_replays, self.tile_shots):
            tiles.addWidget(tile)
        card.add_layout(tiles)

        self.local_rows: dict[str, widgets.InfoRow] = {}
        for key, label in (
            ("game", "游戏目录"),
            ("version", "客户端版本"),
            ("integrity", "文件校验"),
            ("latest", "最近一场回放"),
            ("logs", "日志占用"),
            ("session", "本次会话"),
        ):
            info = widgets.InfoRow(label, "—")
            self.local_rows[key] = info
            card.add(info)

        row = QHBoxLayout()
        row.setSpacing(9)
        row.addWidget(widgets.subtle_button("刷新统计", "refresh", self.refresh_local))
        row.addWidget(
            widgets.ghost_button("打开回放目录", "folder", self._open_replays)
        )
        row.addStretch(1)
        card.add_layout(row)

        card.add(
            widgets.make_label(
                "注：Gaijin 账号的在线战绩（胜率、击杀、研发进度）只存在于官方服务器上，"
                "本工具无法离线取得；上面的数字全部来自本机文件，不包含任何推测值。",
                "Faint", wrap=True,
            )
        )
        return card

    # ------------------------------------------------------------- disclaimer
    def _ask_disclaimer(self) -> None:
        if widgets.disclaimer_dialog(self, self.ctx):
            self.ctx.settings.set("stats_disclaimer_accepted", True)
            self.ctx.settings.set("stats_disclaimer_at", __import__("time").time())
            self.ctx.log.ok("已确认战绩功能免责声明", "战绩")
            self._remount()
            self.refresh_local()

    def _revoke(self) -> None:
        accepted, _ = widgets.confirm(
            self,
            "撤回同意",
            "撤回后战绩页面将重新回到免责声明确认状态，你随时可以再次同意。",
            ok_text="撤回",
            danger=True,
        )
        if not accepted:
            return
        self.ctx.settings.set("stats_disclaimer_accepted", False)
        self.ctx.log.info("已撤回战绩功能免责声明的同意", "战绩")
        self._remount()

    def _remount(self) -> None:
        """Rebuild the body after the gate flips."""
        self._mount()

    # ----------------------------------------------------------------- actions
    def _current_nick(self) -> str:
        return self.nick_edit.text().strip() if hasattr(self, "nick_edit") else ""

    def _save_nick(self, nick: str) -> None:
        if nick:
            self.ctx.settings.set("wt_nickname", nick)

    def _open(self, url: str) -> None:
        ok, detail = widgets.open_external(url)
        if ok:
            self.ctx.log.info(f"已打开链接：{url}", "战绩")
            self.ctx.notify(self, "已在浏览器中打开", "success", 2000)
        else:
            self.ctx.notify(self, f"无法打开浏览器（{detail}）", "error", 5000)

    def open_profile(self) -> None:
        nick = self._current_nick()
        if not nick:
            self.ctx.notify(self, "请先填写你的游戏昵称", "warn")
            if hasattr(self, "nick_edit"):
                self.nick_edit.setFocus()
            return
        self._save_nick(nick)
        if hasattr(self, "nick_status"):
            self.nick_status.setText(
                f"即将打开：{PROFILE_URL.format(nick=nick)}\n"
                "如果浏览器提示找不到玩家，请检查昵称大小写与特殊字符。"
            )
        self._open(PROFILE_URL.format(nick=nick))

    def open_live(self) -> None:
        nick = self._current_nick()
        if not nick:
            self.ctx.notify(self, "请先填写你的游戏昵称", "warn")
            return
        self._save_nick(nick)
        self._open(LIVE_URL.format(nick=nick))

    def _copy_link(self) -> None:
        nick = self._current_nick()
        if not nick:
            self.ctx.notify(self, "请先填写你的游戏昵称", "warn")
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(PROFILE_URL.format(nick=nick))
        self.ctx.notify(self, "战绩页链接已复制", "success")

    def _detect_nick(self) -> None:
        install = self.ctx.install
        nick, where = find_local_nickname(install)
        if nick:
            self.nick_edit.setText(nick)
            self._save_nick(nick)
            self.nick_status.setText(f"已从 {where} 中识别到昵称：{nick}（请自行确认是否正确）")
            self.ctx.notify(self, f"已填入识别到的昵称：{nick}", "success", 3500)
        else:
            self.nick_status.setText(
                "在本机的启动器/更新器日志里没有找到昵称。\n"
                "账号登录信息在注册表中是加密存储的，本工具不会去读取它，请手动填写昵称。"
            )
            self.ctx.notify(self, "未能自动识别昵称，请手动填写", "warn", 3500)

    def _open_replays(self) -> None:
        install = self.ctx.install
        if install is None or not install.exists:
            self.ctx.notify(self, "尚未设置游戏目录", "warn")
            return
        winutil.open_path(install.replays)

    # ------------------------------------------------------------ local stats
    def refresh_local(self) -> None:
        if not hasattr(self, "local_rows"):
            return
        install = self.ctx.install
        stats = self.ctx.settings.get("stats") or {}
        seconds = stats.get("play_seconds") or 0
        self.tile_playtime.set_value(winutil.human_duration(seconds) if seconds else "—")
        self.tile_launches.set_value(str(stats.get("launches") or 0))
        last = stats.get("last_launch")
        if last:
            import time as _time

            self.local_rows["session"].set_value(
                _time.strftime("%Y-%m-%d %H:%M", _time.localtime(last))
            )

        if install is None or not install.exists:
            for key in ("game", "version", "integrity", "latest", "logs"):
                self.local_rows[key].set_value("—")
            self.tile_replays.set_value("—")
            self.tile_shots.set_value("—")
            return

        self.local_rows["game"].set_value(install.root)
        self.local_rows["version"].set_value(install.version() or "—")
        validation = install.validate()
        self.local_rows["integrity"].set_value(validation.summary)

        try:
            items = replays.list_replays(install.replays)
        except Exception:  # noqa: BLE001
            items = []
        self.tile_replays.set_value(str(len(items)))
        if items:
            newest = items[0]
            self.local_rows["latest"].set_value(
                f"{newest.map_name} · {newest.mode_display} · "
                f"{__import__('time').strftime('%m-%d %H:%M', newest.datetime)}"
            )
        else:
            self.local_rows["latest"].set_value("没有回放")

        try:
            shots = sum(
                1
                for entry in os.scandir(install.screenshots)
                if entry.is_file(follow_symlinks=False)
            )
        except OSError:
            shots = 0
        self.tile_shots.set_value(str(shots))

        total = 0
        for folder in (install.game_logs, install.launcher_logs, install.startapp_logs):
            if not folder or not os.path.isdir(folder):
                continue
            try:
                for entry in os.scandir(folder):
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
            except OSError:
                continue
        self.local_rows["logs"].set_value(winutil.human_size(total))

    # ------------------------------------------------------------------ hooks
    def on_show(self) -> None:
        self.refresh_local()

    def on_install_changed(self) -> None:
        self.refresh_local()
