"""Vector icon set.

Icons are defined as SVG fragments on a 24x24 grid and rasterised through
``QSvgRenderer``.  Keeping them here (rather than shipping icon files) means the
colour can follow the active theme exactly and the bundle stays small.

Two small SVG assets are also written to disk at runtime because Qt style
sheets can only reference images by URL - see :func:`write_qss_assets`.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..core import appdirs

__all__ = ["icon", "icon_pixmap", "ICONS", "logo_mark", "write_qss_assets", "available"]


def _gear(cx: float = 12.0, cy: float = 12.0, r_out: float = 9.2, r_in: float = 6.6,
          teeth: int = 8, width: float = 0.42) -> str:
    """Build a gear silhouette as an SVG polygon."""
    points: list[tuple[float, float]] = []
    step = 2 * math.pi / teeth
    half = step * width * 0.5
    for index in range(teeth):
        angle = index * step - math.pi / 2
        for radius, offset in (
            (r_in, 0.0),
            (r_out, half),
            (r_out, step - half),
            (r_in, step),
        ):
            a = angle + offset
            points.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


_GEAR = _gear()

# --------------------------------------------------------------------------- #
#  Definitions.  ``{color}`` is substituted at render time.
# --------------------------------------------------------------------------- #
ICONS: dict[str, str] = {
    "home": '<path d="M3.6 11.2 12 4.2l8.4 7"/><path d="M5.9 9.9v9.6h12.2V9.9"/>',
    "sound": (
        '<path d="M4 9.6h3.1L11.9 5.6v12.8L7.1 14.4H4z"/>'
        '<path d="M15.3 9.3a3.9 3.9 0 0 1 0 5.4"/>'
        '<path d="M17.7 6.9a7.3 7.3 0 0 1 0 10.2"/>'
    ),
    "grid": (
        '<rect x="3.8" y="3.8" width="7.2" height="7.2" rx="1.8"/>'
        '<rect x="13" y="3.8" width="7.2" height="7.2" rx="1.8"/>'
        '<rect x="3.8" y="13" width="7.2" height="7.2" rx="1.8"/>'
        '<rect x="13" y="13" width="7.2" height="7.2" rx="1.8"/>'
    ),
    "layers": (
        '<path d="M12 3.6 3.9 7.6 12 11.6l8.1-4z"/>'
        '<path d="M3.9 12.1 12 16.1l8.1-4"/>'
        '<path d="M3.9 16.4 12 20.4l8.1-4"/>'
    ),
    "gear": (
        f'<polygon points="{_GEAR}"/>'
        '<circle cx="12" cy="12" r="3.1"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2.8v2.3M12 18.9v2.3M2.8 12h2.3M18.9 12h2.3"/>'
        '<path d="M5.5 5.5 7.1 7.1M16.9 16.9l1.6 1.6M18.5 5.5 16.9 7.1M7.1 16.9l-1.6 1.6"/>'
    ),
    "moon": '<path d="M20.2 14.8A8.6 8.6 0 0 1 9.4 3.9a8.6 8.6 0 1 0 10.8 10.9z"/>',
    "min": '<path d="M5.5 12h13"/>',
    "max": '<rect x="5.6" y="5.6" width="12.8" height="12.8" rx="2"/>',
    "restore": (
        '<rect x="4.4" y="8" width="11.6" height="11.6" rx="2"/>'
        '<path d="M8.6 4.4h8.8a2.2 2.2 0 0 1 2.2 2.2v8.8"/>'
    ),
    "close": '<path d="M6.2 6.2 17.8 17.8"/><path d="M17.8 6.2 6.2 17.8"/>',
    "folder": (
        '<path d="M3.6 6.9a2 2 0 0 1 2-2h3.3l2 2.4h7.5a2 2 0 0 1 2 2v8.1a2 2 0 0 1-2 2H5.6a2 2 0 0 1-2-2z"/>'
    ),
    "folder-open": (
        '<path d="M3.6 6.9a2 2 0 0 1 2-2h3.3l2 2.4h7.5a2 2 0 0 1 2 2v1.2"/>'
        '<path d="M3.6 9.9h16.9l-2 9.1a2 2 0 0 1-2 1.6H5.6a2 2 0 0 1-2-2z"/>'
    ),
    "search": '<circle cx="10.6" cy="10.6" r="6.4"/><path d="M15.4 15.4 20.4 20.4"/>',
    "play": '<path d="M8.2 5.4 18.6 12 8.2 18.6z" fill="{color}" stroke="none"/>',
    "stop": '<rect x="6.4" y="6.4" width="11.2" height="11.2" rx="2" fill="{color}" stroke="none"/>',
    "trash": (
        '<path d="M4.6 7.1h14.8"/>'
        '<path d="M9.6 7.1V5.4a1.3 1.3 0 0 1 1.3-1.3h2.2a1.3 1.3 0 0 1 1.3 1.3v1.7"/>'
        '<path d="M6.6 7.1l.9 11.4a1.7 1.7 0 0 0 1.7 1.6h5.6a1.7 1.7 0 0 0 1.7-1.6l.9-11.4"/>'
    ),
    "refresh": '<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20.4 4.4v5.5h-5.5"/>',
    "chev-right": '<path d="M9.6 6.2 15.4 12l-5.8 5.8"/>',
    "chev-down": '<path d="M6.2 9.6 12 15.4l5.8-5.8"/>',
    "chev-up": '<path d="M6.2 14.4 12 8.6l5.8 5.8"/>',
    "check": '<path d="M5.2 12.6 9.7 17 18.8 7.4"/>',
    "external": (
        '<path d="M14.2 4.6h5.2v5.2"/><path d="M19.4 4.6 11.2 12.8"/>'
        '<path d="M17.6 13.6v4.6a2.2 2.2 0 0 1-2.2 2.2H5.8a2.2 2.2 0 0 1-2.2-2.2V8.6a2.2 2.2 0 0 1 2.2-2.2h4.6"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="8.4"/><path d="M12 6.9v5.4l3.5 2.1"/>',
    "image": (
        '<rect x="3.6" y="5" width="16.8" height="14" rx="2"/>'
        '<circle cx="8.6" cy="9.8" r="1.5"/>'
        '<path d="M4.4 17.4 9 12.9l3.3 3 2.9-2.5 5 4.1"/>'
    ),
    "replay": (
        '<rect x="3.6" y="5" width="16.8" height="14" rx="2"/>'
        '<path d="M9.9 9.4 15.2 12l-5.3 2.6z" fill="{color}" stroke="none"/>'
    ),
    "paint": (
        '<path d="M12 3.6c-4.8 0-8.4 3-8.4 6.8 0 3.4 2.6 5.3 5 5.3 1.3 0 2.1.7 2.1 1.7 0 .9-.7 1.2-.7 2.2 0 .9.7 1.6 1.9 1.6 4.6 0 8.5-3.6 8.5-8.6 0-4.3-3.6-9-8.4-9z"/>'
        '<circle cx="8.2" cy="11" r="1.1"/><circle cx="11.6" cy="8.2" r="1.1"/>'
        '<circle cx="15.6" cy="10" r="1.1"/>'
    ),
    "crosshair": (
        '<circle cx="12" cy="12" r="7.4"/>'
        '<path d="M12 2.6v5M12 16.4v5M2.6 12h5M16.4 12h5"/>'
    ),
    "package": (
        '<path d="M12 3.7 20.3 8v8L12 20.3 3.7 16V8z"/>'
        '<path d="M3.7 8 12 12.3 20.3 8"/><path d="M12 12.3v8"/>'
    ),
    "sparkle": (
        '<path d="M10.4 3.6 12 8.2l4.6 1.6-4.6 1.6-1.6 4.6-1.6-4.6L4.2 9.8l4.6-1.6z"/>'
        '<path d="M18 14.4l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>'
    ),
    "shield": (
        '<path d="M12 3.5 5.1 6.2v5.4c0 4.2 2.8 7.6 6.9 8.9 4.1-1.3 6.9-4.7 6.9-8.9V6.2z"/>'
        '<path d="M9.1 12.1 11.3 14.3 15.1 10.1"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="8.4"/>'
        '<path d="M12 11.2v5.4"/><path d="M12 7.9v.02" stroke-width="2.4"/>'
    ),
    "warning": (
        '<path d="M12 4.3 20.8 19.4H3.2z"/>'
        '<path d="M12 10v4.3"/><path d="M12 17.2v.02" stroke-width="2.4"/>'
    ),
    "x-circle": (
        '<circle cx="12" cy="12" r="8.4"/>'
        '<path d="M9.2 9.2 14.8 14.8M14.8 9.2 9.2 14.8"/>'
    ),
    "check-circle": (
        '<circle cx="12" cy="12" r="8.4"/><path d="M8.1 12.3 10.9 15.1 16 9.6"/>'
    ),
    "monitor": (
        '<rect x="3.2" y="4.6" width="17.6" height="12.4" rx="2"/>'
        '<path d="M8.4 20.4h7.2M12 17v3.4"/>'
    ),
    "cpu": (
        '<rect x="7.4" y="7.4" width="9.2" height="9.2" rx="1.8"/>'
        '<path d="M10 3.4v4M14 3.4v4M10 16.6v4M14 16.6v4M3.4 10h4M3.4 14h4M16.6 10h4M16.6 14h4"/>'
    ),
    "drive": (
        '<path d="M3.6 13.4 6.1 6.9A2 2 0 0 1 8 5.6h8a2 2 0 0 1 1.9 1.3l2.5 6.5v3.6a2 2 0 0 1-2 2H5.6a2 2 0 0 1-2-2z"/>'
        '<path d="M3.6 13.4h16.8"/><circle cx="8" cy="16" r="1"/>'
    ),
    "activity": '<path d="M3.4 12h3.2l2.4-6.2 3.6 12.4 2.4-6.2h5.6"/>',
    "link": (
        '<path d="M10.2 13.8a3.7 3.7 0 0 0 5.2 0l2.8-2.8a3.7 3.7 0 0 0-5.2-5.2l-1.2 1.2"/>'
        '<path d="M13.8 10.2a3.7 3.7 0 0 0-5.2 0l-2.8 2.8a3.7 3.7 0 0 0 5.2 5.2l1.2-1.2"/>'
    ),
    "pin": '<path d="M9.1 4.2h5.8l-.8 4.1 2.8 2.6H7.1l2.8-2.6z"/><path d="M12 10.9v8.9"/>',
    "star": '<path d="M12 4.1 14.4 9.2 20 9.9l-4.1 3.9 1 5.6L12 16.8l-4.9 2.6 1-5.6L4 9.9l5.6-.7z"/>',
    "eye": (
        '<path d="M2.9 12S6.6 6.1 12 6.1 21.1 12 21.1 12 17.4 17.9 12 17.9 2.9 12 2.9 12z"/>'
        '<circle cx="12" cy="12" r="2.9"/>'
    ),
    "download": '<path d="M12 4v10.6"/><path d="M8.1 11 12 14.9 15.9 11"/><path d="M4.6 19.4h14.8"/>',
    "upload": '<path d="M12 20V9.4"/><path d="M8.1 13 12 9.1 15.9 13"/><path d="M4.6 4.6h14.8"/>',
    "plus": '<path d="M12 5.4v13.2M5.4 12h13.2"/>',
    "minus": '<path d="M5.4 12h13.2"/>',
    "save": (
        '<path d="M5.4 4.6h9.4l4.8 4.8v10H5.4z"/>'
        '<path d="M8.6 4.6v5h6v-5"/><rect x="8.4" y="13" width="7.2" height="3.6" rx="1"/>'
    ),
    "undo": '<path d="M9.1 8.4H4.6V3.9"/><path d="M4.9 8.4a8 8 0 1 1 1.7 8.6"/>',
    "filter": '<path d="M4.2 5.8h15.6l-6.1 7.3v5.5l-3.4-1.7v-3.8z"/>',
    "sort": (
        '<path d="M7.2 5.2v13.6"/><path d="M4.2 15.6 7.2 18.8 10.2 15.6"/>'
        '<path d="M16.8 18.8V5.2"/><path d="M13.8 8.4 16.8 5.2 19.8 8.4"/>'
    ),
    "question": (
        '<circle cx="12" cy="12" r="8.4"/>'
        '<path d="M9.7 9.6a2.4 2.4 0 1 1 3.3 2.2c-.7.3-1 .9-1 1.6v.5"/>'
        '<path d="M12 17v.02" stroke-width="2.4"/>'
    ),
    "file": (
        '<path d="M6.2 3.9h7l5.6 5.6v10.6H6.2z"/>'
        '<path d="M13.1 3.9v5.7h5.7"/>'
    ),
    "text": (
        '<path d="M5.2 6.4h13.6"/><path d="M12 6.4v11.2"/>'
        '<path d="M9 17.6h6"/>'
    ),
    "lock": (
        '<rect x="4.8" y="10.4" width="14.4" height="9.2" rx="2.2"/>'
        '<path d="M8.2 10.4V7.9a3.8 3.8 0 0 1 7.6 0v2.5"/>'
    ),
    "lightning": (
        '<path d="M13.6 3.2 6.4 13.4h4.6l-1.6 7.4 7.4-10.6h-4.6z" '
        'fill="{color}" stroke="none"/>'
    ),
    "wing": (
        '<polygon points="12,3.4 14.9,10.2 21,12.6 14.9,15 12,21.2 9.1,15 3,12.6 9.1,10.2" '
        'fill="{color}" stroke="none"/>'
    ),
}


def available() -> list[str]:
    return sorted(ICONS)


def _wrap(name: str, color: str, stroke: float) -> bytes:
    body = ICONS.get(name)
    if body is None:
        body = ICONS["question"]
    body = body.replace("{color}", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    return svg.encode("utf-8")


def _screen_dpr() -> float:
    """Device pixel ratio of the primary screen (1.0 when unavailable)."""
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


@lru_cache(maxsize=1024)
def icon_pixmap(name: str, color: str, size: int = 20, stroke: float = 1.7,
                dpr: float = 0.0) -> QPixmap:
    """Rasterise an icon at *size* logical pixels, sharp at the given DPR.

    Rendering the SVG at ``size`` physical pixels and letting Qt upscale it on a
    200 % display is what made the icons look soft.  The bitmap is therefore
    rendered at ``size * dpr`` physical pixels and tagged with
    ``setDevicePixelRatio`` so Qt draws it at exactly ``size`` logical pixels.
    """
    ratio = float(dpr) if dpr and dpr > 0 else _screen_dpr()
    physical = max(1, int(round(size * ratio)))
    renderer = QSvgRenderer(QByteArray(_wrap(name, color, stroke)))
    pixmap = QPixmap(physical, physical)
    pixmap.fill(Qt.transparent)
    # Paint in *device* coordinates (the pixmap is still dpr 1 here), then tag
    # the ratio afterwards.  Tagging it first scales QPainter's coordinate
    # system, so drawing into (0, 0, physical, physical) would overflow the
    # bitmap and only the top-left corner of the glyph would survive.
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#1B2130", size: int = 20, stroke: float = 1.7) -> QIcon:
    """An icon carrying 1x/2x/3x renditions so Qt picks the crisp one per screen."""
    result = QIcon()
    for ratio in (1.0, 2.0, 3.0):
        result.addPixmap(icon_pixmap(name, color, size, stroke, ratio))
    return result


def logo_mark(color: str = "#FF9E1B", size: int = 26, dpr: float = 0.0) -> QPixmap:
    """The WTToolbox mark: a bolt whose upper arms sweep out like a wing."""
    body = (
        '<path d="M12 2.6 19.6 8.1 16.2 9.9 12 7.2 7.8 9.9 4.4 8.1z" fill="{c}"/>'
        '<path d="M12 9.1 15.6 12 12 21.4 8.4 12z" fill="{c}"/>'
    ).replace("{c}", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    ).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    ratio = float(dpr) if dpr and dpr > 0 else _screen_dpr()
    physical = max(1, int(round(size * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def write_qss_assets(accent: str, on_accent: str = "#FFFFFF") -> dict[str, str]:
    """Write the tiny SVGs that Qt style sheets can reference by URL.

    Returns a mapping of logical name -> absolute file path.
    """
    folder = os.path.join(appdirs.cache_dir(), "qss")
    os.makedirs(folder, exist_ok=True)

    assets = {
        "check": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<path d="M3.4 8.4 6.4 11.4 12.6 4.9" fill="none" stroke="{on_accent}" '
            'stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "dash": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<path d="M4 8h8" fill="none" stroke="{on_accent}" stroke-width="2.1" '
            'stroke-linecap="round"/></svg>'
        ),
        "radio": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<circle cx="8" cy="8" r="3.4" fill="{on_accent}"/></svg>'
        ),
        "chevron-down": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            '<path d="M4 6.2 8 10.2 12 6.2" fill="none" stroke="#7A8497" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chevron-up": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            '<path d="M4 9.8 8 5.8 12 9.8" fill="none" stroke="#7A8497" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }

    paths: dict[str, str] = {}
    for name, markup in assets.items():
        path = os.path.join(folder, f"{name}.svg")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(markup)
            paths[name] = path.replace("\\", "/")
        except OSError:
            continue
    return paths
