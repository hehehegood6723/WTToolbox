"""主页 - game path, launching, official news, live log and readiness state.

Layout mirrors the reference launcher: a hero panel plus path picker on the top
row, then a left column (official news + local status) and a right column
(activity log + launch controls).

Everything shown here is real: the news comes from ``warthunder.com``, the log is
WTToolbox's own activity log, and the readiness line reflects the *local* game
state.  There is deliberately no fake "connected to server" indicator.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import appdirs, gamelaunch, gamepath, news, winutil
from ...core.applog import LogRecord
from .. import icons, theme, widgets

__all__ = ["HomePage"]


class _ChannelBlock(QWidget):
    """One client (live or DEV-server): its own path, its own controls.

    The two channels are completely separate installations, so each block owns
    its own widgets and never writes into the other one's state.
    """

    SOURCE_LABELS = {
        "registry": "注册表",
        "netagent": "启动器记录",
        "steam": "Steam",
        "process": "运行进程",
        "scan": "磁盘扫描",
        "dev-hint": "常见位置",
        "remembered": "历史记录",
        "manual": "手动选择",
    }

    def __init__(self, page: "HomePage", channel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self.channel = channel
        self.is_dev = channel == gamepath.CHANNEL_DEV
        self.label = gamepath.CHANNEL_LABELS[channel]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ---- header: name + badges + "use this client" ---------------------
        header = QHBoxLayout()
        header.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        header.addWidget(self.icon)
        title = widgets.make_label(f"{self.label}客户端", "H3")
        header.addWidget(title)
        self.circuit_badge = widgets.Badge("—", "neutral")
        self.circuit_badge.setToolTip("config.blk 中 yunetwork.curCircuit 的值")
        header.addWidget(self.circuit_badge)
        self.state_badge = widgets.Badge("检测中", "neutral")
        header.addWidget(self.state_badge)
        header.addStretch(1)
        self.use_button = widgets.subtle_button(
            "设为当前操作对象", "check-circle", lambda: self.page._set_active_channel(self.channel),
            tooltip="音效模组 / 工具箱 / 信息库等页面将对这台客户端生效",
        )
        header.addWidget(self.use_button)
        outer.addLayout(header)

        # ---- path row ------------------------------------------------------
        self.path_row = QWidget()
        row = QHBoxLayout(self.path_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "例如 D:\\WarThunderDev" if self.is_dev else "例如 D:\\WarThunder"
        )
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._commit)
        self.path_edit.editingFinished.connect(self._commit)
        self.path_edit.setMinimumHeight(36)
        row.addWidget(self.path_edit, 1)
        self.browse_button = widgets.subtle_button(
            "浏览", "folder", self._browse, tooltip=f"手动指定{self.label}目录"
        )
        self.browse_button.setMinimumHeight(36)
        row.addWidget(self.browse_button)
        self.detect_button = widgets.primary_button(
            f"检测{self.label}", "search",
            self._detect,
            tooltip="注册表 / 启动器记录 / Steam / 运行进程 / 磁盘扫描",
        )
        self.detect_button.setMinimumHeight(36)
        row.addWidget(self.detect_button)
        outer.addWidget(self.path_row)

        # ---- chips ---------------------------------------------------------
        self.chips = QWidget()
        chips = QHBoxLayout(self.chips)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(7)
        self.chip_version = widgets.Badge("版本 —", "neutral")
        self.chip_arch = widgets.Badge("架构 —", "neutral")
        self.chip_launcher = widgets.Badge("启动器 —", "neutral")
        self.chip_source = widgets.Badge("来源 —", "neutral")
        for chip in (self.chip_version, self.chip_arch, self.chip_launcher, self.chip_source):
            chips.addWidget(chip)
        chips.addStretch(1)
        self.open_button = widgets.link_button(
            "打开目录", lambda: self.page._open_channel_folder(self.channel), icon_name="external"
        )
        chips.addWidget(self.open_button)
        outer.addWidget(self.chips)

        # ---- explanatory line (doubles as the "not installed" message) -----
        self.hint = widgets.make_label("", "Faint", wrap=True)
        outer.addWidget(self.hint)

        self.alt_combo = QComboBox()
        self.alt_combo.setVisible(False)
        self.alt_combo.currentIndexChanged.connect(self._alt_selected)
        outer.addWidget(self.alt_combo)

    # ------------------------------------------------------------------ state
    def install(self):
        return self.page.ctx.install_for(self.channel)

    def refresh(self) -> None:
        palette = theme.current()
        install = self.install()
        active = self.page.ctx.active_channel == self.channel

        self.icon.setPixmap(
            icons.icon_pixmap(
                "crosshair" if self.is_dev else "home",
                palette.accent if active else palette.text_faint,
                20,
                1.8,
            )
        )
        self.use_button.setVisible(install is not None and not active)
        self.use_button.setEnabled(install is not None)

        # The inactive channel is dimmed so "which client are the tools on?" is
        # answerable at a glance.
        self.setProperty("tkActive", active)
        theme.restyle(self)

        if install is None:
            self.path_edit.setText("")
            # The controls stay visible even for a missing client: the user has
            # to be able to point at a folder manually or run a detection.
            self.path_row.setVisible(True)
            self.chips.setVisible(False)
            self.alt_combo.setVisible(False)
            self.circuit_badge.setVisible(False)
            self.state_badge.set_kind("warn")
            if self.is_dev:
                self.state_badge.setText("未安装测试版")
                self.hint.setText(
                    "未检测到测试版（DEV 服务器）客户端。它是独立的另一套安装，与正式版互不影响："
                    "官方用 wt_dev_launcher.exe 装到另一个目录，或在复制的目录里放一个 "
                    'matchingdevmode 空文件、并把 config.blk 的 curCircuit 设为 "dev"。'
                )
                self.hint.setToolTip(
                    "测试版判别依据（官方 Wiki「DEV-server」）：\n"
                    "· config.blk 中 yunetwork { curCircuit:t=\"dev\" }\n"
                    "· 根目录存在空文件 matchingdevmode\n"
                    "· 根目录存在 wt_dev_launcher.exe\n"
                    "满足任意一条即认定为测试版客户端。"
                )
            else:
                self.state_badge.setText("未检测到正式版")
                self.hint.setText(
                    "尚未设置正式版游戏目录。点「检测正式版」自动扫描，或用「浏览」手动指定。"
                )
            self.page._sync_clients_card()
            return

        self.path_row.setVisible(True)
        self.chips.setVisible(True)
        self.circuit_badge.setVisible(True)
        self.path_edit.setText(install.root)
        validation = install.validate()
        if validation.ok:
            self.state_badge.setText("校验通过")
            self.state_badge.set_kind("success")
        else:
            self.state_badge.setText("校验异常")
            self.state_badge.set_kind("warn")

        circ = install.circuit or "—"
        self.circuit_badge.setText(f"curCircuit {circ}")
        self.circuit_badge.set_kind("info" if circ == "dev" else "neutral")

        evidence = install.dev_evidence
        if self.is_dev and evidence:
            self.hint.setText("测试版依据：" + "、".join(evidence))
        elif validation.ok:
            self.hint.setText(
                "已通过文件校验：config.blk 与游戏可执行文件均已找到。"
                + ("（当前操作对象）" if active else "")
            )
        else:
            self.hint.setText("存在缺失项：" + validation.summary)

        self.chip_version.setText(f"版本 {install.version() or '—'}")
        self.chip_arch.setText(f"架构 {install.arch}")
        self.chip_launcher.setText(f"启动器 {install.launcher_version() or '—'}")
        source = self.SOURCE_LABELS.get(install.source, install.source)
        self.chip_source.setText(f"来源 {source}")
        self.page._sync_clients_card()

    # ---------------------------------------------------------------- actions
    def _commit(self) -> None:
        text = self.path_edit.text().strip().strip('"')
        if not text:
            return
        current = self.install()
        if current is not None and os.path.normcase(text) == os.path.normcase(current.root):
            return
        self.page._set_install_for(self.channel, text, source="manual", quiet=True)

    def _browse(self) -> None:
        current = self.path_edit.text().strip() or "D:\\"
        chosen = QFileDialog.getExistingDirectory(
            self, f"选择{self.label}客户端目录", current
        )
        if chosen:
            self.page._set_install_for(self.channel, chosen, source="manual")

    def _detect(self) -> None:
        self.page._auto_detect(self.channel)

    def _alt_selected(self, index: int) -> None:
        if index < 0:
            return
        path = self.alt_combo.itemData(index)
        if path:
            self.page._set_install_for(self.channel, path, source="manual", quiet=True)


# --------------------------------------------------------------------------- #
#  Hero banner
# --------------------------------------------------------------------------- #
class BannerView(QWidget):
    """The branded hero panel with a live game-state chip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap(theme.banner_path())
        self._state_text = "正在初始化…"
        self._state_kind = "idle"
        self._caption = ""
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, text: str, kind: str, caption: str = "") -> None:
        self._state_text = text
        self._state_kind = kind
        self._caption = caption
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        painter.setClipPath(path)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            # Anchor the crop to the left: the artwork carries the wordmark
            # there, so centring a narrow card would cut the "TH" off.
            painter.drawPixmap(
                0,
                int((self.height() - scaled.height()) / 2),
                scaled,
            )
        else:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor("#1B2230"))
            gradient.setColorAt(1.0, QColor("#0C1017"))
            painter.fillRect(rect, gradient)

        # Legibility scrim at the bottom, then the state chip.
        scrim = QLinearGradient(0, self.height() * 0.45, 0, self.height())
        scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 165))
        painter.fillRect(rect, scrim)

        kind_colors = {
            "running": palette.success,
            "ready": palette.accent,
            "warn": palette.warn,
            "error": palette.error,
            "idle": palette.text_muted,
        }
        dot_colour = QColor(kind_colors.get(self._state_kind, palette.accent))

        chip = QRectF(14, self.height() - 42, min(self.width() - 28, 330), 28)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(12, 16, 23, 175))
        painter.drawRoundedRect(chip, 14, 14)
        painter.setBrush(dot_colour)
        painter.drawEllipse(QRectF(chip.left() + 12, chip.center().y() - 4, 8, 8))

        font = QFont(painter.font())
        font.setPointSizeF(9.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#F4F6FA"))
        painter.drawText(
            QRectF(chip.left() + 27, chip.top(), chip.width() - 36, chip.height()),
            int(Qt.AlignVCenter | Qt.AlignLeft),
            self._state_text,
        )

        if self._caption:
            font.setBold(False)
            font.setPointSizeF(8.2)
            painter.setFont(font)
            painter.setPen(QColor(0xF4, 0xF6, 0xFA, 190))
            painter.drawText(
                QRectF(16, 14, self.width() - 32, 20),
                int(Qt.AlignTop | Qt.AlignRight),
                self._caption,
            )
        painter.end()


# --------------------------------------------------------------------------- #
#  News row
# --------------------------------------------------------------------------- #
class NewsRow(QFrame):
    activated = Signal(str)

    _KIND_COLORS = {
        "accent": "accent",
        "success": "success",
        "warn": "warn",
        "error": "error",
        "info": "info",
        "neutral": "text_faint",
    }

    def __init__(self, item: news.NewsItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NewsRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.item = item

        palette = theme.current()
        kind = self._guess_kind(item)
        colour = getattr(palette, self._KIND_COLORS.get(kind, "text_faint"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(9)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {colour}; border-radius: 4px;")
        badge = widgets.Badge(item.tag or self._kind_label(kind), kind)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        self.title = widgets.ElidedLabel(item.title, "RowTitle")
        meta = widgets.ElidedLabel(self._meta_text(item), "RowMeta")
        meta.setToolTip(self._meta_text(item))
        texts.addWidget(self.title)
        texts.addWidget(meta)

        chevron = QLabel()
        chevron.setPixmap(icons.icon_pixmap("chev-right", palette.text_faint, 14, 2.0))
        chevron.setFixedWidth(16)

        layout.addWidget(dot, 0, Qt.AlignTop)
        layout.addWidget(badge, 0, Qt.AlignTop)
        layout.addLayout(texts, 1)
        layout.addWidget(chevron, 0, Qt.AlignVCenter)
        self.setToolTip(f"{item.title}\n{item.url}")

    @staticmethod
    def _guess_kind(item: news.NewsItem) -> str:
        text = f"{item.title} {item.comment}".lower()
        if any(word in text for word in ("discount", "sale", "shop", "折扣", "特惠", "礼包")):
            return "success"
        if any(word in text for word in ("fair play", "ban", "封禁", "公平")):
            return "error"
        if any(word in text for word in ("esports", "wtcs", "赛事", "联赛")):
            return "info"
        if any(word in text for word in ("update", "major update", "更新", "版本")):
            return "accent"
        return "neutral"

    @staticmethod
    def _kind_label(kind: str) -> str:
        return {
            "accent": "更新",
            "success": "福利",
            "warn": "公告",
            "error": "公告",
            "info": "赛事",
            "neutral": "动态",
        }.get(kind, "动态")

    @staticmethod
    def _meta_text(item: news.NewsItem) -> str:
        parts = []
        if item.date:
            parts.append(item.date)
        elif item.comment:
            parts.append(item.short_comment)
        if item.pinned:
            parts.append("置顶")
        return " · ".join(parts) if parts else "点击查看详情"

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.item.url)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt naming
        menu = QMenu(self)
        menu.addAction("在浏览器中打开", lambda: self.activated.emit(self.item.url))
        menu.addAction(
            "复制链接",
            lambda: (
                QApplication.clipboard().setText(self.item.url),
                widgets.notify(self, "链接已复制到剪贴板", "success", 1800),
            ),
        )
        menu.addSeparator()
        copy_title = menu.addAction("复制标题", lambda: QApplication.clipboard().setText(self.item.title))
        copy_title.setEnabled(bool(self.item.title))
        menu.exec(event.globalPos())


# --------------------------------------------------------------------------- #
#  Home page
# --------------------------------------------------------------------------- #
class HomePage(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._news_items: list[news.NewsItem] = []
        self._log_records_shown = 0
        self._news_task = None
        self._log_filter = "ALL"
        self._log_view: widgets.LogView | None = None
        self._play_started: float | None = None
        self._running = False

        self._build()

        # Subscribe directly so the panel works both inside MainWindow and when
        # the page is rendered on its own (tools/render_page.py).
        self.ctx.log.subscribe(self.append_log_record)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(14)

        outer.addLayout(self._build_top_row(), 0)
        outer.addLayout(self._build_bottom_rows(), 1)

        # Parented timers: this page is destroyed and rebuilt on every theme
        # switch, and a bare singleShot would fire into a deleted widget.
        widgets.later(self, 60, self._tick)
        widgets.later(self, 200, lambda: self._load_news(force=False))

    # --------------------------------------------------------------- top row
    def _build_top_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        hero = widgets.Card()
        hero.setMinimumWidth(380)
        hero.setMaximumWidth(470)
        self.banner = BannerView()
        hero.add(self.banner, 1)
        row.addWidget(hero, 4)

        row.addWidget(self._build_clients_card(), 7)
        return row

    def _build_clients_card(self) -> widgets.Card:
        """Two independent client blocks: the live client and the DEV client."""
        card = widgets.Card(
            "游戏客户端",
            "正式版与测试版分开检测，互不影响",
            icon_name="folder-open",
        )
        self.active_badge = widgets.Badge("当前操作：正式版", "info")
        card.add_action(self.active_badge)

        self.channel_blocks: dict[str, _ChannelBlock] = {}
        for index, channel in enumerate(gamepath.CHANNELS):
            if index:
                divider = QFrame()
                divider.setObjectName("Divider")
                divider.setFrameShape(QFrame.HLine)
                card.add(divider)
            block = _ChannelBlock(self, channel)
            self.channel_blocks[channel] = block
            card.add(block)
        card.add_stretch(1)
        return card

    # ------------------------------------------------------------ bottom rows
    def _build_bottom_rows(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._build_news_card(), 1)
        left.addWidget(self._build_status_card(), 0)

        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_log_card(), 1)
        right.addWidget(self._build_launch_card(), 0)

        holder_left = QWidget()
        holder_left.setLayout(left)
        holder_left.setFixedWidth(352)
        holder_right = QWidget()
        holder_right.setLayout(right)

        row.addWidget(holder_left, 0)
        row.addWidget(holder_right, 1)
        return row

    def _build_news_card(self) -> widgets.Card:
        card = widgets.Card("官方资讯", "", icon_name="activity")
        self.news_locale = QComboBox()
        for code, label in news.LOCALES:
            self.news_locale.addItem(label, code)
        index = self.news_locale.findData(self.ctx.settings.get("news_locale", "zh"))
        self.news_locale.setCurrentIndex(max(0, index))
        self.news_locale.setFixedWidth(96)
        self.news_locale.currentIndexChanged.connect(lambda _=0: self._load_news(force=True))
        card.add_action(self.news_locale)
        card.add_action(
            widgets.icon_button("refresh", "刷新资讯", on_click=lambda: self._load_news(force=True))
        )
        card.add_action(
            widgets.icon_button(
                "external", "在浏览器中打开官网", on_click=lambda: winutil.open_url("https://warthunder.com/zh/news/")
            )
        )

        self.news_scroll = widgets.ScrollColumn()
        self.news_scroll.body.setSpacing(3)
        self.news_scroll.setMinimumHeight(116)
        card.add(self.news_scroll, 1)

        self.news_footer = widgets.make_label("", "Faint")
        card.add(self.news_footer)
        return card

    def _build_status_card(self) -> widgets.Card:
        card = widgets.Card()
        palette = theme.current()

        row = QHBoxLayout()
        row.setSpacing(9)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {palette.accent}; border-radius: 4px;")
        text = widgets.make_label("本地模式 · 数据源：官方资讯站", "Muted")
        row.addWidget(dot)
        row.addWidget(text, 1)
        row.addWidget(
            widgets.link_button(
                "清理磁盘 →",
                lambda: self.ctx.requestPage.emit("tools"),
                tooltip="前往工具箱清理缓存与旧日志",
            )
        )
        card.add_layout(row)

        self.status_line = widgets.make_label("", "Faint", wrap=True)
        card.add(self.status_line)
        return card

    def _build_log_card(self) -> widgets.Card:
        card = widgets.Card("运行日志", "System Log", icon_name="text")
        self.log_filter = QComboBox()
        for label, value in (("全部", "ALL"), ("信息+", "INFO"), ("警告+", "WARN"), ("错误", "ERROR")):
            self.log_filter.addItem(label, value)
        self.log_filter.setFixedWidth(88)
        self.log_filter.currentIndexChanged.connect(self._apply_log_filter)
        card.add_action(self.log_filter)
        card.add_action(
            widgets.icon_button("folder", "打开日志所在目录", on_click=lambda: winutil.open_path(appdirs.logs_dir()))
        )
        card.add_action(
            widgets.icon_button("trash", "清空当前显示", on_click=self._clear_log_view)
        )

        self._log_view = widgets.LogView(max_lines=1200)
        self._log_view.setMinimumHeight(92)
        self._log_view.setPlaceholderText(
            "暂无日志。WTToolbox 的操作记录（路径检测、启动、清理、安装等）会实时显示在这里。"
        )
        card.add(self._log_view, 1)
        return card

    def _build_launch_card(self) -> widgets.Card:
        card = widgets.Card()
        palette = theme.current()

        row = QHBoxLayout()
        row.setSpacing(12)

        self.state_icon = QLabel()
        self.state_icon.setFixedSize(46, 46)
        self.state_icon.setAlignment(Qt.AlignCenter)
        self.state_icon.setStyleSheet(
            f"background: {palette.accent_soft}; border-radius: 23px;"
        )

        texts = QVBoxLayout()
        texts.setSpacing(2)
        caption = widgets.make_label("当前准备状态", "Faint")
        self.state_text = widgets.make_label("正在检测…", "CardTitle")
        self.state_detail = widgets.make_label("", "Muted")
        texts.addWidget(caption)
        texts.addWidget(self.state_text)
        texts.addWidget(self.state_detail)

        self.launch_button = widgets.primary_button("开始游戏", "play", self.start_game)
        self.launch_button.setMinimumHeight(42)
        self.launch_button.setMinimumWidth(150)

        self.launch_menu_button = QToolButton()
        self.launch_menu_button.setObjectName("IconBtn")
        self.launch_menu_button.setCursor(Qt.PointingHandCursor)
        self.launch_menu_button.setPopupMode(QToolButton.InstantPopup)
        self.launch_menu_button.setIcon(icons.icon("chev-down", palette.text_muted, 16, 2.0))
        self.launch_menu_button.setIconSize(QSize(16, 16))
        self.launch_menu_button.setFixedSize(30, 42)
        self.launch_menu_button.setToolTip("更多启动方式")
        menu = QMenu(self)
        menu.addAction("通过官方启动器启动（推荐）", self.start_game)
        menu.addAction("直接启动游戏客户端（跳过启动器）", self.start_direct)
        menu.addAction("以管理员身份启动启动器", self.start_elevated)
        menu.addSeparator()
        menu.addAction("关闭官方启动器进程", self._quit_launcher)
        menu.addAction("结束游戏进程", self._kill_game)
        menu.addSeparator()
        menu.addAction("打开图形配置编辑器", self._open_config_editor)
        self.launch_menu_button.setMenu(menu)

        gear = widgets.icon_button("gear", "打开工具箱", on_click=lambda: self.ctx.requestPage.emit("tools"))

        row.addWidget(self.state_icon, 0, Qt.AlignVCenter)
        row.addLayout(texts, 1)
        row.addWidget(gear, 0, Qt.AlignVCenter)
        launch_group = QHBoxLayout()
        launch_group.setSpacing(3)
        launch_group.addWidget(self.launch_button)
        launch_group.addWidget(self.launch_menu_button)
        row.addLayout(launch_group)
        card.add_layout(row)
        return card

    # ------------------------------------------------------------------- hooks
    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not getattr(self, "_shown", False):
            self._shown = True
            widgets.later(self, 0, self.on_show)
    def shutdown(self) -> None:
        """Release subscriptions so a rebuild does not stack duplicates."""
        try:
            self.ctx.log.unsubscribe(self.append_log_record)
        except Exception:
            pass
        try:
            self._clock.stop()
        except Exception:
            pass
        if self._news_task is not None:
            self._news_task.cancel()
            self._news_task = None

    def on_show(self) -> None:
        self._refresh_path_ui()
        self._flush_log()
        self._tick()
        if not self._news_items:
            self._load_news(force=False)

    def on_install_changed(self) -> None:
        self._refresh_path_ui()
        self._tick()

    def append_log_record(self, record: LogRecord) -> None:
        if self._log_view is None:
            return
        if not self._level_allowed(record.level):
            return
        self._log_view.append_line(record.format_line(), record.level)
        self._log_records_shown += 1

    # ------------------------------------------------------------------- state
    def _tick(self) -> None:
        install = self.ctx.install
        running = False
        if install is not None and install.exists:
            try:
                running = install.is_running()
            except Exception:
                running = False
        if running and self._play_started is None:
            self._play_started = time.time()
        elif not running:
            self._play_started = None
        self._running = running
        self._update_state()

    def _update_state(self) -> None:
        palette = theme.current()
        install = self.ctx.install

        if install is None or not install.exists:
            text, detail, kind, icon_name, colour = (
                "未设置游戏目录",
                "请在右侧选择或自动搜索游戏安装位置",
                "error",
                "warning",
                palette.error,
            )
        elif self._running:
            elapsed = time.time() - self._play_started if self._play_started else 0
            text = "游戏正在运行"
            detail = f"已运行 {winutil.human_duration(elapsed)} · 改动 config.blk 前请先退出游戏"
            kind, icon_name, colour = "warn", "activity", palette.success
        else:
            validation = install.validate()
            if validation.ok:
                text = "准备就绪 · 可以开始游戏"
                detail = "启动器会自动检查更新；也可从右侧菜单直接启动客户端"
                kind, icon_name, colour = "ready", "check-circle", palette.accent
            else:
                text = "游戏文件不完整"
                detail = validation.summary
                kind, icon_name, colour = "warn", "warning", palette.warn

        self.state_text.setText(text)
        self.state_detail.setText(detail)
        self.state_icon.setPixmap(icons.icon_pixmap(icon_name, colour, 24, 1.8))
        self.state_icon.setStyleSheet(
            f"background: {palette.accent_soft if kind != 'error' else palette.error_soft};"
            f" border-radius: 23px;"
        )
        self.banner.set_state(
            ("运行中 " + winutil.human_duration(time.time() - self._play_started))
            if self._running and self._play_started
            else text,
            kind,
            caption=f"v{self.ctx.version}",
        )
        self.launch_button.setEnabled(install is not None and install.exists)
        self.launch_button.setText("游戏运行中" if self._running else "开始游戏")

        # Cheap, factual local status line.
        if install is not None and install.exists:
            replays = self._count_files(install.replays, ".wrpl")
            shots = self._count_files(install.screenshots, None)
            stats = self.ctx.settings.get("stats") or {}
            parts = [
                f"版本 {install.version() or '—'}",
                f"{install.arch}",
                f"{replays} 回放 · {shots} 截图",
                f"日志 {winutil.human_size(self._log_bytes(install))}",
            ]
            if stats.get("play_seconds"):
                parts.append(f"记录时长 {winutil.human_duration(stats['play_seconds'])}")
            self.status_line.setText(" · ".join(parts))
        else:
            self.status_line.setText("尚未选择游戏目录 · 请在上方设置后再使用其他功能")

    @staticmethod
    def _count_files(folder: str, suffix: str | None) -> int:
        if not folder or not os.path.isdir(folder):
            return 0
        try:
            return sum(
                1
                for entry in os.scandir(folder)
                if entry.is_file(follow_symlinks=False)
                and (suffix is None or entry.name.lower().endswith(suffix))
            )
        except OSError:
            return 0

    @staticmethod
    def _log_bytes(install) -> int:
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
        return total

    # ------------------------------------------------------------- path picker
    def _refresh_path_ui(self) -> None:
        """Refresh both client blocks."""
        for block in getattr(self, "channel_blocks", {}).values():
            block.refresh()

    def _sync_clients_card(self) -> None:
        """Keep the card's summary badge in step with the active channel."""
        if not hasattr(self, "active_badge"):
            return
        channel = self.ctx.active_channel
        label = gamepath.CHANNEL_LABELS.get(channel, channel)
        install = self.ctx.install
        self.active_badge.setText(f"当前操作：{label}")
        self.active_badge.set_kind("info" if install is not None else "warn")

    def _set_active_channel(self, channel: str) -> None:
        self.ctx.set_active_channel(channel)
        self._sync_clients_card()
        self._refresh_path_ui()
        label = gamepath.CHANNEL_LABELS.get(channel, channel)
        install = self.ctx.install_for(channel)
        if install is None:
            self.ctx.notify(self, f"{label}客户端尚未设置目录", "warn")
        else:
            self.ctx.notify(self, f"当前操作对象已切换到{label}", "success")
            self.ctx.log.info(f"当前操作对象切换为{label}：{install.root}", "路径")

    def _set_install_for(
        self, channel: str, path: str, *, source: str, quiet: bool = False
    ) -> None:
        label = gamepath.CHANNEL_LABELS.get(channel, channel)
        install = gamepath.GameInstall(root=os.path.normpath(path), source=source)
        if not install.exists:
            if not quiet:
                self.ctx.notify(self, f"目录不存在：{path}", "error")
            self.ctx.log.warn(f"目录不存在：{path}", "路径")
            self._refresh_path_ui()
            return

        detected = gamepath.channel_of(install.root)
        install.channel = detected
        if detected == gamepath.CHANNEL_DEV and channel != gamepath.CHANNEL_DEV:
            # A dev folder must never be stored as the live client.
            self.ctx.log.warn(f"这是测试版客户端目录，已改为记录到测试版：{install.root}", "路径")
            self._set_install_for(gamepath.CHANNEL_DEV, path, source=source, quiet=quiet)
            if not quiet:
                self.ctx.notify(self, "检测到测试版客户端，已记录到「测试版」一栏", "info", 4000)
            return
        if detected == gamepath.CHANNEL_STABLE and channel == gamepath.CHANNEL_DEV:
            self.ctx.log.warn(f"这是正式版客户端目录，已改为记录到正式版：{install.root}", "路径")
            self._set_install_for(gamepath.CHANNEL_STABLE, path, source=source, quiet=quiet)
            if not quiet:
                self.ctx.notify(self, "检测到正式版客户端，已记录到「正式版」一栏", "info", 4000)
            return

        validation = install.validate()
        self.ctx.set_install(install, channel=channel)
        # Refresh directly as well: the context signal only reaches us when the
        # page lives in a MainWindow, and the blocks must always reflect reality.
        self._refresh_path_ui()
        if validation.ok:
            self.ctx.log.ok(f"已设置{label}目录：{install.root}（{source}）", "路径")
            if not quiet:
                self.ctx.notify(
                    self, f"已设置{label}目录 · 版本 {install.version() or '未知'}", "success"
                )
        else:
            self.ctx.log.warn(f"{label}目录校验未通过：{validation.summary}", "路径")
            if not quiet:
                self.ctx.notify(self, f"目录已设置，但{validation.summary}", "warn")

    def _open_channel_folder(self, channel: str) -> None:
        install = self.ctx.install_for(channel)
        if install is None or not install.exists:
            label = gamepath.CHANNEL_LABELS.get(channel, channel)
            self.ctx.notify(self, f"{label}客户端尚未设置目录", "warn")
            return
        if not winutil.open_path(install.root):
            self.ctx.notify(self, "无法打开游戏目录", "error")

    def _auto_detect(self, channel: str = gamepath.CHANNEL_STABLE) -> None:
        block = self.channel_blocks.get(channel)
        label = gamepath.CHANNEL_LABELS.get(channel, channel)
        if block is None:
            return
        block.detect_button.setEnabled(False)
        block.state_badge.setText("搜索中…")
        block.state_badge.set_kind("info")
        remembered = self.ctx.settings.known_game_paths(channel)
        self.ctx.log.info(f"开始自动搜索{label}客户端目录", "路径")

        def done(candidates) -> None:
            block.detect_button.setEnabled(True)
            if not candidates:
                block.state_badge.set_kind("warn")
                if channel == gamepath.CHANNEL_DEV:
                    block.state_badge.setText("未安装测试版")
                    self.ctx.log.info("未检测到测试版（DEV 服务器）客户端", "路径")
                    self.ctx.notify(
                        self,
                        "未安装测试版：未找到 DEV 服务器客户端",
                        "warn",
                        4500,
                    )
                else:
                    block.state_badge.setText("未找到正式版")
                    self.ctx.log.warn("自动搜索未找到正式版客户端", "路径")
                    self.ctx.notify(self, "未找到正式版游戏目录，请使用「浏览」手动选择", "warn", 4000)
                self._refresh_path_ui()
                return

            valid = [c for c in candidates if c.validate().ok]
            pool = valid or candidates
            self.ctx.known_installs = candidates
            if len(pool) > 1:
                block.alt_combo.blockSignals(True)
                block.alt_combo.clear()
                for candidate in pool:
                    block.alt_combo.addItem(
                        f"{candidate.root}   [{candidate.source}]", candidate.root
                    )
                block.alt_combo.setVisible(True)
                block.alt_combo.blockSignals(False)
                self.ctx.log.info(f"{label}：找到 {len(pool)} 个候选目录", "路径")
            best = pool[0]
            self._set_install_for(channel, best.root, source=best.source)

        widgets.run_task(
            self,
            gamepath.detect_all,
            kwargs={
                "channel": channel,
                "include_scan": True,
                "extra_candidates": remembered,
            },
            wants_progress=True,
            wants_cancel=True,
            on_done=done,
            on_error=lambda message, b=block: (
                b.detect_button.setEnabled(True),
                b.state_badge.setText("搜索失败"),
                b.state_badge.set_kind("error"),
                self.ctx.notify(self, f"搜索失败：{message}", "error"),
            ),
            on_progress=lambda done_count, total, text, b=block: b.state_badge.setText(
                f"已扫描 {text}" if text else f"搜索中 {done_count}"
            ),
            label=f"detect_game_{channel}",
        )

    def _open_folder(self) -> None:
        self._open_channel_folder(self.ctx.active_channel)

    # -------------------------------------------------------------------- news
    def _load_news(self, *, force: bool) -> None:
        # on_show fires from several places during startup; without this guard a
        # single window open produced four concurrent identical fetches.
        if self._news_task is not None and not force:
            return
        locale_choice = self.news_locale.currentData() or "zh"
        self.ctx.settings.set("news_locale", locale_choice, save=True)
        if self._news_task is not None:
            self._news_task.cancel()
        self.news_footer.setText("正在获取官方资讯…")

        def done(result) -> None:
            self._news_task = None
            self._populate_news(result)

        def failed(message: str) -> None:
            self._news_task = None
            self.news_footer.setText(f"获取失败：{message}")

        self._news_task = widgets.run_task(
            self,
            news.fetch_news,
            kwargs={
                "locale": locale_choice,
                "force": force,
                "max_age_seconds": max(60, int(self.ctx.settings.get("news_cache_minutes", 30)) * 60),
            },
            on_done=done,
            on_error=failed,
            label="news",
        )

    def _populate_news(self, result: news.NewsResult) -> None:
        body = self.news_scroll.body
        while body.count():
            item = body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self._news_items = list(result.items)
        if not result.items:
            self.news_scroll.add(
                widgets.EmptyState(
                    "暂无资讯",
                    result.error or "检查网络后点击右上角刷新",
                    icon_name="activity",
                )
            )
            self.news_footer.setText(result.error or "暂无内容")
            return

        for item in result.items:
            row = NewsRow(item)
            row.activated.connect(self._open_news)
            self.news_scroll.add(row)
        self.news_scroll.body.addStretch(1)

        footer = result.age_text or ""
        if result.from_cache:
            footer += " · 本地缓存"
        if result.error:
            footer += f" · {result.error}"
        self.news_footer.setText(footer)

    def _open_news(self, url: str) -> None:
        if not url:
            self.ctx.notify(self, "这条资讯没有可打开的链接", "warn")
            return
        ok, detail = widgets.open_external(url)
        if ok:
            self.ctx.log.info(f"已在浏览器中打开资讯：{url}", "资讯")
            self.ctx.notify(self, "已在浏览器中打开", "success", 2000)
            return
        self.ctx.log.warn(f"无法打开链接（{detail}）：{url}", "资讯")
        # Never leave the user stuck: offer the link itself.
        accepted, _ = widgets.confirm(
            self,
            "无法自动打开浏览器",
            "系统拒绝了打开链接的请求。你可以复制下面的地址，手动粘贴到浏览器中。",
            ok_text="复制链接",
            cancel_text="关闭",
            detail=f"{url}\n\n底层错误：{detail}",
            icon_name="warning",
        )
        if accepted:
            QApplication.clipboard().setText(url)
            self.ctx.notify(self, "链接已复制到剪贴板", "success")

    # --------------------------------------------------------------------- log
    def _level_allowed(self, level: str) -> bool:
        order = {"DEBUG": 0, "INFO": 1, "OK": 1, "WARN": 2, "ERROR": 3}
        thresholds = {"ALL": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
        return order.get(level.upper(), 1) >= thresholds.get(self._log_filter, 0)

    def _flush_log(self) -> None:
        if self._log_view is None:
            return
        self._log_view.clear()
        self._log_records_shown = 0
        for record in self.ctx.log.records(min_level="DEBUG", limit=600):
            if self._level_allowed(record.level):
                self._log_view.append_line(record.format_line(), record.level)
                self._log_records_shown += 1

    def _apply_log_filter(self) -> None:
        self._log_filter = self.log_filter.currentData() or "ALL"
        self._flush_log()

    def _clear_log_view(self) -> None:
        if self._log_view is not None:
            self._log_view.clear()

    # ------------------------------------------------------------------ launch
    def start_game(self) -> None:
        """Launch via the official launcher (also used by the tray menu)."""
        install = self.ctx.require_install(self, reason="启动游戏")
        if install is None:
            return
        if self._running:
            self.ctx.notify(self, "游戏已经在运行了", "warn")
            return
        result = gamelaunch.launch_launcher(install)
        self._after_launch(result, "官方启动器")

    def start_direct(self) -> None:
        install = self.ctx.require_install(self, reason="启动游戏")
        if install is None:
            return
        if self._running:
            self.ctx.notify(self, "游戏已经在运行了", "warn")
            return
        result = gamelaunch.launch_game(install)
        self._after_launch(result, "游戏客户端（跳过更新检查）")

    def start_elevated(self) -> None:
        install = self.ctx.require_install(self, reason="启动游戏")
        if install is None:
            return
        if winutil.is_admin():
            self.ctx.notify(self, "当前已经是管理员权限，直接启动即可", "info")
            self.start_game()
            return
        result = gamelaunch.launch_launcher(install, elevated=True)
        self._after_launch(result, "官方启动器（管理员）")

    def _after_launch(self, result: gamelaunch.LaunchResult, what: str) -> None:
        if result.ok:
            self.ctx.settings.bump_stat("launches")
            self.ctx.settings.set("stats", {**(self.ctx.settings.get("stats") or {}),
                                           "last_launch": time.time()})
            self.ctx.log.ok(f"已启动 {what} · {result.command_line}", "启动")
            self.ctx.notify(self, f"正在启动 {what}…", "success")
            self._play_started = time.time()
            widgets.later(self, 2500, self._tick)
        else:
            self.ctx.notify(self, f"启动失败：{result.error}", "error", 5000)

    def _quit_launcher(self) -> None:
        install = self.ctx.require_install(self, reason="关闭启动器")
        if install is None:
            return
        selected, _ = widgets.confirm(
            self, "关闭官方启动器", "确定要结束正在运行的 launcher.exe 进程吗？",
            ok_text="结束进程", danger=True,
        )
        if not selected:
            return
        ok, message = gamelaunch.quit_launcher(install)
        self.ctx.notify(self, message, "success" if ok else "warn")

    def _kill_game(self) -> None:
        install = self.ctx.require_install(self, reason="结束游戏进程")
        if install is None:
            return
        procs = install.running_processes()
        if not procs:
            self.ctx.notify(self, "没有检测到正在运行的游戏进程", "info")
            return
        selected, _ = widgets.confirm(
            self,
            "结束游戏进程",
            f"将强制结束 {len(procs)} 个游戏进程。未保存的战斗进度会丢失。",
            ok_text="强制结束",
            danger=True,
            detail="仅在游戏卡死无响应时使用。",
        )
        if not selected:
            return
        ok, message = gamelaunch.terminate_game(install, force=True)
        self.ctx.notify(self, message, "success" if ok else "error")
        widgets.later(self, 1200, self._tick)

    def _open_config_editor(self) -> None:
        install = self.ctx.require_install(self, reason="编辑图形配置")
        if install is None:
            return
        try:
            from ..dialogs.config_editor import ConfigEditorDialog

            dialog = ConfigEditorDialog(self.ctx, install, self)
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(self, f"配置编辑器打开失败：{exc}", "error")
        self._refresh_path_ui()
