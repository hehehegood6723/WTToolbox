"""设置 page: appearance, behaviour, startup, data / cache and about.

Every control writes straight through to :class:`~wttoolbox.core.settings.Settings`
and takes effect immediately - there is no staged "apply" step.  The two
exceptions are deliberate and are called out in the UI itself:

* 界面缩放 is read once when :func:`wttoolbox.ui.theme.apply_theme` runs, so the
  window has to be restarted for a new scale to be picked up.
* Anything that walks the filesystem (回收站大小, 清空回收站, 清空资讯缓存) runs on
  a worker thread through :func:`wttoolbox.ui.widgets.run_task` so the GUI
  thread is never blocked.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, qVersion
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import appdirs, news, trash, winutil
from .. import theme, widgets
from ..context import PAGE_LABELS, PAGE_ORDER

__all__ = ["SettingsPage"]

#: Width of the value controls on the right of a settings row.  Pinning them
#: keeps the column edge straight instead of letting a lone combo box stretch.
_CONTROL_WIDTH = 168
_SPIN_WIDTH = 100

#: 界面缩放 choices as ``(setting value, label)``.
_FONT_SCALES: tuple[tuple[float, str], ...] = (
    (1.0, "100%"),
    (1.1, "110%"),
    (1.25, "125%"),
)
_NEWS_CACHE_CHOICES: tuple[tuple[int, str], ...] = (
    (15, "15 分钟"),
    (30, "30 分钟"),
    (60, "60 分钟"),
    (120, "120 分钟"),
)

_DISCLAIMER = (
    "本工具是第三方非官方辅助工具，与 Gaijin Entertainment 无关；"
    "它只读写游戏目录中的配置与文件（config.blk、日志、回放、涂装、瞄具、音效文件），"
    "不注入、不读取、不修改游戏进程内存，不提供任何影响对局的作弊功能；"
    "对游戏文件的修改请在游戏关闭时进行。"
)

_AUTOSTART_SOURCE_HINT = (
    "提示：源码运行时注册的命令会指向 Python 解释器（需要解释器与源码仍在原位置才有效）；"
    "打包成 exe 后完全可用。"
)


# --------------------------------------------------------------------------- #
#  Small page-local layout helpers
# --------------------------------------------------------------------------- #
def _fixed_width(widget: QWidget, width: int) -> QWidget:
    widget.setFixedWidth(width)
    return widget


def _button_row(*items: QWidget) -> QHBoxLayout:
    """A left-aligned row of buttons sharing the page's 9px gap."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    for item in items:
        row.addWidget(item)
    row.addWidget(widgets.hspacer())
    return row


class SettingsPage(QWidget):
    """The 设置 page."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        # ``_loading`` guards the read-modify-write path while controls are
        # being refreshed from the store, so syncing never writes back.
        self._loading = False
        self._busy_count = 0
        #: ``callable() -> None`` entries that re-read the store into a control.
        self._syncers: list[Callable[[], None]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = widgets.ScrollColumn()
        outer.addWidget(self.scroll, 1)

        self.appearance_card = self._build_appearance()
        self.behaviour_card = self._build_behaviour()
        self.startup_card = self._build_startup()
        self.data_card = self._build_data()
        self.about_card = self._build_about()

        for card in (
            self.appearance_card,
            self.behaviour_card,
            self.startup_card,
            self.data_card,
            self.about_card,
        ):
            self.scroll.add(card)

        self._sync_all()
        self._refresh_trash()
        self._load_install_facts()

    # ------------------------------------------------------------------ utils
    def _card(self, title: str, subtitle: str, icon_name: str) -> widgets.Card:
        return widgets.Card(title, subtitle, icon_name=icon_name)

    def _label_row(
        self,
        title: str,
        hint: str,
        control: QWidget,
        *,
        title_role: str | None = None,
        hint_role: str = "Faint",
    ) -> QWidget:
        """``title (+ hint)`` on the left, a fixed-width *control* on the right."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(widgets.make_label(title, title_role))
        if hint:
            column.addWidget(widgets.make_label(hint, hint_role, wrap=True))
        layout.addLayout(column, 1)
        layout.addWidget(control, 0, Qt.AlignTop | Qt.AlignRight)
        return row

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index < 0:
            # Stored value is not one of the offered choices - show it honestly
            # rather than silently displaying the first entry.
            combo.addItem(f"{value}", value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _combo(
        self,
        choices: tuple[tuple[Any, str], ...],
        *,
        key: str,
        width: int = _CONTROL_WIDTH,
        on_change: Callable[[Any], None] | None = None,
    ) -> QComboBox:
        combo = QComboBox()
        for value, label in choices:
            combo.addItem(label, value)
        _fixed_width(combo, width)
        combo.currentIndexChanged.connect(
            lambda _index, box=combo: self._on_combo(box, key, on_change)
        )
        self._syncers.append(
            lambda box=combo, setting=key: self._set_combo_data(
                box, self.ctx.settings.get(setting)
            )
        )
        return combo

    def _on_combo(self, combo: QComboBox, key: str, on_change: Callable[[Any], None] | None) -> None:
        if self._loading:
            return
        value = combo.currentData()
        self.ctx.settings.set(key, value)
        if on_change is not None:
            on_change(value)

    def _switch(self, key: str, on_change: Callable[[bool], None] | None = None) -> widgets.ToggleSwitch:
        switch = widgets.ToggleSwitch()
        switch.toggled.connect(
            lambda checked, setting=key, hook=on_change: self._on_switch(setting, checked, hook)
        )
        self._syncers.append(
            lambda widget=switch, setting=key: widget.setChecked(
                bool(self.ctx.settings.get(setting))
            )
        )
        return switch

    def _on_switch(self, key: str, checked: bool, on_change: Callable[[bool], None] | None) -> None:
        if self._loading:
            return
        self.ctx.settings.set(key, bool(checked))
        if on_change is not None:
            on_change(bool(checked))

    def _spin(
        self,
        key: str,
        *,
        minimum: int,
        maximum: int,
        suffix: str = "",
    ) -> QSpinBox:
        box = QSpinBox()
        box.setRange(minimum, maximum)
        if suffix:
            box.setSuffix(suffix)
        _fixed_width(box, _SPIN_WIDTH)
        box.valueChanged.connect(lambda value, setting=key: self._on_spin(setting, value))
        self._syncers.append(
            lambda widget=box, setting=key: widget.setValue(
                int(self.ctx.settings.get(setting) or minimum)
            )
        )
        return box

    def _on_spin(self, key: str, value: int) -> None:
        if self._loading:
            return
        self.ctx.settings.set(key, int(value))

    def _note(self, text: str) -> Any:
        label = widgets.make_label(text, "Faint", wrap=True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _sync_all(self) -> None:
        self._loading = True
        try:
            for sync in self._syncers:
                sync()
        finally:
            self._loading = False

    # ----------------------------------------------------------- async helper
    def _run(
        self,
        fn: Callable[..., Any],
        *,
        kwargs: dict[str, Any] | None = None,
        label: str,
        on_done: Callable[[Any], None],
        wants_progress: bool = False,
    ) -> Any:
        """``run_task`` plus the shared busy strip bookkeeping."""
        self._busy_count += 1
        self.busy.start(label)
        task = widgets.run_task(
            self,
            fn,
            kwargs=kwargs or {},
            on_done=on_done,
            wants_progress=wants_progress,
            label=label,
        )
        task.finished.connect(lambda _result: self._task_finished())
        task.failed.connect(lambda _message: self._task_finished())
        return task

    def _task_finished(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.busy.stop()

    # ------------------------------------------------------------- 外观 card
    def _build_appearance(self) -> widgets.Card:
        card = self._card(
            "外观",
            "主题、启动页与界面缩放。",
            "sun",
        )

        theme_box = QWidget()
        theme_row = QHBoxLayout(theme_box)
        # 2px of top inset so the segmented control centres on the row title
        # rather than on the title + hint column.
        theme_row.setContentsMargins(0, 2, 0, 0)
        theme_row.setSpacing(7)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_buttons: dict[str, QPushButton] = {}
        for name, label in (("light", "浅色"), ("dark", "深色")):
            button = QPushButton(label)
            button.setObjectName("Segment")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, value=name: self._choose_theme(value)
            )
            self.theme_group.addButton(button)
            self.theme_buttons[name] = button
            theme_row.addWidget(button)
        _fixed_width(theme_box, theme_box.sizeHint().width() + 4)

        self.start_page_combo = self._combo(
            tuple((key, PAGE_LABELS.get(key, key)) for key in PAGE_ORDER),
            key="start_page",
        )
        self.font_scale_combo = self._combo(_FONT_SCALES, key="font_scale")

        card.add(self._label_row("主题", "切换浅色 / 深色界面，立即生效。", theme_box))
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "启动时打开的页面",
                "下次启动 WTToolbox 时默认停留的页面。",
                self.start_page_combo,
            )
        )
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "界面缩放",
                "放大字体与控件尺寸，适合高分辨率屏幕。",
                self.font_scale_combo,
            )
        )
        card.add(self._note("界面缩放需要重启 WTToolbox 后生效。"))

        self._syncers.append(self._sync_theme_buttons)
        return card

    def _sync_theme_buttons(self) -> None:
        current = str(self.ctx.settings.get("theme") or "light").lower()
        for name, button in self.theme_buttons.items():
            button.setChecked(name == current)

    def _choose_theme(self, name: str) -> None:
        # ``set_theme`` short-circuits when the palette already matches, so the
        # value is written first to guarantee the choice is persisted.
        self.ctx.settings.set("theme", name)
        self.ctx.set_theme(name)
        self._sync_theme_buttons()

    # --------------------------------------------------------- 行为 card
    def _build_behaviour(self) -> widgets.Card:
        card = self._card(
            "行为",
            "窗口与删除操作的默认行为。",
            "gear",
        )
        rows = (
            ("关闭窗口时最小化到托盘", "点击右上角关闭按钮时不退出程序，而是收进托盘。", "close_to_tray"),
            ("最小化时隐藏到托盘", "最小化窗口时从任务栏移除，只保留托盘图标。", "minimize_to_tray"),
            ("托盘气泡通知", "后台任务完成或失败时弹出托盘气泡提醒。", "tray_notifications"),
            ("删除前需要确认", "删除文件或清理缓存前弹出确认对话框。", "confirm_delete"),
            ("删除时移入回收站（可还原）", "删除的文件先移入 WTToolbox 回收站，可随时还原。", "use_trash"),
        )
        for index, (title, hint, key) in enumerate(rows):
            if index:
                card.add(widgets.hline())
            card.add(self._label_row(title, hint, self._switch(key)))
        return card

    # ------------------------------------------------------- 启动 card
    def _build_startup(self) -> widgets.Card:
        card = self._card(
            "启动",
            "开机自启与启动时的默认动作。",
            "activity",
        )

        self.startup_switch = widgets.ToggleSwitch()
        self.startup_switch.toggled.connect(self._on_startup_toggled)
        # Not registered in ``_syncers``: the truth lives in the registry, so it
        # is re-derived by ``_sync_startup_switch`` instead.
        card.add(
            self._label_row(
                "开机自动启动",
                "登录 Windows 后自动启动 WTToolbox。",
                self.startup_switch,
            )
        )

        self.startup_command_label = widgets.make_label("", "Faint", wrap=True)
        self.startup_command_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.add(self.startup_command_label)
        card.add(self._note(_AUTOSTART_SOURCE_HINT))
        card.add(widgets.hline())

        card.add(
            self._label_row(
                "启动时最小化到托盘",
                "程序启动后不显示主窗口，直接收进托盘。",
                self._switch("start_minimized"),
            )
        )
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "启动时自动搜索游戏目录",
                "启动时自动扫描磁盘寻找《战争雷霆》安装位置。",
                self._switch("auto_detect_on_start"),
            )
        )
        return card

    def _sync_startup_switch(self) -> None:
        """Checked exactly when a Run entry exists - never a stored guess."""
        command = ""
        try:
            command = winutil.get_startup_command() or ""
        except Exception as exc:  # noqa: BLE001 - registry access is best effort
            self.ctx.log.warn(f"读取开机启动项失败：{exc}", "设置")
        self._loading = True
        try:
            self.startup_switch.setChecked(bool(command))
        finally:
            self._loading = False
        self.startup_command = command
        if command:
            self.startup_command_label.setText(f"已注册命令：{command}")
        else:
            self.startup_command_label.setText("已注册命令：—（未设置开机启动）")

    def _on_startup_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            command = winutil.startup_command_for_exe(winutil.app_executable())
            ok, message = winutil.set_startup_enabled(True, command)
        else:
            ok, message = winutil.set_startup_enabled(False)
        if not ok:
            # Put the switch back where it really is and say why.
            self._sync_startup_switch()
            self.ctx.log.warn(f"设置开机启动失败：{message}", "设置")
            widgets.notify(self, f"设置开机启动失败：{message}", "error")
            return
        self.ctx.settings.set("start_with_windows", bool(checked))
        self._sync_startup_switch()
        self.ctx.log.ok(message, "设置")
        widgets.notify(self, message, "success")

    # --------------------------------------------------- 数据与缓存 card
    def _build_data(self) -> widgets.Card:
        card = self._card(
            "数据与缓存",
            "资讯、日志、备份与回收站。",
            "drive",
        )

        self.news_locale_combo = self._combo(
            tuple((code, label) for code, label in news.LOCALES),
            key="news_locale",
        )
        self.news_cache_combo = self._combo(_NEWS_CACHE_CHOICES, key="news_cache_minutes")
        self.log_keep_spin = self._spin("log_keep_days", minimum=3, maximum=90, suffix=" 天")
        self.backup_keep_spin = self._spin("backup_keep", minimum=5, maximum=100, suffix=" 个")

        card.add(
            self._label_row(
                "资讯语言",
                "主页资讯与公告使用的语言。",
                self.news_locale_combo,
            )
        )
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "资讯缓存时长",
                "缓存新鲜期内直接读取本地缓存，不会重复联网。",
                self.news_cache_combo,
            )
        )
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "日志保留天数",
                "自动清理超过该天数的日志文件。",
                self.log_keep_spin,
            )
        )
        card.add(self._note("该值同时作为 工具箱 → 磁盘清理 的默认时间筛选条件。"))
        card.add(widgets.hline())
        card.add(
            self._label_row(
                "备份保留数量",
                "配置备份最多保留的份数，超出后自动删除最旧的备份。",
                self.backup_keep_spin,
            )
        )
        card.add(widgets.hline())
        card.add_layout(
            _button_row(
                widgets.subtle_button("打开数据目录", "folder", self._open_app_dir),
                widgets.subtle_button("打开日志文件", "file", self._open_log_file),
                widgets.subtle_button("清空资讯缓存", "trash", self._clear_news_cache),
            )
        )
        card.add(widgets.hline())

        # ---- 回收站 -------------------------------------------------------- #
        card.add(widgets.SectionHeader("回收站", "删除的文件暂存在这里，可还原。", icon_name="trash"))
        self.trash_tile = widgets.StatTile("回收站占用", "统计中…", icon_name="trash")
        card.add(self.trash_tile)
        card.add_layout(
            _button_row(
                widgets.subtle_button("打开回收站目录", "folder", self._open_trash_dir),
                widgets.danger_button("清空回收站", "trash", self._empty_trash),
            )
        )
        card.add(widgets.hline())

        card.add_layout(
            _button_row(
                widgets.danger_button("重置全部设置", "refresh", self._reset_all_settings)
            )
        )

        self.busy = widgets.BusyStrip()
        card.add(self.busy)
        return card

    # -- data actions ------------------------------------------------------- #
    def _open_app_dir(self) -> None:
        self._open(self.ctx.app_dir, "数据目录")

    def _open_log_file(self) -> None:
        self._open(self.ctx.log_file, "日志文件")

    def _open_trash_dir(self) -> None:
        self._open(appdirs.trash_dir(), "回收站目录")

    def _open(self, path: str, what: str) -> None:
        if not path or not os.path.exists(path):
            self.ctx.log.warn(f"{what}不存在：{path or '—'}", "设置")
            widgets.notify(self, f"{what}不存在：{path or '—'}", "warn")
            return
        if not winutil.open_path(path):
            self.ctx.log.warn(f"无法打开{what}：{path}", "设置")
            widgets.notify(self, f"无法打开{what}", "error")

    def _clear_news_cache(self) -> None:
        self._run(
            news.clear_cache,
            label="清空资讯缓存",
            on_done=self._news_cache_cleared,
        )

    def _news_cache_cleared(self, removed: Any) -> None:
        count = int(removed or 0)
        if count:
            message = f"已清空资讯缓存，移除 {count} 个文件"
            self.ctx.log.ok(message, "设置")
            widgets.notify(self, message, "success")
        else:
            message = "资讯缓存本来就是空的"
            self.ctx.log.info(message, "设置")
            widgets.notify(self, message, "info")

    def _refresh_trash(self) -> None:
        self._run(trash.totals, label="统计回收站", on_done=self._trash_totals_ready)

    def _trash_totals_ready(self, result: Any) -> None:
        try:
            size, file_count, entry_count = result
        except (TypeError, ValueError):
            self.ctx.log.warn("无法统计回收站大小", "设置")
            self.trash_tile.set_value("—")
            self.trash_tile.setToolTip("回收站统计失败")
            return
        size = int(size or 0)
        entries = int(entry_count or 0)
        files = int(file_count or 0)
        self.trash_tile.set_value(f"{entries} 个项目 · {size / (1024 * 1024):.1f} MB")
        self.trash_tile.setToolTip(
            f"{entries} 个项目 · {files} 个文件 · {winutil.human_size(size)}"
        )

    def _empty_trash(self) -> None:
        accepted, _checked = widgets.confirm(
            self,
            "清空回收站",
            "回收站中的所有文件将被永久删除，此操作无法撤销。",
            ok_text="永久删除",
            danger=True,
            detail="如需保留，请先点击“打开回收站目录”手动取回文件。",
        )
        if not accepted:
            return
        self._run(trash.empty, label="清空回收站", on_done=self._trash_emptied)

    def _trash_emptied(self, result: Any) -> None:
        try:
            freed, removed, errors = result
        except (TypeError, ValueError):
            self.ctx.log.warn("清空回收站返回了意外的结果", "设置")
            self._refresh_trash()
            return
        freed = int(freed or 0)
        removed = int(removed or 0)
        size_text = f"{freed / (1024 * 1024):.1f} MB"
        if removed:
            message = f"已清空回收站，删除 {removed} 个项目，释放 {size_text}"
            self.ctx.log.ok(message, "设置")
            widgets.notify(self, message, "success")
        else:
            message = "回收站本来就是空的"
            self.ctx.log.info(message, "设置")
            widgets.notify(self, message, "info")
        if errors:
            self.ctx.log.warn(f"{len(errors)} 个项目未能删除：{errors[0]}", "设置")
        self._refresh_trash()

    def _reset_all_settings(self) -> None:
        accepted, _checked = widgets.confirm(
            self,
            "重置全部设置",
            "所有设置将恢复为默认值（游戏目录等已保存的路径不会删除）。",
            ok_text="重置",
            danger=True,
            detail="界面缩放、开机启动等改动需要重启 WTToolbox 后才完全生效。",
        )
        if not accepted:
            return
        self.ctx.settings.reset()
        self.ctx.log.warn("已重置全部设置", "设置")
        self._sync_all()
        self._sync_startup_switch()
        self._refresh_trash()
        widgets.notify(self, "已重置全部设置，建议重启 WTToolbox", "success")

    # --------------------------------------------------------- 关于 card
    def _build_about(self) -> widgets.Card:
        card = self._card("关于", "版本信息、运行环境与免责声明。", "info")

        version_row = widgets.InfoRow(
            "应用版本",
            f"{self.ctx.name}（{self.ctx.name_zh}） {self.ctx.version}",
        )
        runtime_row = widgets.InfoRow(
            "运行环境",
            f"Python {platform.python_version()} · Qt {qVersion()}",
        )
        packaged_row = widgets.InfoRow(
            "打包方式",
            "单文件 exe" if getattr(sys, "frozen", False) else "源码运行",
        )
        appdir_row = widgets.InfoRow("数据目录", self.ctx.app_dir)
        logfile_row = widgets.InfoRow("日志文件", self.ctx.log_file)
        config_row = widgets.InfoRow("配置文件", str(self.ctx.settings.path))
        logdir_row = widgets.InfoRow("日志目录", appdirs.logs_dir())

        self.game_path_row = widgets.InfoRow("正式版目录", "—")
        self.game_path_dev_row = widgets.InfoRow("测试版目录", "—")
        self.game_version_row = widgets.InfoRow("游戏版本", "读取中…")
        self.game_check_row = widgets.InfoRow("安装校验", "读取中…")

        card.add(version_row)
        card.add(runtime_row)
        card.add(packaged_row)
        card.add(appdir_row)
        card.add(logfile_row)
        card.add(config_row)
        card.add(logdir_row)
        card.add(widgets.hline())
        card.add(self.game_path_row)
        card.add(self.game_path_dev_row)
        card.add(self.game_version_row)
        card.add(self.game_check_row)
        card.add(widgets.hline())

        card.add(widgets.SectionHeader("免责声明", "", icon_name="shield"))
        card.add(widgets.make_label(_DISCLAIMER, "Muted", wrap=True))
        card.add(widgets.hline())
        card.add_layout(
            _button_row(
                widgets.subtle_button("打开项目目录", "folder", self._open_project_dir),
                widgets.subtle_button("复制诊断信息", "file", self._copy_diagnostics),
            )
        )
        return card

    @staticmethod
    def _project_dir() -> str:
        # .../src/wttoolbox/ui/pages/settings.py -> .../
        here = os.path.abspath(__file__)
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))

    def _open_project_dir(self) -> None:
        self._open(self._project_dir(), "项目目录")

    def _install_facts(self) -> dict[str, str]:
        """``(path, version, validation summary)`` as display strings."""
        install = getattr(self.ctx, "install", None)
        if install is None:
            return {"path": "—", "version": "—", "check": "—"}
        try:
            version = install.version() or "—"
        except Exception as exc:  # noqa: BLE001 - reading PE resources is best effort
            self.ctx.log.warn(f"读取游戏版本失败：{exc}", "设置")
            version = "—"
        try:
            validation = install.validate()
            check = validation.summary if validation.ok else f"未通过（{validation.summary}）"
        except Exception as exc:  # noqa: BLE001
            self.ctx.log.warn(f"校验游戏目录失败：{exc}", "设置")
            check = "—"
        return {"path": install.root or "—", "version": version, "check": check}

    def _load_install_facts(self) -> None:
        """Fill the game rows on a worker thread (version + validation hit disk)."""
        from ...core import gamepath

        dev = self.ctx.install_for(gamepath.CHANNEL_DEV)
        self.game_path_dev_row.set_value(
            dev.root if dev is not None and dev.exists else "未安装测试版"
        )
        if getattr(self.ctx, "install", None) is None:
            self.ctx.log.warn("当前未设置游戏目录，关于页中的游戏信息显示为 —", "设置")
            self.game_path_row.set_value("—")
            self.game_version_row.set_value("—")
            self.game_check_row.set_value("—")
            return
        self.game_path_row.set_value(self.ctx.install.root)
        self._run(self._install_facts, label="读取游戏信息", on_done=self._install_facts_ready)

    def _install_facts_ready(self, facts: Any) -> None:
        if not isinstance(facts, dict):
            self.ctx.log.warn("读取游戏信息返回了意外的结果", "设置")
            return
        self.game_path_row.set_value(facts.get("path", "—"))
        self.game_version_row.set_value(facts.get("version", "—"))
        self.game_check_row.set_value(facts.get("check", "—"))

    def _diagnostics_text(self) -> str:
        install = getattr(self.ctx, "install", None)
        if install is None:
            game_path, game_version, game_check = "—", "—", "—"
        else:
            game_path = install.root
            try:
                game_version = install.version() or "—"
            except Exception:  # noqa: BLE001
                game_version = "—"
            try:
                game_check = install.validate().summary
            except Exception:  # noqa: BLE001
                game_check = "—"
        lines = [
            f"WTToolbox {self.ctx.version}（{self.ctx.name_zh}）",
            f"系统：{platform.platform()}",
            f"Python：{platform.python_version()}（{platform.architecture()[0]}）",
            f"Qt：{qVersion()}",
            f"打包方式：{'单文件 exe' if getattr(sys, 'frozen', False) else '源码运行'}",
            f"游戏目录：{game_path}",
            f"游戏版本：{game_version}",
            f"安装校验：{game_check}",
            f"数据目录：{self.ctx.app_dir}",
            f"日志文件：{self.ctx.log_file}",
            "说明：只读取安装目录中的文件用于校验，不会修改游戏。",
        ]
        return "\n".join(lines)

    def _copy_diagnostics(self) -> None:
        text = self._diagnostics_text()
        clipboard = QApplication.clipboard()
        if clipboard is None:
            self.ctx.log.warn("系统剪贴板不可用", "设置")
            widgets.notify(self, "系统剪贴板不可用", "error")
            return
        clipboard.setText(text)
        self.ctx.log.ok("已复制诊断信息到剪贴板", "设置")
        widgets.notify(self, "已复制诊断信息到剪贴板", "success")

    # ------------------------------------------------------------ lifecycle
    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        # The registry can change behind our back (another tool, a script), so
        # the switch is re-derived every time the page becomes visible.
        self._sync_startup_switch()
        self._sync_all()
        QTimer.singleShot(0, self, self._refresh_trash)
