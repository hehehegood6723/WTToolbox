"""科技树 page: every nation's tree, and what it costs to research a vehicle.

The layout is the wiki's own grid (which mirrors the game): per nation and rank,
rows of five columns, each column a vertical branch that continues into the next
rank.  Clicking a vehicle asks :mod:`wttoolbox.core.techtree` for the minimum
research points and silver lions to reach it, plus the exact research order.

Nothing here is invented.  Costs, armour and penetration come from the wiki
crawl; the rank-unlock numbers come from the game's dialogs and live in
``techtree.RANK_RULES`` so they can be corrected without a rebuild.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import techtree
from .. import theme, widgets

CLASS_LABELS = {
    "tank": "陆战",
    "aircraft": "空军",
    "helicopter": "直升机",
    "ship": "远洋舰船",
    "boat": "近岸舰艇",
}
NATION_LABELS = {
    "usa": "美国", "germany": "德国", "ussr": "苏联", "britain": "英国",
    "japan": "日本", "china": "中国", "italy": "意大利", "france": "法国",
    "sweden": "瑞典", "israel": "以色列",
}

CELL_WIDTH = 132
CELL_HEIGHT = 44


class _Cell(QFrame):
    """One grid slot: a single vehicle, or a folder with a row per member.

    Every member is its own click target, because a folder's vehicles are
    separate things to research and each has its own cost.
    """

    def __init__(self, nodes: list[techtree.Node], on_pick) -> None:
        super().__init__()
        self.nodes = nodes
        self._on_pick = on_pick
        premium = all(node.premium for node in nodes)
        self.setObjectName("TreeCellPrem" if premium else "TreeCell")
        self.setFixedWidth(CELL_WIDTH)
        rows = max(1, len(nodes))
        self.setFixedHeight(max(CELL_HEIGHT, 19 * rows + 10))
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        if len(nodes) > 1:
            head = QLabel(f"▸ {nodes[0].folder_label or f'{len(nodes)} 辆'}")
            head.setObjectName("TreeCellSub")
            head_font = head.font()
            head_font.setPointSizeF(max(6.5, head_font.pointSizeF() - 2.2))
            head.setFont(head_font)
            layout.addWidget(head)

        self._labels: list[QLabel] = []
        for node in nodes:
            label = QLabel(techtree.world().name_of(node.slug))
            label.setWordWrap(False)
            font = label.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() - (1.6 if len(nodes) > 1 else 1.2)))
            label.setFont(font)
            label.setToolTip(self._tooltip(node))
            layout.addWidget(label)
            self._labels.append(label)

        tip = "\n".join(self._tooltip(node) for node in nodes)
        self.setToolTip(tip)

    @staticmethod
    def _tooltip(node: techtree.Node) -> str:
        world = techtree.world()
        names = techtree.name_lines(node.slug)
        lines = [
            # every name we have: Chinese short, full designation, English
            " / ".join(names[:3]) if names else node.slug,
            f"{CLASS_LABELS.get(node.cls, node.cls)} · "
            f"{NATION_LABELS.get(node.nation, node.nation)} · "
            f"第 {techtree.RANK_LABELS.get(node.rank, node.rank)} 级",
        ]
        rp, sl = world.cost_of(node.slug)
        if rp is not None:
            lines.append(f"研发 {rp:,} RP · 购买 {sl or 0:,} SL")
        elif node.premium:
            lines.append("金币 / 礼包载具，不能用研发点解锁")
        else:
            lines.append("Wiki 未公布研发成本（初始 / 备用载具）")
        if node.req:
            lines.append(f"前置：{world.name_of(node.req)}")
        if node.origin == "column":
            lines.append("该前置由「同一列上下相邻」推导（Wiki 未标注）")
        lines.append("点击查看研发成本与路线")
        return "\n".join(lines)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        # pick whichever member the click landed on
        child = self.childAt(event.position().toPoint())
        chosen = self.nodes[0]
        for label, node in zip(self._labels, self.nodes):
            if child is label or (child is not None and label.geometry().contains(
                child.mapTo(self, child.rect().center())
            )):
                chosen = node
                break
        self._on_pick(chosen)


class _Empty(QWidget):
    """Keeps a column's x position when the grid cell is empty."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(CELL_WIDTH, CELL_HEIGHT)


class TechTreePage(QWidget):
    """Nation/class pickers, the grid, and the research analysis."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._selected: techtree.Node | None = None
        self._cells: list[_Cell] = []
        self._build()
        changed = getattr(ctx, "installChanged", None)
        if changed is not None:
            changed.connect(self._on_install_changed)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_tree_card(), 3)
        body.addWidget(self._build_analysis_card(), 2)
        outer.addLayout(body, 1)

        self.reload()

    def _build_header(self) -> QWidget:
        card = widgets.Card("科技树", "按 Wiki 科技树结构还原：每级逐行、每行五列，列会在下一级延续")
        row = QHBoxLayout()
        row.setSpacing(9)

        row.addWidget(widgets.make_label("类型", "Muted"))
        self.class_combo = QComboBox()
        for key in techtree.classes():
            self.class_combo.addItem(CLASS_LABELS.get(key, key), key)
        self.class_combo.setFixedWidth(126)
        self.class_combo.currentIndexChanged.connect(lambda _=0: self.reload())
        row.addWidget(self.class_combo)

        row.addWidget(widgets.make_label("国家", "Muted"))
        self.nation_combo = QComboBox()
        self.nation_combo.setFixedWidth(126)
        self.nation_combo.currentIndexChanged.connect(lambda _=0: self.reload())
        row.addWidget(self.nation_combo)

        self.stats_label = widgets.make_label("", "Muted")
        row.addWidget(self.stats_label, 1)
        card.body.addLayout(row)

        self.legend = widgets.make_label(
            "左半区为可研发载具，右半区为金币 / 礼包载具；点击任意载具查看研发成本与路线。",
            "Faint",
        )
        card.body.addWidget(self.legend)
        return card

    def _build_tree_card(self) -> QWidget:
        card = widgets.Card("科技树布局", "行 = 同级内的位置，列 = 同一条研发分支")
        self.tree_area = QScrollArea()
        self.tree_area.setWidgetResizable(True)
        self.tree_area.setFrameShape(QFrame.NoFrame)
        self.tree_host = QWidget()
        self.tree_layout = QVBoxLayout(self.tree_host)
        self.tree_layout.setContentsMargins(4, 4, 4, 4)
        self.tree_layout.setSpacing(6)
        self.tree_area.setWidget(self.tree_host)
        card.body.addWidget(self.tree_area, 1)
        return card

    def _build_analysis_card(self) -> QWidget:
        card = widgets.Card("研发分析", "点选载具后显示最少研发点、银狮与研发顺序")

        self.pick_label = widgets.make_label("尚未选择载具", "H2")
        card.body.addWidget(self.pick_label)
        self.pick_meta = widgets.make_label("", "Muted")
        card.body.addWidget(self.pick_meta)

        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        self.tile_rp = widgets.StatTile("研发点", "—", icon_name="lightning")
        self.tile_sl = widgets.StatTile("银狮", "—", icon_name="star")
        self.tile_count = widgets.StatTile("需要研发", "—", icon_name="sort")
        for tile in (self.tile_rp, self.tile_sl, self.tile_count):
            tiles.addWidget(tile)
        card.body.addLayout(tiles)

        self.rule_label = widgets.make_label("", "Faint")
        self.rule_label.setWordWrap(True)
        card.body.addWidget(self.rule_label)

        self.plan_table = widgets.selectable_table(
            ["顺序", "载具", "阶级", "研发点", "银狮", "计入原因"],
            stretch_column=1,
            row_height=26,
        )
        self.plan_table.setMinimumHeight(220)
        card.body.addWidget(self.plan_table, 1)

        self.notes = widgets.make_label("", "Faint")
        self.notes.setWordWrap(True)
        card.body.addWidget(self.notes)
        return card

    # ----------------------------------------------------------------- render
    def reload(self) -> None:
        vehicle_class = self.class_combo.currentData() or "tank"
        nations = techtree.nations_for(vehicle_class)
        if self.nation_combo.count() != len(nations) or (
            self.nation_combo.count() and self.nation_combo.itemData(0) != nations[0]
        ):
            self.nation_combo.blockSignals(True)
            self.nation_combo.clear()
            for key in nations:
                self.nation_combo.addItem(NATION_LABELS.get(key, key), key)
            self.nation_combo.blockSignals(False)
        nation = self.nation_combo.currentData() or (nations[0] if nations else "")

        self._render_tree(vehicle_class, nation)

        stats = techtree.stats()
        info = stats["per_class"].get(vehicle_class, {})
        self.stats_label.setText(
            f"{CLASS_LABELS.get(vehicle_class, vehicle_class)} · "
            f"{len(nations)} 个国家 · 本树 {info.get('vehicles', 0)} 辆"
            f"（可研发 {info.get('researchable', 0)} / 金币 {info.get('premium', 0)}）"
            f" · 全库 {stats['data_records']} 条数据，"
            f"{stats['vehicles_with_cost']} 辆有研发成本"
        )

    def _render_tree(self, vehicle_class: str, nation: str) -> None:
        while self.tree_layout.count():
            item = self.tree_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cells = []

        ranks = techtree.ranks_for(vehicle_class, nation)
        if not ranks:
            self.tree_layout.addWidget(
                widgets.make_label("这个国家没有该类载具的科技树。", "Muted")
            )
            return

        palette = theme.current()
        for rank_block in ranks:
            header = QLabel(f"第 {techtree.RANK_LABELS.get(rank_block['rank'], rank_block['rank'])} 级")
            header.setObjectName("TreeRankHeader")
            self.tree_layout.addWidget(header)

            for row in rank_block.get("rows", []):
                research = list(row.get("research") or [])
                premium = list(row.get("premium") or [])
                width = max(len(research), len(premium))
                if width == 0:
                    continue
                line = QHBoxLayout()
                line.setSpacing(4)

                def add_half(cells: list, count: int) -> None:
                    for index in range(count):
                        cell = cells[index] if index < len(cells) else None
                        if cell is None:
                            line.addWidget(_Empty())
                            continue
                        units = cell["units"] if cell["type"] == "folder" else [cell]
                        nodes = [
                            node for node in (techtree.find_node(u["slug"]) for u in units)
                            if node is not None
                        ]
                        if not nodes:
                            line.addWidget(_Empty())
                            continue
                        widget = _Cell(nodes, self._select)
                        self._cells.append(widget)
                        line.addWidget(widget)

                add_half(research, len(research) if research else 0)
                if premium:
                    divider = QFrame()
                    divider.setFrameShape(QFrame.VLine)
                    divider.setStyleSheet(f"color: {palette.border};")
                    line.addWidget(divider)
                    add_half(premium, len(premium))
                line.addStretch(1)
                holder = QWidget()
                holder.setLayout(line)
                holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.tree_layout.addWidget(holder)

        self.tree_layout.addStretch(1)
        self._apply_cell_style()

    def _apply_cell_style(self) -> None:
        palette = theme.current()
        self.tree_host.setStyleSheet(
            f"""
            QFrame#TreeCell {{
                background: {palette.surface_2};
                border: 1px solid {palette.border};
                border-radius: 7px;
            }}
            QFrame#TreeCell:hover {{ border-color: {palette.accent}; }}
            QFrame#TreeCellPrem {{
                background: {palette.surface_3};
                border: 1px dashed {palette.border};
                border-radius: 7px;
            }}
            QFrame#TreeCellPrem:hover {{ border-color: {palette.accent}; }}
            QLabel#TreeCellSub {{ color: {palette.text_faint}; }}
            QLabel#TreeRankHeader {{
                color: {palette.text};
                font-weight: 600;
                padding: 6px 2px 2px 2px;
            }}
            """
        )

    # --------------------------------------------------------------- analysis
    def _select(self, node: techtree.Node) -> None:
        self._selected = node
        plan = techtree.research_plan(node.slug)
        summary = techtree.vehicle_summary(node.slug)

        self.pick_label.setText(summary["name"])
        english = techtree.world().name_en(node.slug)
        full = techtree.name_zh_full(node.slug)
        extra = [item for item in (full, english) if item and item != summary["name"]]
        self.pick_label.setToolTip(" / ".join([summary["name"], *extra]))
        bits = [
            CLASS_LABELS.get(node.cls, node.cls),
            NATION_LABELS.get(node.nation, node.nation),
            f"第 {techtree.RANK_LABELS.get(node.rank, node.rank)} 级",
        ]
        if summary.get("br"):
            bits.append(f"BR {summary['br']}")
        if summary.get("rp") is not None:
            bits.append(f"自身研发 {summary['rp']:,} RP / 购买 {summary['sl'] or 0:,} SL")
        if node.premium:
            bits.append("金币 / 礼包载具")
        self.pick_meta.setText(" · ".join(bits))

        self.plan_table.setRowCount(0)
        if plan is None:
            self.tile_rp.set_value("—")
            self.tile_sl.set_value("—")
            self.tile_count.set_value("—")
            self.rule_label.setText("这辆载具不在任何科技树里，无法计算研发路线。")
            self.notes.setText("")
            return

        self.tile_rp.set_value(f"{plan.research_points:,}")
        self.tile_sl.set_value(f"{plan.silver_lions:,}")
        self.tile_count.set_value(str(plan.node_count))

        if plan.rule_source:
            self.rule_label.setText(f"阶级解锁规则：{plan.rule_source}")
        resolved = "，".join(
            f"{techtree.RANK_LABELS.get(rank, rank)}级需 {count} 辆"
            for rank, count in sorted(plan.rank_rules.items())
            if count
        )
        if resolved:
            self.rule_label.setText(
                (self.rule_label.text() + "　|　" if self.rule_label.text() else "")
                + f"本车实际用到：{resolved}"
            )

        palette = theme.current()
        for index, step in enumerate(plan.steps, start=1):
            row = self.plan_table.rowCount()
            self.plan_table.insertRow(row)
            cells = [
                str(index),
                step.name,
                techtree.RANK_LABELS.get(step.rank, str(step.rank or "?")),
                f"{step.rp:,}" if step.rp else "0（备用）",
                f"{step.sl:,}" if step.sl else "0",
                step.reason,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                if step.rp == 0 and column == 3:
                    item.setForeground(QColor(palette.text_faint))
                self.plan_table.setItem(row, column, item)

        self.notes.setText("\n".join("· " + note for note in plan.notes))
        self.ctx.log.info(
            f"研发分析：{summary['name']} · {plan.node_count} 辆 · "
            f"{plan.research_points:,} 研发点 / {plan.silver_lions:,} 银狮",
            "科技树",
        )

    def _on_install_changed(self, *_args) -> None:
        return
