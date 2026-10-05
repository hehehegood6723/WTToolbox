"""The main application window.

Frameless, with:

* a custom :class:`~wttoolbox.ui.titlebar.TitleBar`
* lazily loaded pages in a ``QStackedWidget``
* eight invisible edge grips that drive ``startSystemResize`` so the frameless
  window still resizes like a native one
* a system tray icon with a working menu
* persisted geometry, live game-process monitoring and a status strip
"""

from __future__ import annotations

import os
import time
import traceback

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..core import appdirs, gamelaunch, gamepath, winutil
from ..core.applog import LogRecord
from . import icons, theme, widgets
from .context import PAGE_ICONS, PAGE_LABELS, PAGE_ORDER, AppContext
from .titlebar import TitleBar

__all__ = ["MainWindow"]

GRIP = 5
MIN_WIDTH = 1040
MIN_HEIGHT = 780   # home page needs 687 px of content; title bar + status + shadow add ~110


class _Grip(QWidget):
    """An invisible strip over a window edge that starts a native resize."""

    def __init__(self, parent: "MainWindow", edges: Qt.Edges, cursor: Qt.CursorShape) -> None:
        super().__init__(parent)
        self._edges = edges
        self._cursor = cursor
        self.setCursor(cursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    @property
    def edges(self) -> Qt.Edges:
        return self._edges

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)
                event.accept()
                return
        super().mousePressEvent(event)


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("MainWindow")
        self.setWindowTitle(f"{ctx.name} · {ctx.name_zh}")
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        icon_file = appdirs.resource_path("assets", "icon.ico")
        if os.path.isfile(icon_file):
            self.setWindowIcon(QIcon(icon_file))
        self.setMinimumSize(
            MIN_WIDTH + 2 * theme.WINDOW_SHADOW, MIN_HEIGHT + 2 * theme.WINDOW_SHADOW
        )

        self._pages: dict[str, QWidget] = {}
        self._built: set[str] = set()
        self._current_key = "home"
        self._page_errors: dict[str, str] = {}
        self._play_started: float | None = None
        self._was_running = False
        self._grips: list[_Grip] = []
        self._grip_specs: list = []
        self._force_quit = False

        self._build()
        self._create_grips()
        self._restore_geometry()
        self._setup_tray()
        self._setup_shortcuts()
        self._connect_context()

        self._monitor = QTimer(self)
        self._monitor.setInterval(4000)
        self._monitor.timeout.connect(self._poll_game)
        self._monitor.start()
        widgets.later(self, 120, self._poll_game)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        margin = self._corner_margin()
        outer.setContentsMargins(margin, margin, margin, margin)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self.ctx, self)
        outer.addWidget(self.titlebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        outer.addWidget(self.stack, 1)

        self.status = self._build_status()
        outer.addWidget(self.status)

        self._build_pages()

        self.titlebar.set_current_page("home")
        self._apply_page("home")
        self._apply_corner_style()

    def _load_page(self, key: str) -> QWidget:
        """Import and build a page, degrading to an error card on failure.

        The imports are written out literally rather than through
        ``__import__`` with a computed name, because PyInstaller's static
        analysis cannot see the latter and would leave every page out of the
        frozen bundle.
        """
        try:
            if key == "home":
                from .pages.home import HomePage as page_class
            elif key == "sound":
                from .pages.sound import SoundPage as page_class
            elif key == "tools":
                from .pages.tools import ToolsPage as page_class
            elif key == "library":
                from .pages.library import LibraryPage as page_class
            elif key == "vehicles":
                from .pages.vehicles import VehiclesPage as page_class
            elif key == "stats":
                from .pages.stats import StatsPage as page_class
            elif key == "settings":
                from .pages.settings import SettingsPage as page_class
            else:  # pragma: no cover - PAGE_ORDER is fixed
                raise KeyError(f"unknown page {key!r}")
            return page_class(self.ctx)
        except Exception:  # noqa: BLE001 - one bad page must not kill the app
            detail = traceback.format_exc()
            self.ctx.log.error(f"页面 {PAGE_LABELS.get(key, key)} 加载失败", "界面")
            print(detail)
            self._page_errors[key] = detail
            return self._error_page(key, detail)

    def _error_page(self, key: str, detail: str) -> QWidget:
        holder = widgets.ScrollColumn()
        holder.body.addStretch(1)
        card = widgets.Card(
            f"{PAGE_LABELS.get(key, key)} 加载失败",
            "该页面初始化时抛出异常，其余功能不受影响。",
            icon_name="warning",
        )
        view = widgets.LogView()
        view.setMinimumHeight(240)
        view.load_text(detail, "ERROR")
        card.add(view)
        holder.add(card)
        holder.body.addStretch(1)
        return holder

    def _build_status(self) -> QFrame:
        palette = theme.current()
        strip = QFrame()
        strip.setObjectName("StatusStrip")
        strip.setFixedHeight(30)
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(9)

        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.dot.setStyleSheet(
            f"background: {palette.text_faint}; border-radius: 4px;"
        )
        self.status_left = widgets.make_label("正在检测游戏目录…", "Faint")
        self.status_mid = widgets.make_label("", "Faint")
        self.status_right = widgets.make_label(
            f"{self.ctx.name} v{self.ctx.version} · 本地模式", "Faint"
        )

        layout.addWidget(self.dot)
        layout.addWidget(self.status_left)
        layout.addWidget(widgets.vline())
        layout.addWidget(self.status_mid)
        layout.addStretch(1)
        layout.addWidget(self.status_right)
        return strip

    def _create_grips(self) -> None:
        edge = Qt.Edge
        self._grip_specs = [
            (edge.LeftEdge, Qt.SizeHorCursor, "left"),
            (edge.RightEdge, Qt.SizeHorCursor, "right"),
            (edge.TopEdge, Qt.SizeVerCursor, "top"),
            (edge.BottomEdge, Qt.SizeVerCursor, "bottom"),
            (edge.TopEdge | edge.LeftEdge, Qt.SizeFDiagCursor, "tl"),
            (edge.BottomEdge | edge.RightEdge, Qt.SizeFDiagCursor, "br"),
            (edge.TopEdge | edge.RightEdge, Qt.SizeBDiagCursor, "tr"),
            (edge.BottomEdge | edge.LeftEdge, Qt.SizeBDiagCursor, "bl"),
        ]
        for edges, cursor, _tag in self._grip_specs:
            self._grips.append(_Grip(self, edges, cursor))

    def _layout_grips(self) -> None:
        if self.isMaximized() or self.isFullScreen():
            for grip in self._grips:
                grip.hide()
            return
        width, height = self.width(), self.height()
        # Keep the grips on the opaque body, never in the transparent shadow
        # margin: Windows hit-tests layered windows per pixel, so fully
        # transparent areas would not receive the click at all.
        inset = self._corner_margin()
        # Corner grips are nudged inwards: the pixels outside the corner arc are
        # transparent, and Windows passes clicks through those.
        corner = inset + 3
        span = GRIP + 3
        edge = Qt.Edge
        geometry = {
            "left": (inset, inset + GRIP, GRIP, height - 2 * (inset + GRIP)),
            "right": (width - inset - GRIP, inset + GRIP, GRIP, height - 2 * (inset + GRIP)),
            "top": (inset + GRIP, inset, width - 2 * (inset + GRIP), GRIP),
            "bottom": (inset + GRIP, height - inset - GRIP, width - 2 * (inset + GRIP), GRIP),
            "tl": (corner, corner, span, span),
            "tr": (width - corner - span, corner, span, span),
            "bl": (corner, height - corner - span, span, span),
            "br": (width - corner - span, height - corner - span, span, span),
        }
        for grip, (_edges, _cursor, tag) in zip(self._grips, self._grip_specs):
            rect = geometry.get(tag)
            if rect is None:
                continue
            grip.setGeometry(*rect)
            grip.show()
            grip.raise_()

    # ---------------------------------------------------------------- context
    def _connect_context(self) -> None:
        self.titlebar.pageRequested.connect(self._apply_page)
        self.titlebar.themeToggled.connect(self._toggle_theme)
        self.titlebar.minimizeRequested.connect(self.showMinimized)
        self.titlebar.maximizeRequested.connect(self._toggle_maximized)
        self.titlebar.closeRequested.connect(self.close)
        self.titlebar.openGameFolderRequested.connect(self._open_game_folder)
        self.titlebar.helpRequested.connect(self._show_help)

        self.ctx.installChanged.connect(self._on_install_changed)
        self.ctx.channelChanged.connect(self._on_channel_changed)
        self.ctx.requestPage.connect(self._apply_page)
        self.ctx.themeChanged.connect(self._on_theme_changed)

    def _on_channel_changed(self, _channel: str) -> None:
        """The tools now point at the other client: rebuild the pages.

        Every page caches the install it was built with, and the two channels
        are different installations, so they must be recreated rather than
        nudged.  The window shell (title bar, stack, status strip) stays put,
        exactly like a theme switch; ``_teardown_pages`` leaves one empty slot
        per page, so the pages are simply rebuilt on demand.
        """
        key = self.current_page_key
        self._teardown_pages()
        self._apply_page(key)
        self._update_status()

    def _setup_shortcuts(self) -> None:
        for index, key in enumerate(PAGE_ORDER, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=key: self._apply_page(k))
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self._quit)
        QShortcut(QKeySequence("F11"), self).activated.connect(self._toggle_maximized)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._on_escape)

    def _on_escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    # ------------------------------------------------------------------- pages
    @property
    def current_page_key(self) -> str:
        return self._current_key if self._current_key in PAGE_ORDER else "home"

    def _slot_index(self, key: str) -> int:
        return PAGE_ORDER.index(key)

    def _ensure_page(self, key: str) -> QWidget:
        """Build *key*'s page on first use, replacing its placeholder slot."""
        if key in self._built:
            return self._pages[key]
        index = self._slot_index(key)
        holder = self.stack.widget(index)
        page = self._load_page(key)
        self.stack.insertWidget(index, page)
        if holder is not None:
            self.stack.removeWidget(holder)
            holder.setParent(None)
            holder.deleteLater()
        self._pages[key] = page
        self._built.add(key)
        return page

    def _apply_page(self, key: str) -> None:
        if key not in PAGE_ORDER:
            return
        try:
            page = self._ensure_page(key)
        except Exception:  # noqa: BLE001
            self.ctx.log.error(f"{key} 页面创建失败：{traceback.format_exc(limit=2)}", "界面")
            return
        self._current_key = key
        self.stack.setCurrentWidget(page)
        self.titlebar.set_current_page(key)
        self.ctx.settings.set("start_page", key, save=False)
        hook = getattr(page, "on_show", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # noqa: BLE001 - a page hook must not crash us
                self.ctx.log.error(f"{key} 页面刷新失败：{traceback.format_exc(limit=2)}", "界面")

    # ------------------------------------------------------------------- theme
    def _toggle_theme(self) -> None:
        self.ctx.toggle_theme()

    def _on_theme_changed(self, name: str) -> None:
        from . import theme as theme_module

        palette = theme_module.palette_for(name)
        # Defer: destroying widgets from inside the signal emitted by the theme
        # button (which is one of those widgets) is not safe.
        QTimer.singleShot(0, lambda: self._apply_theme_and_rebuild(palette))

    def _apply_theme_and_rebuild(self, palette) -> None:
        """Swap the palette, then rebuild the pages.

        Order matters a lot.  ``setStyleSheet`` re-polishes every *live* widget:
        with the five pages alive (~1000 widgets) that measured 2.9 s, and it
        dropped to 0.03 s once they were really gone.  ``deleteLater`` alone is
        not enough - the widgets stay alive until DeferredDelete is processed -
        so the queue is flushed before the new sheet is applied.  Only the
        current page is rebuilt immediately; the rest are built on first visit.
        """
        from . import theme as theme_module

        key = self.current_page_key
        self._teardown_pages()
        theme_module.apply_theme(QApplication.instance(), palette)
        self.titlebar.refresh_theme()
        self._apply_corner_style()
        self._update_status()
        self._apply_page(key)
        theme.refresh_widgets(self)

    def _teardown_pages(self) -> None:
        for key in list(self._built):
            page = self._pages.get(key)
            if page is None:
                continue
            widgets.cancel_tasks(page)
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
            index = self.stack.indexOf(page)
            if index < 0:
                continue
            holder = QWidget()
            holder.setObjectName("Root")
            self.stack.insertWidget(index, holder)
            self.stack.removeWidget(page)
            page.setParent(None)
            page.deleteLater()
        self._pages.clear()
        self._built.clear()
        # Make sure those widgets are actually destroyed before the style sheet
        # change walks the widget tree.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def _build_pages(self) -> None:
        """Create one placeholder slot per page; real pages are built lazily."""
        # Idempotent: clear first so a second call cannot stack duplicates.
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        for _key in PAGE_ORDER:
            holder = QWidget()
            holder.setObjectName("Root")
            self.stack.addWidget(holder)
        self._pages = {}
        self._built = set()

    # -------------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_file = appdirs.resource_path("assets", "icon.ico")
        icon = QIcon(icon_file) if os.path.isfile(icon_file) else QIcon(icons.logo_mark())
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f"{self.ctx.name} · {self.ctx.name_zh}")

        menu = QMenu(self)
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self._restore_window)
        act_play = QAction("开始游戏", self)
        act_play.triggered.connect(self._tray_launch)
        act_folder = QAction("打开游戏目录", self)
        act_folder.triggered.connect(self._open_game_folder)
        act_log = QAction("打开日志文件", self)
        act_log.triggered.connect(lambda: winutil.open_path(self.ctx.log_file))
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)

        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_play)
        menu.addAction(act_folder)
        menu.addAction(act_log)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray = tray

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_window()

    def _restore_window(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_launch(self) -> None:
        page = self._pages.get("home")
        starter = getattr(page, "start_game", None)
        if callable(starter):
            starter()
        else:
            self._restore_window()

    def _tray_message(self, title: str, message: str) -> None:
        if self.tray is None:
            return
        if not self.ctx.settings.get("tray_notifications", True):
            return
        try:
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 4000)
        except Exception:
            pass

    # ------------------------------------------------------------------ status
    def _poll_game(self) -> None:
        install = self.ctx.install
        running = False
        if install is not None and install.exists:
            try:
                running = install.is_running()
            except Exception:
                running = False

        if running and not self._was_running:
            self._play_started = self._play_started or time.time()
            self.ctx.log.info("检测到游戏进程已启动", "监控")
            self._tray_message(self.ctx.name, "游戏已启动")
        elif not running and self._was_running:
            if self._play_started:
                elapsed = time.time() - self._play_started
                self.ctx.settings.bump_stat("play_seconds", elapsed)
                self.ctx.log.info(
                    f"游戏已退出 · 本次会话 {winutil.human_duration(elapsed)}", "监控"
                )
                self._play_started = None
            else:
                self.ctx.log.info("游戏进程已退出", "监控")
            self._tray_message(self.ctx.name, "游戏已退出")

        self._was_running = running
        self._running = running
        self._update_status()

    def _update_status(self) -> None:
        palette = theme.current()
        install = self.ctx.install
        running = getattr(self, "_running", False)
        channel = gamepath.CHANNEL_LABELS.get(self.ctx.active_channel, "")

        if install is not None and install.exists:
            validation = install.validate()
            colour = palette.success if validation.ok else palette.warn
            self.dot.setStyleSheet(f"background: {colour}; border-radius: 4px;")
            text = f"[{channel}] {install.root}"
            if not validation.ok:
                text += f"  ·  {validation.summary}"
            self.status_left.setText(text)
        else:
            self.dot.setStyleSheet(f"background: {palette.error}; border-radius: 4px;")
            self.status_left.setText(f"[{channel}] 未设置游戏目录 · 请在主页选择")

        if running:
            elapsed = time.time() - self._play_started if self._play_started else 0
            self.status_mid.setText(f"游戏运行中 · {winutil.human_duration(elapsed)}")
        else:
            stats = self.ctx.settings.get("stats") or {}
            total = stats.get("play_seconds") or 0
            self.status_mid.setText(
                f"游戏未运行 · 累计记录 {winutil.human_duration(total)}" if total else "游戏未运行"
            )
        self.status_right.setText(f"{self.ctx.name} v{self.ctx.version} · 本地模式 · 无作弊功能")

    def _on_install_changed(self, install) -> None:
        self._update_status()
        for page in self._pages.values():
            hook = getattr(page, "on_install_changed", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    self.ctx.log.warn("页面刷新游戏目录失败", "界面")

    # ----------------------------------------------------------------- actions
    def _open_game_folder(self) -> None:
        install = self.ctx.require_install(self, reason="打开游戏目录")
        if install is None:
            return
        if not winutil.open_path(install.root):
            self.ctx.notify(self, "无法打开游戏目录", "error")

    def _show_help(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("使用说明与免责声明")
        dialog.setIcon(QMessageBox.Information)
        dialog.setTextFormat(Qt.RichText)
        dialog.setText(
            "<b>WTToolbox 战雷工具箱</b><br><br>"
            "这是一个第三方、非官方的 War Thunder 桌面辅助工具，与 Gaijin Entertainment 无任何关联。<br><br>"
            "<b>它能做什么</b><br>"
            "• 自动检测游戏目录并启动游戏<br>"
            "• 读取 / 编辑 config.blk 图形配置（自动备份，可一键还原）<br>"
            "• 查看启动器与更新器日志、管理回放 / 截图 / 涂装 / 瞄具 / 自定义任务<br>"
            "• 安装音效模组并完整回滚、清理游戏缓存与旧日志<br>"
            "• 显示官方中文资讯、以系统托盘常驻<br><br>"
            "<b>它不做什么</b><br>"
            "本工具不注入、不读取、不修改游戏进程内存，不提供任何影响对局的作弊功能。"
            "所有功能都只对游戏目录中的普通文件与配置进行操作。<br><br>"
            "修改游戏文件前请先退出游戏，否则游戏退出时可能覆盖你的改动。"
        )
        dialog.exec()

    # ------------------------------------------------------------------ window
    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.set_maximized(self.isMaximized())
        self._layout_grips()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._layout_grips()

    # ------------------------------------------------------------ rounded shell
    def _rounded(self) -> bool:
        return not (self.isMaximized() or self.isFullScreen())

    def _corner_margin(self) -> int:
        return theme.WINDOW_SHADOW if self._rounded() else 0

    def _apply_corner_style(self) -> None:
        """Square off the top/bottom strips while maximised, round otherwise."""
        flat = not self._rounded()
        for widget in (self.titlebar, self.status):
            if widget is None:
                continue
            if widget.property("tkFlat") == flat:
                continue
            widget.setProperty("tkFlat", flat)
            theme.restyle(widget)
        layout = self.layout()
        if layout is not None:
            margin = self._corner_margin()
            if layout.contentsMargins().left() != margin:
                layout.setContentsMargins(margin, margin, margin, margin)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Paint the rounded window body (and its soft shadow) by hand.

        The window is translucent and frameless; DWM will not round a WS_POPUP
        window, so the corners are ours to draw.  While maximised the body
        becomes a plain square with no shadow.
        """
        super().paintEvent(event)
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        margin = self._corner_margin()
        radius = theme.WINDOW_RADIUS if margin else 0
        body = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        if body.width() <= 1 or body.height() <= 1:
            painter.end()
            return

        if margin:
            # Cheap soft shadow: concentric strokes whose alpha accumulates
            # towards the body, which reads as a blur.
            shadow = QColor(palette.shadow) if palette.shadow.startswith("#") else QColor(0, 0, 0)
            for step in range(margin, 0, -1):
                colour = QColor(shadow)
                colour.setAlpha(9)
                painter.setPen(QPen(colour, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(
                    body.adjusted(-step, -step, step, step),
                    radius + step,
                    radius + step,
                )

        painter.setPen(QPen(QColor(palette.border_strong), 1))
        painter.setBrush(QColor(palette.bg))
        painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.end()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            self._apply_corner_style()
            self._layout_grips()
            if self.isMinimized() and self.ctx.settings.get("minimize_to_tray", True):
                if self.tray is not None:
                    QTimer.singleShot(0, self.hide)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self._force_quit and self.ctx.settings.get("close_to_tray", False):
            if self.tray is not None:
                event.ignore()
                self.hide()
                self._tray_message(self.ctx.name, "已最小化到托盘，双击图标可恢复")
                return
        self._persist_geometry()
        widgets.cancel_tasks(self)
        event.accept()
        QApplication.quit()

    def _quit(self) -> None:
        self._force_quit = True
        self.close()

    # --------------------------------------------------------------- geometry
    def _persist_geometry(self) -> None:
        window = dict(self.ctx.settings.get("window") or {})
        if not self.isMaximized() and not self.isMinimized():
            window.update({"w": self.width(), "h": self.height(), "x": self.x(), "y": self.y()})
        window["maximized"] = self.isMaximized()
        self.ctx.settings.set("window", window)

    def _restore_geometry(self) -> None:
        saved = dict(self.ctx.settings.get("window") or {})
        width = int(saved.get("w") or 1180)
        height = int(saved.get("h") or 760)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None

        width = max(MIN_WIDTH, min(width, available.width() if available else width))
        height = max(MIN_HEIGHT, min(height, available.height() if available else height))
        self.resize(width, height)

        x, y = saved.get("x"), saved.get("y")
        if x is None or y is None:
            if available is not None:
                self.move(
                    available.x() + (available.width() - width) // 2,
                    available.y() + max(0, (available.height() - height) // 2 - 20),
                )
        else:
            self.move(int(x), int(y))
            if available is not None:
                # Pull the window back on screen if the monitor layout changed.
                if not available.intersects(self.frameGeometry()):
                    self.move(available.x() + 60, available.y() + 60)

        if saved.get("maximized"):
            self.showMaximized()
        self.titlebar.set_maximized(self.isMaximized())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._layout_grips()
