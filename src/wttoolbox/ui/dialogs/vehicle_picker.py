"""A searchable picker for the ~3.6k vehicles in the bundled index."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import wtdata
from .. import icons, theme, widgets

__all__ = ["VehiclePickerDialog"]

MAX_ROWS = 700


class VehiclePickerDialog(QDialog):
    """Pick one vehicle.  Filters combine; the row cap keeps it responsive."""

    def __init__(
        self,
        ctx,
        parent: QWidget | None = None,
        *,
        title: str = "选择载具",
        initial: wtdata.Vehicle | None = None,
        same_class_as: wtdata.Vehicle | None = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.selected: wtdata.Vehicle | None = None
        self._refresh_timer: QTimer | None = None
        #: When the other side is already chosen the list is narrowed to its
        #: class: a tank cannot be compared with a ship, so offering one would
        #: only lead to a refusal.
        self._class_lock = same_class_as.cls if same_class_as is not None else ""

        self.setWindowTitle(title)
        self.setMinimumSize(760, 560)
        self.resize(820, 620)
        palette = theme.current()
        self.setStyleSheet(f"QDialog {{ background: {palette.bg}; }}")

        stats = wtdata.index_stats()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(11)

        header = QHBoxLayout()
        header.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("crosshair", palette.accent, 22, 1.8))
        glyph.setFixedWidth(26)
        header.addWidget(glyph)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(widgets.make_label(title, "H2"))
        subtitle = (
            f"本地索引收录 {stats['count']:,} 辆载具 · 数据来自 War Thunder 官方 Wiki"
        )
        if self._class_lock:
            zh = stats["classes"].get(self._class_lock, {}).get("zh", self._class_lock)
            subtitle = f"只列出「{zh}」（与另一侧同类型才能对比） · {subtitle}"
        titles.addWidget(widgets.make_label(subtitle, "Muted"))
        header.addLayout(titles, 1)
        layout.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(9)
        self.search = widgets.SearchBox("输入名称或代号，例如 T-34 / tiger / bf-109…")
        self.search.textChanged.connect(self._schedule_refresh)
        filters.addWidget(self.search, 1)

        self.nation_combo = QComboBox()
        self.nation_combo.addItem("全部国家", "")
        for key, value in stats["nations"].items():
            self.nation_combo.addItem(value["zh"], key)
        self.nation_combo.setFixedWidth(112)
        self.nation_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.nation_combo)

        self.class_combo = QComboBox()
        self.class_combo.addItem("全部类型", "")
        for key, value in stats["classes"].items():
            self.class_combo.addItem(value["zh"], key)
        self.class_combo.setFixedWidth(136)
        self.class_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.class_combo)

        self.rank_combo = QComboBox()
        self.rank_combo.addItem("全部阶级", None)
        for rank in range(1, 9):
            self.rank_combo.addItem(f"第 {rank} 级", rank)
        self.rank_combo.setFixedWidth(96)
        self.rank_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.rank_combo)

        if self._class_lock:
            index = self.class_combo.findData(self._class_lock)
            if index >= 0:
                self.class_combo.blockSignals(True)
                self.class_combo.setCurrentIndex(index)
                self.class_combo.blockSignals(False)
            self.class_combo.setEnabled(False)   # stays visible, just locked
            self.class_combo.setToolTip("两侧必须是同一类型才能对比")
        layout.addLayout(filters)

        self.table = widgets.selectable_table(
            ["名称", "国家", "类型", "阶级", "代号"],
            stretch_column=0,
            row_height=27,
            resize_modes={
                1: QHeaderView.ResizeToContents,
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
                4: QHeaderView.ResizeToContents,
            },
        )
        self.table.itemDoubleClicked.connect(lambda _item: self._accept())
        layout.addWidget(self.table, 1)

        self.hint = widgets.make_label("", "Faint")
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addStretch(1)
        buttons.addWidget(widgets.ghost_button("取消", None, self.reject))
        self.ok_button = widgets.primary_button("使用该载具", "check", self._accept)
        self.ok_button.setEnabled(False)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(self._on_selection)
        self._refresh()
        if initial is not None:
            self.search.setText(initial.name)

    # ------------------------------------------------------------------ data
    def _schedule_refresh(self, *_args) -> None:
        """Coalesce rapid filter changes into one rebuild.

        Every keystroke used to rebuild up to 700 rows synchronously; a short
        debounce keeps typing smooth even on a slow machine.
        """
        if self._refresh_timer is None:
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.setInterval(140)
            self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()

    def _refresh(self) -> None:
        vehicles = wtdata.search(
            self.search.text(),
            nation=self.nation_combo.currentData() or "",
            vehicle_class=self.class_combo.currentData() or "",
            rank=self.rank_combo.currentData(),
            limit=MAX_ROWS,
        )
        stats = wtdata.index_stats()
        nations = stats["nations"]
        classes = stats["classes"]

        # One repaint instead of one per inserted row.
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            self.table.setRowCount(len(vehicles))
            for row, vehicle in enumerate(vehicles):
                name = QTableWidgetItem(vehicle.name)
                name.setData(Qt.UserRole, vehicle)
                self.table.setItem(row, 0, name)
                self.table.setItem(
                    row, 1, QTableWidgetItem(nations.get(vehicle.nation, {}).get("zh", vehicle.nation))
                )
                self.table.setItem(
                    row, 2, QTableWidgetItem(classes.get(vehicle.cls, {}).get("zh", vehicle.cls))
                )
                self.table.setItem(row, 3, QTableWidgetItem(f"{vehicle.rank}" if vehicle.rank else "—"))
                self.table.setItem(row, 4, QTableWidgetItem(vehicle.slug))
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)

        if len(vehicles) >= MAX_ROWS:
            self.hint.setText(f"显示前 {MAX_ROWS} 条结果，请输入关键词或使用筛选缩小范围。")
        elif not vehicles:
            self.hint.setText("没有匹配的载具，换个关键词或放宽筛选试试。")
        else:
            self.hint.setText(f"共 {len(vehicles)} 条结果。双击一行即可选择。")
        self.ok_button.setEnabled(self.table.currentRow() >= 0)

    def _on_selection(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        self.ok_button.setEnabled(item is not None)

    def _accept(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        self.selected = item.data(Qt.UserRole)
        self.accept()
