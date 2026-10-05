"""图形配置编辑器 - a real ``config.blk`` editor.

Design notes
------------
* Edits are **staged**, not applied on every keystroke, so the file is written
  exactly once per save and nothing can be half-applied.
* Every save first copies the current file into
  ``%APPDATA%\\WTToolbox\\backups\\config`` and the dialog can restore any of
  those snapshots.
* The underlying :class:`~wttoolbox.core.blk.BlkDocument` only splices the
  value literals that changed, so comments, ordering and unknown keys survive
  byte-for-byte.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import blk, blk_schema, config_backup, winutil
from .. import icons, theme, widgets

__all__ = ["ConfigEditorDialog"]

_INT_LIMIT = 2_000_000_000


class _KeyRow(QFrame):
    """One ``key = value`` editor row."""

    def __init__(
        self,
        block: str,
        key: str,
        type_code: str,
        raw_value: str,
        spec: blk_schema.KeySpec | None,
        *,
        on_change,
        on_revert,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ConfigRow")
        self.block = block
        self.key = key
        self.type_code = type_code
        self.original_raw = raw_value
        self.spec = spec
        self._on_change = on_change
        self._on_revert = on_revert
        self._loading = False

        palette = theme.current()
        self._label = spec.label if spec else key
        self._help = spec.help if spec else ""
        self._dangerous = bool(spec and spec.dangerous)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        name = widgets.make_label(self._label, "RowTitle")
        name.setMinimumWidth(150)
        if self._dangerous:
            name.setText(f"⚠ {self._label}")
            # Repeat the role's font metrics: an inline sheet replaces the rule.
            name.setStyleSheet(
                f"color: {palette.warn}; font-size: 13px; font-weight: 600;"
            )
        top.addWidget(name, 1)

        self.editor = self._build_editor(raw_value, type_code, spec)
        top.addWidget(self.editor)

        self.revert_button = widgets.icon_button(
            "undo", "恢复为文件中的原值", size=15, on_click=self.revert
        )
        self.revert_button.setEnabled(False)
        top.addWidget(self.revert_button)
        layout.addLayout(top)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        key_label = widgets.make_label(f"{key}:{type_code}", "Faint")
        key_label.setStyleSheet(f"color: {palette.text_faint}; font-family: Consolas, monospace;")
        meta.addWidget(key_label)
        if self._help:
            meta.addWidget(widgets.make_label(self._help, "Faint", wrap=True), 1)
        else:
            meta.addStretch(1)
        layout.addLayout(meta)

        self.setProperty("changed", False)

    # ------------------------------------------------------------------ build
    def _build_editor(self, raw: str, type_code: str, spec) -> QWidget:
        code = type_code.lower()
        if code == "b":
            switch = widgets.ToggleSwitch()
            switch.setChecked(raw.strip().lower() in ("yes", "true", "1", "y"))
            switch.toggled.connect(self._on_bool)
            return switch

        if code in ("i", "l", "u"):
            box = QSpinBox()
            box.setRange(
                int(spec.minimum) if spec and spec.minimum is not None else -_INT_LIMIT,
                int(spec.maximum) if spec and spec.maximum is not None else _INT_LIMIT,
            )
            box.setSingleStep(int(spec.step) if spec and spec.step else 1)
            try:
                box.setValue(int(float(raw or 0)))
            except ValueError:
                box.setValue(0)
            box.setFixedWidth(150)
            box.valueChanged.connect(self._on_number)
            return box

        if code in ("r", "f"):
            box = QDoubleSpinBox()
            box.setDecimals(spec.decimals if spec and spec.decimals else 3)
            box.setRange(
                spec.minimum if spec and spec.minimum is not None else -1e6,
                spec.maximum if spec and spec.maximum is not None else 1e6,
            )
            box.setSingleStep(spec.step if spec and spec.step else 0.05)
            try:
                box.setValue(float(raw or 0))
            except ValueError:
                box.setValue(0.0)
            box.setFixedWidth(150)
            box.valueChanged.connect(self._on_number)
            return box

        if code in blk.VECTOR_TYPES:
            edit = QLineEdit(raw)
            edit.setFixedWidth(230)
            edit.textEdited.connect(lambda text: self._commit(text, vector=True))
            return edit

        # Strings: an editable combo when we have curated suggestions.
        if spec and spec.choices:
            combo = QComboBox()
            combo.setEditable(True)
            for value, label in spec.choices:
                combo.addItem(f"{label}  ·  {value}", value)
            current = blk.unquote(raw)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setEditText(current)
            combo.setFixedWidth(230)
            combo.currentIndexChanged.connect(
                lambda _index, c=combo: self._on_combo(c)
            )
            combo.lineEdit().editingFinished.connect(lambda c=combo: self._on_combo(c))
            return combo

        edit = QLineEdit(blk.unquote(raw))
        edit.setFixedWidth(230)
        edit.editingFinished.connect(lambda e=edit: self._commit(e.text()))
        return edit

    # ----------------------------------------------------------------- events
    def _on_combo(self, combo: QComboBox) -> None:
        """Resolve a combo's current value without ever writing a display label.

        When the user picks an entry we take its data; when the combo is in
        free-text mode (editable but unmatched) we take the typed text verbatim.
        """
        text = combo.currentText().strip()
        index = combo.findText(text)
        if index >= 0 and combo.itemData(index) is not None:
            self._commit(str(combo.itemData(index)))
        else:
            self._commit(text)

    def _on_bool(self, checked: bool) -> None:
        self._commit("yes" if checked else "no")

    def _on_number(self, _value) -> None:
        if isinstance(self.editor, QSpinBox):
            self._commit(str(self.editor.value()))
        elif isinstance(self.editor, QDoubleSpinBox):
            self._commit(blk.format_float(self.editor.value()))

    def _commit(self, value, *, vector: bool = False) -> None:
        if self._loading:
            return
        code = self.type_code.lower()
        if vector or code in blk.VECTOR_TYPES:
            raw = str(value).strip()
        elif code in blk.STRING_TYPES:
            raw = blk.quote(str(value))
        else:
            raw = str(value).strip()
        self._on_change(self.block, self.key, self.type_code, raw, self)

    # ------------------------------------------------------------------- api
    def current_raw(self) -> str:
        if isinstance(self.editor, widgets.ToggleSwitch):
            return "yes" if self.editor.isChecked() else "no"
        if isinstance(self.editor, QSpinBox):
            return str(self.editor.value())
        if isinstance(self.editor, QDoubleSpinBox):
            return blk.format_float(self.editor.value())
        if isinstance(self.editor, QComboBox):
            data = self.editor.currentData()
            return blk.quote(str(data if data is not None else self.editor.currentText()))
        if isinstance(self.editor, QLineEdit):
            text = self.editor.text()
            if self.type_code.lower() in blk.STRING_TYPES:
                return blk.quote(text)
            return text
        return self.original_raw

    def set_raw(self, raw: str) -> None:
        self._loading = True
        try:
            if isinstance(self.editor, widgets.ToggleSwitch):
                self.editor.setChecked(blk.BlkParam("", "b", raw).decoded)
            elif isinstance(self.editor, QSpinBox):
                self.editor.setValue(int(blk.BlkParam("", "i", raw).decoded))
            elif isinstance(self.editor, QDoubleSpinBox):
                self.editor.setValue(float(blk.BlkParam("", "r", raw).decoded))
            elif isinstance(self.editor, QComboBox):
                value = blk.unquote(raw)
                index = self.editor.findData(value)
                if index >= 0:
                    self.editor.setCurrentIndex(index)
                else:
                    self.editor.setEditText(value)
            elif isinstance(self.editor, QLineEdit):
                self.editor.setText(
                    blk.unquote(raw) if self.type_code.lower() in blk.STRING_TYPES else raw
                )
        finally:
            self._loading = False

    def revert(self) -> None:
        self.set_raw(self.original_raw)
        self._on_revert(self.block, self.key)

    def set_changed(self, changed: bool) -> None:
        palette = theme.current()
        self.setProperty("changed", changed)
        self.revert_button.setEnabled(changed)
        if changed:
            self.setStyleSheet(
                f"QFrame#ConfigRow {{ background: {palette.accent_soft};"
                f" border-left: 3px solid {palette.accent}; border-radius: 9px; }}"
            )
        else:
            self.setStyleSheet("")
        theme.restyle(self)

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        lowered = needle.lower()
        return lowered in self._label.lower() or lowered in self.key.lower()


class ConfigEditorDialog(QDialog):
    """Modal editor for the game's ``config.blk``."""

    def __init__(self, ctx, install, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.install = install
        self.config_path = install.config

        self._doc: blk.BlkDocument | None = None
        self._originals: dict[tuple[str, str], str] = {}
        self._types: dict[tuple[str, str], str] = {}
        self._pending: dict[tuple[str, str], str] = {}
        self._rows: dict[tuple[str, str], _KeyRow] = {}
        self._by_block: dict[str, list[_KeyRow]] = {}
        self._show_advanced = False
        self._filter = ""

        palette = theme.current()
        self.setWindowTitle("图形配置编辑器 · config.blk")
        self.setMinimumSize(940, 620)
        self.resize(1020, 700)
        self.setStyleSheet(
            f"QDialog {{ background: {palette.bg}; }}"
            f"QFrame#ConfigRow {{ border: 1px solid {palette.border}; border-radius: 9px; }}"
            f"QFrame#ConfigRow:hover {{ border-color: {palette.border_strong}; }}"
        )

        self._load_document()
        self._build()

    # ------------------------------------------------------------------ model
    def _load_document(self) -> None:
        self._doc = blk.BlkDocument.load(self.config_path)
        self._originals.clear()
        self._types.clear()
        for param in self._doc.walk_params():
            block = param.block.path[0] if param.block and param.block.path else ""
            self._originals[(block, param.key)] = param.raw_value
            self._types[(block, param.key)] = param.full_type
        self._pending.clear()

    def _blocks_in_file(self) -> list[str]:
        present = []
        for param in self._doc.walk_params():
            block = param.block.path[0] if param.block and param.block.path else ""
            if block not in present:
                present.append(block)
        ordered = [b for b in blk_schema.BLOCK_ORDER if b in present]
        extra = [b for b in present if b not in ordered]
        return ordered + extra

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(11)

        outer.addWidget(self._build_header())
        self.warning = self._build_warning()
        outer.addWidget(self.warning)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_sidebar(), 0)
        body.addWidget(self._build_content(), 1)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_footer())
        self._select_block(self._blocks_in_file()[0] if self._blocks_in_file() else "")

    def _build_header(self) -> QWidget:
        palette = theme.current()
        box = QFrame()
        box.setObjectName("SubCard")
        row = QHBoxLayout(box)
        row.setContentsMargins(13, 10, 13, 10)
        row.setSpacing(10)

        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("gear", palette.accent, 20, 1.8))
        row.addWidget(glyph)

        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(widgets.make_label("config.blk 图形配置", "CardTitle"))
        path_label = widgets.ElidedLabel(self.config_path, "RowMeta")
        path_label.setToolTip(self.config_path)
        texts.addWidget(path_label)
        row.addLayout(texts, 1)

        self.header_badge = widgets.Badge("已加载", "success")
        row.addWidget(self.header_badge)
        row.addWidget(
            widgets.icon_button(
                "folder", "打开文件所在目录",
                on_click=lambda: winutil.reveal_in_explorer(self.config_path),
            )
        )
        return box

    def _build_warning(self) -> QWidget:
        palette = theme.current()
        box = QFrame()
        box.setObjectName("SubCard")
        box.setStyleSheet(
            f"QFrame#SubCard {{ background: {palette.warn_soft};"
            f" border: 1px solid {palette.warn}; border-radius: 11px; }}"
        )
        row = QHBoxLayout(box)
        row.setContentsMargins(13, 9, 13, 9)
        row.setSpacing(9)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("warning", palette.warn, 18, 1.9))
        row.addWidget(glyph)
        row.addWidget(
            widgets.make_label(
                "游戏正在运行：游戏退出时会用它内存中的设置覆盖 config.blk，"
                "你在此处的修改可能丢失。建议先退出游戏再保存。",
                "Muted", wrap=True,
            ),
            1,
        )
        box.setVisible(bool(self.install.is_running()))
        return box

    def _build_sidebar(self) -> QWidget:
        holder = QWidget()
        holder.setFixedWidth(208)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.search = widgets.SearchBox("搜索选项…")
        self.search.textChanged.connect(self._on_filter)
        layout.addWidget(self.search)

        self.block_list = QListWidget()
        self.block_list.setObjectName("Plain")
        self.block_list.setFrameShape(QFrame.NoFrame)
        self.block_list.currentRowChanged.connect(self._on_block_row)
        layout.addWidget(self.block_list, 1)

        self.summary = widgets.make_label("", "Faint", wrap=True)
        layout.addWidget(self.summary)

        self.advanced_button = QPushButton("显示高级选项")
        self.advanced_button.setObjectName("Segment")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._on_advanced)
        layout.addWidget(self.advanced_button)
        return holder

    def _build_content(self) -> QWidget:
        self.content = widgets.ScrollColumn()
        self.content.body.setSpacing(8)
        return self.content

    def _build_footer(self) -> QWidget:
        palette = theme.current()
        box = QFrame()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)

        self.pending_label = widgets.make_label("未做任何修改", "Muted")
        row.addWidget(self.pending_label)
        row.addStretch(1)

        preset_button = QPushButton("画质预设")
        preset_button.setObjectName("Subtle")
        preset_menu = QMenu(self)
        for value, title, help_text in blk_schema.QUALITY_PRESETS:
            action = preset_menu.addAction(f"{title}  ({value})")
            action.setToolTip(help_text)
            action.triggered.connect(lambda _=False, v=value, t=title: self._apply_preset(v, t))
        preset_button.setMenu(preset_menu)
        row.addWidget(preset_button)

        addon_button = QPushButton("附加优化")
        addon_button.setObjectName("Subtle")
        self.addon_menu = QMenu(self)
        for block, key, type_code, title, help_text, value in blk_schema.ADDON_TOGGLES:
            action = self.addon_menu.addAction(title)
            action.setCheckable(True)
            action.setToolTip(f"{help_text}\n对应的键：{block}.{key}:{type_code}")
            action.toggled.connect(
                lambda checked, b=block, k=key, t=type_code, v=value, a=action: self._toggle_addon(
                    b, k, t, v, checked, a
                )
            )
            action.setData((block, key, type_code, value))
        addon_button.setMenu(self.addon_menu)
        row.addWidget(addon_button)

        backup_button = QPushButton("从备份还原")
        backup_button.setObjectName("Subtle")
        self.backup_menu = QMenu(self)
        self.backup_menu.aboutToShow.connect(self._refresh_backup_menu)
        backup_button.setMenu(self.backup_menu)
        row.addWidget(backup_button)

        revert = widgets.ghost_button("放弃更改", "undo", self._revert_all)
        row.addWidget(revert)

        self.save_button = widgets.primary_button("保存并备份", "save", self._save)
        row.addWidget(self.save_button)
        return box

    # ------------------------------------------------------------------ rows
    def _select_block(self, block: str) -> None:
        for index in range(self.block_list.count()):
            item = self.block_list.item(index)
            if item.data(Qt.UserRole) == block:
                self.block_list.setCurrentRow(index)
                return

    def _on_block_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.block_list.item(row)
        if item is None:
            return
        self._populate(item.data(Qt.UserRole))

    def _populate(self, block: str) -> None:
        while self.content.body.count():
            entry = self.content.body.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        params = [
            (p.key, p.full_type, p.raw_value)
            for p in (self._doc.walk_params() if self._doc else [])
            if (p.block.path[0] if p.block and p.block.path else "") == block
        ]

        shown = 0
        for key, type_code, raw in params:
            spec = blk_schema.spec_for(block, key)
            if spec and spec.advanced and not self._show_advanced:
                continue
            row = _KeyRow(
                block, key, type_code, raw, spec,
                on_change=self._stage,
                on_revert=self._unstage,
            )
            if not row.matches(self._filter):
                continue
            self.content.add(row)
            self._rows[(block, key)] = row
            shown += 1

        if not shown:
            hint = "没有匹配的选项。" if self._filter else "此分类下没有可编辑的选项。"
            self.content.add(widgets.EmptyState(hint, icon_name="search" if self._filter else "info"))
        self.content.body.addStretch(1)
        self._refresh_change_state()

    def _on_filter(self, text: str) -> None:
        self._filter = text.strip()
        if self._filter:
            # Search across every block rather than only the selected one.
            matches: list[tuple[str, str, str, str]] = []
            for param in self._doc.walk_params() if self._doc else []:
                block = param.block.path[0] if param.block and param.block.path else ""
                spec = blk_schema.spec_for(block, param.key)
                if spec and spec.advanced and not self._show_advanced:
                    continue
                label = spec.label if spec else param.key
                if self._filter.lower() in label.lower() or self._filter.lower() in param.key.lower():
                    matches.append((block, param.key, param.full_type, param.raw_value))
            while self.content.body.count():
                entry = self.content.body.takeAt(0)
                widget = entry.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            self._rows.clear()
            if not matches:
                self.content.add(widgets.EmptyState("没有匹配的选项", f"未找到包含“{self._filter}”的键", icon_name="search"))
            for block, key, type_code, raw in matches[:200]:
                row = _KeyRow(
                    block, key, type_code, raw, blk_schema.spec_for(block, key),
                    on_change=self._stage, on_revert=self._unstage,
                )
                self.content.add(row)
                self._rows[(block, key)] = row
            self.content.body.addStretch(1)
            self._refresh_change_state()
            return
        current = self.block_list.currentItem()
        if current is not None:
            self._populate(current.data(Qt.UserRole))

    def _on_advanced(self, checked: bool) -> None:
        self._show_advanced = checked
        self.advanced_button.setText("隐藏高级选项" if checked else "显示高级选项")
        if not self._filter:
            current = self.block_list.currentItem()
            if current is not None:
                self._populate(current.data(Qt.UserRole))

    # -------------------------------------------------------------- staging
    def _stage(self, block: str, key: str, type_code: str, raw: str, row: _KeyRow) -> None:
        original = self._originals.get((block, key))
        if original is not None and raw == original:
            self._pending.pop((block, key), None)
        else:
            self._pending[(block, key)] = raw
        self._types.setdefault((block, key), type_code)
        row.set_changed((block, key) in self._pending)
        self._refresh_change_state()

    def _unstage(self, block: str, key: str) -> None:
        self._pending.pop((block, key), None)
        self._refresh_change_state()

    def _toggle_addon(self, block: str, key: str, type_code: str, value, checked: bool, action) -> None:
        row = self._rows.get((block, key))
        if not checked:
            if row is not None:
                row.revert()
            else:
                self._pending.pop((block, key), None)
            self._refresh_change_state()
            return
        raw = blk.format_value(type_code, value)
        if row is not None:
            row.set_raw(raw)
        self._pending[(block, key)] = raw
        self._types.setdefault((block, key), type_code)
        if row is not None:
            row.set_changed(True)
        self._refresh_change_state()

    def _apply_preset(self, value: str, title: str) -> None:
        stages = [("", "graphicsQuality", "t", blk.format_value("t", value))]
        row = self._rows.get(("", "graphicsQuality"))
        if row is not None:
            row.set_raw(blk.format_value("t", value))
            row.set_changed(True)
        for block, key, type_code, raw in stages:
            self._pending[(block, key)] = raw
            self._types.setdefault((block, key), type_code)
        self._refresh_change_state()
        self.ctx.notify(
            self,
            f"已暂存画质预设“{title}”，点击“保存并备份”后写入 config.blk",
            "info",
            3600,
        )

    def _revert_all(self) -> None:
        for row in list(self._rows.values()):
            row.set_raw(row.original_raw)
            row.set_changed(False)
        self._pending.clear()
        for action in self.addon_menu.actions():
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._refresh_change_state()

    def _refresh_change_state(self) -> None:
        count = len(self._pending)
        if count:
            self.pending_label.setText(f"已修改 {count} 项（尚未写入磁盘）")
            self.pending_label.setStyleSheet(f"color: {theme.current().accent}; font-weight: 700;")
        else:
            self.pending_label.setText("未做任何修改")
            self.pending_label.setStyleSheet("")
        self.save_button.setEnabled(count > 0)
        self.save_button.setText(f"保存并备份（{count}）" if count else "保存并备份")

    # ---------------------------------------------------------------- saving
    def _change_list(self) -> list[tuple[str, str, str, str]]:
        rows = []
        for (block, key), raw in sorted(self._pending.items()):
            original = self._originals.get((block, key), "（不存在）")
            rows.append((block, key, original, raw))
        return rows

    def _save(self) -> None:
        if not self._pending:
            return
        if not os.path.isfile(self.config_path):
            self.ctx.notify(self, "config.blk 不存在，无法保存", "error")
            return

        changes = self._change_list()
        detail_lines = [
            f"{blk_schema.block_label(block)} · {key}:  {old}  →  {new}"
            for block, key, old, new in changes[:12]
        ]
        if len(changes) > 12:
            detail_lines.append(f"…以及另外 {len(changes) - 12} 项")
        detail = "\n".join(detail_lines)

        running = self.install.is_running()
        if running:
            accepted, _ = widgets.confirm(
                self,
                "游戏正在运行",
                "游戏退出时会用它内存中的设置覆盖 config.blk。现在保存的修改可能在游戏退出后失效。",
                ok_text="仍然保存",
                danger=True,
                detail=detail,
            )
            if not accepted:
                return
        else:
            accepted, _ = widgets.confirm(
                self,
                f"写入 {len(changes)} 项修改",
                "保存前会自动把当前 config.blk 备份到 WTToolbox 数据目录，可随时还原。",
                ok_text="保存",
                detail=detail,
            )
            if not accepted:
                return

        backup_path = config_backup.backup_config(
            self.config_path,
            keep=int(self.ctx.settings.get("backup_keep", 20)),
            label=f"保存前自动备份（{len(changes)} 项修改）",
        )

        try:
            doc = blk.BlkDocument.load(self.config_path)
            applied = 0
            for (block, key), raw in self._pending.items():
                block_path = [block] if block else []
                type_code = self._types.get((block, key), "t")
                if doc.param(block_path, key) is not None:
                    if doc.set_raw(block_path, key, raw):
                        applied += 1
                else:
                    value = blk.BlkParam(key=key, type=type_code, raw_value=raw).decoded
                    if doc.ensure(block_path, key, type_code, value):
                        applied += 1
            doc.save(self.config_path, backup=False)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.ctx.log.error(f"保存 config.blk 失败：{exc}", "配置")
            widgets.message_dialog(
                self, "保存失败", f"写入 config.blk 时出错：{exc}",
                detail="原文件未被修改（写入是原子的）。" if backup_path else "",
            )
            return

        self.ctx.log.ok(
            f"config.blk 已更新 {applied} 项"
            + (f"，备份：{os.path.basename(backup_path)}" if backup_path else ""),
            "配置",
        )
        self.ctx.notify(self, f"已写入 {applied} 项修改", "success")
        self._load_document()
        self.header_badge.setText("已保存")
        self.header_badge.set_kind("success")
        if not self._filter:
            current = self.block_list.currentItem()
            if current is not None:
                self._populate(current.data(Qt.UserRole))
        widgets.later(self, 4000, lambda: self.header_badge.set_kind("neutral"))

    # --------------------------------------------------------------- backups
    def _refresh_backup_menu(self) -> None:
        self.backup_menu.clear()
        backups = config_backup.list_backups()
        if not backups:
            action = self.backup_menu.addAction("暂无备份")
            action.setEnabled(False)
            return
        for backup in backups[:20]:
            text = f"{backup.text}  ·  {backup.size_text}"
            if backup.label:
                text += f"  ·  {backup.label}"
            action = self.backup_menu.addAction(text)
            action.triggered.connect(lambda _=False, b=backup: self._restore(b))
        self.backup_menu.addSeparator()
        open_action = self.backup_menu.addAction("打开备份目录")
        open_action.triggered.connect(
            lambda: winutil.open_path(config_backup.backup_dir())
        )

    def _restore(self, backup: config_backup.ConfigBackup) -> None:
        accepted, _ = widgets.confirm(
            self,
            "从备份还原",
            f"将用 {backup.text} 的备份覆盖当前 config.blk。还原前的文件也会自动备份一次。",
            ok_text="还原",
            danger=True,
        )
        if not accepted:
            return
        if self.install.is_running():
            self.ctx.notify(self, "游戏正在运行，还原后可能仍会被游戏覆盖", "warn", 4200)
        ok, message = config_backup.restore_backup(backup, self.config_path)
        if ok:
            self._load_document()
            self._refresh_change_state()
            current = self.block_list.currentItem()
            if current is not None:
                self._populate(current.data(Qt.UserRole))
            self.ctx.notify(self, "已从备份还原 config.blk", "success")
        else:
            self.ctx.notify(self, message, "error")

    # ------------------------------------------------------------------ close
    def reject(self) -> None:
        if self._pending:
            accepted, _ = widgets.confirm(
                self,
                "放弃未保存的修改？",
                f"当前有 {len(self._pending)} 项修改尚未写入 config.blk，关闭后将丢失。",
                ok_text="放弃并关闭",
                cancel_text="继续编辑",
                danger=True,
            )
            if not accepted:
                return
        super().reject()

    # ------------------------------------------------------------------- list
    def _refresh_block_list(self) -> None:
        self.block_list.blockSignals(True)
        self.block_list.clear()
        for block in self._blocks_in_file():
            count = sum(
                1
                for p in self._doc.walk_params()
                if (p.block.path[0] if p.block and p.block.path else "") == block
            )
            label = blk_schema.block_label(block)
            item = QListWidgetItem(f"{label}   ({count})")
            spec_help = blk_schema.BLOCK_HELP.get(block, "")
            if spec_help:
                item.setToolTip(spec_help)
            item.setData(Qt.UserRole, block)
            item.setSizeHint(QSize(0, 38))
            self.block_list.addItem(item)
        self.block_list.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not getattr(self, "_first_show", False):
            self._first_show = True
            self._refresh_block_list()
            self.summary.setText(
                f"共 {sum(1 for _ in self._doc.walk_params())} 个键 · "
                f"{self.block_list.count()} 个分类"
            )
            if self.block_list.count():
                self.block_list.setCurrentRow(0)
            self.warning.setVisible(bool(self.install.is_running()))
