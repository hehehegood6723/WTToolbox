"""Custom title bar: brand, page navigation, theme switch, window controls.

The window is frameless, so this bar also owns drag-to-move and
double-click-to-maximise.  Edge resizing lives in :mod:`mainwindow` because it
needs invisible grips layered over the whole window.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import appdirs
from . import icons, theme
from .context import PAGE_ICONS, PAGE_LABELS, PAGE_ORDER, PAGE_SHORT

__all__ = ["TitleBar"]

TITLEBAR_HEIGHT = 58

#: Below this title-bar width the long nav labels would be elided ("音…"), so
#: the short ones are used instead.  The brand block plus the window buttons
#: need ~430 px and the seven long labels ~700 px.
COMPACT_NAV_WIDTH = 1140


class TitleBar(QWidget):
    """The top strip of the main window."""

    pageRequested = Signal(str)
    themeToggled = Signal()
    openGameFolderRequested = Signal()
    helpRequested = Signal()
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLEBAR_HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_brand())
        layout.addStretch(1)
        layout.addWidget(self._build_nav())
        layout.addStretch(1)
        layout.addLayout(self._build_actions())

    # ------------------------------------------------------------------ parts
    def _build_brand(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        mark = QLabel()
        mark.setFixedSize(28, 28)
        mark.setPixmap(self._brand_pixmap(28))
        row.addWidget(mark)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        name = QLabel(f"{self.ctx.name}  {self.ctx.name_zh}")
        name.setObjectName("BrandName")
        sub = QLabel(f"v{self.ctx.version} · 第三方非官方工具")
        sub.setObjectName("BrandSub")
        column.addWidget(name)
        column.addWidget(sub)
        row.addLayout(column)
        return box

    @staticmethod
    def _brand_pixmap(size: int) -> QPixmap:
        """Load the real application icon so the bar matches the taskbar."""
        path = appdirs.resource_path("assets", "icon.ico")
        icon = QIcon(path)
        if not icon.isNull():
            pixmap = icon.pixmap(QSize(size, size))
            if not pixmap.isNull():
                return pixmap
        # Fallback if the asset is missing from the bundle.
        return icons.logo_mark(theme.current().accent, size)

    def _build_nav(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QToolButton] = {}
        palette = theme.current()

        for key in PAGE_ORDER:
            button = QToolButton()
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setText(PAGE_LABELS[key])
            button.setIcon(icons.icon(PAGE_ICONS[key], palette.text_muted, 17, 1.8))
            button.setIconSize(QSize(17, 17))
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _=False, k=key: self.pageRequested.emit(k))
            button.toggled.connect(
                lambda checked, k=key: self._retint(k, checked) if checked else None
            )
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            row.addWidget(button)

        self.set_current_page(self.ctx.settings.get("start_page") or "home")
        return box

    def _apply_nav_labels(self) -> None:
        """Use short labels when the bar is too narrow for the long ones.

        Without this the buttons are squeezed below their size hint and Qt
        elides them to "音…", which is unreadable.
        """
        compact = self.width() < COMPACT_NAV_WIDTH
        if compact == getattr(self, "_nav_compact", None):
            return
        self._nav_compact = compact
        labels = PAGE_SHORT if compact else PAGE_LABELS
        for key, button in self.nav_buttons.items():
            button.setText(labels.get(key, key))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_nav_labels()

    def _retint(self, key: str, checked: bool) -> None:
        if not checked:
            return
        palette = theme.current()
        for other_key, button in self.nav_buttons.items():
            colour = palette.nav_active_fg if other_key == key else palette.text_muted
            icon_name = PAGE_ICONS[other_key]
            button.setIcon(icons.icon(icon_name, colour, 17, 1.8))

    def set_current_page(self, key: str) -> None:
        button = self.nav_buttons.get(key)
        if button is None:
            return
        if not button.isChecked():
            button.setChecked(True)
        self._retint(key, True)

    def _build_actions(self) -> QHBoxLayout:
        palette = theme.current()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("IconBtn")
        self.theme_button.setCursor(Qt.PointingHandCursor)
        self.theme_button.setAutoRaise(True)
        self.theme_button.setToolTip("切换浅色 / 深色主题")
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.clicked.connect(self.themeToggled.emit)
        self.refresh_theme_icon()

        game_button = QToolButton()
        game_button.setObjectName("IconBtn")
        game_button.setCursor(Qt.PointingHandCursor)
        game_button.setAutoRaise(True)
        game_button.setToolTip("在资源管理器中打开游戏目录")
        game_button.setIcon(icons.icon("folder-open", palette.text_muted, 18, 1.8))
        game_button.setIconSize(QSize(18, 18))
        game_button.clicked.connect(self.openGameFolderRequested.emit)
        self.game_button = game_button

        help_button = QToolButton()
        help_button.setObjectName("IconBtn")
        help_button.setCursor(Qt.PointingHandCursor)
        help_button.setAutoRaise(True)
        help_button.setToolTip("使用说明与免责声明")
        help_button.setIcon(icons.icon("question", palette.text_muted, 18, 1.8))
        help_button.setIconSize(QSize(18, 18))
        help_button.clicked.connect(self.helpRequested.emit)
        self.help_button = help_button

        row.addWidget(self.theme_button)
        row.addWidget(game_button)
        row.addWidget(help_button)
        row.addSpacing(6)

        divider = QFrame()
        divider.setObjectName("VDivider")
        divider.setFixedWidth(1)
        divider.setFixedHeight(20)
        row.addWidget(divider)
        row.addSpacing(4)

        self.min_button = self._window_button("min", "最小化", self.minimizeRequested)
        self.max_button = self._window_button("max", "最大化", self.maximizeRequested)
        self.close_button = self._window_button("close", "关闭", self.closeRequested, close=True)
        row.addWidget(self.min_button)
        row.addWidget(self.max_button)
        row.addWidget(self.close_button)
        return row

    def _window_button(self, icon_name: str, tooltip: str, signal, *, close: bool = False) -> QToolButton:
        palette = theme.current()
        button = QToolButton()
        button.setObjectName("WinBtnClose" if close else "WinBtn")
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        button.setIconSize(QSize(15, 15))
        button.setFixedSize(30, 28)
        colour = palette.text_muted
        button.setIcon(icons.icon(icon_name, colour, 15, 1.8))
        button.clicked.connect(signal.emit)
        if close:
            button.installEventFilter(self)
        return button

    def refresh_theme_icon(self) -> None:
        palette = theme.current()
        name = "moon" if palette.name == "light" else "sun"
        self.theme_button.setIcon(icons.icon(name, palette.text_muted, 18, 1.8))

    def refresh_theme(self) -> None:
        """Re-tint every icon after the palette changed (shell widgets are reused)."""
        palette = theme.current()
        active = next((k for k, b in self.nav_buttons.items() if b.isChecked()), "")
        for key, button in self.nav_buttons.items():
            colour = palette.nav_active_fg if key == active else palette.text_muted
            button.setIcon(icons.icon(PAGE_ICONS[key], colour, 17, 1.8))
        self.refresh_theme_icon()
        for button, name, size, stroke in (
            (self.game_button, "folder-open", 18, 1.8),
            (self.help_button, "question", 18, 1.8),
            (self.min_button, "min", 15, 1.8),
            (self.max_button, "restore" if self.window().isMaximized() else "max", 15, 1.8),
            (self.close_button, "close", 15, 1.8),
        ):
            button.setIcon(icons.icon(name, palette.text_muted, size, stroke))
        self.update()

    def set_maximized(self, maximized: bool) -> None:
        palette = theme.current()
        self.max_button.setIcon(
            icons.icon("restore" if maximized else "max", palette.text_muted, 15, 1.8)
        )
        self.max_button.setToolTip("向下还原" if maximized else "最大化")

    # ------------------------------------------------------------ interactions
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self.maximizeRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        # Keep the close glyph readable when hovered: the style sheet turns the
        # button red, so the icon must turn white too.
        if obj is self.close_button and event.type() in (QEvent.Enter, QEvent.Leave):
            palette = theme.current()
            colour = "#FFFFFF" if event.type() == QEvent.Enter else palette.text_muted
            self.close_button.setIcon(icons.icon("close", colour, 15, 1.8))
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        # A 1px accent hairline along the bottom edge ties the bar to the brand.
        painter = QPainter(self)
        palette = theme.current()
        colour = QColor(palette.accent)
        colour.setAlpha(70)
        painter.setPen(colour)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()
