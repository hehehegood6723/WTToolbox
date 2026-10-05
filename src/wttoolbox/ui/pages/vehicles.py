"""载具对比 - pick any two vehicles and compare them on a radar chart.

Data comes from the public War Thunder wiki (see :mod:`wttoolbox.core.wtdata`).
The bundled index carries every vehicle's name, nation, rank and class; the
characteristics are fetched from that vehicle's own page on demand and cached.

The radar is deliberately *relative*: each axis is normalised to whichever of
the two vehicles is better on it, and the raw numbers are printed next to every
axis so nothing has to be taken on trust.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import wtdata
from .. import theme, widgets
from ..dialogs.vehicle_picker import VehiclePickerDialog

__all__ = ["VehiclesPage"]


class _SidesCard(widgets.Card):
    """One slot: currently selected vehicle plus a button to change it."""

    def __init__(self, ctx, tag: str, on_pick, parent: QWidget | None = None) -> None:
        super().__init__(f"载具 {tag}", "点击选择", icon_name="crosshair", parent=parent)
        self.ctx = ctx
        self.tag = tag
        self._on_pick = on_pick
        self.vehicle: wtdata.Vehicle | None = None

        self.name_label = widgets.make_label("尚未选择", "H2")
        self.meta_label = widgets.make_label("—", "Muted")
        self.status_label = widgets.make_label("", "Faint", wrap=True)
        self.add(self.name_label)
        self.add(self.meta_label)
        self.add(self.status_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.pick_button = widgets.primary_button("选择载具", "search", lambda: self._on_pick(self.tag))
        row.addWidget(self.pick_button)
        row.addStretch(1)
        self.add_layout(row)

    def set_vehicle(self, vehicle: wtdata.Vehicle | None, *, status: str = "") -> None:
        self.vehicle = vehicle
        if vehicle is None:
            self.name_label.setText("尚未选择")
            self.meta_label.setText("—")
        else:
            self.name_label.setText(vehicle.name)
            self.meta_label.setText(
                f"{self._nation_text(vehicle.nation)} · {self._class_text(vehicle.cls)} · "
                f"第 {vehicle.rank or '?'} 级"
            )
        self.status_label.setText(status)
        self.pick_button.setText("更换载具" if vehicle is not None else "选择载具")

    def _nation_text(self, key: str) -> str:
        return wtdata.index_stats()["nations"].get(key, {}).get("zh", key or "—")

    def _class_text(self, key: str) -> str:
        return wtdata.index_stats()["classes"].get(key, {}).get("zh", key or "—")


class VehiclesPage(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.spec_a: wtdata.VehicleSpec | None = None
        self.spec_b: wtdata.VehicleSpec | None = None
        self._pending = 0
        self._shown = False
        self._restored = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(0)

        self.scroll = widgets.ScrollColumn()
        outer.addWidget(self.scroll, 1)

        self.scroll.add(self._build_header())
        self.scroll.add(self._build_pickers())
        self.scroll.add(self._build_chart())
        self.scroll.add(self._build_table())
        self.scroll.add(self._build_details())
        self.scroll.body.addStretch(1)

    # ------------------------------------------------------------------ build
    def _build_header(self) -> widgets.Card:
        stats = wtdata.index_stats()
        cache = wtdata.cache_info()
        card = widgets.Card(
            "载具强度对比",
            "任选两辆载具，按双方都有的指标生成对比雷达图",
            icon_name="crosshair",
        )
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_total = widgets.StatTile("收录载具", f"{stats['count']:,}", icon_name="layers")
        self.tile_nations = widgets.StatTile(
            "国家", f"{len(stats['nations'])}", icon_name="star"
        )
        self.tile_classes = widgets.StatTile(
            "载具类型", f"{len(stats['classes'])}", icon_name="grid"
        )
        self.tile_cache = widgets.StatTile("已缓存详情", f"{cache['count']}", icon_name="save")
        for tile in (self.tile_total, self.tile_nations, self.tile_classes, self.tile_cache):
            tiles.addWidget(tile)
        card.add_layout(tiles)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(widgets.primary_button("开始对比", "activity", self.compare))
        buttons.addWidget(widgets.subtle_button("交换两侧", "sort", self.swap))
        buttons.addWidget(widgets.ghost_button("清空选择", "x-circle", self.reset))
        buttons.addWidget(widgets.ghost_button("清空详情缓存", "trash", self._clear_cache))
        buttons.addStretch(1)
        card.add_layout(buttons)

        card.add(
            widgets.make_label(
                "数据来源：War Thunder 官方 Wiki 载具页面（wiki.warthunder.com）。"
                "雷达图采用相对刻度——每根轴以两车中较强的一方为 100%，具体数值标在轴旁边；"
                "若某项指标在页面上读不到，这根轴就不会出现，不会用推测值填充。",
                "Faint", wrap=True,
            )
        )
        return card

    def _build_pickers(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.card_a = _SidesCard(self.ctx, "A", self._pick)
        self.card_b = _SidesCard(self.ctx, "B", self._pick)
        row.addWidget(self.card_a, 1)
        row.addWidget(self.card_b, 1)
        return holder

    def _build_chart(self) -> widgets.Card:
        card = widgets.Card("强度对比雷达图", "相对刻度，每根轴上更强的一方为 100%", icon_name="activity")
        self.radar = widgets.RadarChart()
        self.radar.setMinimumHeight(430)
        card.add(self.radar, 1)
        self.warning = widgets.make_label("", "Muted", wrap=True)
        self.warning.setVisible(False)
        card.add(self.warning)
        return card

    def _build_table(self) -> widgets.Card:
        card = widgets.Card("指标对照", "原始数值与胜负判定", icon_name="sort")
        self.table = widgets.selectable_table(
            ["指标", "载具 A", "载具 B", "优势方"],
            stretch_column=0,
            row_height=30,
            sortable=False,
            resize_modes={
                1: QHeaderView.ResizeToContents,
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
            },
        )
        self.table.setMinimumHeight(230)
        card.add(self.table)
        self.table_hint = widgets.make_label("尚未对比。", "Faint")
        card.add(self.table_hint)
        return card

    def _build_details(self) -> widgets.Card:
        card = widgets.Card("完整参数", "两辆载具在 Wiki 上的原始条目", icon_name="text")
        self.detail_a = widgets.LogView()
        self.detail_a.setMinimumHeight(200)
        self.detail_a.setPlaceholderText("载具 A 的完整参数会显示在这里。")
        self.detail_b = widgets.LogView()
        self.detail_b.setMinimumHeight(200)
        self.detail_b.setPlaceholderText("载具 B 的完整参数会显示在这里。")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.detail_a, 1)
        row.addWidget(self.detail_b, 1)
        card.add_layout(row)
        return card

    # ---------------------------------------------------------------- picking
    def _pick(self, tag: str) -> None:
        other = self.card_b.vehicle if tag == "A" else self.card_a.vehicle
        current = self.card_a.vehicle if tag == "A" else self.card_b.vehicle
        dialog = VehiclePickerDialog(
            self.ctx,
            self,
            title=f"为载具 {tag} 选择一辆载具",
            initial=current,
            # Narrow to the other side's class: a tank cannot be compared with
            # a ship, so there is no point offering one.
            same_class_as=other,
        )
        if dialog.exec() != VehiclePickerDialog.Accepted or dialog.selected is None:
            return
        card = self.card_a if tag == "A" else self.card_b
        card.set_vehicle(dialog.selected, status="尚未获取详情")
        if tag == "A":
            self.spec_a = None
        else:
            self.spec_b = None
        self._auto_compare()

    def swap(self) -> None:
        self.card_a.vehicle, self.card_b.vehicle = self.card_b.vehicle, self.card_a.vehicle
        self.spec_a, self.spec_b = self.spec_b, self.spec_a
        for card, vehicle in ((self.card_a, self.card_a.vehicle), (self.card_b, self.card_b.vehicle)):
            card.set_vehicle(vehicle, status="已交换，重新对比以刷新" if vehicle else "")
        self._render()

    def reset(self) -> None:
        self.card_a.set_vehicle(None)
        self.card_b.set_vehicle(None)
        self.spec_a = None
        self.spec_b = None
        self.radar.clear()
        self.table.setRowCount(0)
        self.table_hint.setText("尚未对比。")
        self.detail_a.clear()
        self.detail_b.clear()
        self.warning.setVisible(False)

    def _clear_cache(self) -> None:
        removed = wtdata.clear_cache()
        self.tile_cache.set_value("0")
        self.ctx.notify(self, f"已清空 {removed} 份载具详情缓存", "success")

    # ---------------------------------------------------------------- compare
    def _auto_compare(self) -> None:
        if self.card_a.vehicle is not None and self.card_b.vehicle is not None:
            self.compare()

    def compare(self) -> None:
        if self.card_a.vehicle is None or self.card_b.vehicle is None:
            self.ctx.notify(self, "请先选择两辆载具", "warn")
            return
        # Cross-service pairs are refused up front rather than after two
        # downloads that could only produce a meaningless chart.
        first, second = self.card_a.vehicle, self.card_b.vehicle
        if first.cls != second.cls:
            classes = wtdata.index_stats()["classes"]
            self._pending = 0
            self.spec_a = self.spec_b = None
            self._blocked(
                "不同阵营无法比对："
                f"{classes.get(first.cls, {}).get('zh', first.cls or '未知')} ↔ "
                f"{classes.get(second.cls, {}).get('zh', second.cls or '未知')}",
                "请在同类型里选两辆——两辆坦克、两架飞机、两艘舰船……",
                (),
            )
            self.ctx.notify(self, "不同阵营无法比对，请选择同类型的载具", "warn", 4000)
            return

        self._pending = 2
        self.radar.set_busy(
            f"正在获取 {first.name} 与 {second.name} 的数据…\n"
            "首次查询需要联网读取 Wiki 页面，可能需要几秒到十几秒。"
        )
        self.warning.setVisible(False)
        for tag, card in (("A", self.card_a), ("B", self.card_b)):
            card.set_vehicle(card.vehicle, status="正在获取详情…")
            self._fetch(tag, card.vehicle.slug)

    def _fetch(self, tag: str, slug: str) -> None:
        def done(spec: wtdata.VehicleSpec) -> None:
            if tag == "A":
                self.spec_a = spec
                card = self.card_a
            else:
                self.spec_b = spec
                card = self.card_b
            if spec.error:
                card.set_vehicle(card.vehicle, status=f"获取失败：{spec.error}")
            else:
                card.set_vehicle(
                    card.vehicle,
                    status=(
                        f"BR {spec.br or '—'} · 主武器 {spec.primary_weapon or '—'} · "
                        f"{len(spec.metrics)} 项指标 · {spec.age_text}"
                    ),
                )
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._render()

        widgets.run_task(
            self,
            wtdata.fetch_spec,
            kwargs={"slug": slug},
            on_done=done,
            on_error=lambda message, t=tag: self._fetch_failed(t, message),
            label=f"spec_{slug}",
        )

    def _fetch_failed(self, tag: str, message: str) -> None:
        card = self.card_a if tag == "A" else self.card_b
        card.set_vehicle(card.vehicle, status=f"获取失败：{message}")
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self._render()

    def _show_fetch_error(self) -> None:
        """Put the failure in the chart itself, not just a small label."""
        pending = [(tag, card) for tag, card in (("A", self.card_a), ("B", self.card_b))]
        failed = []
        for tag, card in pending:
            spec = self.spec_a if tag == "A" else self.spec_b
            if spec is not None and spec.error:
                failed.append(f"{card.vehicle.name if card.vehicle else tag}：{spec.error}")
        if not failed:
            return
        self.radar.set_blocked(
            "无法获取载具数据",
            "\n".join(failed) + "\n\n点「开始对比」可以重试（Wiki 偶尔会很慢）。",
        )

    def _blocked(self, headline: str, detail: str, specs) -> None:
        """Show a hard stop instead of a meaningless radar."""
        self.radar.set_blocked(headline, detail)
        self.table.setRowCount(0)
        self.table_hint.setText(headline)
        self.warning.setText(f"{headline}　{detail}")
        self.warning.setVisible(True)
        # The side cards must not keep saying "正在获取…" after a refusal.
        if not specs:
            for card in (self.card_a, self.card_b):
                if card.vehicle is not None:
                    card.set_vehicle(card.vehicle, status="类型不同，无法与另一侧对比")
        for view, spec in zip((self.detail_a, self.detail_b), specs):
            if spec.error:
                view.load_text(f"{spec.name or spec.slug}\n\n{spec.error}", "WARN")
            else:
                view.load_text(self._spec_text(spec), "INFO")
        self.ctx.log.warn(f"载具对比已阻止：{headline}", "载具")

    def _spec_text(self, spec: wtdata.VehicleSpec) -> str:
        lines = [
            f"{spec.name}  ·  BR {spec.br or '—'}  ·  第 {spec.rank or '?'} 级",
            f"来源：{spec.source_url}",
            f"获取时间：{spec.age_text}",
            "",
        ]
        for title, rows in spec.groups:
            lines.append(f"[{title}]")
            for label, value in rows:
                lines.append(f"    {label}：{value}")
            lines.append("")
        return "\n".join(lines)

    def _render(self) -> None:
        a, b = self.spec_a, self.spec_b
        if a is None or b is None:
            return
        names = (a.name or a.slug, b.name or b.slug)

        # Different services are not comparable at all: a tank has no climb
        # rate and a ship has no turret traverse worth comparing with a tank's.
        classes = wtdata.index_stats()["classes"]
        if a.cls != b.cls:
            label_a = classes.get(a.cls, {}).get("zh", a.cls or "未知")
            label_b = classes.get(b.cls, {}).get("zh", b.cls or "未知")
            self._blocked(
                f"不同阵营无法比对：{label_a} ↔ {label_b}",
                "请在同类型里选两辆——两辆坦克、两架飞机、两艘舰船……"
                "雷达图的每根轴都要求双方都有该项数据，跨军种比较没有意义。",
                (a, b),
            )
            return

        comparison = wtdata.build_comparison(a, b)

        if a.error or b.error:
            self._show_fetch_error()

        if comparison.warnings:
            self.warning.setText(" · ".join(comparison.warnings))
            self.warning.setVisible(True)
        else:
            self.warning.setVisible(False)

        self.radar.set_data(
            comparison.axes,
            names,
            note="每根轴：较强一方为 100%（数值标注在轴旁）" if comparison.axes else "",
        )

        palette = theme.current()
        self.table.setRowCount(0)
        for axis in comparison.axes:
            row = self.table.rowCount()
            self.table.insertRow(row)
            label = QTableWidgetItem(f"{axis.label}（{axis.unit}）" if axis.unit else axis.label)
            if axis.hint:
                label.setToolTip(axis.hint)
            self.table.setItem(row, 0, label)

            item_a = QTableWidgetItem(axis.text_a)
            item_b = QTableWidgetItem(axis.text_b)
            if axis.hint:
                item_a.setToolTip(f"{axis.hint}\n\n{axis.text_a}")
                item_b.setToolTip(f"{axis.hint}\n\n{axis.text_b}")
            if axis.winner == "a":
                item_a.setForeground(QColor(palette.success))
            elif axis.winner == "b":
                item_b.setForeground(QColor(palette.success))
            self.table.setItem(row, 1, item_a)
            self.table.setItem(row, 2, item_b)

            winner = {"a": names[0], "b": names[1], "tie": "持平"}[axis.winner]
            win_item = QTableWidgetItem(winner)
            win_item.setTextAlignment(Qt.AlignCenter)
            if axis.winner != "tie":
                win_item.setForeground(QColor(palette.success))
            self.table.setItem(row, 3, win_item)

        score_a, score_b = comparison.score
        if comparison.axes:
            self.table_hint.setText(
                f"共比较 {len(comparison.axes)} 项：{names[0]} 占优 {score_a} 项，"
                f"{names[1]} 占优 {score_b} 项。每项权重相同，不代表实战强弱。"
            )
        else:
            self.table_hint.setText(
                "两辆载具没有可共同比较的指标。可以试试选择同类型（同为坦克或同为飞机）的两辆载具。"
            )

        for view, spec in ((self.detail_a, a), (self.detail_b, b)):
            if spec.error:
                view.load_text(f"{spec.name or spec.slug}\n\n{spec.error}", "WARN")
                continue
            view.load_text(self._spec_text(spec), "INFO")

        self.tile_cache.set_value(str(wtdata.cache_info()["count"]))
        # Remember the pair so the comparison survives a restart.
        self.ctx.settings.set("vehicle_compare", [a.slug, b.slug])
        self.ctx.log.info(
            f"载具对比：{names[0]} vs {names[1]} · {len(comparison.axes)} 项指标", "载具"
        )

    # ------------------------------------------------------------------ hooks
    def on_show(self) -> None:
        self.tile_cache.set_value(str(wtdata.cache_info()["count"]))
        if not self._restored:
            self._restored = True
            self._restore_last()

    def _restore_last(self) -> None:
        """Reload the pair that was compared last time, if it still exists."""
        if self.card_a.vehicle is not None or self.card_b.vehicle is not None:
            return
        saved = self.ctx.settings.get("vehicle_compare") or []
        if not isinstance(saved, (list, tuple)) or len(saved) != 2:
            return
        # find() is case-insensitive: the index uses the wiki's canonical mixed
        # case, while anything saved earlier is lower case.
        first, second = wtdata.find(saved[0]), wtdata.find(saved[1])
        if first is None or second is None:
            return
        self.card_a.set_vehicle(first, status="正在恢复上次的对比…")
        self.card_b.set_vehicle(second, status="正在恢复上次的对比…")
        self.compare()

    def shutdown(self) -> None:
        pass
