"""工具箱 - the sub-function library.

Five independent tools behind a left nav:

* 图形配置   - inspect ``config.blk`` and open the real config editor
* 磁盘清理   - measure and remove the game's throwaway folders
* 日志查看   - read the launcher/updater logs (and explain why ``.clog`` cannot
               be decoded), with live tailing and level filtering
* 完整性自检 - structural check of the files the game ships
* 回收站     - restore or permanently remove anything this toolkit deleted
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import (
    appdirs,
    blk,
    config_backup,
    gamelog,
    healthcheck,
    winutil,
)
from ...core import cleaner, trash
from .. import icons, theme, widgets

__all__ = ["ToolsPage"]


class ToolsPage(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx

        self._clean_targets: list[cleaner.CleanTarget] = []
        self._clean_loaded = False
        self._log_groups: dict[str, list[gamelog.LogFile]] = {}
        self._log_selected: gamelog.LogFile | None = None
        self._tailer: gamelog.TextTailer | None = None
        self._check_loaded = False
        self._trash_entries: list[trash.TrashEntry] = []

        self._build()
        self._install_timer = QTimer(self)
        self._install_timer.setSingleShot(True)
        self._install_timer.timeout.connect(self._on_install_settled)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(0)

        self.subnav = widgets.SubNavPanel(nav_width=170)
        self.subnav.nav.currentRowChanged.connect(self._on_sub_changed)

        self.subnav.add_page("图形配置", self._build_config_tool(), icon_name="monitor")
        self.subnav.add_page("磁盘清理", self._build_cleaner_tool(), icon_name="sparkle")
        self.subnav.add_page("日志查看", self._build_log_tool(), icon_name="text")
        self.subnav.add_page("完整性自检", self._build_check_tool(), icon_name="shield")
        self.subnav.add_page("回收站", self._build_trash_tool(), icon_name="trash")

        outer.addWidget(self.subnav)

        self._tail_timer = QTimer(self)
        self._tail_timer.setInterval(1500)
        self._tail_timer.timeout.connect(self._poll_tail)

    # =========================================================== 1. config ==
    def _build_config_tool(self) -> QWidget:
        page = widgets.ScrollColumn()

        self.config_warning = self._warning_banner(
            "游戏正在运行：游戏退出时会覆盖 config.blk，改动可能丢失。建议先退出游戏再编辑。"
        )
        page.add(self.config_warning)

        card = widgets.Card("config.blk 图形配置", "游戏读取的图形与显示设置", icon_name="monitor")
        self.config_path_label = widgets.ElidedLabel("—", "RowMeta")
        card.add(self.config_path_label)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.config_tiles = {
            "keys": widgets.StatTile("可编辑键数量", "—", icon_name="text"),
            "quality": widgets.StatTile("当前画质预设", "—", icon_name="star"),
            "backups": widgets.StatTile("可用备份", "—", icon_name="save"),
            "version": widgets.StatTile("客户端版本", "—", icon_name="cpu"),
        }
        for tile in self.config_tiles.values():
            tiles.addWidget(tile)
        card.add_layout(tiles)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(
            widgets.primary_button("打开图形配置编辑器", "gear", self._open_config_editor)
        )
        buttons.addWidget(
            widgets.subtle_button(
                "打开文件所在目录", "folder",
                lambda: self._reveal_config_file(),
            )
        )
        buttons.addWidget(
            widgets.ghost_button("重新读取", "refresh", self._load_config_tool)
        )
        buttons.addStretch(1)
        card.add_layout(buttons)
        card.add(
            widgets.make_label(
                "编辑器只修改 config.blk 中已存在的值，写入前会自动备份，"
                "并且可以随时从下方的备份列表中还原。",
                "Faint", wrap=True,
            )
        )
        page.add(card)

        backup_card = widgets.Card("配置备份", "每次保存前自动创建的快照", icon_name="save")
        backup_card.add_action(
            widgets.icon_button("folder", "打开备份目录", on_click=lambda: winutil.open_path(config_backup.backup_dir()))
        )
        backup_card.add_action(
            widgets.icon_button("refresh", "刷新备份列表", on_click=self._load_config_tool)
        )
        self.backup_table = widgets.selectable_table(
            ["备份时间", "大小", "备注"], stretch_column=2, row_height=28,
            resize_modes={0: QHeaderView.ResizeToContents, 1: QHeaderView.ResizeToContents},
        )
        self.backup_table.setMinimumHeight(150)
        backup_card.add(self.backup_table)
        self.backup_empty = widgets.make_label(
            "还没有备份。打开编辑器并保存一次，或在下方点击“立即备份一次”创建一个快照。",
            "Faint", wrap=True,
        )
        backup_card.add(self.backup_empty)
        backup_buttons = QHBoxLayout()
        backup_buttons.setSpacing(9)
        backup_buttons.addWidget(
            widgets.primary_button("还原选中的备份", "undo", self._restore_backup)
        )
        backup_buttons.addWidget(
            widgets.danger_button("删除备份", "trash", self._delete_backup)
        )
        backup_buttons.addWidget(
            widgets.ghost_button(
                "立即备份一次", "save",
                lambda: self._manual_backup(),
            )
        )
        backup_buttons.addStretch(1)
        backup_card.add_layout(backup_buttons)
        page.add(backup_card)
        page.body.addStretch(1)
        return page

    def _load_config_tool(self) -> None:
        install = self.ctx.install
        if install is None or not install.exists:
            self.config_path_label.setText("尚未设置游戏目录")
            return
        self.config_path_label.setText(install.config)
        self.config_path_label.setToolTip(install.config)
        self.config_warning.setVisible(install.is_running())
        self.config_tiles["version"].set_value(install.version() or "—")

        if os.path.isfile(install.config):
            try:
                document = blk.BlkDocument.load(install.config)
                count = sum(1 for _ in document.walk_params())
                self.config_tiles["keys"].set_value(str(count))
                quality = document.value([], "graphicsQuality", "—")
                self.config_tiles["quality"].set_value(str(quality))
            except Exception as exc:  # noqa: BLE001
                self.config_tiles["keys"].set_value("解析失败")
                self.ctx.log.warn(f"读取 config.blk 失败：{exc}", "配置")
        else:
            self.config_tiles["keys"].set_value("文件缺失")

        self._populate_backups()

    def _populate_backups(self) -> None:
        backups = config_backup.list_backups()
        self.backup_table.setRowCount(0)
        for backup in backups:
            row = self.backup_table.rowCount()
            self.backup_table.insertRow(row)
            values = (backup.text, backup.size_text, backup.label or "—")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, backup)
                if column == 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.backup_table.setItem(row, column, item)
        self.config_tiles["backups"].set_value(str(len(backups)))
        self.backup_empty.setVisible(not backups)

    def _selected_backup(self) -> config_backup.ConfigBackup | None:
        row = self.backup_table.currentRow()
        if row < 0:
            return None
        item = self.backup_table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _restore_backup(self) -> None:
        install = self.ctx.require_install(self, reason="还原配置备份")
        if install is None:
            return
        backup = self._selected_backup()
        if backup is None:
            self.ctx.notify(self, "请先在列表中选中一个备份", "warn")
            return
        accepted, _ = widgets.confirm(
            self,
            "还原 config.blk",
            f"将用 {backup.text} 的备份覆盖当前的 config.blk。",
            ok_text="还原",
            danger=True,
            detail="还原前的当前文件也会被自动备份一份，因此这一步是可逆的。",
        )
        if not accepted:
            return
        ok, message = config_backup.restore_backup(backup, install.config)
        self.ctx.notify(self, message if ok else f"还原失败：{message}", "success" if ok else "error")
        self._load_config_tool()

    def _delete_backup(self) -> None:
        backup = self._selected_backup()
        if backup is None:
            self.ctx.notify(self, "请先在列表中选中一个备份", "warn")
            return
        accepted, _ = widgets.confirm(
            self, "删除备份", f"永久删除 {backup.text} 的备份文件？",
            ok_text="删除", danger=True,
        )
        if accepted and config_backup.delete_backup(backup):
            self.ctx.notify(self, "已删除备份", "success")
            self._load_config_tool()

    def _manual_backup(self) -> None:
        install = self.ctx.require_install(self, reason="备份配置")
        if install is None:
            return
        path = config_backup.backup_config(
            install.config,
            keep=int(self.ctx.settings.get("backup_keep", 20)),
            label="手动备份",
        )
        if path:
            self.ctx.notify(self, f"已备份为 {os.path.basename(path)}", "success")
        else:
            self.ctx.notify(self, "备份失败：config.blk 不存在或无法读取", "error")
        self._load_config_tool()

    def _open_config_editor(self) -> None:
        install = self.ctx.require_install(self, reason="编辑图形配置")
        if install is None:
            return
        try:
            from ..dialogs.config_editor import ConfigEditorDialog

            dialog = ConfigEditorDialog(self.ctx, install, self)
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            self.ctx.log.error(f"配置编辑器打开失败：{exc}", "配置")
            self.ctx.notify(self, f"配置编辑器打开失败：{exc}", "error", 5000)
        self._load_config_tool()

    def _reveal_config_file(self) -> None:
        install = self.ctx.require_install(self, reason="打开配置文件")
        if install is None:
            return
        if os.path.isfile(install.config):
            winutil.reveal_in_explorer(install.config)
        else:
            winutil.open_path(install.root)

    # ========================================================== 2. cleaner ==
    def _build_cleaner_tool(self) -> QWidget:
        page = widgets.ScrollColumn()

        card = widgets.Card("磁盘清理", "删除游戏可以自行重建的缓存与旧日志", icon_name="sparkle")
        self.clean_summary = widgets.make_label("尚未扫描。点击“开始扫描”统计各项占用。", "Muted", wrap=True)
        card.add(self.clean_summary)

        self.clean_busy = widgets.BusyStrip()
        card.add(self.clean_busy)

        self.clean_table = widgets.selectable_table(
            ["", "清理项", "大小", "文件数", "风险说明"],
            stretch_column=4,
            row_height=30,
            sortable=False,
            resize_modes={
                0: QHeaderView.ResizeToContents,
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
            },
        )
        self.clean_table.setMinimumHeight(280)
        self.clean_table.itemChanged.connect(lambda _item: self._update_clean_summary())
        card.add(self.clean_table)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(widgets.primary_button("开始扫描", "refresh", self._scan_targets))
        buttons.addWidget(widgets.subtle_button("全选安全项", "check", self._select_safe))
        buttons.addWidget(widgets.ghost_button("全不选", "x-circle", self._select_none))
        buttons.addWidget(widgets.danger_button("清理选中项", "trash", self._run_clean))
        buttons.addStretch(1)
        self.clean_trash_toggle = widgets.CheckBox("移入回收站（可还原）")
        self.clean_trash_toggle.setChecked(bool(self.ctx.settings.get("use_trash", True)))
        self.clean_trash_toggle.toggled.connect(
            lambda checked: self.ctx.settings.set("use_trash", checked)
        )
        buttons.addWidget(self.clean_trash_toggle)
        card.add_layout(buttons)

        card.add(
            widgets.make_label(
                "着色器缓存与内容缓存在游戏运行时可能被占用，建议先退出游戏再清理。"
                "个人数据（回放、截图）默认不勾选。",
                "Faint", wrap=True,
            )
        )
        page.add(card)
        page.body.addStretch(1)
        return page

    def _scan_targets(self) -> None:
        install = self.ctx.require_install(self, reason="磁盘清理")
        if install is None:
            return
        self.clean_busy.start("正在统计占用…")
        targets = cleaner.build_targets(
            install,
            log_keep_days=float(self.ctx.settings.get("log_keep_days", 14)),
        )

        def done(measured) -> None:
            self._clean_targets = list(measured)
            self._clean_loaded = True
            self.clean_busy.stop()
            self._populate_clean_table()

        widgets.run_task(
            self,
            cleaner.measure_all,
            kwargs={"targets": targets},
            wants_progress=True,
            wants_cancel=True,
            on_done=done,
            on_error=lambda message: (self.clean_busy.stop(), self.ctx.notify(self, f"扫描失败：{message}", "error")),
            on_progress=lambda done_count, total, text: self.clean_busy.set_progress(
                done_count, total, text or "统计中"
            ),
            label="clean_scan",
        )

    def _populate_clean_table(self) -> None:
        table = self.clean_table
        table.blockSignals(True)
        table.setRowCount(0)
        for target in self._clean_targets:
            row = table.rowCount()
            table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if target.enabled_by_default else Qt.Unchecked)
            check.setData(Qt.UserRole, target)
            table.setItem(row, 0, check)

            name = target.label
            if target.missing:
                name += "（不存在）"
            table.setItem(row, 1, QTableWidgetItem(name))

            size_item = QTableWidgetItem(target.size_text if target.size else "—")
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 2, size_item)

            count_item = QTableWidgetItem(f"{target.file_count:,}" if target.file_count else "—")
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 3, count_item)

            risk = QTableWidgetItem(target.risk or "无风险")
            palette = theme.current()
            if target.category == "userdata":
                risk.setForeground(QColor(palette.error))
            elif target.category == "cache":
                risk.setForeground(QColor(palette.warn))
            table.setItem(row, 4, risk)

            if target.missing:
                for column in range(5):
                    item = table.item(row, column)
                    if item:
                        item.setForeground(QColor(theme.current().text_faint))
        table.blockSignals(False)
        self._update_clean_summary()

    def _checked_targets(self) -> list[cleaner.CleanTarget]:
        out = []
        for row in range(self.clean_table.rowCount()):
            item = self.clean_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                target = item.data(Qt.UserRole)
                if target and not target.missing:
                    out.append(target)
        return out

    def _update_clean_summary(self) -> None:
        selected = self._checked_targets()
        total = sum(t.size for t in selected)
        files = sum(t.file_count for t in selected)
        measured = sum(1 for t in self._clean_targets if t.measured and not t.missing)
        if not self._clean_loaded:
            return
        self.clean_summary.setText(
            f"已扫描 {measured} 项 · 勾选 {len(selected)} 项 · "
            f"预计释放 {winutil.human_size(total)}（{files:,} 个文件）"
        )

    def _select_safe(self) -> None:
        for row in range(self.clean_table.rowCount()):
            item = self.clean_table.item(row, 0)
            target = item.data(Qt.UserRole) if item else None
            if item and target and target.category != "userdata" and not target.missing:
                item.setCheckState(Qt.Checked)

    def _select_none(self) -> None:
        for row in range(self.clean_table.rowCount()):
            item = self.clean_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Unchecked)

    def _run_clean(self) -> None:
        selected = self._checked_targets()
        if not selected:
            self.ctx.notify(self, "请先勾选要清理的项目", "warn")
            return
        total = sum(t.size for t in selected)
        userdata = [t.label for t in selected if t.category == "userdata"]
        detail = ""
        if userdata:
            detail = "包含个人数据：" + "、".join(userdata) + "。回收站可还原，但仍请确认。"
        use_trash = bool(self.clean_trash_toggle.isChecked())
        if not use_trash:
            detail += "\n已关闭“移入回收站”，删除后无法通过本工具恢复。"

        if self.ctx.settings.get("confirm_delete", True):
            accepted, _ = widgets.confirm(
                self,
                "清理磁盘",
                f"将清理 {len(selected)} 项，预计释放 {winutil.human_size(total)}。",
                ok_text="开始清理",
                danger=True,
                detail=detail,
            )
            if not accepted:
                return

        self.clean_busy.start("正在清理…")

        def done(report) -> None:
            self.clean_busy.stop()
            if report.cancelled:
                self.ctx.notify(self, "清理已取消", "warn")
            elif report.failures:
                self.ctx.notify(
                    self,
                    f"释放 {report.freed_text}，{len(report.failures)} 个文件被占用未能删除",
                    "warn", 5000,
                )
            else:
                self.ctx.notify(
                    self, f"清理完成，释放 {report.freed_text}", "success", 4200
                )
            self._scan_targets()

        widgets.run_task(
            self,
            cleaner.clean_many,
            kwargs={
                "targets": selected,
                "use_trash": use_trash,
                "trash_root": appdirs.trash_dir(),
            },
            wants_progress=True,
            wants_cancel=True,
            on_done=done,
            on_error=lambda message: (self.clean_busy.stop(), self.ctx.notify(self, f"清理失败：{message}", "error")),
            on_progress=lambda done_count, total_count, text: self.clean_busy.set_progress(
                done_count, total_count, text or "清理中"
            ),
            label="clean_run",
        )

    # ============================================================== 3. logs ==
    def _build_log_tool(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = widgets.Card("运行日志", "启动器与游戏客户端的日志文件", icon_name="text")
        controls = QHBoxLayout()
        controls.setSpacing(9)
        self.log_source = QComboBox()
        for label, key in (
            ("启动器日志", gamelog.LogKind.LAUNCHER),
            ("更新器日志", gamelog.LogKind.STARTAPP),
            ("游戏运行日志 (.clog)", gamelog.LogKind.GAME),
        ):
            self.log_source.addItem(label, key)
        self.log_source.setFixedWidth(190)
        self.log_source.currentIndexChanged.connect(lambda _=0: self._refresh_log_files())
        controls.addWidget(self.log_source)

        self.log_filter = QComboBox()
        for label, value in (("全部", "ALL"), ("警告+", "WARN"), ("仅错误", "ERROR")):
            self.log_filter.addItem(label, value)
        self.log_filter.setFixedWidth(96)
        self.log_filter.currentIndexChanged.connect(lambda _=0: self._render_log())
        controls.addWidget(self.log_filter)

        self.log_follow = widgets.CheckBox("实时跟随最新日志")
        self.log_follow.setChecked(True)
        self.log_follow.toggled.connect(self._on_follow_toggled)
        controls.addWidget(self.log_follow)

        controls.addWidget(widgets.ghost_button("刷新", "refresh", self._refresh_log_files))
        controls.addWidget(
            widgets.subtle_button("打开日志目录", "folder", self._open_log_folder)
        )
        controls.addWidget(
            widgets.danger_button("删除旧日志", "trash", self._clean_logs)
        )
        controls.addStretch(1)
        card.add_layout(controls)

        self.log_note = widgets.make_label("", "Faint", wrap=True)
        card.add(self.log_note)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.log_table = widgets.selectable_table(
            ["文件", "大小", "时间"],
            stretch_column=0,
            row_height=27,
            resize_modes={1: QHeaderView.ResizeToContents, 2: QHeaderView.ResizeToContents},
        )
        self.log_table.setMinimumWidth(320)
        self.log_table.itemSelectionChanged.connect(self._on_log_selected)
        body.addWidget(self.log_table, 3)

        self.log_view = widgets.LogView(max_lines=4000)
        self.log_view.setPlaceholderText("选择左侧的日志文件以查看内容。")
        body.addWidget(self.log_view, 7)
        card.add_layout(body)
        layout.addWidget(card, 1)
        return page

    def _on_follow_toggled(self, checked: bool) -> None:
        if checked and self._log_selected is not None and self._log_selected.is_text:
            self._tail_timer.start()
        else:
            self._tail_timer.stop()

    def _refresh_log_files(self) -> None:
        install = self.ctx.install
        if install is None or not install.exists:
            self.log_table.setRowCount(0)
            self.log_note.setText("尚未设置游戏目录。")
            return
        kind = self.log_source.currentData() or gamelog.LogKind.LAUNCHER
        self._log_groups = gamelog.list_logs(install)
        files = self._log_groups.get(kind, [])

        self.log_table.setRowCount(0)
        for log_file in files[:400]:
            row = self.log_table.rowCount()
            self.log_table.insertRow(row)
            name = QTableWidgetItem(log_file.name)
            name.setData(Qt.UserRole, log_file)
            self.log_table.setItem(row, 0, name)
            size_item = QTableWidgetItem(winutil.human_size(log_file.size))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.log_table.setItem(row, 1, size_item)
            self.log_table.setItem(row, 2, QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", log_file.datetime)))

        is_clog = kind == gamelog.LogKind.GAME
        self.log_note.setText(
            gamelog.CLOG_NOTE
            if is_clog
            else (
                "这些是纯文本日志，可以实时跟随、按级别过滤与搜索。"
                "选择左侧任一文件即可查看；勾选“实时跟随”会在游戏运行时自动滚动到最新内容。"
            )
        )
        self.log_follow.setEnabled(not is_clog)
        if is_clog:
            self.log_follow.setChecked(False)
            self._tail_timer.stop()

        if files:
            # Prefer the newest readable file.
            target = 0
            if not is_clog:
                newest_text = gamelog.newest_text_log(install)
                if newest_text is not None:
                    for index, log_file in enumerate(files[:400]):
                        if log_file.path == newest_text.path:
                            target = index
                            break
            self.log_table.selectRow(target)
        else:
            self.log_view.clear()
            self._log_selected = None
            self.log_view.setPlaceholderText("此分类下没有日志文件。")

    def _on_log_selected(self) -> None:
        row = self.log_table.currentRow()
        if row < 0:
            return
        item = self.log_table.item(row, 0)
        if item is None:
            return
        log_file: gamelog.LogFile = item.data(Qt.UserRole)
        if log_file is None:
            return
        self._log_selected = log_file
        self._render_log(reset=True)
        if log_file.is_text and self.log_follow.isChecked():
            self._tailer = gamelog.TextTailer(log_file.path)
            self._tail_timer.start()
        else:
            self._tail_timer.stop()
            self._tailer = None

    def _render_log(self, reset: bool = True) -> None:
        log_file = self._log_selected
        if log_file is None:
            return
        if not log_file.is_text:
            self.log_view.load_text(gamelog.CLOG_NOTE, "WARN")
            return
        if reset:
            text = gamelog.read_tail(log_file.path, max_bytes=512 * 1024)
            self.log_view.clear()
            threshold = {"ALL": 0, "WARN": 2, "ERROR": 3}.get(self.log_filter.currentData() or "ALL", 0)
            ranks = {"D": 0, "I": 1, "W": 2, "E": 3}
            shown = 0
            for line in text.splitlines():
                match = gamelog.LEVEL_RE.search(line)
                level = "INFO"
                rank = 1
                if match:
                    code = match.group(1)
                    rank = ranks.get(code, 1)
                    level = {"D": "DEBUG", "I": "INFO", "W": "WARN", "E": "ERROR"}[code]
                if rank >= threshold:
                    self.log_view.append_line(line, level)
                    shown += 1
            if not shown:
                self.log_view.append_line("（没有符合当前过滤条件的行）", "DEBUG")
            self.log_view.append_line(
                f"—— 已加载 {os.path.basename(log_file.path)} 的最后 {winutil.human_size(min(log_file.size, 512 * 1024))} ——",
                "DEBUG",
            )

    def _poll_tail(self) -> None:
        if self._tailer is None or self._log_selected is None:
            return
        chunk = self._tailer.read_new()
        if not chunk:
            return
        threshold = {"ALL": 0, "WARN": 2, "ERROR": 3}.get(self.log_filter.currentData() or "ALL", 0)
        ranks = {"D": 0, "I": 1, "W": 2, "E": 3}
        for line in chunk.splitlines():
            match = gamelog.LEVEL_RE.search(line)
            rank = 1
            level = "INFO"
            if match:
                code = match.group(1)
                rank = ranks.get(code, 1)
                level = {"D": "DEBUG", "I": "INFO", "W": "WARN", "E": "ERROR"}[code]
            if rank >= threshold:
                self.log_view.append_line(line, level)

    def _open_log_folder(self) -> None:
        install = self.ctx.require_install(self, reason="打开日志目录")
        if install is None:
            return
        kind = self.log_source.currentData() or gamelog.LogKind.LAUNCHER
        folder = {
            gamelog.LogKind.LAUNCHER: install.launcher_logs,
            gamelog.LogKind.STARTAPP: install.startapp_logs,
            gamelog.LogKind.GAME: install.game_logs,
        }.get(kind, install.root)
        if not winutil.open_path(folder):
            self.ctx.notify(self, "无法打开日志目录", "error")

    def _clean_logs(self) -> None:
        install = self.ctx.require_install(self, reason="删除旧日志")
        if install is None:
            return
        keep_days = int(self.ctx.settings.get("log_keep_days", 14))
        targets = [
            t
            for t in cleaner.build_targets(install, log_keep_days=float(keep_days))
            if t.category == "logs"
        ]
        measured = cleaner.measure_all(targets)
        total = sum(t.size for t in measured)
        if total == 0:
            self.ctx.notify(self, f"没有超过 {keep_days} 天的日志需要删除", "info")
            return
        accepted, _ = widgets.confirm(
            self,
            "删除旧日志",
            f"将删除 {sum(t.file_count for t in measured):,} 个日志文件，约 {winutil.human_size(total)}。",
            ok_text="删除",
            danger=True,
            detail=f"只删除超过 {keep_days} 天的文件。天数可在“设置 → 数据与缓存”中调整。",
        )
        if not accepted:
            return
        report = cleaner.clean_many(
            measured,
            use_trash=bool(self.ctx.settings.get("use_trash", True)),
            trash_root=appdirs.trash_dir(),
        )
        self.ctx.notify(self, f"已释放 {report.freed_text}", "success")
        self._refresh_log_files()

    # ========================================================== 4. checks ==
    def _build_check_tool(self) -> QWidget:
        page = widgets.ScrollColumn()

        card = widgets.Card("完整性自检", "检查游戏运行所需的文件是否齐全", icon_name="shield")
        card.add(
            widgets.make_label(
                "本自检只能确认文件是否存在、是否为空、配置是否可解析，"
                "无法像官方启动器那样校验文件内容（那需要官方清单文件）。",
                "Faint", wrap=True,
            )
        )
        self.check_busy = widgets.BusyStrip()
        card.add(self.check_busy)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.check_tiles = {
            "ok": widgets.StatTile("通过", "—", icon_name="check-circle"),
            "warn": widgets.StatTile("警告", "—", icon_name="warning"),
            "error": widgets.StatTile("缺失", "—", icon_name="x-circle"),
        }
        for tile in self.check_tiles.values():
            tiles.addWidget(tile)
        card.add_layout(tiles)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(widgets.primary_button("开始自检", "shield", self._run_checks))
        buttons.addWidget(
            widgets.subtle_button(
                "打开游戏目录", "folder",
                lambda: self._open(install_root(self.ctx)),
            )
        )
        buttons.addStretch(1)
        card.add_layout(buttons)

        self.check_table = widgets.selectable_table(
            ["结果", "检查项", "详情", "建议"],
            stretch_column=2,
            row_height=29,
            sortable=False,
            resize_modes={0: QHeaderView.ResizeToContents},
        )
        self.check_table.setMinimumHeight(420)
        card.add(self.check_table)
        page.add(card)
        page.body.addStretch(1)
        return page

    @staticmethod
    def _open(path: str) -> None:
        if path:
            winutil.open_path(path)

    def _run_checks(self) -> None:
        install = self.ctx.require_install(self, reason="完整性自检")
        if install is None:
            return
        self.check_busy.start("正在检查游戏文件…")
        self.check_table.setRowCount(0)

        def done(results) -> None:
            self.check_busy.stop()
            self._check_loaded = True
            self._populate_checks(results)

        widgets.run_task(
            self,
            healthcheck.run_checks,
            kwargs={"install": install},
            wants_progress=True,
            wants_cancel=True,
            on_done=done,
            on_error=lambda message: (self.check_busy.stop(), self.ctx.notify(self, f"自检失败：{message}", "error")),
            on_progress=lambda done_count, total, text: self.check_busy.set_progress(
                done_count, total, text or "检查中"
            ),
            label="healthcheck",
        )

    def _populate_checks(self, results) -> None:
        summary = healthcheck.summarise(results)
        self.check_tiles["ok"].set_value(str(summary["ok"]))
        self.check_tiles["warn"].set_value(str(summary["warn"]))
        self.check_tiles["error"].set_value(str(summary["error"]))

        palette = theme.current()
        self.check_table.setRowCount(0)
        for result in results:
            row = self.check_table.rowCount()
            self.check_table.insertRow(row)
            badge = QTableWidgetItem(result.badge_text)
            badge.setTextAlignment(Qt.AlignCenter)
            badge.setForeground(QColor(getattr(palette, {
                "ok": "success", "info": "info", "warn": "warn", "error": "error",
            }.get(result.level, "text_muted"))))
            self.check_table.setItem(row, 0, badge)

            name = QTableWidgetItem(f"{result.category} · {result.name}")
            self.check_table.setItem(row, 1, name)

            detail = QTableWidgetItem(result.detail)
            if result.level in ("warn", "error"):
                detail.setForeground(QColor(getattr(palette, result.level if result.level != "error" else "error")))
            self.check_table.setItem(row, 2, detail)
            self.check_table.setItem(row, 3, QTableWidgetItem(result.hint or "—"))

        if not results:
            self.check_table.insertRow(0)
            self.check_table.setItem(0, 0, QTableWidgetItem("—"))

    # =========================================================== 5. trash ==
    def _build_trash_tool(self) -> QWidget:
        page = widgets.ScrollColumn()

        card = widgets.Card("回收站", "本工具删除的内容会先放到这里，可以随时还原", icon_name="trash")
        self.trash_summary = widgets.make_label("正在统计…", "Muted")
        card.add(self.trash_summary)
        card.add_action(widgets.icon_button("refresh", "刷新回收站", on_click=self._load_trash))
        card.add_action(
            widgets.icon_button("folder", "打开回收站目录", on_click=lambda: winutil.open_path(appdirs.trash_dir()))
        )

        self.trash_table = widgets.selectable_table(
            ["名称", "原始位置", "大小", "文件数", "删除时间"],
            stretch_column=1,
            row_height=28,
            resize_modes={
                2: QHeaderView.ResizeToContents,
                3: QHeaderView.ResizeToContents,
                4: QHeaderView.ResizeToContents,
            },
        )
        self.trash_table.setMinimumHeight(300)
        card.add(self.trash_table)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addWidget(widgets.primary_button("还原选中项", "undo", self._restore_trash))
        buttons.addWidget(widgets.subtle_button("还到指定文件夹…", "folder", self._restore_trash_to))
        buttons.addWidget(widgets.danger_button("永久删除选中项", "trash", self._delete_trash))
        buttons.addWidget(widgets.danger_button("清空回收站", "x-circle", self._empty_trash))
        buttons.addStretch(1)
        card.add_layout(buttons)

        card.add(
            widgets.make_label(
                "回收站位于 WTToolbox 的数据目录，不占用游戏目录空间。"
                "清空回收站会永久删除其中的内容。",
                "Faint", wrap=True,
            )
        )
        page.add(card)
        page.body.addStretch(1)
        return page

    def _load_trash(self) -> None:
        self._trash_entries = trash.list_entries()
        table = self.trash_table
        table.setRowCount(0)
        for entry in self._trash_entries:
            row = table.rowCount()
            table.insertRow(row)
            name = QTableWidgetItem(entry.name)
            name.setData(Qt.UserRole, entry)
            table.setItem(row, 0, name)
            origin = QTableWidgetItem(entry.origin_text)
            origin.setToolTip(entry.origin_text)
            table.setItem(row, 1, origin)
            size_item = QTableWidgetItem(entry.size_text)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 2, size_item)
            count_item = QTableWidgetItem(f"{entry.file_count:,}")
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 3, count_item)
            table.setItem(row, 4, QTableWidgetItem(entry.created_text))

        total, files, count = trash.totals()
        self.trash_summary.setText(
            f"{count} 个项目 · {files:,} 个文件 · {winutil.human_size(total)}"
            if count
            else "回收站是空的。"
        )

    def _selected_trash(self) -> list[trash.TrashEntry]:
        rows = sorted({index.row() for index in self.trash_table.selectedIndexes()})
        out = []
        for row in rows:
            item = self.trash_table.item(row, 0)
            entry = item.data(Qt.UserRole) if item else None
            if entry is not None:
                out.append(entry)
        return out

    def _restore_trash(self) -> None:
        entries = self._selected_trash()
        if not entries:
            self.ctx.notify(self, "请先选中要还原的项目", "warn")
            return
        restored = 0
        for entry in entries:
            ok, message = trash.restore_entry(entry)
            if ok:
                restored += 1
            else:
                self.ctx.notify(self, f"{entry.name}：{message}", "error", 5000)
        if restored:
            self.ctx.notify(self, f"已还原 {restored} 个项目到原始位置", "success")
        self._load_trash()

    def _restore_trash_to(self) -> None:
        entries = self._selected_trash()
        if not entries:
            self.ctx.notify(self, "请先选中要还原的项目", "warn")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择还原到的文件夹", appdirs.appdata_dir())
        if not folder:
            return
        restored = 0
        for entry in entries:
            ok, _message = trash.restore_entry(entry, folder)
            if ok:
                restored += 1
        if restored:
            self.ctx.notify(self, f"已还原 {restored} 个项目到 {folder}", "success")
        self._load_trash()

    def _delete_trash(self) -> None:
        entries = self._selected_trash()
        if not entries:
            self.ctx.notify(self, "请先选中要删除的项目", "warn")
            return
        accepted, _ = widgets.confirm(
            self,
            "永久删除",
            f"将永久删除 {len(entries)} 个项目，此操作不可撤销。",
            ok_text="永久删除",
            danger=True,
        )
        if not accepted:
            return
        for entry in entries:
            trash.delete_entry(entry)
        self.ctx.notify(self, "已永久删除选中项", "success")
        self._load_trash()

    def _empty_trash(self) -> None:
        total, files, count = trash.totals()
        if not count:
            self.ctx.notify(self, "回收站已经是空的", "info")
            return
        accepted, _ = widgets.confirm(
            self,
            "清空回收站",
            f"将永久删除 {count} 个项目（{files:,} 个文件，{winutil.human_size(total)}）。",
            ok_text="清空",
            danger=True,
            detail="此操作不可撤销。",
        )
        if not accepted:
            return
        freed, removed, errors = trash.empty()
        if errors:
            self.ctx.notify(self, f"清空时出现 {len(errors)} 个问题", "warn")
        else:
            self.ctx.notify(self, f"已清空 {removed} 个项目，释放 {winutil.human_size(freed)}", "success")
        self._load_trash()

    # ------------------------------------------------------------- helpers
    def _warning_banner(self, text: str) -> QFrame:
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
        row.addWidget(widgets.make_label(text, "Muted", wrap=True), 1)
        box.setVisible(False)
        return box

    def _on_sub_changed(self, index: int) -> None:
        # A tool must never take the window down with it: a missing or
        # unwritable data directory is reported, not raised.
        try:
            if index == 0:
                self._load_config_tool()
            elif index == 1 and not self._clean_loaded:
                self._scan_targets()
            elif index == 2:
                self._refresh_log_files()
            elif index == 3 and not self._check_loaded:
                self._run_checks()
            elif index == 4:
                self._load_trash()
        except OSError as exc:
            self.ctx.log.warn(f"工具箱子页面加载失败：{exc}", "工具箱")
            self.ctx.notify(self, f"该功能不可用：{exc}", "warn", 4200)
        except Exception as exc:  # noqa: BLE001
            self.ctx.log.error(f"工具箱子页面异常：{exc}", "工具箱")
            self.ctx.notify(self, f"该功能出现异常：{exc}", "error", 5000)

    # --------------------------------------------------------------- hooks
    def on_show(self) -> None:
        self._on_sub_changed(self.subnav.current_index())
        self._install_timer.start(200)

    def on_install_changed(self) -> None:
        self._clean_loaded = False
        self._check_loaded = False
        self._install_timer.start(300)

    def _on_install_settled(self) -> None:
        install = self.ctx.install
        running = bool(install and install.exists and install.is_running())
        self.config_warning.setVisible(running)
        self.on_show()


def install_root(ctx) -> str:
    install = ctx.install
    return install.root if install and install.exists else ""
