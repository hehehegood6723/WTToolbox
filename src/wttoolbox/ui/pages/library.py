"""信息库 page - replays, screenshots, custom skins, sights and missions.

Five sub-pages behind a :class:`~wttoolbox.ui.widgets.SubNavPanel`.  Every
sub-page owns its own lazy-loaded cache and drives the core modules on worker
threads, so nothing here walks the filesystem on the GUI thread.

Frozen-API notes
----------------
* ``widgets.EmptyState`` has no ``set_title`` / ``set_subtitle``, so each pane
  keeps two static instances (data empty / no search hit) and toggles them
  instead of mutating shared internals.
* ``widgets.Task`` injects ``on_progress`` / ``should_cancel`` only when asked,
  so :func:`_list_replays` forwards them straight to ``replays.list_replays``.
* ``QTableWidgetItem`` sorts by display text, which would order "9.00 MB" above
  "1.20 GB"; :class:`_Cell` overrides ``__lt__`` with an explicit sort key
  (allowed: it is page-local, not a change to the shared widgets).
* ``widgets``' ``#Danger`` button style has no ``:disabled`` rule, so a disabled
  delete button would still look active - :func:`_mute_when_disabled` restates
  that one state with palette colours.

Deliberate deviations on measured evidence
------------------------------------------
* Thumbnails are decoded with :class:`QImageReader` + a pre-scaled read on a
  worker thread, then converted to a ``QPixmap`` on the GUI thread.  Measured on
  this machine's real 2520x1680 screenshots: ``QPixmap(path)`` costs ~137 ms per
  image and a cold-cache decode peaked at 172 ms, i.e. a single
  ``QPixmap(path).scaled(...)`` on the GUI thread already breaks the "never
  block the GUI thread for ~100 ms" rule; the scaled worker read averages ~57 ms
  off-thread, and the GUI thread only builds the pixmap and repaints one tile.
* ``QHeaderView``'s sort arrow is hidden (:func:`_tame_header`): under the frozen
  header style sheet Qt draws it above the header text where it reads as a
  stray glyph.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import appdirs, gamelaunch, library, replays, winutil
from .. import icons, theme, widgets

__all__ = ["LibraryPage"]

# --------------------------------------------------------------------------- #
#  Tunables
# --------------------------------------------------------------------------- #
THUMB_SIZE = QSize(232, 132)
THUMB_GRID = QSize(252, 190)
THUMB_BATCH = 60
"""How many thumbnails are rendered before the 显示全部 button appears."""
THUMB_MAX_PER_TICK = 1
"""Decoded images applied to the grid per tick (via :data:`_ThumbSignals`), so a
fast decoder cannot flood the GUI thread with one repaint per image."""
THUMB_ADD_PER_TICK = 12
"""Grid rows created per tick; building the whole batch at once cost ~140 ms."""

REPLAY_HEADERS = ["文件名", "地图", "模式", "大小", "时间"]
CONTENT_HEADERS = ["名称", "大小", "文件", "状态", "修改时间"]


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #
class _Cell(QTableWidgetItem):
    """A cell that sorts by an explicit key instead of its display text."""

    def __init__(self, text: str, key=None, *, tooltip: str = "") -> None:
        super().__init__(text)
        self._sort_key = key
        if tooltip:
            self.setToolTip(tooltip)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105 - Qt hook
        mine = self._sort_key if self._sort_key is not None else self.text()
        theirs = getattr(other, "_sort_key", None)
        if theirs is None:
            theirs = other.text()
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


def _selected_rows(table) -> list[int]:
    model = table.selectionModel()
    if model is None:
        return []
    return sorted({index.row() for index in model.selectedIndexes()})


def _time_text(stamp: float) -> str:
    if not stamp:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def _list_replays(folder: str, on_progress=None, should_cancel=None):
    """``replays.list_replays`` bound to :class:`widgets.Task`'s ``on_progress``.

    ``Task.progress`` is ``(int, int, str)`` and ``list_replays`` now reports the
    same shape, so the callback can be forwarded directly.
    """
    return replays.list_replays(
        folder=folder, on_progress=on_progress, should_cancel=should_cancel
    )


class _ThumbSignals(QObject):
    """Hands decoded thumbnails from the worker thread to the GUI thread."""

    decoded = Signal(int, object)


def _read_thumb_image(path: str, target: QSize):
    """Decode one aspect-preserving thumbnail into a ``QImage``.

    Safe on a worker thread (``QImage`` is, ``QPixmap`` is not).  A pre-scaled
    read is used because a full-size 2520x1680 game screenshot costs ~137 ms to
    decode into a pixmap versus ~47 ms for a scaled read - and a cold cache can
    push a single decode past 170 ms, which is why the decode does not happen on
    the GUI thread at all.
    """
    reader = QImageReader(path)
    if not reader.canRead():
        return None
    source = reader.size()
    if source.isValid():
        reader.setScaledSize(source.scaled(target, Qt.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else image


def _decode_thumbnails(jobs, size: QSize, on_image, should_cancel=None) -> int:
    """Worker body: decode *jobs* and report each image through ``on_image``."""
    done = 0
    for index, path in jobs:
        if should_cancel is not None and should_cancel():
            break
        image = _read_thumb_image(path, size)
        if image is not None:
            on_image(index, image)
            done += 1
    return done


def _trash_detail(use_trash: bool) -> str:
    if use_trash:
        return "默认移入 WTToolbox 回收站，可在 工具箱 → 回收站 还原。"
    return "回收站已关闭，所选文件将被永久删除，无法还原。"


def _tame_header(table) -> None:
    """Keep sorting but drop Qt's sort arrow.

    The frozen style sheet (``QHeaderView::section`` with padding) draws that
    arrow *above* the header text, where it reads as a stray glyph, so it is
    hidden and the click-to-sort affordance is moved into a tooltip.

    ``QTableView.setSortingEnabled(True)`` re-shows the arrow, so
    :func:`_quiet_sort_arrow` must be called again after every re-population.
    """
    table.horizontalHeader().setToolTip("点击列标题可按该列排序")
    _quiet_sort_arrow(table)


def _quiet_sort_arrow(table) -> None:
    table.horizontalHeader().setSortIndicatorShown(False)


def _mute_when_disabled(button) -> None:
    """Give a ``#Danger`` button a disabled look using palette colours.

    The frozen style sheet defines ``#Danger`` but no ``#Danger:disabled``, so
    ``setEnabled(False)`` left the button fully red; widget-level rules win, so
    the disabled state is restated here without hard-coding a colour.
    """
    palette = theme.current()
    button.setStyleSheet(
        "QPushButton:disabled {"
        f" background: {palette.surface_2};"
        f" border: 1px solid {palette.border};"
        f" color: {palette.text_faint}; }}"
    )


def _notify(widget, text: str, kind: str = "info") -> None:
    """``ctx.notify`` that can never abort the work around it.

    ``widgets.notify`` re-checks ``isVisible()`` on every toast it remembers for
    the window; once an earlier toast has been destroyed by ``deleteLater`` that
    raises ``RuntimeError`` (a shared-file bug).  A notification failing must
    never skip the ``refresh()`` that follows a delete or an install, so it is
    logged and swallowed instead.
    """
    ctx = getattr(widget, "ctx", None)
    if ctx is None:
        return
    try:
        ctx.notify(widget, text, kind)
    except Exception as exc:  # noqa: BLE001 - never let a toast break a flow
        try:
            ctx.log.warn(f"通知显示失败：{exc}", "信息库")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Pane base
# --------------------------------------------------------------------------- #
class _PaneBase(QWidget):
    """Shared plumbing for the five sub-pages: busy strip, cancel, task owner."""

    def __init__(self, page: "LibraryPage", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self._active = None

    # ------------------------------------------------------------------ busy
    def _build_busy(self) -> QWidget:
        self.busy = widgets.BusyStrip()
        self.cancel_btn = widgets.ghost_button("取消")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel_active)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(self.busy, 1)
        layout.addWidget(self.cancel_btn)
        return row

    def _cancel_active(self) -> None:
        if self._active is not None:
            self._active.cancel()
            _notify(self, "正在取消…", "info")

    def _settle(self) -> None:
        self.busy.stop()
        self.cancel_btn.hide()
        self._active = None

    # ----------------------------------------------------------------- tasks
    def _start(
        self,
        fn,
        *,
        kwargs: dict | None = None,
        wants_progress: bool = False,
        wants_cancel: bool = False,
        on_done=None,
        on_error=None,
        on_progress=None,
        label: str = "",
        busy_text: str = "",
    ):
        if busy_text:
            self.busy.start(busy_text)
        self.cancel_btn.setVisible(bool(wants_cancel))
        task = widgets.run_task(
            self, fn,
            kwargs=kwargs,
            wants_progress=wants_progress,
            wants_cancel=wants_cancel,
            on_done=on_done,
            on_error=on_error or self._on_task_error,
            on_progress=on_progress,
            label=label,
        )
        self._active = task
        return task

    def _on_task_error(self, message: str) -> None:
        self._settle()
        self.page.ctx.log.error(f"{self._log_name} 操作失败：{message}", "信息库")
        _notify(self, f"操作失败：{message}", "error")

    _log_name = "信息库"

    # ------------------------------------------------------------- utilities
    @property
    def ctx(self):
        return self.page.ctx

    def _require_install(self, reason: str):
        install = self.page.current_install()
        if install is None:
            self.page.require_install(reason)
        return install

    def _reveal(self, path: str, label: str) -> None:
        if not os.path.isdir(path):
            _notify(self, f"{label}不存在", "warn")
            return
        if not winutil.reveal_in_explorer(path):
            _notify(self, "无法打开资源管理器", "warn")

    def _confirm_delete(self, title: str, message: str) -> bool:
        if not self.ctx.settings.get("confirm_delete"):
            return True
        allowed, _checked = widgets.confirm(
            self, title, message,
            ok_text="删除",
            danger=True,
            detail=_trash_detail(bool(self.ctx.settings.get("use_trash"))),
        )
        return allowed

    def _delete_payload(self, items: list, title: str, unit: str, on_done) -> None:
        """Confirm, then move *items* to the trash (or delete) on a worker."""
        if not items:
            return
        size = sum(getattr(item, "size", 0) for item in items)
        message = f"将移除 {len(items)} 个{unit}（{winutil.human_size(size)}）"
        if not self._confirm_delete(title, message):
            return
        payload = [
            library.LibraryItem(
                path=item.path, name=getattr(item, "name", os.path.basename(item.path)),
                is_dir=getattr(item, "is_dir", False), size=getattr(item, "size", 0),
                mtime=getattr(item, "mtime", 0.0),
            )
            for item in items
        ]
        self._start(
            library.delete_items,
            kwargs={
                "items": payload,
                "use_trash": bool(self.ctx.settings.get("use_trash")),
                "trash_root": appdirs.trash_dir(),
            },
            wants_cancel=True,
            on_done=on_done,
            label=title,
            busy_text=f"正在移除{unit}…",
        )

    @staticmethod
    def _report_delete(pane, result) -> None:
        pane._settle()
        if not result:
            return
        deleted, freed, errors = result
        if deleted:
            _notify(
                pane, f"已移除 {deleted} 个文件（{winutil.human_size(freed)}）", "success"
            )
        if errors:
            pane.ctx.log.warn(f"{len(errors)} 个文件未能移除", "信息库")
            _notify(pane, f"{len(errors)} 个文件未能移除：{errors[0]}", "error")


# --------------------------------------------------------------------------- #
#  1. 回放
# --------------------------------------------------------------------------- #
class _ReplayPane(_PaneBase):
    _log_name = "回放"
    loaded = False

    def __init__(self, page: "LibraryPage", parent: QWidget | None = None) -> None:
        super().__init__(page, parent)
        self._items: list = []
        self._rows: list = []
        self._play_notice_shown = False
        self._sort_column = 4
        self._sort_order = Qt.DescendingOrder
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        self.card = widgets.Card(
            "回放", "Replays 目录中的 .wrpl 战斗记录", icon_name="replay"
        )
        outer.addWidget(self.card, 1)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.search = widgets.SearchBox("搜索地图 / 模式 / 文件名…")
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        bar.addWidget(widgets.subtle_button("刷新", "refresh", self.refresh))
        bar.addWidget(widgets.subtle_button("打开回放目录", "folder", self._open_dir))
        self.play_btn = widgets.primary_button("播放选中", "play", self._play_selected)
        self.play_btn.setEnabled(False)
        bar.addWidget(self.play_btn)
        self.delete_btn = widgets.danger_button("删除选中", "trash", self._delete_selected)
        self.delete_btn.setEnabled(False)
        _mute_when_disabled(self.delete_btn)
        bar.addWidget(self.delete_btn)
        self.card.add_layout(bar)

        stats = QHBoxLayout()
        stats.setSpacing(11)
        self.tile_count = widgets.StatTile("回放总数", "—", icon_name="replay")
        self.tile_size = widgets.StatTile("总占用", "—", icon_name="drive")
        self.tile_maps = widgets.StatTile("已解析地图数", "—", icon_name="layers")
        for tile in (self.tile_count, self.tile_size, self.tile_maps):
            stats.addWidget(tile, 1)
        self.card.add_layout(stats)

        self.card.add(self._build_busy())

        self.table = widgets.selectable_table(
            REPLAY_HEADERS,
            stretch_column=0,
            resize_modes={
                3: QHeaderView.ResizeToContents,
                4: QHeaderView.ResizeToContents,
            },
        )
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        _tame_header(self.table)
        self.card.add(self.table, 1)

        self.empty_none = widgets.EmptyState(
            "暂无回放文件",
            "Replays 目录里还没有 .wrpl 文件，先去打一场战斗吧。",
            icon_name="replay",
        )
        self.empty_search = widgets.EmptyState(
            "没有匹配的回放", "换个关键词试试，或清空搜索框。", icon_name="search"
        )
        self.card.add(self.empty_none, 1)
        self.card.add(self.empty_search, 1)
        self.empty_none.hide()
        self.empty_search.hide()

    # ------------------------------------------------------------- loading
    def refresh(self) -> None:
        install = self.page.current_install()
        if install is None:
            self._items = []
            self._apply_filter()
            return
        self._start(
            _list_replays,
            kwargs={"folder": install.replays},
            wants_progress=True,
            wants_cancel=True,
            on_progress=self._on_progress,
            on_done=self._on_listed,
            label="列出回放",
            busy_text="正在读取回放目录…",
        )

    def _on_progress(self, done: int, total: int, _text: str) -> None:
        self.busy.set_progress(done, total, "正在解析回放")

    def _on_listed(self, infos) -> None:
        self._settle()
        self.loaded = True
        self._items = list(infos or [])
        self._apply_filter()
        self.ctx.log.info(f"已读取 {len(self._items)} 个回放", "信息库")

    # -------------------------------------------------------------- filtering
    def _apply_filter(self, *_args) -> None:
        needle = self.search.text().strip()
        self._rows = [info for info in self._items if info.matches(needle)]
        self._fill_table()
        self._update_stats()
        self._update_actions()

    def _fill_table(self) -> None:
        table = self.table
        self._filling = True
        try:
            table.setSortingEnabled(False)
            table.setRowCount(0)
            table.setRowCount(len(self._rows))
            for row, info in enumerate(self._rows):
                name = _Cell(info.filename, info.filename.lower(), tooltip=info.path)
                name.setData(Qt.UserRole, info)
                table.setItem(row, 0, name)

                map_text = info.map_name if info.map_code else "—"
                tip = "\n".join(p for p in (info.map_name_en, info.level_path) if p)
                map_cell = _Cell(map_text, (info.map_name or "").lower(), tooltip=tip)
                table.setItem(row, 1, map_cell)

                table.setItem(row, 2, _Cell(info.mode_display, info.mode_display))

                size_cell = _Cell(winutil.human_size(info.size), float(info.size))
                size_cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 3, size_cell)

                time_cell = _Cell(_time_text(info.mtime), float(info.mtime))
                table.setItem(row, 4, time_cell)

            table.setSortingEnabled(True)
            table.sortItems(self._sort_column, self._sort_order)
            _quiet_sort_arrow(table)
        finally:
            self._filling = False

        has_rows = bool(self._rows)
        self.table.setVisible(has_rows)
        self.empty_none.setVisible(not has_rows and not self._items)
        self.empty_search.setVisible(not has_rows and bool(self._items))

    def _on_sort_changed(self, column: int, order) -> None:
        if self._filling:
            return
        self._sort_column = column
        self._sort_order = order

    def _update_stats(self) -> None:
        total = len(self._items)
        size = sum(info.size for info in self._items)
        maps = {info.map_code for info in self._items if info.map_code}
        self.tile_count.set_value(str(total) if total else "—")
        self.tile_size.set_value(winutil.human_size(size) if size else "—")
        self.tile_maps.set_value(str(len(maps)) if maps else "—")

    def _update_actions(self) -> None:
        count = len(_selected_rows(self.table))
        self.play_btn.setEnabled(bool(count))
        self.delete_btn.setEnabled(bool(count))
        self.delete_btn.setText(f"删除选中 ({count})" if count else "删除选中")

    # ------------------------------------------------------------- selection
    def _row_info(self, row: int):
        cell = self.table.item(row, 0)
        return None if cell is None else cell.data(Qt.UserRole)

    def _on_double_click(self, row: int, _column: int) -> None:
        info = self._row_info(row)
        if info is not None:
            self._play(info)

    def _play_selected(self) -> None:
        rows = _selected_rows(self.table)
        if not rows:
            return
        info = self._row_info(rows[0])
        if info is not None:
            self._play(info)

    def _play(self, info) -> None:
        install = self._require_install("播放回放")
        if install is None:
            return
        if not os.path.isfile(info.path):
            _notify(self, "回放文件已不存在，列表已刷新", "warn")
            self.refresh()
            return
        if not self._play_notice_shown:
            allowed, _checked = widgets.confirm(
                self, "播放回放",
                "WTToolbox 会把选中的 .wrpl 文件作为参数启动游戏客户端。",
                ok_text="继续播放",
                detail=(
                    "说明：工具只是把文件路径交给 win64 / win32 目录下的 aces.exe，"
                    "能否直接进入回放取决于游戏版本本身。如果游戏没有响应或忽略了这个参数，"
                    "你仍然可以在游戏内的「回放浏览器」中手动打开这个文件。"
                ),
            )
            if not allowed:
                return
            self._play_notice_shown = True
        self._start(
            gamelaunch.open_replay,
            kwargs={"install": install, "replay_path": info.path},
            on_done=lambda result: self._on_played(result, info),
            label="播放回放",
        )

    def _on_played(self, result, info) -> None:
        self._settle()
        if result is None or not getattr(result, "ok", False):
            error = getattr(result, "error", "") or "未知错误"
            _notify(self, f"启动游戏失败：{error}", "error")
            return
        _notify(self, f"已请求游戏打开回放 · {info.filename}", "success")

    # ---------------------------------------------------------------- actions
    def _open_dir(self) -> None:
        install = self._require_install("打开回放目录")
        if install is not None:
            self._reveal(install.replays, "回放目录")

    def _delete_selected(self) -> None:
        rows = _selected_rows(self.table)
        items = [info for info in (self._row_info(r) for r in rows) if info is not None]
        self._delete_payload(items, "删除回放", "回放文件", self._on_deleted)

    def _on_deleted(self, result) -> None:
        self._report_delete(self, result)
        self.refresh()


# --------------------------------------------------------------------------- #
#  2. 截图
# --------------------------------------------------------------------------- #
class _ScreenshotPane(_PaneBase):
    _log_name = "截图"
    loaded = False

    def __init__(self, page: "LibraryPage", parent: QWidget | None = None) -> None:
        super().__init__(page, parent)
        self._items: list = []
        self._limit = THUMB_BATCH
        self._pending_entries: list = []
        self._decode_queue: list[int] = []
        self._incoming: list = []
        self._thumb_total = 0
        self._thumb_done = 0
        self._decode_task = None
        self._decode_running = False
        self._placeholder_icon = QIcon()
        self._signals = _ThumbSignals(self)
        self._signals.decoded.connect(self._on_decoded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        self.card = widgets.Card(
            "截图", "Screenshots 目录中的游戏截图", icon_name="image"
        )
        outer.addWidget(self.card, 1)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.caption = widgets.make_label("—", "Muted")
        bar.addWidget(self.caption)
        bar.addWidget(widgets.hspacer())
        self.open_btn = widgets.primary_button("打开", "external", self._open_selected)
        self.open_btn.setEnabled(False)
        bar.addWidget(self.open_btn)
        self.reveal_btn = widgets.subtle_button(
            "打开所在位置", "folder", self._reveal_selected
        )
        self.reveal_btn.setEnabled(False)
        bar.addWidget(self.reveal_btn)
        bar.addWidget(widgets.subtle_button("刷新", "refresh", self.refresh))
        self.more_btn = widgets.subtle_button("显示全部", "chev-down", self._show_all)
        self.more_btn.hide()
        bar.addWidget(self.more_btn)
        self.delete_btn = widgets.danger_button("删除选中", "trash", self._delete_selected)
        self.delete_btn.setEnabled(False)
        _mute_when_disabled(self.delete_btn)
        bar.addWidget(self.delete_btn)
        self.card.add_layout(bar)

        self.card.add(self._build_busy())

        self.grid = QListWidget()
        self.grid.setObjectName("ThumbGrid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setIconSize(THUMB_SIZE)
        self.grid.setGridSize(THUMB_GRID)
        self.grid.setMovement(QListView.Static)
        self.grid.setSpacing(8)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setTextElideMode(Qt.ElideMiddle)
        self.grid.setUniformItemSizes(True)
        self.grid.itemDoubleClicked.connect(self._on_double_click)
        self.grid.itemSelectionChanged.connect(self._update_actions)
        self.card.add(self.grid, 1)

        self.empty_none = widgets.EmptyState(
            "暂无截图",
            "Screenshots 目录里还没有图片，游戏内按截图键就可以生成。",
            icon_name="image",
        )
        self.card.add(self.empty_none, 1)
        self.empty_none.hide()

        self._add_timer = QTimer(self)
        self._add_timer.setInterval(1)
        self._add_timer.timeout.connect(self._add_chunk)
        self._apply_timer = QTimer(self)
        self._apply_timer.setInterval(1)
        self._apply_timer.timeout.connect(self._apply_chunk)

    # ------------------------------------------------------------- loading
    def refresh(self) -> None:
        install = self.page.current_install()
        if install is None:
            self._items = []
            self._rebuild()
            return
        self._start(
            library.list_screenshots,
            kwargs={"install": install},
            on_done=self._on_listed,
            label="列出截图",
            busy_text="正在读取截图目录…",
        )

    def _on_listed(self, items) -> None:
        self._settle()
        self.loaded = True
        self._items = list(items or [])
        self._limit = min(len(self._items), THUMB_BATCH)
        self._rebuild()

    # ----------------------------------------------------------- thumbnails
    def _ratio(self) -> float:
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:  # pragma: no cover - defensive
            return 1.0

    def _placeholder(self) -> QIcon:
        ratio = self._ratio()
        pixmap = QPixmap(
            int(THUMB_SIZE.width() * ratio), int(THUMB_SIZE.height() * ratio)
        )
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(theme.current().surface_3))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        glyph_size = 40
        glyph = icons.icon_pixmap("image", theme.current().text_faint, glyph_size, 1.5)
        glyph = glyph.scaled(
            int(glyph_size * ratio), int(glyph_size * ratio),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        glyph.setDevicePixelRatio(ratio)
        painter.drawPixmap(
            int((THUMB_SIZE.width() - glyph_size) / 2),
            int((THUMB_SIZE.height() - glyph_size) / 2),
            glyph,
        )
        painter.end()
        return QIcon(pixmap)

    def _icon_from_image(self, image) -> QIcon:
        """Turn a decoded ``QImage`` into a dpr-aware icon (GUI thread only)."""
        ratio = self._ratio()
        target = QSize(
            int(THUMB_SIZE.width() * ratio), int(THUMB_SIZE.height() * ratio)
        )
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return self._placeholder()
        if pixmap.width() > target.width() or pixmap.height() > target.height():
            pixmap = pixmap.scaled(
                target, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        pixmap.setDevicePixelRatio(ratio)
        return QIcon(pixmap)

    # ---------------------------------------------------- incremental loading
    def _thumb_target_size(self) -> QSize:
        ratio = self._ratio()
        return QSize(
            int(THUMB_SIZE.width() * ratio), int(THUMB_SIZE.height() * ratio)
        )

    def _rebuild(self) -> None:
        """Reset the grid and start the two-stage loader (rows, then images)."""
        self._stop_loading()
        self.grid.clear()
        shown = self._items[: self._limit]
        self._placeholder_icon = self._placeholder()
        self._pending_entries = list(shown)
        self._thumb_total = len(shown)
        self._thumb_done = 0

        self._update_caption()
        self._update_actions()
        self.grid.setVisible(bool(shown))
        self.empty_none.setVisible(not shown)
        if self._pending_entries:
            self._add_timer.start()

    def _stop_loading(self) -> None:
        self._add_timer.stop()
        self._apply_timer.stop()
        self._incoming.clear()
        self._decode_queue.clear()
        self._decode_running = False
        if self._decode_task is not None:
            self._decode_task.cancel()
            self._decode_task = None

    def _add_chunk(self) -> None:
        """Create a few grid rows; never the whole batch in one go."""
        added = 0
        while self._pending_entries and added < THUMB_ADD_PER_TICK:
            item = self._pending_entries.pop(0)
            entry = QListWidgetItem(
                self._placeholder_icon, f"{item.name}\n{winutil.human_size(item.size)}"
            )
            entry.setData(Qt.UserRole, item)
            entry.setToolTip(f"{item.name}\n{item.path}")
            entry.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self._decode_queue.append(self.grid.count())
            self.grid.addItem(entry)
            added += 1
        if not self._pending_entries:
            self._add_timer.stop()
            self._start_decode()

    def _start_decode(self) -> None:
        """Decode every queued row on a worker thread (one task at a time)."""
        if self._decode_running or not self._decode_queue:
            if not self._decode_queue and not self._decode_running:
                self._update_caption()
            return
        jobs = [(index, self._items[index].path) for index in self._decode_queue]
        self._decode_queue = []
        self._decode_running = True
        self._decode_task = widgets.run_task(
            self,
            _decode_thumbnails,
            kwargs={
                "jobs": jobs,
                "size": self._thumb_target_size(),
                "on_image": self._signals.decoded.emit,
            },
            wants_cancel=True,
            on_done=self._on_decode_done,
            on_error=self._on_task_error,
            label="载入缩略图",
        )

    def _on_decoded(self, index: int, image) -> None:
        """Worker result: queue it; a timer applies a bounded number per tick."""
        self._incoming.append((index, image))
        if not self._apply_timer.isActive():
            self._apply_timer.start()

    def _apply_chunk(self) -> None:
        applied = 0
        while self._incoming and applied < THUMB_MAX_PER_TICK:
            index, image = self._incoming.pop(0)
            applied += 1
            entry = self.grid.item(index)
            if entry is None:
                continue
            entry.setIcon(self._icon_from_image(image))
        self._thumb_done = min(self._thumb_total, self._thumb_done + applied)
        if not self._incoming:
            self._apply_timer.stop()
            self._update_caption()

    def _on_decode_done(self, _decoded) -> None:
        self._decode_running = False
        self._decode_task = None
        if self._decode_queue:
            self._start_decode()
        if not self._incoming:
            self._update_caption()

    def _show_all(self) -> None:
        """Append the remaining rows without reloading what is already there."""
        self._limit = len(self._items)
        already = self.grid.count()
        self._pending_entries = list(self._items[already : self._limit])
        self._thumb_total += len(self._pending_entries)
        self._update_caption()
        if self._pending_entries:
            self._add_timer.start()
        else:
            self._start_decode()

    def _update_caption(self) -> None:
        if not self._items:
            self.caption.setText("—")
            self.more_btn.hide()
            return
        total = sum(item.size for item in self._items)
        text = f"{len(self._items)} 张截图 · {winutil.human_size(total)}"
        if self._limit < len(self._items):
            text += f" · 已显示 {self._limit}"
            self.more_btn.setText(f"显示全部（还剩 {len(self._items) - self._limit}）")
            self.more_btn.show()
        else:
            self.more_btn.hide()
        if (
            self._pending_entries
            or self._decode_queue
            or self._incoming
            or self._decode_running
        ):
            text += f" · 正在载入缩略图 {self._thumb_done}/{self._thumb_total}"
        self.caption.setText(text)

    # ------------------------------------------------------------- selection
    def _selected(self) -> list:
        return [entry.data(Qt.UserRole) for entry in self.grid.selectedItems()
                if entry.data(Qt.UserRole) is not None]

    def _update_actions(self) -> None:
        count = len(self.grid.selectedItems())
        for button in (self.open_btn, self.reveal_btn, self.delete_btn):
            button.setEnabled(bool(count))
        self.delete_btn.setText(f"删除选中 ({count})" if count else "删除选中")

    def _on_double_click(self, entry: QListWidgetItem) -> None:
        item = entry.data(Qt.UserRole)
        if item is not None:
            self._open(item.path)

    def _open(self, path: str) -> None:
        if not winutil.open_path(path):
            _notify(self, "无法打开该文件", "warn")

    def _open_selected(self) -> None:
        items = self._selected()
        if items:
            self._open(items[0].path)

    def _reveal_selected(self) -> None:
        items = self._selected()
        if items:
            winutil.reveal_in_explorer(items[0].path)

    # --------------------------------------------------------------- actions
    def _delete_selected(self) -> None:
        items = self._selected()
        self._delete_payload(items, "删除截图", "截图", self._on_deleted)

    def _on_deleted(self, result) -> None:
        self._report_delete(self, result)
        self.refresh()


# --------------------------------------------------------------------------- #
#  3-5. 涂装 / 瞄具 / 自定义任务
# --------------------------------------------------------------------------- #
class _ContentPane(_PaneBase):
    """A generic "content root" pane: skins, sights and custom missions."""

    loaded = False

    def __init__(
        self,
        page: "LibraryPage",
        *,
        title: str,
        subtitle: str,
        icon_name: str,
        root_of,
        list_fn,
        list_kwargs,
        wants_cancel: bool,
        empty_title: str,
        empty_subtitle: str,
        where: str,
        log_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(page, parent)
        self.title = title
        self.icon_name = icon_name
        self.root_of = root_of
        self.list_fn = list_fn
        self.list_kwargs = list_kwargs
        self._wants_cancel = wants_cancel
        self.where = where
        self._log_name = log_name
        self._items: list = []
        self._sort_column = 4
        self._sort_order = Qt.DescendingOrder
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        self.card = widgets.Card(title, subtitle, icon_name=icon_name)
        outer.addWidget(self.card, 1)

        info_row = QHBoxLayout()
        info_row.setSpacing(9)
        self.caption = widgets.make_label("—", "Muted")
        info_row.addWidget(self.caption)
        info_row.addWidget(widgets.hspacer())
        self.path_label = widgets.make_label("", "Faint")
        info_row.addWidget(self.path_label)
        self.card.add_layout(info_row)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        bar.addWidget(widgets.primary_button("安装压缩包", "upload", self._install_archive))
        bar.addWidget(widgets.subtle_button("从文件夹安装", "folder-open", self._install_folder))
        bar.addWidget(widgets.subtle_button("刷新", "refresh", self.refresh))
        bar.addWidget(widgets.subtle_button("打开目录", "folder", self._open_dir))
        self.delete_btn = widgets.danger_button("删除选中", "trash", self._delete_selected)
        self.delete_btn.setEnabled(False)
        _mute_when_disabled(self.delete_btn)
        bar.addWidget(self.delete_btn)
        bar.addWidget(widgets.hspacer())
        self.card.add_layout(bar)

        self.card.add(self._build_busy())

        self.table = widgets.selectable_table(
            CONTENT_HEADERS,
            stretch_column=0,
            resize_modes={
                1: QHeaderView.ResizeToContents,
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
                4: QHeaderView.ResizeToContents,
            },
        )
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        _tame_header(self.table)
        self.card.add(self.table, 1)

        self.empty_none = widgets.EmptyState(
            empty_title, empty_subtitle, icon_name=icon_name
        )
        self.card.add(self.empty_none, 1)
        self.empty_none.hide()

    # ------------------------------------------------------------- loading
    def _root(self, install) -> str:
        return self.root_of(install)

    def refresh(self) -> None:
        install = self.page.current_install()
        if install is None:
            self._items = []
            self._fill_table()
            self._update_caption()
            return
        root = self._root(install)
        self.path_label.setText(root)
        self._start(
            self.list_fn,
            kwargs=self.list_kwargs(root),
            wants_cancel=self._wants_cancel,
            on_done=self._on_listed,
            label=f"列出{self.title}",
            busy_text=f"正在读取{self.title}…",
        )

    def _on_listed(self, items) -> None:
        self._settle()
        self.loaded = True
        self._items = list(items or [])
        self._fill_table()
        self._update_caption()
        self._update_actions()

    # --------------------------------------------------------------- table
    def _status(self, item) -> tuple[str, str]:
        palette = theme.current()
        parts: list[str] = []
        color = palette.text_muted
        if item.is_dir:
            if item.has_blk:
                parts.append("含配置")
                color = palette.success
            else:
                parts.append("缺少 .blk")
                color = palette.warn
        if item.images:
            parts.append(f"{item.images} 张图片")
        if not parts:
            parts.append("—")
        return " · ".join(parts), color

    def _fill_table(self) -> None:
        table = self.table
        self._filling = True
        try:
            table.setSortingEnabled(False)
            table.setRowCount(0)
            table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                name = _Cell(item.name, item.name.lower(), tooltip=item.path)
                name.setData(Qt.UserRole, item)
                table.setItem(row, 0, name)

                size_cell = _Cell(item.size_text, float(item.size))
                size_cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 1, size_cell)

                count_cell = _Cell(str(item.file_count), int(item.file_count))
                count_cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 2, count_cell)

                text, color = self._status(item)
                status = _Cell(
                    text, text, tooltip=item.detail or item.path
                )
                status.setForeground(QColor(color))
                table.setItem(row, 3, status)

                table.setItem(row, 4, _Cell(item.datetime_text, float(item.mtime)))

            table.setSortingEnabled(True)
            table.sortItems(self._sort_column, self._sort_order)
            _quiet_sort_arrow(table)
        finally:
            self._filling = False

        has_rows = bool(self._items)
        self.table.setVisible(has_rows)
        self.empty_none.setVisible(not has_rows)

    def _on_sort_changed(self, column: int, order) -> None:
        if self._filling:
            return
        self._sort_column = column
        self._sort_order = order

    def _update_caption(self) -> None:
        if not self._items:
            self.caption.setText("0 个项目 · 0 B")
            return
        total = sum(item.size for item in self._items)
        self.caption.setText(
            f"{len(self._items)} 个项目 · {winutil.human_size(total)}"
        )

    def _update_actions(self) -> None:
        count = len(_selected_rows(self.table))
        self.delete_btn.setEnabled(bool(count))
        self.delete_btn.setText(f"删除选中 ({count})" if count else "删除选中")

    def _selected_items(self) -> list:
        items = []
        for row in _selected_rows(self.table):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.UserRole) is not None:
                items.append(cell.data(Qt.UserRole))
        return items

    # --------------------------------------------------------- installation
    def _install_archive(self) -> None:
        install = self._require_install("安装内容")
        if install is None:
            return
        path, _selected = QFileDialog.getOpenFileName(
            self, "选择压缩包", "", "压缩包 (*.zip)"
        )
        if not path:
            return
        self._start(
            library.inspect_archive,
            kwargs={"path": path},
            on_done=lambda preview: self._confirm_archive(path, preview),
            label="解析压缩包",
            busy_text="正在读取压缩包…",
        )

    def _confirm_archive(self, path: str, preview) -> None:
        self._settle()
        if preview is None or not getattr(preview, "ok", False):
            error = getattr(preview, "error", "") or "无法读取压缩包"
            _notify(self, f"压缩包不可用：{error}", "error")
            return
        detail = (
            f"包含 {preview.entries} 个文件 · {winutil.human_size(preview.total_size)}\n"
            f"安装位置：{self.path_label.text()}"
        )
        allowed, _checked = widgets.confirm(
            self, "安装压缩包",
            f"将安装为「{preview.suggested_name}」",
            ok_text="安装",
            detail=detail,
        )
        if allowed:
            self._start_install(path, overwrite=False)

    def _install_folder(self) -> None:
        install = self._require_install("安装内容")
        if install is None:
            return
        root = self._root(install)
        folder = QFileDialog.getExistingDirectory(self, "选择要安装的文件夹", "")
        if not folder:
            return
        source = os.path.normcase(os.path.abspath(folder))
        target = os.path.normcase(os.path.abspath(root))
        if source == target or target.startswith(source + os.sep):
            _notify(
                self, "请选择具体的文件夹，而不是目标目录本身或它的上级目录", "warn"
            )
            return
        self._start_install(folder, overwrite=False)

    def _start_install(self, source: str, *, overwrite: bool) -> None:
        install = self.page.current_install()
        if install is None:
            self._require_install("安装内容")
            return
        root = self._root(install)
        self.ctx.log.info(
            f"开始安装 {os.path.basename(source)} → {root}", "信息库"
        )
        self._start(
            library.install_from_path,
            kwargs={
                "source": source,
                "dest_root": root,
                "overwrite": overwrite,
                "use_trash": bool(self.ctx.settings.get("use_trash")),
                "trash_root": appdirs.trash_dir(),
            },
            wants_progress=True,
            wants_cancel=True,
            on_progress=self._on_install_progress,
            on_done=lambda outcome: self._on_install_done(outcome, source),
            label="安装内容",
            busy_text=f"正在安装 {os.path.basename(source)}…",
        )

    def _on_install_progress(self, done: int, total: int, text: str) -> None:
        self.busy.set_progress(done, total, "正在安装")

    def _on_install_done(self, outcome, source: str) -> None:
        self._settle()
        if outcome is None:
            return
        if outcome.cancelled:
            _notify(
                self, f"安装已取消（已复制 {outcome.installed} 个文件）", "warn"
            )
            self.refresh()
            return
        if outcome.ok:
            note = (
                f"已安装 {outcome.installed} 个文件"
                f"（{winutil.human_size(outcome.bytes_copied)}）"
            )
            if outcome.replaced:
                note += " · 已覆盖旧版本"
            _notify(self, note, "success")
            self.refresh()
            return
        if "目标已存在" in (outcome.error or ""):
            name = os.path.basename(outcome.target or "") or "该项目"
            allowed, _checked = widgets.confirm(
                self, "目标已存在",
                f"「{name}」已经存在于{self.where}中。",
                ok_text="覆盖",
                danger=True,
                detail=(
                    "覆盖会先把现有内容移入 WTToolbox 回收站"
                    "（可在 工具箱 → 回收站 还原），然后重新安装。"
                ),
            )
            if allowed:
                self._start_install(source, overwrite=True)
            return
        _notify(self, f"安装失败：{outcome.error or '未知错误'}", "error")

    # --------------------------------------------------------------- actions
    def _open_dir(self) -> None:
        install = self._require_install(f"打开{self.where}")
        if install is not None:
            self._reveal(self._root(install), self.where)

    def _delete_selected(self) -> None:
        self._delete_payload(
            self._selected_items(), f"删除{self.title}", "项目", self._on_deleted
        )

    def _on_deleted(self, result) -> None:
        self._report_delete(self, result)
        self.refresh()


# --------------------------------------------------------------------------- #
#  Page
# --------------------------------------------------------------------------- #
class LibraryPage(QWidget):
    """信息库 - one sub-page per content type behind a left-hand nav."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._requested: set[int] = set()

        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        self.stack = QStackedWidget()

        self.subnav = widgets.SubNavPanel()
        self.nav = self.subnav.nav
        self.replays = _ReplayPane(self)
        self.screenshots = _ScreenshotPane(self)
        self.skins = _ContentPane(
            self,
            title="涂装",
            subtitle="UserSkins 目录中的自定义涂装",
            icon_name="paint",
            root_of=lambda install: install.user_skins,
            list_fn=library.list_content,
            list_kwargs=lambda root: {"root": root, "kind": "skin"},
            wants_cancel=True,
            empty_title="暂无自定义涂装",
            empty_subtitle="可以安装 .zip 压缩包，或把涂装文件夹直接放进 UserSkins 目录。",
            where="涂装目录",
            log_name="涂装",
        )
        self.sights = _ContentPane(
            self,
            title="瞄具",
            subtitle="UserSights 目录中的自定义瞄具（按国家 / 载具）",
            icon_name="crosshair",
            root_of=lambda install: install.user_sights,
            list_fn=library.list_sights,
            list_kwargs=lambda root: {"root": root},
            wants_cancel=False,
            empty_title="暂无自定义瞄具",
            empty_subtitle="可以安装 .zip 压缩包，或把瞄具文件夹放进 UserSights\\<国家>\\<载具>。",
            where="瞄具目录",
            log_name="瞄具",
        )
        self.missions = _ContentPane(
            self,
            title="自定义任务",
            subtitle="UserMissions 目录中的自定义任务",
            icon_name="package",
            root_of=lambda install: install.user_missions,
            list_fn=library.list_content,
            list_kwargs=lambda root: {"root": root, "kind": "mission"},
            wants_cancel=True,
            empty_title="暂无自定义任务",
            empty_subtitle="可以安装 .zip 压缩包，或把任务文件夹放进 UserMissions 目录。",
            where="自定义任务目录",
            log_name="自定义任务",
        )
        self._panes = [
            self.replays, self.screenshots, self.skins, self.sights, self.missions,
        ]

        self.subnav.add_page("回放", self.replays, icon_name="replay", tooltip="战斗回放文件")
        self.subnav.add_page("截图", self.screenshots, icon_name="image", tooltip="游戏截图")
        self.subnav.add_page("涂装", self.skins, icon_name="paint", tooltip="自定义涂装")
        self.subnav.add_page("瞄具", self.sights, icon_name="crosshair", tooltip="自定义瞄具")
        self.subnav.add_page(
            "自定义任务", self.missions, icon_name="package", tooltip="自定义任务"
        )

        self.noinstall = QWidget()
        noinstall_layout = QVBoxLayout(self.noinstall)
        noinstall_layout.setContentsMargins(0, 0, 0, 0)
        noinstall_layout.addWidget(
            widgets.EmptyState(
                "尚未设置游戏目录",
                "请先在主页选择游戏目录，信息库才能读取回放、截图与用户内容。",
                icon_name="info",
            ),
            1,
        )

        self.stack.addWidget(self.subnav)
        self.stack.addWidget(self.noinstall)
        layout.addWidget(self.stack, 1)

        self.subnav.nav.currentRowChanged.connect(self._on_nav_changed)
        ctx.installChanged.connect(self._on_install_changed)

        self._apply_install_state()
        self._ensure_loaded(self.subnav.current_index())

    # ---------------------------------------------------------------- state
    def current_install(self):
        """The active :class:`GameInstall`, or ``None`` when there is none."""
        install = self.ctx.install
        if install is None or not install.exists:
            return None
        return install

    def require_install(self, reason: str = "此功能"):
        return self.ctx.require_install(self, reason=reason)

    def _apply_install_state(self) -> None:
        self.stack.setCurrentWidget(
            self.subnav if self.current_install() is not None else self.noinstall
        )

    def _ensure_loaded(self, index: int) -> None:
        if index < 0 or index >= len(self._panes):
            return
        if index in self._requested:
            return
        self._requested.add(index)
        self._panes[index].refresh()

    def _on_nav_changed(self, row: int) -> None:
        self._ensure_loaded(row)

    def _on_install_changed(self, _install) -> None:
        self._requested.clear()
        self._apply_install_state()
        if self.current_install() is not None:
            self._ensure_loaded(self.subnav.current_index())
