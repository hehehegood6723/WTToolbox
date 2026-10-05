"""Reusable widgets shared by every page.

Custom-painted controls read :func:`wttoolbox.ui.theme.current` at paint time,
so a theme switch only needs a page rebuild (which :class:`MainWindow` does)
rather than per-widget plumbing.
"""

from __future__ import annotations

import os
import threading
import traceback
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Property,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from ..core import winutil

__all__ = [
    "hspacer",
    "vspacer",
    "hline",
    "vline",
    "make_label",
    "icon_button",
    "primary_button",
    "ghost_button",
    "subtle_button",
    "danger_button",
    "link_button",
    "Card",
    "SubCard",
    "SectionHeader",
    "Badge",
    "ToggleSwitch",
    "CheckBox",
    "ElidedLabel",
    "LogView",
    "SearchBox",
    "EmptyState",
    "ElidedLabel",
    "Toast",
    "StatTile",
    "InfoRow",
    "BusyStrip",
    "Task",
    "run_task",
    "cancel_tasks",
    "later",
    "open_external",
    "confirm",
    "notify",
    "disclaimer_dialog",
    "DisclaimerDialog",
    "DISCLAIMER_TEXT",
    "SubNavPanel",
    "ScrollColumn",
    "selectable_table",
    "configure_table",
    "format_spin",
    "RadarChart",
]


# --------------------------------------------------------------------------- #
#  Layout helpers
# --------------------------------------------------------------------------- #
def hspacer(width: int | None = None) -> QWidget:
    widget = QWidget()
    if width is None:
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    else:
        widget.setFixedWidth(width)
    return widget


def vspacer(height: int | None = None) -> QWidget:
    widget = QWidget()
    if height is None:
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    else:
        widget.setFixedHeight(height)
    return widget


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def vline() -> QFrame:
    line = QFrame()
    line.setObjectName("VDivider")
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    return line


def make_label(text: str, role: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setObjectName(role)
    label.setWordWrap(wrap)
    return label


def _tint(widget: QToolButton, name: str, size: int, color: str | None, stroke: float = 1.7) -> None:
    palette = theme.current()
    widget.setIcon(icons.icon(name, color or palette.text_muted, size, stroke))
    widget.setIconSize(QSize(size, size))


def icon_button(
    name: str,
    tooltip: str = "",
    *,
    size: int = 18,
    color: str | None = None,
    stroke: float = 1.7,
    object_name: str = "IconBtn",
    on_click: Callable[[], None] | None = None,
) -> QToolButton:
    button = QToolButton()
    button.setObjectName(object_name)
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoRaise(True)
    _tint(button, name, size, color, stroke)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click:
        button.clicked.connect(lambda: on_click())
    return button


def _make_button(
    text: str,
    object_name: str,
    icon_name: str | None,
    on_click: Callable[[], None] | None,
    *,
    icon_color: str | None = None,
    tooltip: str = "",
) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setCursor(Qt.PointingHandCursor)
    if icon_name:
        palette = theme.current()
        default = palette.on_accent if object_name == "Primary" else palette.text_muted
        button.setIcon(icons.icon(icon_name, icon_color or default, 16, 1.8))
        button.setIconSize(QSize(16, 16))
    if tooltip:
        button.setToolTip(tooltip)
    if on_click:
        button.clicked.connect(lambda: on_click())
    return button


def primary_button(text: str, icon_name: str | None = None, on_click=None, *, tooltip: str = "") -> QPushButton:
    return _make_button(text, "Primary", icon_name, on_click, tooltip=tooltip)


def ghost_button(text: str, icon_name: str | None = None, on_click=None, *, tooltip: str = "") -> QPushButton:
    return _make_button(text, "Ghost", icon_name, on_click, tooltip=tooltip)


def subtle_button(text: str, icon_name: str | None = None, on_click=None, *, tooltip: str = "") -> QPushButton:
    return _make_button(text, "Subtle", icon_name, on_click, tooltip=tooltip)


def danger_button(text: str, icon_name: str | None = None, on_click=None, *, tooltip: str = "") -> QPushButton:
    return _make_button(text, "Danger", icon_name, on_click, icon_color="#FFFFFF", tooltip=tooltip)


def link_button(text: str, on_click=None, *, icon_name: str | None = None, tooltip: str = "") -> QPushButton:
    return _make_button(text, "Link", icon_name, on_click, icon_color=theme.current().accent, tooltip=tooltip)


# --------------------------------------------------------------------------- #
#  Containers
# --------------------------------------------------------------------------- #
class Card(QFrame):
    """A rounded surface with an optional icon/title header and action row."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        *,
        icon_name: str | None = None,
        parent: QWidget | None = None,
        padding: int = 16,
        spacing: int = 11,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._icon_name = icon_name

        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding, padding, padding)
        outer.setSpacing(spacing)
        self._outer = outer

        self._header = QHBoxLayout()
        self._header.setSpacing(9)
        self._title_labels = QVBoxLayout()
        self._title_labels.setSpacing(1)
        self._title = make_label(title, "CardTitle")
        self._subtitle = make_label(subtitle, "CardSubtitle", wrap=True)
        self._title_labels.addWidget(self._title)
        self._title_labels.addWidget(self._subtitle)
        self._icon_label = QLabel()
        if icon_name:
            self._icon_label.setPixmap(
                icons.icon_pixmap(icon_name, theme.current().accent, 18, 1.85)
            )
            self._icon_label.setFixedWidth(20)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(6)
        self._header.addWidget(self._icon_label)
        self._header.addLayout(self._title_labels, 1)
        self._header.addLayout(self._actions)

        self._body = QVBoxLayout()
        self._body.setSpacing(spacing)
        outer.addLayout(self._header)
        outer.addLayout(self._body)

        self._sync_header()

    # ------------------------------------------------------------------ api
    def _sync_header(self) -> None:
        has_header = bool(
            self._title.text() or self._subtitle.text() or self._actions.count() or self._icon_name
        )
        for index in range(self._header.count()):
            item = self._header.itemAt(index)
            widget = item.widget()
            if widget is self._icon_label:
                widget.setVisible(bool(self._icon_name))
        self._title.setVisible(bool(self._title.text()))
        self._subtitle.setVisible(bool(self._subtitle.text()))
        self._header_widget_visible = has_header
        # Collapse spacing when there is no header at all.
        self._outer.setSpacing(11 if has_header else 0)
        if not has_header:
            for index in range(self._header.count()):
                item = self._header.itemAt(index)
                if item.widget():
                    item.widget().hide()

    def set_title(self, text: str) -> None:
        self._title.setText(text)
        self._sync_header()

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._sync_header()

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        self._sync_header()
        return widget

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self._body.addLayout(layout)

    def add_stretch(self, stretch: int = 1) -> None:
        self._body.addStretch(stretch)

    @property
    def body(self) -> QVBoxLayout:
        return self._body

    @property
    def header_layout(self) -> QHBoxLayout:
        return self._header


class SubCard(QFrame):
    """A nested, lower-contrast surface used inside cards."""

    def __init__(self, parent: QWidget | None = None, *, padding: int = 12, spacing: int = 8) -> None:
        super().__init__(parent)
        self.setObjectName("SubCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class SectionHeader(QWidget):
    """``icon + title (+ subtitle)`` with room for trailing controls."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self._icon = QLabel()
        if icon_name:
            self._icon.setPixmap(icons.icon_pixmap(icon_name, theme.current().accent, 18, 1.85))
            self._icon.setFixedWidth(20)
        else:
            self._icon.hide()
        self._title = make_label(title, "H2")
        self._subtitle = make_label(subtitle, "Muted")
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addStretch(1)
        self.trailing = layout

    def add_trailing(self, widget: QWidget) -> QWidget:
        self.trailing.addWidget(widget)
        return widget


class ScrollColumn(QScrollArea):
    """A vertical scroll area wrapping a single content widget."""

    def __init__(self, parent: QWidget | None = None, *, margins: tuple[int, int, int, int] = (0, 0, 8, 0)) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("ScrollContent")
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(14)
        self.setWidget(self.content)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


# --------------------------------------------------------------------------- #
#  Small display widgets
# --------------------------------------------------------------------------- #
_BADGE_KINDS = {
    "accent": ("accent_soft", "accent"),
    "success": ("success_soft", "success"),
    "warn": ("warn_soft", "warn"),
    "error": ("error_soft", "error"),
    "info": ("info_soft", "info"),
    "neutral": ("surface_3", "text_muted"),
}


class Badge(QLabel):
    """A small colour-coded tag, e.g. 日常 / 福利 / BUG."""

    def __init__(self, text: str, kind: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.set_kind(kind)
        self.setAlignment(Qt.AlignCenter)

    def set_kind(self, kind: str) -> None:
        palette = theme.current()
        bg_key, fg_key = _BADGE_KINDS.get(kind, _BADGE_KINDS["neutral"])
        background = getattr(palette, bg_key)
        foreground = getattr(palette, fg_key)
        self.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 7px;"
            f" padding: 2px 7px; font-size: 11px; font-weight: 700;"
        )


class ToggleSwitch(QAbstractButton):
    """An animated iOS-style switch for boolean settings."""

    def __init__(self, parent: QWidget | None = None, *, width: int = 42, height: int = 23) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(width, height)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = float(value)
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt naming
        super().setChecked(checked)
        self._animation.stop()
        self._offset = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(42, 23)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = rect.height() / 2

        off_bg = QColor(palette.surface_3)
        on_bg = QColor(palette.accent)
        track = QColor(
            int(off_bg.red() + (on_bg.red() - off_bg.red()) * self._offset),
            int(off_bg.green() + (on_bg.green() - off_bg.green()) * self._offset),
            int(off_bg.blue() + (on_bg.blue() - off_bg.blue()) * self._offset),
        )
        if not self.isEnabled():
            track.setAlpha(120)
        painter.setPen(QPen(QColor(palette.border_strong), 1))
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        knob_d = rect.height() - 5
        travel = rect.width() - knob_d - 5
        knob_x = 2.5 + travel * self._offset
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(knob_x, 2.5, knob_d, knob_d))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CheckBox(QCheckBox):
    """Stock check box; exists so pages have one import for controls."""


class ElidedLabel(QLabel):
    """A single-line label that truncates with an ellipsis instead of clipping.

    The horizontal size policy is ``Ignored`` so the layout decides the width;
    otherwise the full-text size hint would fight the elided text and the label
    would either overflow its card or never show an ellipsis.
    """

    def __init__(self, text: str = "", role: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text or ""
        if role:
            self.setObjectName(role)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        super().setText(self._full)
        self.setToolTip(self._full if len(self._full) > 18 else "")

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 18 else "")
        self._update_elided()

    def full_text(self) -> str:
        return self._full

    def _update_elided(self) -> None:
        width = self.contentsRect().width()
        if width <= 12:
            super().setText(self._full)
            return
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, width))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._update_elided()


class StatTile(QFrame):
    """``value`` over ``label`` with an accent icon."""

    def __init__(
        self,
        label: str,
        value: str = "—",
        *,
        icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SubCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(9)
        self._icon = QLabel()
        if icon_name:
            self._icon.setPixmap(icons.icon_pixmap(icon_name, theme.current().accent, 18, 1.8))
            self._icon.setFixedWidth(20)
            layout.addWidget(self._icon)
        column = QVBoxLayout()
        column.setSpacing(0)
        self._value = make_label(value, "CardTitle")
        self._label = make_label(label, "Faint")
        column.addWidget(self._value)
        column.addWidget(self._label)
        layout.addLayout(column, 1)
        self.setToolTip(label)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class InfoRow(QWidget):
    """``key`` on the left, ``value`` on the right - used in details panels."""

    def __init__(self, key: str, value: str = "", *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(10)
        self._key = make_label(key, "Muted")
        self._key.setMinimumWidth(96)
        self._value = make_label(value, None)
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._key)
        layout.addStretch(1)
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class SearchBox(QLineEdit):
    """A line edit with a leading search glyph and a clear button."""

    def __init__(self, placeholder: str = "搜索…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Search")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._glyph = QLabel(self)
        self._glyph.setPixmap(icons.icon_pixmap("search", theme.current().text_faint, 15, 1.9))
        self._glyph.setFixedSize(15, 15)
        self._glyph.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._place()

    def _place(self) -> None:
        self._glyph.move(10, (self.height() - 15) // 2)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._place()


class EmptyState(QWidget):
    """A centred placeholder for empty lists."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        icon_name: str = "info",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 26, 16, 26)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap(icon_name, theme.current().text_faint, 34, 1.5))
        glyph.setAlignment(Qt.AlignCenter)
        self._title = make_label(title, "CardTitle")
        self._title.setAlignment(Qt.AlignCenter)
        self._subtitle = make_label(subtitle, "Muted", wrap=True)
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(glyph)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        self.actions = QHBoxLayout()
        self.actions.setAlignment(Qt.AlignCenter)
        layout.addLayout(self.actions)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


class LogView(QPlainTextEdit):
    """A read-only, colour-coded, line-capped log viewer."""

    _LEVEL_COLORS = {
        "DEBUG": "text_faint",
        "INFO": "text_muted",
        "OK": "success",
        "WARN": "warn",
        "ERROR": "error",
    }

    def __init__(self, parent: QWidget | None = None, *, max_lines: int = 1200) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setUndoRedoEnabled(False)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.viewport().setCursor(Qt.IBeamCursor)

    def append_line(self, text: str, level: str = "INFO") -> None:
        palette = theme.current()
        color = getattr(palette, self._LEVEL_COLORS.get(level.upper(), "text_muted"), palette.text)
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.appendHtml(
            f'<span style="color:{color}; white-space:pre-wrap;">{escaped}</span>'
        )
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_plain_block(self, text: str, color_key: str = "text_muted") -> None:
        palette = theme.current()
        color = getattr(palette, color_key, palette.text)
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.appendHtml(f'<span style="color:{color};">{escaped}</span>')

    def load_text(self, text: str, level: str = "INFO") -> None:
        self.clear()
        if not text:
            return
        for line in text.splitlines():
            self.append_line(line, level)


class BusyStrip(QWidget):
    """A thin indeterminate progress strip with a status caption."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._label = make_label("", "Muted")
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._label)
        self.hide()

    def start(self, text: str = "处理中…") -> None:
        self._label.setText(text)
        self._bar.setRange(0, 0)
        self.show()

    def set_progress(self, done: int, total: int, text: str = "") -> None:
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(min(done, total))
            self._label.setText(f"{text}  {done}/{total}" if text else f"{done}/{total}")
        else:
            self._bar.setRange(0, 0)
            if text:
                self._label.setText(text)
        self.show()

    def stop(self) -> None:
        self.hide()


# --------------------------------------------------------------------------- #
#  Background work
# --------------------------------------------------------------------------- #
class _TaskRelay(QObject):
    """Main-thread receiver for a worker's signals.

    Without this, a plain Python callable connected to a signal emitted from
    another thread is invoked **in that thread** by PySide6, which corrupts the
    widget tree ("QObject::setParent: ... different thread").  A QObject created
    in the GUI thread forces Qt to use a queued connection instead.
    """

    def __init__(
        self,
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[str], None] | None,
        on_progress: Callable[[int, int, str], None] | None,
        finish: Callable[[], None],
    ) -> None:
        super().__init__()
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        self._finish = finish

    def _guard(self, action, *args) -> None:
        """Run a UI callback, tolerating a widget that has already been deleted.

        A page can be torn down (theme switch, window close) while its worker
        thread is still finishing; the queued callback would then touch a
        deleted C++ object and raise RuntimeError from PySide6.
        """
        try:
            if action is not None:
                action(*args)
        except RuntimeError as exc:
            if "already deleted" in str(exc) or "Internal C++ object" in str(exc):
                return
            print(f"[task callback] RuntimeError: {exc}")
        except Exception as exc:  # noqa: BLE001 - a callback must never crash us
            print(f"[task callback] {type(exc).__name__}: {exc}")

    def handle_progress(self, done: int, total: int, text: str) -> None:
        self._guard(self._on_progress, done, total, text)

    def handle_finished(self, result: Any) -> None:
        try:
            self._guard(self._on_done, result)
        finally:
            self._finish()

    def handle_failed(self, message: str) -> None:
        try:
            self._guard(self._on_error, message)
        finally:
            self._finish()


class Task(QObject):
    """Run a blocking core function on a worker thread.

    ``kwargs_template`` is copied per run; ``on_progress`` / ``should_cancel``
    are injected only when the caller asks for them, so a function's signature
    is never guessed.
    """

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        kwargs: dict[str, Any] | None = None,
        wants_progress: bool = False,
        wants_cancel: bool = False,
        label: str = "",
        parent: QObject | None = None,
    ) -> None:
        # NOTE: deliberately no QObject parent.  A parented QObject cannot be
        # moved to another thread ("QObject::moveToThread: Cannot move objects
        # with a parent"), so ownership is held by the caller's task registry.
        super().__init__(None)
        self._fn = fn
        self._kwargs = dict(kwargs or {})
        self._wants_progress = wants_progress
        self._wants_cancel = wants_cancel
        self.label = label or getattr(fn, "__name__", "task")
        self._cancel = threading.Event()
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._execute)
        self._done = False

    # ------------------------------------------------------------------ api
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> "Task":
        self._thread.start()
        return self

    def wait(self, timeout_ms: int = 5000) -> bool:
        return self._thread.wait(timeout_ms)

    # -------------------------------------------------------------- internal
    def _execute(self) -> None:
        kwargs = dict(self._kwargs)
        try:
            if self._wants_progress:
                kwargs["on_progress"] = self.progress.emit
            if self._wants_cancel:
                kwargs["should_cancel"] = self._cancel.is_set
            result = self._fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            detail = f"{type(exc).__name__}: {exc}"
            print(f"[task:{self.label}] {detail}\n{traceback.format_exc()}")
            self.failed.emit(detail)
        else:
            self.finished.emit(result)
        finally:
            self._thread.quit()

    def cleanup(self) -> None:
        if not self._done:
            self._done = True
        try:
            self._thread.quit()
            self._thread.wait(3000)
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Task {self.label} running={self._thread.isRunning()}>"


def run_task(
    owner: QObject,
    fn: Callable[..., Any],
    *,
    kwargs: dict[str, Any] | None = None,
    wants_progress: bool = False,
    wants_cancel: bool = False,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    label: str = "",
) -> Task:
    """Start *fn* on a worker thread; keep a reference on *owner* until done.

    All callbacks are delivered on the GUI thread.
    """
    task = Task(
        fn,
        kwargs=kwargs,
        wants_progress=wants_progress,
        wants_cancel=wants_cancel,
        label=label,
    )

    registry = getattr(owner, "_tk_tasks", None)
    if registry is None:
        registry = set()
        setattr(owner, "_tk_tasks", registry)
    registry.add(task)

    relays = getattr(owner, "_tk_relays", None)
    if relays is None:
        relays = set()
        setattr(owner, "_tk_relays", relays)

    def _finish() -> None:
        registry.discard(task)
        relays.discard(relay)

    relay = _TaskRelay(
        on_done,
        on_error if on_error is not None else lambda message: notify(
            owner, f"操作失败：{message}", "error"
        ),
        on_progress,
        _finish,
    )
    relays.add(relay)

    task.progress.connect(relay.handle_progress)
    task.finished.connect(relay.handle_finished)
    task.failed.connect(relay.handle_failed)
    task._thread.finished.connect(task.deleteLater)
    return task.start()


def cancel_tasks(owner: QObject) -> None:
    for task in list(getattr(owner, "_tk_tasks", ())):
        task.cancel()


def later(owner: QObject, delay_ms: int, callback) -> QTimer:
    """A single-shot timer **parented to** *owner*.

    ``QTimer.singleShot(ms, lambda: self.method())`` registers a callback that
    keeps ``self`` alive in the event loop, so it still fires after the widget
    has been destroyed - which is exactly what a theme switch does to every
    page.  The callback then touches deleted C++ objects and raises
    ``RuntimeError: Internal C++ object already deleted`` from inside the event
    loop.  Parenting the timer to the owner means Qt destroys it together with
    the widget, so the callback simply never runs.
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.start(max(0, int(delay_ms)))
    return timer


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #
class Toast(QWidget):
    """A transient message anchored to the bottom-right of a parent window."""

    _kinds = {
        "info": ("info", "info_soft", "info"),
        "success": ("check-circle", "success_soft", "success"),
        "warn": ("warning", "warn_soft", "warn"),
        "error": ("x-circle", "error_soft", "error"),
    }

    def __init__(self, parent: QWidget, text: str, kind: str = "info", duration: int = 2600) -> None:
        super().__init__(parent)
        self.setObjectName("ToastFrame")
        self.setAttribute(Qt.WA_StyledBackground, True)
        palette = theme.current()
        icon_name, _bg_key, fg_key = self._kinds.get(kind, self._kinds["info"])
        accent = getattr(palette, fg_key, palette.text)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 10, 15, 10)
        layout.setSpacing(9)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap(icon_name, accent, 17, 1.9))
        glyph.setFixedWidth(19)
        label = make_label(text, "ToastText", wrap=False)
        label.setStyleSheet(f"color: {palette.text};")
        layout.addWidget(glyph)
        layout.addWidget(label)
        self.setFixedHeight(42)
        self.adjustSize()

        self._effect = None
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._duration = duration
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setDuration(220)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()

    def show_at(self, offset: int = 0) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        width = min(max(self.sizeHint().width(), 210), max(240, parent.width() - 60))
        self.setFixedWidth(width)
        x = parent.width() - width - 22
        y = parent.height() - self.height() - 22 - offset
        self.move(max(12, x), max(12, y))
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        fade_in.setDuration(160)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start()
        self._fade_in = fade_in
        self._timer.start(self._duration)


def open_external(url: str) -> tuple[bool, str]:
    """Open a URL in the default browser.  Returns ``(ok, how_or_error)``.

    Tries Qt first (it goes through the shell association and reports failure),
    then falls back to the Qt-free chain in :mod:`wttoolbox.core.winutil`.
    """
    if not url:
        return False, "链接为空"
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if QDesktopServices.openUrl(QUrl(url)):
            return True, "Qt"
    except Exception:
        pass
    return winutil.open_url_verbose(url)


def notify(widget: QWidget | None, text: str, kind: str = "info", duration: int = 2600) -> None:
    """Show a toast over the widget's window.

    Toasts self-destruct after their fade-out, which leaves dangling wrappers in
    the per-window list; touching one raises ``RuntimeError`` from PySide6.  The
    list is therefore pruned defensively on every call.
    """
    window = widget.window() if widget is not None else None
    if window is None:
        return
    existing = getattr(window, "_tk_toasts", None)
    if existing is None:
        existing = []
        setattr(window, "_tk_toasts", existing)

    alive = []
    for toast in existing:
        try:
            if toast.isVisible():
                alive.append(toast)
        except RuntimeError:
            continue  # underlying C++ object already deleted
    existing[:] = alive

    toast = Toast(window, text, kind, duration)
    toast.destroyed.connect(lambda *_: existing.remove(toast) if toast in existing else None)
    existing.append(toast)
    toast.show_at(offset=sum(t.height() + 8 for t in existing[:-1]))


# --------------------------------------------------------------------------- #
#  Dialogs
# --------------------------------------------------------------------------- #
class ConfirmDialog(QDialog):
    """A themed confirmation dialog with an optional "don't ask again" box."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        ok_text: str = "确定",
        cancel_text: str = "取消",
        danger: bool = False,
        detail: str = "",
        checkbox_text: str = "",
        icon_name: str = "warning",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        palette = theme.current()
        self.setMinimumWidth(420)
        self.setStyleSheet(f"QDialog {{ background: {palette.bg}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(11)
        glyph = QLabel()
        glyph.setPixmap(
            icons.icon_pixmap(icon_name, palette.error if danger else palette.accent, 24, 1.8)
        )
        glyph.setFixedWidth(28)
        header.addWidget(glyph, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        heading = make_label(title, "H2")
        body = make_label(message, None, wrap=True)
        texts.addWidget(heading)
        texts.addWidget(body)
        if detail:
            small = make_label(detail, "Faint", wrap=True)
            texts.addWidget(small)
        header.addLayout(texts, 1)
        layout.addLayout(header)

        self.checkbox: CheckBox | None = None
        if checkbox_text:
            self.checkbox = CheckBox(checkbox_text)
            layout.addWidget(self.checkbox)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addStretch(1)
        cancel = ghost_button(cancel_text, None, self.reject)
        ok = danger_button(ok_text, None, self.accept) if danger else primary_button(ok_text, None, self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        ok.setDefault(True)


def confirm(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    ok_text: str = "确定",
    danger: bool = False,
    detail: str = "",
    checkbox_text: str = "",
) -> tuple[bool, bool]:
    """Show a confirmation.  Returns ``(accepted, checkbox_checked)``."""
    dialog = ConfirmDialog(
        parent, title, message,
        ok_text=ok_text, danger=danger, detail=detail, checkbox_text=checkbox_text,
    )
    accepted = dialog.exec() == QDialog.Accepted
    checked = bool(dialog.checkbox and dialog.checkbox.isChecked())
    return accepted, checked


def message_dialog(parent: QWidget | None, title: str, message: str, *, detail: str = "") -> None:
    dialog = ConfirmDialog(
        parent, title, message, ok_text="知道了", cancel_text="关闭",
        detail=detail, icon_name="info",
    )
    dialog.exec()


DISCLAIMER_TEXT = """一、关于本工具
WTToolbox 战雷工具箱是第三方、非官方的辅助工具，与 Gaijin Entertainment 及其关联公司没有任何关系，
也未获得其授权、认可或背书。

二、本功能会做什么
· 根据你填写的游戏昵称，在你的默认浏览器中打开 War Thunder 官方社区页面；
· 在程序内显示可以从本机文件真实统计的信息（游戏时长、启动次数、回放与截图数量、安装校验等）。

三、本功能不会做什么
· 不抓取、不解析、不代填你的账号战绩数据；
· 不绕过官方站点的任何访问限制或人机验证（官方接口目前对自动请求返回 403，本工具不会尝试规避）；
· 不读取、不保存、不上传你的账号密码或登录令牌；启动器在注册表中保存的加密凭据，本工具从不读取。

四、数据与隐私
· 你填写的昵称只保存在本机的 %APPDATA%\\WTToolbox\\settings.json 中；
· 本工具不会向任何服务器上传你的数据，也不包含任何云端账号体系；
· 你可以随时在战绩页面撤回同意，撤回后该页面会重新回到需确认状态。

五、风险提示
· 请在确认自己的网络环境与账号安全的前提下使用；
· 不要在公共或共享电脑上保存昵称等信息；
· 因使用本功能产生的任何后果由使用者自行承担。"""


class DisclaimerDialog(QDialog):
    """A blocking disclaimer that must be explicitly accepted."""

    def __init__(self, parent: QWidget | None, *, title: str = "战绩功能免责声明") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        palette = theme.current()
        self.setMinimumSize(620, 520)
        self.setStyleSheet(f"QDialog {{ background: {palette.bg}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(11)
        glyph = QLabel()
        glyph.setPixmap(icons.icon_pixmap("shield", palette.accent, 24, 1.8))
        glyph.setFixedWidth(28)
        header.addWidget(glyph, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(make_label(title, "H2"))
        texts.addWidget(
            make_label("请阅读以下内容；确认后才能使用战绩功能。", "Muted", wrap=True)
        )
        header.addLayout(texts, 1)
        layout.addLayout(header)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(DISCLAIMER_TEXT)
        view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        layout.addWidget(view, 1)

        self.check = CheckBox("我已阅读、理解并接受上述免责声明")
        self.check.toggled.connect(lambda checked: self.ok_button.setEnabled(checked))
        layout.addWidget(self.check)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        buttons.addStretch(1)
        buttons.addWidget(ghost_button("取消", None, self.reject))
        self.ok_button = primary_button("我已知悉并同意", "check", self.accept)
        self.ok_button.setEnabled(False)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)


def disclaimer_dialog(parent: QWidget | None, _ctx=None) -> bool:
    """Show the disclaimer.  Returns True only on an explicit acceptance."""
    dialog = DisclaimerDialog(parent)
    return dialog.exec() == QDialog.Accepted


# --------------------------------------------------------------------------- #
#  Navigation / tables
# --------------------------------------------------------------------------- #
class SubNavPanel(QWidget):
    """A left-hand list nav driving a ``QStackedWidget``."""

    def __init__(self, parent: QWidget | None = None, *, nav_width: int = 168) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.nav_card = QWidget()
        self.nav_card.setObjectName("SideNav")
        self.nav_card.setFixedWidth(nav_width)
        nav_layout = QVBoxLayout(self.nav_card)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("SideNavList")
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_layout.addWidget(self.nav)

        # Keep the nav card hugging its items instead of stretching to the
        # bottom of the page, which would leave a large empty panel.
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.nav_card, 0)
        column.addStretch(1)
        layout.addLayout(column)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self._pages: list[QWidget] = []
        self._item_height = 42

    def _resize_nav(self) -> None:
        height = self.nav.count() * self._item_height + 12
        self.nav.setFixedHeight(height)
        self.nav_card.setFixedHeight(height)

    def add_page(self, label: str, widget: QWidget, *, icon_name: str | None = None, tooltip: str = "") -> QWidget:
        item = QListWidgetItem(label)
        if icon_name:
            item.setIcon(icons.icon(icon_name, theme.current().text_muted, 17, 1.8))
        if tooltip:
            item.setToolTip(tooltip)
        item.setSizeHint(QSize(0, self._item_height))
        self.nav.addItem(item)
        self.stack.addWidget(widget)
        self._pages.append(widget)
        if self.nav.count() == 1:
            self.nav.setCurrentRow(0)
        self._resize_nav()
        return widget

    def current_index(self) -> int:
        return self.nav.currentRow()

    def set_current_index(self, index: int) -> None:
        if 0 <= index < self.nav.count():
            self.nav.setCurrentRow(index)


def configure_table(
    table: QTableWidget,
    headers: Sequence[str],
    *,
    stretch_column: int = 0,
    row_height: int = 30,
    select_rows: bool = True,
    sortable: bool = True,
    resize_modes: dict[int, QHeaderView.ResizeMode] | None = None,
) -> None:
    """Apply WTToolbox's standard table behaviour."""
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setSortingEnabled(sortable)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(
        QAbstractItemView.SelectRows if select_rows else QAbstractItemView.SelectItems
    )
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setFocusPolicy(Qt.StrongFocus)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setSectionsMovable(False)
    for column in range(len(headers)):
        header.setSectionResizeMode(column, QHeaderView.Interactive)
    header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    if resize_modes:
        for column, mode in resize_modes.items():
            header.setSectionResizeMode(column, mode)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)


def selectable_table(
    headers: Sequence[str],
    *,
    stretch_column: int = 0,
    row_height: int = 30,
    sortable: bool = True,
    resize_modes: dict[int, QHeaderView.ResizeMode] | None = None,
) -> QTableWidget:
    table = QTableWidget()
    table.setFrameShape(QFrame.NoFrame)
    configure_table(
        table, headers,
        stretch_column=stretch_column,
        row_height=row_height,
        sortable=sortable,
        resize_modes=resize_modes,
    )
    return table


def format_spin(*, minimum: float, maximum: float, step: float = 1.0, decimals: int = 0, value: float = 0.0):
    """A compact spin box with sensible width for the config editor."""
    if decimals > 0:
        box = QDoubleSpinBox()
        box.setDecimals(decimals)
    else:
        box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(value)
    box.setFixedWidth(112)
    return box


# --------------------------------------------------------------------------- #
#  Radar chart
# --------------------------------------------------------------------------- #
class RadarChart(QWidget):
    """A two-series radar/spider chart drawn from pre-normalised ratios.

    The caller supplies, per axis, a 0..1 ratio for each series (1.0 = the
    better of the two vehicles on that axis) plus the raw display text, so the
    chart itself never has to know what the numbers mean.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._axes: list = []
        self._names: tuple[str, str] = ("A", "B")
        self._colors: tuple[str, str] = (theme.current().accent, "#3B82F6")
        self._note = ""
        self._message = "选择两辆载具后显示对比雷达图"
        self._kind = "hint"
        self.setMinimumSize(360, 330)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ api
    def set_data(
        self,
        axes: list,
        names: tuple[str, str],
        *,
        colors: tuple[str, str] | None = None,
        note: str = "",
    ) -> None:
        """``axes`` items need ``label``, ``ratio_a``, ``ratio_b``, ``text_a``, ``text_b``."""
        self._axes = list(axes or [])
        self._names = names
        if colors:
            self._colors = colors
        else:
            palette = theme.current()
            self._colors = (palette.accent, palette.info)
        self._note = note
        self.setToolTip(self._tooltip())
        self.update()

    def clear(self) -> None:
        self._axes = []
        self._note = ""
        self._message = "选择两辆载具后显示对比雷达图"
        self._kind = "hint"
        self.update()

    def set_message(self, text: str, kind: str = "hint") -> None:
        """A whole-chart message (``hint`` / ``busy`` / ``blocked``).

        Used for the "still fetching" and "not comparable" states so the chart
        never silently keeps showing a stale comparison.
        """
        self._axes = []
        self._note = ""
        self._message = text
        self._kind = kind
        self.setToolTip("")
        self.update()

    def set_blocked(self, headline: str, detail: str = "") -> None:
        self.set_message(f"{headline}\n{detail}" if detail else headline, "blocked")

    def set_busy(self, text: str = "正在获取载具数据…") -> None:
        self.set_message(text, "busy")

    def _tooltip(self) -> str:
        if not self._axes:
            return ""
        lines = [f"{self._names[0]}  vs  {self._names[1]}"]
        for axis in self._axes:
            lines.append(f"{axis.label}：{axis.text_a}  /  {axis.text_b}")
            hint = getattr(axis, "hint", "")
            if hint:
                lines.append(f"    {hint}")
        return "\n".join(lines)

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if len(self._axes) < 3:
            colour = {
                "busy": palette.accent,
                "blocked": palette.error,
            }.get(self._kind, palette.text_faint)
            painter.setPen(QColor(colour))
            font = QFont(self.font())
            font.setPointSizeF(max(9.0, self.font().pointSizeF() + 0.5))
            painter.setFont(font)
            painter.drawText(
                # Anchored to the top: the chart card is tall, and a centred
                # message can sit below the fold right after a click.
                self.rect().adjusted(28, 22, -28, -22),
                int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap),
                self._message or "选择两辆载具后显示对比雷达图",
            )
            painter.end()
            return

        import math

        count = len(self._axes)
        width, height = self.width(), self.height()
        margin_x, margin_top, margin_bottom = 104, 46, 30
        center = QPoint(int(width / 2), int(margin_top + (height - margin_top - margin_bottom) / 2))
        radius = max(40.0, min((width - 2 * margin_x) / 2.0, (height - margin_top - margin_bottom) / 2.0))

        def point(index: int, ratio: float) -> QPoint:
            angle = -math.pi / 2 + index * 2 * math.pi / count
            return QPoint(
                int(center.x() + math.cos(angle) * radius * ratio),
                int(center.y() + math.sin(angle) * radius * ratio),
            )

        # --- grid rings -----------------------------------------------------
        grid_pen = QPen(QColor(palette.border), 1)
        for ring in (0.25, 0.5, 0.75, 1.0):
            polygon = QPolygon([point(i, ring) for i in range(count)])
            painter.setPen(QPen(QColor(palette.border if ring < 1.0 else palette.border_strong), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(polygon)
            del polygon
        painter.setPen(grid_pen)
        for index in range(count):
            painter.setPen(QPen(QColor(palette.border_strong), 1))
            painter.drawLine(center, point(index, 1.0))

        # --- series ---------------------------------------------------------
        for series, color_hex, key_ratio, key_text in (
            (0, self._colors[0], "ratio_a", "text_a"),
            (1, self._colors[1], "ratio_b", "text_b"),
        ):
            colour = QColor(color_hex)
            polygon = QPolygon([point(i, float(getattr(axis, key_ratio))) for i, axis in enumerate(self._axes)])
            fill = QColor(colour)
            fill.setAlpha(58)
            painter.setBrush(fill)
            painter.setPen(QPen(colour, 2))
            painter.drawPolygon(polygon)
            painter.setBrush(colour)
            painter.setPen(Qt.NoPen)
            for i, axis in enumerate(self._axes):
                ratio = float(getattr(axis, key_ratio))
                painter.drawEllipse(point(i, ratio), 4, 4)

        # --- axis labels + values ------------------------------------------
        label_font = QFont(self.font())
        label_font.setPointSizeF(max(8.0, self.font().pointSizeF() - 0.5))
        label_font.setBold(True)
        value_font = QFont(self.font())
        value_font.setPointSizeF(max(7.5, self.font().pointSizeF() - 1.5))
        metrics_bold = QFontMetrics(label_font)
        metrics_value = QFontMetrics(value_font)

        for index, axis in enumerate(self._axes):
            outer = point(index, 1.0)
            angle = -math.pi / 2 + index * 2 * math.pi / count
            dx, dy = math.cos(angle), math.sin(angle)
            anchor_x = outer.x() + dx * 20
            anchor_y = outer.y() + dy * 16

            if abs(dx) < 0.25:
                flags = int(Qt.AlignHCenter | Qt.AlignVCenter)
            elif dx > 0:
                flags = int(Qt.AlignLeft | Qt.AlignVCenter)
            else:
                flags = int(Qt.AlignRight | Qt.AlignVCenter)

            label_w = metrics_bold.horizontalAdvance(axis.label) + 14
            label_h = 16
            if flags & int(Qt.AlignHCenter):
                rect = QRectF(anchor_x - label_w / 2, anchor_y - label_h, label_w, label_h)
            elif dx > 0:
                rect = QRectF(anchor_x, anchor_y - label_h, label_w, label_h)
            else:
                rect = QRectF(anchor_x - label_w, anchor_y - label_h, label_w, label_h)

            painter.setFont(label_font)
            painter.setPen(QColor(palette.text))
            painter.drawText(rect, flags, axis.label)

            # value line: "A / B" with each value tinted by its series colour
            value_y = rect.bottom() + 1
            value_text_a = f"{getattr(axis, 'text_a')}"
            value_text_b = f"{getattr(axis, 'text_b')}"
            if len(value_text_a) > 16:
                value_text_a = value_text_a[:15] + "…"
            if len(value_text_b) > 16:
                value_text_b = value_text_b[:15] + "…"
            wa = metrics_value.horizontalAdvance(value_text_a)
            wsep = metrics_value.horizontalAdvance(" / ")
            wb = metrics_value.horizontalAdvance(value_text_b)
            total = wa + wsep + wb
            if flags & int(Qt.AlignHCenter):
                start_x = anchor_x - total / 2
            elif dx > 0:
                start_x = anchor_x
            else:
                start_x = anchor_x - total

            painter.setFont(value_font)
            painter.setPen(QColor(self._colors[0]))
            painter.drawText(QRectF(start_x, value_y, wa, 14), int(Qt.AlignLeft | Qt.AlignVCenter), value_text_a)
            painter.setPen(QColor(palette.text_faint))
            painter.drawText(QRectF(start_x + wa, value_y, wsep, 14), int(Qt.AlignLeft | Qt.AlignVCenter), " / ")
            painter.setPen(QColor(self._colors[1]))
            painter.drawText(
                QRectF(start_x + wa + wsep, value_y, wb, 14),
                int(Qt.AlignLeft | Qt.AlignVCenter),
                value_text_b,
            )

        # --- legend ---------------------------------------------------------
        legend_font = QFont(self.font())
        legend_font.setBold(True)
        painter.setFont(legend_font)
        y = 16
        x = 14
        for name, colour_hex in zip(self._names, self._colors):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colour_hex))
            painter.drawEllipse(QRectF(x, y + 2, 9, 9))
            painter.setPen(QColor(palette.text))
            painter.drawText(QRectF(x + 14, y - 2, 240, 18), int(Qt.AlignLeft | Qt.AlignVCenter), name)
            x += 14 + painter.fontMetrics().horizontalAdvance(name) + 22

        if self._note:
            painter.setFont(value_font)
            painter.setPen(QColor(palette.text_faint))
            painter.drawText(
                QRectF(14, height - 20, width - 28, 16),
                int(Qt.AlignLeft | Qt.AlignVCenter),
                self._note,
            )
        painter.end()
