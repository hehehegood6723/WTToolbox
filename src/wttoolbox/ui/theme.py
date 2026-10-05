"""Application palette and style sheet.

A single :class:`Palette` drives a hand written Qt style sheet, so light and
dark modes stay in sync and every control shares one visual language.

Conventions used by the style sheet
-----------------------------------
``QFrame#Card``        a rounded surface panel
``#CardTitle``         panel heading
``#H1`` ``#H2``        page headings
``#Muted`` ``#Faint``  secondary / tertiary text
``QPushButton#Primary``
``QPushButton#Ghost``
``QPushButton#Subtle``
``QPushButton#Danger``
``QPushButton#Link``
``QToolButton#IconBtn``
``QToolButton#Nav``    checkable title-bar navigation pill
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from . import icons

__all__ = [
    "Palette",
    "LIGHT",
    "DARK",
    "palette_for",
    "apply_theme",
    "restyle",
    "UI_FONT",
    "MONO_FONT",
    "banner_path",
    "icon_path",
]

UI_FONT = "Microsoft YaHei UI"
UI_FONT_FALLBACKS = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable Text", "Segoe UI")

#: Corner radius of the frameless window body, in logical pixels.
WINDOW_RADIUS = 14
#: Transparent margin kept around the body so the soft shadow has room to fade.
WINDOW_SHADOW = 10
MONO_FONT = "Consolas"


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    bg_alt: str
    surface: str
    surface_2: str
    surface_3: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_press: str
    accent_soft: str
    on_accent: str
    success: str
    success_soft: str
    warn: str
    warn_soft: str
    error: str
    error_soft: str
    info: str
    info_soft: str
    log_bg: str
    log_text: str
    shadow: str
    scrollbar: str
    scrollbar_hover: str
    nav_active_bg: str
    nav_active_fg: str
    is_dark: bool = False

    @property
    def titlebar(self) -> str:
        return self.surface if not self.is_dark else self.surface

    def with_accent(self, accent: str) -> "Palette":
        from dataclasses import replace

        return replace(self, accent=accent, accent_hover=_lighten(accent, 0.10),
                       accent_press=_darken(accent, 0.08))


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _shift(hex_color: str, amount: float) -> str:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return hex_color
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    if amount >= 0:
        rgb = [c + (255 - c) * amount for c in rgb]
    else:
        rgb = [c * (1 + amount) for c in rgb]
    return "#{:02X}{:02X}{:02X}".format(*(_clamp(c) for c in rgb))


def _lighten(hex_color: str, amount: float) -> str:
    return _shift(hex_color, abs(amount))


def _darken(hex_color: str, amount: float) -> str:
    return _shift(hex_color, -abs(amount))


LIGHT = Palette(
    name="light",
    bg="#EEF1F5",
    bg_alt="#E7EBF1",
    surface="#FFFFFF",
    surface_2="#F5F7FA",
    surface_3="#EDF0F5",
    border="#E3E7EE",
    border_strong="#CFD6E1",
    text="#1B2130",
    text_muted="#6F7A8C",
    text_faint="#9AA4B4",
    accent="#FF9E1B",
    accent_hover="#FFB03F",
    accent_press="#EE8A00",
    accent_soft="#FFF3E0",
    on_accent="#FFFFFF",
    success="#16A34A",
    success_soft="#E7F7EE",
    warn="#D97706",
    warn_soft="#FEF3E2",
    error="#DC2626",
    error_soft="#FDECEC",
    info="#2563EB",
    info_soft="#E8F0FE",
    log_bg="#F7F9FC",
    log_text="#2A3344",
    shadow="rgba(15, 23, 42, 0.07)",
    scrollbar="#C9D0DA",
    scrollbar_hover="#AEB7C4",
    nav_active_bg="#FFF1DC",
    nav_active_fg="#C2700A",
    is_dark=False,
)

DARK = Palette(
    name="dark",
    bg="#0F1319",
    bg_alt="#0B0E13",
    surface="#171C24",
    surface_2="#1E242E",
    surface_3="#242B37",
    border="#272E3A",
    border_strong="#364051",
    text="#E7EBF2",
    text_muted="#8E98A9",
    text_faint="#6B7688",
    accent="#FF9E1B",
    accent_hover="#FFB03F",
    accent_press="#E88A05",
    accent_soft="#33260F",
    on_accent="#1A1204",
    success="#34D399",
    success_soft="#123024",
    warn="#FBBF24",
    warn_soft="#332711",
    error="#F87171",
    error_soft="#341A1A",
    info="#60A5FA",
    info_soft="#152438",
    log_bg="#12161D",
    log_text="#C6CDDA",
    shadow="rgba(0, 0, 0, 0.35)",
    scrollbar="#333C4A",
    scrollbar_hover="#445064",
    nav_active_bg="#33260F",
    nav_active_fg="#FFBE5C",
    is_dark=True,
)


def palette_for(name: str) -> Palette:
    return DARK if str(name).lower() == "dark" else LIGHT


_current: Palette = LIGHT


def set_current(palette: Palette) -> None:
    global _current
    _current = palette


def current() -> Palette:
    """The palette in force right now (used by custom-painted widgets)."""
    return _current


def refresh_widgets(root: QWidget) -> None:
    """Force a repaint of *root* and every custom-painted descendant."""
    try:
        for child in root.findChildren(QWidget):
            child.update()
        root.update()
    except Exception:
        pass


def icon_path(name: str) -> str:
    if name == "banner":
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "banner.png")
    if name == "icon":
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", name)


def banner_path() -> str:
    """Path to the hero banner, resolved for both source and frozen builds."""
    from ..core import appdirs

    return appdirs.resource_path("assets", "banner.png")


def icon_file() -> str:
    from ..core import appdirs

    return appdirs.resource_path("assets", "icon.ico")


# --------------------------------------------------------------------------- #
#  Style sheet
# --------------------------------------------------------------------------- #
def build_qss(p: Palette, assets: dict[str, str]) -> str:
    check = assets.get("check", "")
    dash = assets.get("dash", "")
    radio = assets.get("radio", "")
    down = assets.get("chevron-down", "")
    up = assets.get("chevron-up", "")

    return f"""
* {{
    outline: none;
}}

QWidget {{
    color: {p.text};
    font-family: "{UI_FONT}";
}}

QWidget#Root {{
    background: {p.bg};
}}

/* The window is translucent and paints its own rounded body (see
   MainWindow.paintEvent); the title bar and status strip must round their own
   corners to match, and go square while maximised. */
QWidget#MainWindow {{
    background: transparent;
    border: none;
}}
QWidget#TitleBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
    border-top-left-radius: {WINDOW_RADIUS}px;
    border-top-right-radius: {WINDOW_RADIUS}px;
}}
QWidget#TitleBar[tkFlat="true"] {{
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
}}
QStackedWidget#PageStack {{
    background: {p.bg};
    border: none;
}}
QWidget#ScrollContent {{
    background: transparent;
}}

/* ------------------------------------------------------- clickable list rows */
QFrame#NewsRow, QFrame#ListRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
}}
QFrame#NewsRow:hover, QFrame#ListRow:hover {{
    background: {p.surface_2};
    border-color: {p.border};
}}
QFrame#NewsRow[pressed="true"], QFrame#ListRow[pressed="true"] {{
    background: {p.surface_3};
}}
QLabel#RowTitle {{ font-size: 13px; font-weight: 600; color: {p.text}; }}
QLabel#RowMeta {{ font-size: 11px; color: {p.text_faint}; }}
QLabel#RowValue {{ font-size: 12px; color: {p.text_muted}; }}

QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 6px 9px;
}}

/* ---------------------------------------------------------------- headings */
QLabel#H1 {{ font-size: 19px; font-weight: 700; color: {p.text}; }}
QLabel#H2 {{ font-size: 15px; font-weight: 700; color: {p.text}; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 700; color: {p.text}; }}
QLabel#CardSubtitle {{ font-size: 12px; color: {p.text_muted}; }}
QLabel#Muted {{ color: {p.text_muted}; font-size: 12px; }}
QLabel#Faint {{ color: {p.text_faint}; font-size: 12px; }}
QLabel#Value {{ font-size: 20px; font-weight: 700; color: {p.text}; }}
QLabel#SectionHint {{ color: {p.text_faint}; font-size: 11px; }}

/* ------------------------------------------------------------------- cards */
QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 14px;
}}
QFrame#SubCard {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 11px;
}}
QFrame#Divider {{
    background: {p.border};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#VDivider {{
    background: {p.border};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}

/* ------------------------------------------------------------------ badges */
QLabel#Badge {{
    border-radius: 7px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 700;
}}

/* --------------------------------------------------------------- title bar */
QWidget#TitleBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
QLabel#BrandName {{ font-size: 14px; font-weight: 700; color: {p.text}; }}
QLabel#BrandSub {{ font-size: 10px; color: {p.text_faint}; }}

QToolButton#Nav {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 6px 13px;
    color: {p.text_muted};
    font-size: 13px;
    font-weight: 600;
}}
QToolButton#Nav:hover {{
    background: {p.surface_2};
    color: {p.text};
}}
QToolButton#Nav:checked {{
    background: {p.nav_active_bg};
    color: {p.nav_active_fg};
}}

QToolButton#WinBtn {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 5px;
}}
QToolButton#WinBtn:hover {{ background: {p.surface_3}; }}
QToolButton#WinBtnClose:hover {{ background: {p.error}; }}

QToolButton#IconBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 5px;
}}
QToolButton#IconBtn:hover {{
    background: {p.surface_2};
    border-color: {p.border};
}}

/* ----------------------------------------------------------------- buttons */
QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: 10px;
    padding: 7px 15px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {p.surface_2}; }}
QPushButton:pressed {{ background: {p.surface_3}; }}
QPushButton:disabled {{ color: {p.text_faint}; border-color: {p.border}; background: {p.surface_2}; }}

QPushButton#Primary {{
    background: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    font-weight: 700;
    padding: 8px 20px;
}}
QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_press}; border-color: {p.accent_press}; }}
QPushButton#Primary:disabled {{ background: {p.surface_3}; color: {p.text_faint}; border-color: {p.border}; }}

QPushButton#Subtle {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    color: {p.text};
}}
QPushButton#Subtle:hover {{ background: {p.surface_3}; }}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {p.border_strong};
    color: {p.text_muted};
}}
QPushButton#Ghost:hover {{ background: {p.surface_2}; color: {p.text}; }}

QPushButton#Danger {{
    background: {p.error};
    border: 1px solid {p.error};
    color: #FFFFFF;
    font-weight: 700;
}}
QPushButton#Danger:hover {{ background: {_lighten(p.error, 0.12)}; }}
QPushButton#Danger:disabled {{
    background: {p.surface_3};
    border-color: {p.border};
    color: {p.text_faint};
}}

QPushButton#Link {{
    background: transparent;
    border: none;
    color: {p.accent};
    font-weight: 600;
    padding: 3px 6px;
    text-align: left;
}}
QPushButton#Link:hover {{ color: {p.accent_hover}; text-decoration: underline; }}

QPushButton#Segment {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 6px 14px;
    color: {p.text_muted};
    font-weight: 600;
}}
QPushButton#Segment:checked {{
    background: {p.nav_active_bg};
    border-color: {p.accent};
    color: {p.nav_active_fg};
}}
QPushButton#Segment:hover:!checked {{ background: {p.surface_3}; color: {p.text}; }}

/* --------------------------------------------------------------- text input */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 10px;
    padding: 7px 11px;
    font-size: 13px;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled {{ background: {p.surface_2}; color: {p.text_faint}; }}
QLineEdit#Search {{
    padding-left: 30px;
    background: {p.surface_2};
    border-color: {p.border};
}}

QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 18px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}

/* -------------------------------------------------------------- checkboxes */
QCheckBox, QRadioButton {{ spacing: 8px; font-size: 13px; color: {p.text}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px; height: 17px;
    border: 1.5px solid {p.border_strong};
    background: {p.surface};
}}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{check}");
}}
QCheckBox::indicator:indeterminate {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{dash}");
}}
QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{radio}");
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {p.surface_3};
    border-color: {p.border};
}}

/* ------------------------------------------------------------------ tables */
QTableWidget, QTableView {{
    background: {p.surface};
    alternate-background-color: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 11px;
    gridline-color: transparent;
    font-size: 12px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p.surface_2};
    color: {p.text_muted};
    padding: 8px 9px;
    border: none;
    border-bottom: 1px solid {p.border};
    font-weight: 700;
    font-size: 12px;
}}
QHeaderView::section:first {{ border-top-left-radius: 11px; }}
QHeaderView::section:last {{ border-top-right-radius: 11px; }}
/* Qt draws the sort indicator above the header text under a styled header,
   where it reads as a stray clipped glyph.  Hide it; the tooltip explains that
   clicking a column still sorts. */
QHeaderView::up-arrow, QHeaderView::down-arrow {{
    image: none;
    width: 0;
    height: 0;
}}
QTableCornerButton::section {{ background: {p.surface_2}; border: none; }}

QTableWidget::indicator, QTableView::indicator {{
    width: 16px; height: 16px;
    border: 1.5px solid {p.border_strong};
    border-radius: 5px;
    background: {p.surface};
}}
QTableWidget::indicator:hover, QTableView::indicator:hover {{ border-color: {p.accent}; }}
QTableWidget::indicator:checked, QTableView::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{check}");
}}
QTableWidget::indicator:indeterminate, QTableView::indicator:indeterminate {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{dash}");
}}
QTableWidget::indicator:disabled, QTableView::indicator:disabled {{
    background: {p.surface_3};
    border-color: {p.border};
}}

QListWidget, QListView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 11px;
    font-size: 13px;
    outline: none;
    padding: 4px;
}}
QListWidget::item {{
    border-radius: 9px;
    padding: 7px 9px;
    color: {p.text};
}}
QListWidget::item:hover {{ background: {p.surface_2}; }}
QListWidget::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QListWidget#Plain {{ border: none; background: transparent; padding: 0; }}

/* --------------------------------------------------------------- text areas */
QPlainTextEdit, QTextEdit {{
    background: {p.log_bg};
    border: 1px solid {p.border};
    border-radius: 11px;
    padding: 8px 10px;
    color: {p.log_text};
    font-family: "{MONO_FONT}", "{UI_FONT}";
    font-size: 12px;
    selection-background-color: {p.accent};
}}
QPlainTextEdit#ReadOnly {{ background: {p.log_bg}; }}

/* -------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.scrollbar};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.scrollbar_hover}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.scrollbar};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.scrollbar_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* -------------------------------------------------------------- progressbar */
QProgressBar {{
    background: {p.surface_3};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}

/* ------------------------------------------------------------------- menus */
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 12px;
    border-radius: 7px;
    font-size: 12px;
    color: {p.text};
}}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

/* ---------------------------------------------------------------- splitter */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}

/* ----------------------------------------------------------------- dialogs */
QDialog {{ background: {p.bg}; }}
QFrame#DialogHeader {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}

/* ------------------------------------------------------------------- misc */
QFrame#StatusStrip {{
    background: {p.surface};
    border-top: 1px solid {p.border};
    border-bottom-left-radius: {WINDOW_RADIUS}px;
    border-bottom-right-radius: {WINDOW_RADIUS}px;
}}
QFrame#StatusStrip[tkFlat="true"] {{
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
}}
QFrame#HeroFrame {{
    border: 1px solid {p.border};
    border-radius: 14px;
    background: {p.surface};
}}
QWidget#ToastFrame {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 12px;
}}
QLabel#ToastText {{ font-size: 12px; font-weight: 600; color: {p.text}; }}
QWidget#SideNav {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 13px;
}}
QListWidget#SideNavList {{
    background: transparent; border: none; padding: 6px;
}}
QListWidget#SideNavList::item {{
    padding: 9px 11px; border-radius: 9px; color: {p.text_muted}; font-size: 13px; font-weight: 600;
}}
QListWidget#SideNavList::item:hover {{ background: {p.surface_2}; color: {p.text}; }}
QListWidget#SideNavList::item:selected {{
    background: {p.nav_active_bg}; color: {p.nav_active_fg};
}}
QWidget#ThumbGrid {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
}}
"""


@lru_cache(maxsize=1)
def _resolve_font() -> str:
    """Pick the first available UI font.  Cached: QFontDatabase is slow."""
    try:
        families = set(QFontDatabase.families())
    except Exception:
        return UI_FONT
    for candidate in UI_FONT_FALLBACKS:
        if candidate in families:
            return candidate
    return UI_FONT


def apply_theme(app: QApplication, palette: Palette, *, font_scale: float = 1.0) -> dict:
    """Apply *palette* to the whole application.  Returns generated asset paths."""
    family = _resolve_font()
    base = QFont(family)
    point = max(8.0, min(16.0, 9.9 * float(font_scale or 1.0)))
    base.setPointSizeF(point)
    base.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(base)

    assets = icons.write_qss_assets(palette.accent, palette.on_accent)
    set_current(palette)
    app.setStyleSheet(build_qss(palette, assets))
    app.setProperty("tk_palette_name", palette.name)
    return assets


def restyle(widget: QWidget) -> None:
    """Re-evaluate style sheet selectors after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
