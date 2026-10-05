"""音效模组 page - inspect the game's ``sound`` folder, install file sets and
roll them back.

Everything that could touch 2.5 GB of game data (directory sizing, inventory,
install, restore, backup sizing) runs through :func:`widgets.run_task` so the GUI
thread never blocks.  The page only ever writes through
:mod:`wttoolbox.core.soundmods`, which backs up every file it overwrites
before touching it.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import library, soundmods, winutil
from .. import icons, theme
from .. import widgets
from ..widgets import (
    BusyStrip,
    Card,
    EmptyState,
    InfoRow,
    ScrollColumn,
    SearchBox,
    StatTile,
    SubCard,
    confirm,
    danger_button,
    ghost_button,
    hline,
    hspacer,
    make_label,
    notify,
    primary_button,
    run_task,
    subtle_button,
)

__all__ = ["SoundPage"]

#: Sentinel stored in the destination combo box for "install into a new folder".
SUBDIR_TOKEN = "__subdir__"

#: kind -> (中文标签, icon name)
_KIND_LABELS = {
    "folder": ("文件夹", "folder"),
    "bank": ("音效库(.bank)", "sound"),
    "text": ("文本", "text"),
    "file": ("文件", "file"),
}

_INVALID_NAME_CHARS = '<>:"/\\|?*'
_DASH = "—"
_SHIM_DONE = False


def _make_tables_work() -> bool:
    """Work around a defect in the frozen ``ui.widgets`` module.

    ``widgets.configure_table`` (and therefore ``selectable_table``) calls
    ``QHeaderView.Interactive`` / ``QHeaderView.Stretch``, but ``QHeaderView`` is
    never imported there, so every call raises ``NameError``.  ``widgets.py`` is
    frozen for this task, so the page injects the missing name into that module's
    namespace before building a table.  Without it - and without a full page
    reload afterwards - no table can be created at all.

    Returns ``True`` when the workaround was applied.
    """
    global _SHIM_DONE
    if _SHIM_DONE:
        return True
    try:
        if hasattr(widgets, "QHeaderView"):
            _SHIM_DONE = True
            return False
        widgets.QHeaderView = QHeaderView
        _SHIM_DONE = True
        print(
            "[sound-page] widgets.QHeaderView was missing; injected the name so "
            "configure_table()/selectable_table() can run (widgets.py is frozen)."
        )
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
#  Page-local helpers
# --------------------------------------------------------------------------- #
def _cancel_own_tasks(owner, key: str | None = None) -> None:
    """Cancel the owner's background tasks (all of them, or just one *key*).

    ``widgets.cancel_tasks`` exists but is absent from the module's ``__all__``,
    so the page reaches for it defensively instead of relying on the export.
    Per-key cancellation matters here: a full cancel in one refresh would abort
    the other in-flight scan of the 2.5 GB sound folder midway.
    """
    try:
        from ..widgets import cancel_tasks

        registry = list(getattr(owner, "_tk_tasks", ()) or ())
        if key is None:
            cancel_tasks(owner)
        else:
            for task in registry:
                if getattr(task, "label", "") == key:
                    task.cancel()
        return
    except Exception:  # noqa: BLE001 - never let housekeeping break a click
        pass
    for task in list(getattr(owner, "_tk_tasks", ()) or ()):
        if key is not None and getattr(task, "label", "") != key:
            continue
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass


def _size_text(value) -> str:
    """Human size, or ``—`` when the number is not known."""
    if value is None:
        return _DASH
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _DASH
    return winutil.human_size(number)


def _count_text(value) -> str:
    if value is None:
        return _DASH
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _DASH


def _sanitize_name(text: str) -> str:
    """Make *text* usable as a single folder name on Windows."""
    cleaned = "".join(
        "_" if (char in _INVALID_NAME_CHARS or ord(char) < 32) else char
        for char in str(text or "")
    )
    cleaned = cleaned.replace(" ", "_").strip(" ._")
    return cleaned[:64]


def _folder_preview(folder: str) -> dict:
    """Count files/bytes under *folder*; runs on a worker thread."""
    total = 0
    count = 0
    tops: list[str] = []
    seen: set[str] = set()
    try:
        with os.scandir(folder) as scanner:
            root_names = [entry.name for entry in scanner]
    except OSError:
        root_names = []

    for current, _dirs, names in os.walk(folder):
        at_root = os.path.normcase(os.path.abspath(current)) == os.path.normcase(
            os.path.abspath(folder)
        )
        for name in names:
            if name.lower() in library.IGNORED_NAMES:
                continue
            try:
                total += os.path.getsize(os.path.join(current, name))
            except OSError:
                continue
            count += 1
            if at_root and name not in seen and len(tops) < 6:
                seen.add(name)
                tops.append(name)
        if at_root:
            for name in root_names:
                if os.path.isdir(os.path.join(current, name)) and name not in seen and len(tops) < 6:
                    seen.add(name)
                    tops.append(name)
    return {"files": count, "bytes": total, "top_level": tops}


def _load_backup_rows() -> list[dict]:
    """Backup sets plus their on-disk footprint; runs on a worker thread.

    ``SoundSet.size`` walks the backup folder, so reading it on the GUI thread
    would freeze the window on a large set - it is computed here instead.
    """
    rows: list[dict] = []
    for sound_set in soundmods.list_sets():
        try:
            size, count = winutil.walk_size(sound_set.folder)
        except OSError:
            size, count = 0, 0
        rows.append({"set": sound_set, "bytes": size, "files": count})
    return rows


class _EmptyPanel(EmptyState):
    """``EmptyState`` whose copy can be replaced when data arrives.

    ``EmptyState`` exposes ``add_action`` but no text setters, so this thin
    subclass keeps the mutable labels instead of reaching into privates from the
    page body.  It is page-local: nothing shared is modified.
    """

    def __init__(self, title: str, subtitle: str = "", *, icon_name: str = "info",
                 parent: QWidget | None = None) -> None:
        super().__init__(title, subtitle, icon_name=icon_name, parent=parent)
        self._title_label = self.findChild(QLabel, "CardTitle")
        self._subtitle_label = self.findChild(QLabel, "Muted")

    def set_text(self, title: str, subtitle: str = "") -> None:
        if self._title_label is not None:
            self._title_label.setText(title)
        if self._subtitle_label is not None:
            self._subtitle_label.setText(subtitle)
            self._subtitle_label.setVisible(bool(subtitle))


# --------------------------------------------------------------------------- #
#  Page
# --------------------------------------------------------------------------- #
class SoundPage(QWidget):
    """音效目录总览 + 音效模组安装 + 备份还原."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        # Same root treatment as LibraryPage: the page paints its own background
        # so it never shows a stale light window colour in dark mode.
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # --- cached state ---------------------------------------------------
        self._catalog: dict | None = None
        self._inv_rows: list[soundmods.SoundInventoryRow] = []
        self._set_rows: list[dict] = []
        self._source_path: str = ""
        self._preview: dict | None = None
        self._busy_depth = 0
        self._preview_panel: _PreviewPanel | None = None

        install = ctx.require_install(self)
        self._install = install
        self._sound_dir = install.sound if install is not None else ""
        self._game_running = bool(install is not None and install.is_running())

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(18, 16, 18, 16)
        self._outer.setSpacing(14)
        _make_tables_work()
        self._build_header(self._outer)
        self._mount(install)
        # React to the game directory appearing later (startup auto-detection).
        ctx.installChanged.connect(lambda _install: self.on_install_changed())

    # ------------------------------------------------------------------ mount
    def _mount(self, install) -> None:
        """Build the page body for *install*, replacing whatever was there.

        Called once from ``__init__`` and again from
        :meth:`on_install_changed`, because at startup the game directory is
        often detected a moment *after* the pages are constructed - without the
        remount the page stayed stuck on "尚未设置游戏目录".
        """
        while self._outer.count() > 1:  # keep the header at index 0
            item = self._outer.takeAt(self._outer.count() - 1)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self._install = install
        self._sound_dir = install.sound if install is not None else ""
        self._game_running = bool(install is not None and install.is_running())
        self._catalog = None
        self._inv_rows = []
        self._set_rows = []
        self._source_path = ""
        self._preview = None
        self._preview_panel = None

        if install is None:
            empty = EmptyState(
                "尚未设置游戏目录",
                "音效模组页面需要指向一个 War Thunder 安装目录：它会读取 sound 文件夹，"
                "并在覆盖前把原文件备份到 WTToolbox 的数据目录。",
                icon_name="sound",
            )
            empty.add_action(primary_button("返回主页设置游戏目录", "home", self._goto_home))
            self._outer.addWidget(empty, 1)
            return

        self._scroll = ScrollColumn()
        self._outer.addWidget(self._scroll, 1)

        self._build_overview()
        self._build_inventory()
        self._build_installer()
        self._build_backups()

        # Initial population - every call is asynchronous.
        self.refresh_catalog()
        self.refresh_inventory()
        self.refresh_backups()

    def on_install_changed(self) -> None:
        """React to the game directory being set, changed or cleared."""
        install = self.ctx.install
        new_dir = install.sound if install is not None else ""
        if new_dir == self._sound_dir and install is not None:
            return
        self._mount(install)

    def on_show(self) -> None:
        """Refresh times when the page becomes visible."""
        if self._install is None and self.ctx.has_install:
            self._mount(self.ctx.install)

    # ------------------------------------------------------------------ misc
    def _goto_home(self) -> None:
        try:
            self.ctx.requestPage.emit("home")
        except Exception:  # noqa: BLE001
            pass

    def _begin_busy(self, text: str) -> None:
        self._busy_depth += 1
        self._busy.start(text)

    def _end_busy(self) -> None:
        self._busy_depth = max(0, self._busy_depth - 1)
        if self._busy_depth == 0:
            self._busy.stop()

    def _build_header(self, outer: QVBoxLayout) -> None:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(11)

        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("sound", theme.current().accent, 26, 1.8))
        glyph.setFixedWidth(30)
        glyph.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        row.addWidget(glyph)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(make_label("音效模组", "H1"))
        titles.addWidget(
            make_label("查看 sound 目录内容，安装第三方音效包，并在需要时完整还原。", "Muted")
        )
        row.addLayout(titles, 1)

        self._busy = BusyStrip()
        row.addWidget(self._busy, 0)
        row.addWidget(
            subtle_button("刷新页面", "refresh", self.refresh_all,
                          tooltip="重新扫描音效目录与备份记录")
        )
        outer.addWidget(header)

    # ------------------------------------------------------------- A. 音效目录
    def _build_overview(self) -> None:
        card = Card(
            "音效目录",
            "工具读取的位置。备份保存在 WTToolbox 的数据目录中，不占用游戏目录空间。",
            icon_name="sound",
        )
        body = SubCard()
        self._path_label = make_label(self._sound_dir or _DASH, "Muted", wrap=True)
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setToolTip(self._sound_dir or _DASH)
        body.add(self._path_label)
        card.add(body)

        tiles = QHBoxLayout()
        tiles.setSpacing(11)
        self._tile_files = StatTile("文件总数", _DASH, icon_name="file")
        self._tile_size = StatTile("占用空间", _DASH, icon_name="drive")
        self._tile_sets = StatTile("备份记录数", _DASH, icon_name="undo")
        for tile in (self._tile_files, self._tile_size, self._tile_sets):
            tiles.addWidget(tile, 1)
        card.add_layout(tiles)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(
            ghost_button("打开音效目录", "folder",
                         lambda: self._open_path(self._sound_dir, "音效目录不存在"),
                         tooltip=self._sound_dir or "")
        )
        actions.addWidget(ghost_button("重新扫描", "refresh", self.refresh_catalog))
        actions.addWidget(hspacer())
        card.add_layout(actions)
        self._scroll.add(card)

    def refresh_catalog(self) -> None:
        """Re-read ``describe_sound_dir`` (walks the whole folder) - async."""
        _cancel_own_tasks(self, "describe_sound_dir")
        self._begin_busy("正在统计音效目录…")
        run_task(
            self,
            lambda: soundmods.describe_sound_dir(self._sound_dir),
            label="describe_sound_dir",
            on_done=self._show_catalog,
            on_error=self._on_catalog_error,
        )

    def _on_catalog_error(self, message: str) -> None:
        self._end_busy()
        notify(self, f"扫描音效目录失败：{message}", "error")

    def _show_catalog(self, data) -> None:
        self._end_busy()
        if not isinstance(data, dict):
            data = {}
        self._catalog = data
        exists = bool(data.get("exists"))
        path = str(data.get("path") or self._sound_dir or _DASH)

        if exists:
            self._path_label.setText(path)
        else:
            self._path_label.setText(f"{path}（目录不存在，请先在设置中确认游戏目录）")
        self._path_label.setToolTip(path)

        self._tile_files.set_value(_count_text(data.get("files")) if exists else _DASH)
        self._tile_size.set_value(_size_text(data.get("bytes")) if exists else _DASH)
        self._sync_set_tile()

    def _sync_set_tile(self) -> None:
        if self._set_rows:
            self._tile_sets.set_value(_count_text(len(self._set_rows)))
        else:
            self._tile_sets.set_value("0" if self._catalog is not None else _DASH)

    # ---------------------------------------------------- B. 当前音效目录内容
    def _build_inventory(self) -> None:
        card = Card(
            "当前音效目录内容",
            "只列出 sound 目录的第一层条目；文件夹大小为其内部文件总和。",
            icon_name="folder-open",
        )

        self._inv_search = SearchBox("按名称筛选…")
        self._inv_search.textChanged.connect(self._apply_inv_filter)
        card.add(self._inv_search)

        self._inv_table = widgets.selectable_table(
            ["名称", "类型", "大小", "文件数"],
            stretch_column=0,
            resize_modes={
                1: QHeaderView.ResizeToContents,
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
            },
        )
        self._inv_table.setMinimumHeight(324)
        card.add(self._inv_table)

        self._inv_note = make_label("", "Faint", wrap=True)
        self._inv_note.setVisible(False)
        card.add(self._inv_note)

        self._inv_empty = _EmptyPanel(
            "正在读取音效目录…", "首次扫描 2.5 GB 的音效目录需要一点时间。",
            icon_name="folder-open",
        )
        card.add(self._inv_empty)

        self._scroll.add(card)

    def refresh_inventory(self) -> None:
        _cancel_own_tasks(self, "sound_inventory")
        self._inv_table.setRowCount(0)
        self._inv_table.setVisible(False)
        self._inv_search.setVisible(False)
        self._inv_note.setVisible(False)
        self._inv_empty.set_text("正在读取音效目录…", "首次扫描 2.5 GB 的音效目录需要一点时间。")
        self._inv_empty.setVisible(True)
        self._begin_busy("正在读取音效目录内容…")
        run_task(
            self,
            lambda: soundmods.sound_inventory(self._sound_dir),
            label="sound_inventory",
            on_done=self._show_inventory,
            on_error=self._on_inventory_error,
        )

    def _on_inventory_error(self, message: str) -> None:
        self._end_busy()
        self._inv_empty.set_text("读取失败", f"无法读取音效目录：{message}")
        self._inv_empty.setVisible(True)
        notify(self, f"读取音效目录内容失败：{message}", "error")

    def _show_inventory(self, rows) -> None:
        self._end_busy()
        self._inv_rows = list(rows or [])
        self._fill_inventory()

    def _fill_inventory(self) -> None:
        palette = theme.current()
        rows = self._inv_rows

        self._inv_table.setSortingEnabled(False)
        self._inv_table.setRowCount(0)
        for row in rows:
            index = self._inv_table.rowCount()
            self._inv_table.insertRow(index)

            if getattr(row, "is_dir", False):
                label, icon_name = _KIND_LABELS["folder"]
            else:
                label, icon_name = _KIND_LABELS.get(
                    getattr(row, "kind", ""), (getattr(row, "kind", "") or _DASH, "file")
                )

            name = getattr(row, "name", "") or _DASH
            name_item = QTableWidgetItem(
                icons.icon(icon_name, palette.text_muted, 15, 1.8), name
            )
            name_item.setToolTip(getattr(row, "path", "") or "")
            name_item.setData(Qt.UserRole, name.lower())
            self._inv_table.setItem(index, 0, name_item)

            kind_item = QTableWidgetItem(label)
            kind_item.setForeground(QColor(palette.text_muted))
            self._inv_table.setItem(index, 1, kind_item)

            size_item = QTableWidgetItem(_size_text(getattr(row, "size", 0)))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._inv_table.setItem(index, 2, size_item)

            count_item = QTableWidgetItem(_count_text(getattr(row, "file_count", 0)))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._inv_table.setItem(index, 3, count_item)
        self._inv_table.setSortingEnabled(True)

        has_rows = bool(rows)
        self._inv_table.setVisible(has_rows)
        self._inv_search.setVisible(has_rows)
        if has_rows:
            self._inv_empty.setVisible(False)
            self._inv_note.setVisible(True)
            self._inv_note.setText(f"共 {len(rows)} 个顶层条目 · 按大小从大到小排列")
            self._apply_inv_filter(self._inv_search.text())
        else:
            self._inv_note.setVisible(False)
            exists = bool(self._catalog and self._catalog.get("exists"))
            if exists:
                self._inv_empty.set_text(
                    "音效目录为空",
                    "sound 目录中没有找到任何文件或文件夹，请先通过启动器完整校验游戏文件。",
                )
            else:
                self._inv_empty.set_text(
                    "音效目录不存在",
                    f"未找到 {self._sound_dir or _DASH}，请先在设置中确认游戏目录。",
                )
            self._inv_empty.setVisible(True)

    def _apply_inv_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        hidden = 0
        self._inv_table.setUpdatesEnabled(False)
        try:
            for index in range(self._inv_table.rowCount()):
                item = self._inv_table.item(index, 0)
                name = ""
                if item is not None:
                    stored = item.data(Qt.UserRole)
                    name = str(stored) if stored is not None else item.text().lower()
                matched = (not needle) or needle in name
                self._inv_table.setRowHidden(index, not matched)
                if not matched:
                    hidden += 1
        finally:
            self._inv_table.setUpdatesEnabled(True)
        if self._inv_rows:
            if needle:
                self._inv_note.setText(
                    f"共 {len(self._inv_rows)} 个顶层条目 · 筛选 “{text.strip()}” 匹配 "
                    f"{len(self._inv_rows) - hidden} 个"
                )
            else:
                self._inv_note.setText(f"共 {len(self._inv_rows)} 个顶层条目 · 按大小从大到小排列")

    # ------------------------------------------------------- C. 安装音效模组
    def _build_installer(self) -> None:
        card = Card(
            "安装音效模组",
            "支持 .zip 压缩包或已解压的文件夹；安装前会把被覆盖的原文件完整备份。",
            icon_name="upload",
        )

        if self._game_running:
            card.add(self._warning_banner(
                "游戏正在运行，覆盖音效文件可能导致游戏读取异常或改动被覆盖，建议先退出游戏。"
            ))

        step1 = QHBoxLayout()
        step1.setSpacing(9)
        step1.addWidget(make_label("1 · 选择来源", "CardTitle"))
        step1.addWidget(hspacer())
        step1.addWidget(primary_button("选择压缩包 (.zip)", "upload", self._choose_zip,
                                       tooltip="从 zip 压缩包安装"))
        step1.addWidget(ghost_button("从文件夹选择", "folder-open", self._choose_folder,
                                     tooltip="从已解压的文件夹安装"))
        step1.addWidget(ghost_button("清除选择", "close", self._clear_source,
                                     tooltip="清除当前来源与预览"))
        card.add_layout(step1)

        self._preview_section = QWidget()
        preview_layout = QVBoxLayout(self._preview_section)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(9)
        preview_layout.addWidget(make_label("2 · 安装预览", "CardTitle"))
        self._preview_host = QVBoxLayout()
        self._preview_host.setSpacing(9)
        preview_layout.addLayout(self._preview_host)
        self._preview_section.setVisible(False)
        card.add(self._preview_section)

        step3 = QVBoxLayout()
        step3.setSpacing(7)
        step3.addWidget(make_label("3 · 安装位置", "CardTitle"))

        combo_row = QHBoxLayout()
        combo_row.setSpacing(9)
        self._dest_combo = QComboBox()
        self._dest_combo.addItem("覆盖到 sound 根目录", "")
        self._dest_combo.addItem("安装到 sound 下的新文件夹", SUBDIR_TOKEN)
        self._dest_combo.setMinimumWidth(262)
        self._dest_combo.currentIndexChanged.connect(self._on_dest_changed)
        combo_row.addWidget(self._dest_combo)

        self._subdir_edit = QLineEdit()
        self._subdir_edit.setPlaceholderText("新文件夹名称")
        self._subdir_edit.setMinimumWidth(190)
        self._subdir_edit.setMaxLength(64)
        self._subdir_edit.setVisible(False)
        self._subdir_edit.textChanged.connect(lambda _text: self._on_subdir_edited())
        combo_row.addWidget(self._subdir_edit, 1)
        step3.addLayout(combo_row)

        self._dest_hint = make_label("", "Muted", wrap=True)
        step3.addWidget(self._dest_hint)

        honesty = SubCard()
        honesty.add(make_label(
            "本工具会把文件写入游戏的 sound 文件夹；覆盖前会把每个原文件备份到 WTToolbox 数据目录，"
            "之后可以完整还原。第三方音效包能否在游戏中生效取决于该音效包本身，本工具不会也无法验证这一点。",
            "Faint", wrap=True,
        ))
        step3.addWidget(honesty)
        card.add_layout(step3)

        card.add(hline())
        self._install_busy = BusyStrip()
        card.add(self._install_busy)

        action_row = QHBoxLayout()
        action_row.setSpacing(9)
        self._install_note = make_label("", "Faint", wrap=True)
        action_row.addWidget(self._install_note, 1)
        self._install_button = primary_button("安装", "download", self._start_install)
        self._install_button.setEnabled(False)
        action_row.addWidget(self._install_button)
        card.add_layout(action_row)

        self._on_dest_changed(0)
        self._scroll.add(card)

    # ------------------------------------------------------------ source flow
    def _choose_zip(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择音效模组压缩包", self._initial_dir(), "压缩包 (*.zip);;所有文件 (*.*)"
        )
        if not path:
            return
        preview = library.inspect_archive(path)
        self._set_source(
            path,
            preview={
                "ok": bool(preview.ok),
                "error": preview.error,
                "name": preview.suggested_name,
                "files": preview.entries,
                "bytes": preview.total_size,
                "top_level": list(preview.top_level or []),
                "is_zip": True,
            },
        )

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择音效模组文件夹", self._initial_dir())
        if not path:
            return
        self._set_source(
            path,
            preview={
                "ok": True, "error": "",
                "name": os.path.basename(path.rstrip("\\/")) or "",
                "files": None, "bytes": None, "top_level": [], "is_zip": False,
            },
        )
        self._preview_panel.set_loading("正在统计文件夹内容…")
        run_task(
            self,
            _folder_preview,
            kwargs={"folder": path},
            label="folder_preview",
            on_done=lambda data, source=path: self._show_folder_preview(source, data),
            on_error=lambda message: self._install_notify(f"读取文件夹失败：{message}", "error"),
        )

    def _show_folder_preview(self, source: str, data) -> None:
        if source != self._source_path or not isinstance(data, dict):
            return  # the user already picked something else
        self._preview = {
            "ok": True, "error": "",
            "name": os.path.basename(source.rstrip("\\/")) or "",
            "files": int(data.get("files") or 0),
            "bytes": int(data.get("bytes") or 0),
            "top_level": list(data.get("top_level") or []),
            "is_zip": False,
            "path": source,
        }
        self._preview_panel.update(self._preview)
        self._apply_suggested_subdir()
        self._update_install_state()

    def _set_source(self, path: str, *, preview: dict) -> None:
        _cancel_own_tasks(self, "folder_preview")
        self._source_path = path
        self._preview = dict(preview)
        self._preview["path"] = path
        self._ensure_preview_panel()
        self._preview_panel.update(self._preview)
        self._preview_section.setVisible(True)
        self._apply_suggested_subdir()
        self._update_install_state()

    def _clear_source(self) -> None:
        _cancel_own_tasks(self, "folder_preview")
        self._source_path = ""
        self._preview = None
        self._preview_section.setVisible(False)
        self._subdir_edit.setText("")
        self._subdir_edit.setStyleSheet("")
        self._install_busy.stop()
        self._update_install_state()

    def _ensure_preview_panel(self) -> None:
        if self._preview_panel is not None:
            return
        self._preview_panel = _PreviewPanel()
        self._preview_host.addWidget(self._preview_panel)

    def _initial_dir(self) -> str:
        if self._source_path:
            return os.path.dirname(self._source_path) or os.path.expanduser("~")
        return os.path.expanduser("~")

    # ------------------------------------------------------------ destination
    def _on_dest_changed(self, _index: int) -> None:
        palette = theme.current()
        if self._dest_combo.currentData() == SUBDIR_TOKEN:
            self._dest_hint.setText(
                "将在 sound 目录下新建一个文件夹，并把模组内容写入其中；适合自带目录结构的音效包。"
            )
        else:
            self._dest_hint.setText(
                "适用于要求覆盖 sound 根目录中 .bank 文件的模组：模组的目录结构会原样展开到 sound 目录。"
            )
        self._dest_hint.setStyleSheet(f"color: {palette.text_muted};")
        self._update_install_state()

    def _on_subdir_edited(self) -> None:
        self._update_install_state()
        if self._dest_combo.currentData() == SUBDIR_TOKEN:
            self._on_dest_changed(self._dest_combo.currentIndex())

    def _apply_suggested_subdir(self) -> None:
        base = ""
        if isinstance(self._preview, dict):
            base = str(self._preview.get("name") or "")
        if not base and self._source_path:
            base = os.path.basename(self._source_path.rstrip("\\/")) or ""
        if not base:
            base = "sound_mod"
        self._subdir_edit.setText(_sanitize_name(base) or "sound_mod")

    def _dest_subdir(self) -> str:
        if self._dest_combo.currentData() == SUBDIR_TOKEN:
            return _sanitize_name(self._subdir_edit.text())
        return ""

    def _destination_text(self) -> str:
        sub = self._dest_subdir()
        if sub:
            return os.path.join(self._sound_dir, sub)
        return f"{self._sound_dir}（sound 根目录）"

    def _update_install_state(self) -> None:
        """Single place that decides whether 安装 may run."""
        if not hasattr(self, "_install_button"):
            return
        ready = False
        reason = "请先选择一个 .zip 压缩包或文件夹。"
        if self._source_path:
            if self._preview is None:
                reason = "正在读取来源…"
            elif not self._preview.get("ok"):
                reason = "来源无效，无法安装。"
            elif not (self._preview.get("files") or 0):
                reason = "来源中没有找到可安装的文件。"
            elif self._dest_combo.currentData() == SUBDIR_TOKEN and not self._dest_subdir():
                reason = "请填写新文件夹名称（不能只包含特殊字符）。"
            else:
                ready = True
                reason = f"将写入：{self._destination_text()}"

        if getattr(self, "_installed_once", False):
            ready = False

        self._install_button.setEnabled(ready)
        self._install_note.setText(reason)

        want_edit = bool(
            self._source_path and self._dest_combo.currentData() == SUBDIR_TOKEN
        )
        if self._subdir_edit.isVisible() != want_edit:
            self._subdir_edit.setVisible(want_edit)
        invalid = want_edit and not self._dest_subdir()
        self._subdir_edit.setStyleSheet(
            f"border-color: {theme.current().warn};" if invalid else ""
        )

    # ---------------------------------------------------------------- install
    def _start_install(self) -> None:
        if not self._source_path or not self._preview or not self._preview.get("ok"):
            self._install_notify("请先选择一个有效的来源", "warn")
            return
        if self._dest_combo.currentData() == SUBDIR_TOKEN and not self._dest_subdir():
            self._install_notify("请填写有效的文件夹名称", "warn")
            return

        files = int(self._preview.get("files") or 0)
        size_text = _size_text(self._preview.get("bytes"))
        destination = self._destination_text()
        extra = ""
        if self._game_running:
            extra = "游戏当前正在运行，覆盖音效文件可能导致读取异常，建议先退出游戏。"

        accepted, _checked = confirm(
            self,
            "确认安装音效模组",
            f"将把 {files} 个文件（{size_text}）写入：{destination}",
            ok_text="开始安装",
            detail=(
                "写入前会把每个被覆盖的原文件备份到 WTToolbox 数据目录，"
                "安装结束后可在“备份与还原”中完整还原。" + (" " + extra if extra else "")
            ),
        )
        if not accepted:
            return

        sub = self._dest_subdir()
        label = str(
            self._preview.get("name") or os.path.basename(self._source_path.rstrip("\\/"))
        ) or "音效模组"
        source = self._source_path
        self._install_button.setEnabled(False)
        self._install_busy.start("正在安装…")

        def _work(**kwargs):
            return soundmods.install_sound_set(
                source, self._sound_dir, dest_subdir=sub, label=label, **kwargs
            )

        run_task(
            self,
            _work,
            wants_progress=True,
            wants_cancel=True,
            label="install_sound_set",
            on_progress=self._on_install_progress,
            on_done=self._on_install_done,
            on_error=self._on_install_error,
        )

    def _on_install_progress(self, done: int, total: int, name: str) -> None:
        self._install_busy.set_progress(done, total, os.path.basename(name or ""))

    def _on_install_error(self, message: str) -> None:
        self._install_busy.stop()
        self._update_install_state()
        self._install_notify(f"安装失败：{message}", "error")

    def _on_install_done(self, outcome) -> None:
        self._install_busy.stop()
        if outcome is None:
            self._update_install_state()
            self._install_notify("安装失败：没有返回结果", "error")
            return

        if not getattr(outcome, "ok", False):
            self._update_install_state()
            self._install_notify(getattr(outcome, "error", "") or "安装失败", "error")
            self.refresh_backups()
            return

        copied = getattr(outcome, "copied", 0)
        detail = f"覆盖 {getattr(outcome, 'replaced', 0)} 个 · 新增 {getattr(outcome, 'added', 0)} 个"
        if getattr(outcome, "cancelled", False):
            self._installed_once = True
            self._update_install_state()
            self._install_notify(
                f"安装已取消，已写入 {copied} 个文件，可通过备份记录还原", "warn"
            )
        else:
            self._installed_once = True
            self._update_install_state()
            self._install_note.setText(f"安装完成：{detail}；再次安装请重新选择来源。")
            self._install_notify(f"已安装 {copied} 个文件（{detail}）", "success")
        self.refresh_inventory()
        self.refresh_backups()

    def _install_notify(self, text: str, kind: str = "info") -> None:
        notify(self.window() or self, text, kind)

    def _warning_banner(self, text: str) -> QWidget:
        palette = theme.current()
        banner = SubCard()
        banner.setStyleSheet(
            f"QFrame#SubCard {{ background: {palette.warn_soft}; "
            f"border: 1px solid {palette.warn}; }}"
        )
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("warning", palette.warn, 18, 1.9))
        glyph.setFixedWidth(20)
        row.addWidget(glyph, 0, Qt.AlignTop)
        label = make_label(text, None, wrap=True)
        label.setStyleSheet(f"color: {palette.warn}; font-weight: 700;")
        row.addWidget(label, 1)
        banner.add_layout(row)
        return banner

    # ------------------------------------------------------- D. 备份与还原
    def _build_backups(self) -> None:
        card = Card(
            "备份与还原",
            "每次安装都会自动记录一条回滚记录：还原会恢复被覆盖的原文件，并删除该次安装新增的文件。",
            icon_name="undo",
        )

        toolbar = QHBoxLayout()
        toolbar.setSpacing(9)
        self._restore_button = primary_button("还原选中", "undo", self._restore_selected)
        self._restore_button.setEnabled(False)
        self._delete_button = danger_button("删除备份记录", "trash", self._delete_selected)
        self._delete_button.setEnabled(False)
        toolbar.addWidget(self._restore_button)
        toolbar.addWidget(self._delete_button)
        toolbar.addWidget(hspacer())
        toolbar.addWidget(ghost_button(
            "打开备份目录", "folder",
            lambda: self._open_path(soundmods.backup_root(), "备份目录不可用"),
            tooltip="WTToolbox 数据目录中的音效备份",
        ))
        toolbar.addWidget(ghost_button("刷新", "refresh", self.refresh_backups))
        card.add_layout(toolbar)

        self._set_table = widgets.selectable_table(
            ["标签", "时间", "覆盖", "新增", "占用", "状态"],
            stretch_column=0,
            resize_modes={column: QHeaderView.ResizeToContents for column in range(1, 6)},
        )
        self._set_table.setMinimumHeight(262)
        self._set_table.itemSelectionChanged.connect(self._update_backup_buttons)
        card.add(self._set_table)

        self._set_note = make_label("", "Faint", wrap=True)
        self._set_note.setVisible(False)
        card.add(self._set_note)

        self._set_empty = _EmptyPanel(
            "还没有备份记录",
            "安装音效模组时会自动记录一条回滚记录并在此列出，可随时还原或删除。",
            icon_name="undo",
        )
        card.add(self._set_empty)

        self._update_backup_buttons()
        self._scroll.add(card)

    def refresh_backups(self) -> None:
        self._set_restore_enabled(False)
        self._set_delete_enabled(False)
        self._begin_busy("正在读取备份记录…")
        run_task(
            self,
            _load_backup_rows,
            label="list_sets",
            on_done=self._show_backups,
            on_error=self._on_backup_error,
        )

    def _on_backup_error(self, message: str) -> None:
        self._end_busy()
        self._set_empty.set_text("读取失败", f"无法读取备份记录：{message}")
        self._set_empty.setVisible(True)
        notify(self, f"读取备份记录失败：{message}", "error")

    def _show_backups(self, rows) -> None:
        self._end_busy()
        self._set_rows = list(rows or [])
        self._fill_backups()
        self._sync_set_tile()

    def _fill_backups(self) -> None:
        palette = theme.current()
        self._set_table.setSortingEnabled(False)
        self._set_table.setRowCount(0)

        for info in self._set_rows:
            sound_set = info["set"]
            index = self._set_table.rowCount()
            self._set_table.insertRow(index)

            label_item = QTableWidgetItem(sound_set.label or sound_set.ident or _DASH)
            label_item.setData(Qt.UserRole, sound_set.ident)
            label_item.setToolTip(sound_set.source or "")
            self._set_table.setItem(index, 0, label_item)

            self._set_table.setItem(index, 1, QTableWidgetItem(sound_set.created_text or _DASH))

            replaced = QTableWidgetItem(_count_text(sound_set.replacements))
            replaced.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._set_table.setItem(index, 2, replaced)

            added = QTableWidgetItem(_count_text(sound_set.additions))
            added.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._set_table.setItem(index, 3, added)

            size_item = QTableWidgetItem(_size_text(info.get("bytes")))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            size_item.setToolTip(f"{_count_text(info.get('files'))} 个备份文件")
            self._set_table.setItem(index, 4, size_item)

            restored = bool(sound_set.is_restored)
            status = QTableWidgetItem("已还原" if restored else "可还原")
            status.setForeground(QColor(palette.text_muted if restored else palette.success))
            self._set_table.setItem(index, 5, status)

        self._set_table.setSortingEnabled(True)
        has_rows = bool(self._set_rows)
        self._set_table.setVisible(has_rows)
        self._set_empty.setVisible(not has_rows)
        self._set_note.setVisible(has_rows)
        if has_rows:
            pending = sum(1 for row in self._set_rows if not row["set"].is_restored)
            self._set_note.setText(
                f"共 {len(self._set_rows)} 条备份记录 · {pending} 条可还原 · "
                "还原只影响 sound 目录内由该次安装写入的文件"
            )
        self._update_backup_buttons()

    def _selected_sets(self) -> list:
        if not hasattr(self, "_set_table"):
            return []
        chosen: list = []
        for index in sorted({i.row() for i in self._set_table.selectedIndexes()}):
            item = self._set_table.item(index, 0)
            if item is None:
                continue
            ident = item.data(Qt.UserRole)
            for info in self._set_rows:
                sound_set = info["set"]
                if sound_set.ident == ident and sound_set not in chosen:
                    chosen.append(sound_set)
        return chosen

    def _update_backup_buttons(self) -> None:
        enabled = bool(self._selected_sets())
        self._set_restore_enabled(enabled)
        self._set_delete_enabled(enabled)

    def _set_restore_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_restore_button"):
            self._restore_button.setEnabled(bool(enabled))

    def _set_delete_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_delete_button"):
            self._delete_button.setEnabled(bool(enabled))

    def _subject_text(self, chosen: list) -> str:
        if len(chosen) == 1:
            return f"“{chosen[0].label}”（{chosen[0].created_text}）"
        return f"{len(chosen)} 条备份记录"

    def _restore_selected(self) -> None:
        chosen = self._selected_sets()
        if not chosen:
            self._install_notify("请先在表格中选择要还原的备份记录", "warn")
            return
        if not self._confirm_destructive(
            "确认还原",
            f"即将还原 {self._subject_text(chosen)}。",
            "将把被覆盖的文件还原为原始内容，并删除本次新增的文件；"
            "此操作只影响 sound 目录内由该次安装写入的文件。",
            ok_text="开始还原",
        ):
            return

        self._set_restore_enabled(False)
        self._set_delete_enabled(False)
        self._begin_busy("正在还原…")

        def _work(**kwargs):
            return [soundmods.restore_set(sound_set, **kwargs) for sound_set in chosen]

        run_task(
            self,
            _work,
            wants_progress=True,
            wants_cancel=True,
            label="restore_set",
            on_progress=self._on_restore_progress,
            on_done=self._on_restore_done,
            on_error=self._on_restore_error,
        )

    def _on_restore_progress(self, done: int, total: int, name: str) -> None:
        self._busy.set_progress(done, total, os.path.basename(name or ""))

    def _on_restore_error(self, message: str) -> None:
        self._end_busy()
        notify(self, f"还原失败：{message}", "error")
        self.refresh_backups()
        self.refresh_inventory()

    def _on_restore_done(self, results) -> None:
        self._end_busy()
        entries = list(results or [])
        if not entries:
            notify(self, "还原失败：没有返回结果", "error")
        else:
            failures = [message for ok, message in entries if not ok]
            if failures:
                notify(self, failures[0], "error")
            else:
                notify(self, entries[-1][1] or f"已还原 {len(entries)} 条备份记录", "success")
        self.refresh_backups()
        self.refresh_inventory()

    def _delete_selected(self) -> None:
        chosen = self._selected_sets()
        if not chosen:
            self._install_notify("请先在表格中选择要删除的备份记录", "warn")
            return
        if not self._confirm_destructive(
            "确认删除备份记录",
            f"即将删除 {self._subject_text(chosen)}。",
            "只删除备份文件与回滚清单，已经写入 sound 目录的文件不会被改动；"
            "删除后将无法再通过本工具还原这一次安装。",
            ok_text="删除记录",
        ):
            return

        self._set_restore_enabled(False)
        self._set_delete_enabled(False)
        self._begin_busy("正在删除备份记录…")

        def _work():
            return [soundmods.delete_set(sound_set) for sound_set in chosen]

        run_task(
            self,
            _work,
            label="delete_set",
            on_done=self._on_delete_done,
            on_error=self._on_restore_error,
        )

    def _on_delete_done(self, results) -> None:
        self._end_busy()
        entries = list(results or [])
        failures = [message for ok, message in entries if not ok]
        if failures:
            notify(self, failures[0], "error")
        else:
            notify(self, f"已删除 {len(entries)} 条备份记录", "success")
        self.refresh_backups()

    def _confirm_destructive(self, title: str, message: str, detail: str, *, ok_text: str) -> bool:
        """Honour the ``confirm_delete`` setting for anything destructive."""
        if not bool(self.ctx.settings.get("confirm_delete", True)):
            return True
        accepted, _checked = confirm(
            self, title, message, ok_text=ok_text, danger=True, detail=detail
        )
        return accepted

    def refresh_all(self) -> None:
        self.refresh_catalog()
        self.refresh_inventory()
        self.refresh_backups()

    # ------------------------------------------------------------------ utils
    def _open_path(self, path: str, missing_message: str) -> None:
        if not path or not os.path.exists(path):
            self._install_notify(missing_message, "warn")
            return
        if not winutil.open_path(path):
            self._install_notify("无法打开该路径", "error")


# --------------------------------------------------------------------------- #
#  Preview panel for the install card
# --------------------------------------------------------------------------- #
class _PreviewPanel(SubCard):
    """来源路径 / 名称 / 文件数 / 总大小 / 顶层结构."""

    ROWS = ("来源路径", "名称", "文件数", "总大小")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        palette = theme.current()

        self._mode = make_label("正在读取来源…", "Muted", wrap=True)
        self.add(self._mode)

        self._rows: dict[str, InfoRow] = {}
        for key in self.ROWS:
            info = InfoRow(key, _DASH)
            self._rows[key] = info
            self.add(info)

        struct_row = QWidget()
        struct_layout = QHBoxLayout(struct_row)
        struct_layout.setContentsMargins(0, 1, 0, 1)
        struct_layout.setSpacing(10)
        struct_key = make_label("顶层结构", "Muted")
        struct_key.setMinimumWidth(96)
        struct_layout.addWidget(struct_key, 0, Qt.AlignTop)
        self._structure = make_label(_DASH, "Faint", wrap=True)
        self._structure.setTextInteractionFlags(Qt.TextSelectableByMouse)
        struct_layout.addWidget(self._structure, 1)
        self.add(struct_row)

        self._error = make_label("", None, wrap=True)
        self._error.setStyleSheet(f"color: {palette.error}; font-weight: 600;")
        self._error.setVisible(False)
        self.add(self._error)

        self._mode.setVisible(False)

    # ------------------------------------------------------------------ api
    def set_loading(self, text: str) -> None:
        """Show a transient 'reading…' state for folder sizing."""
        self._mode.setText(text)
        self._mode.setStyleSheet(f"color: {theme.current().text_muted}; font-weight: 600;")
        self._mode.setVisible(bool(text))
        if text:
            self._rows["名称"].set_value(_DASH)
            self._rows["文件数"].set_value("正在统计…")
            self._rows["总大小"].set_value("正在统计…")
            self._structure.setText(_DASH)
            self._error.setVisible(False)

    def update(self, data: dict) -> None:  # noqa: A003 - mirrors QWidget.update style
        palette = theme.current()
        ok = bool(data.get("ok"))

        self._rows["来源路径"].set_value(str(data.get("path") or _DASH))
        self._rows["名称"].set_value(str(data.get("name") or _DASH))

        files = data.get("files")
        if ok and files is not None:
            self._rows["文件数"].set_value(f"{_count_text(files)} 个")
        else:
            self._rows["文件数"].set_value(_DASH)

        if ok and data.get("bytes") is not None:
            self._rows["总大小"].set_value(_size_text(data.get("bytes")))
        else:
            self._rows["总大小"].set_value(_DASH)

        top = [str(entry) for entry in (data.get("top_level") or []) if str(entry)]
        if not ok:
            self._structure.setText(_DASH)
        elif top:
            self._structure.setText("、".join(top) + (" …" if len(top) >= 6 else ""))
        else:
            self._structure.setText("（没有顶层文件夹，文件会按原结构展开）")

        error = str(data.get("error") or "")
        if not ok and error:
            self._mode.setText("来源无效")
            self._mode.setStyleSheet(f"color: {palette.error}; font-weight: 700;")
            self._mode.setVisible(True)
            self._error.setText(error)
            self._error.setVisible(True)
        else:
            self._mode.setText(
                "压缩包可直接安装" if data.get("is_zip") else "文件夹可直接安装"
            )
            self._mode.setStyleSheet(f"color: {palette.success}; font-weight: 700;")
            self._mode.setVisible(True)
            self._error.setVisible(False)

        self.setStyleSheet(
            f"QFrame#SubCard {{ border: 1px solid "
            f"{palette.error if not ok else palette.border}; }}"
        )
