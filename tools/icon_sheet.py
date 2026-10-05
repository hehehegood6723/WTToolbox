"""Render every icon in the set to a labelled contact sheet for visual review.

Usage:  python tools/icon_sheet.py [out.png]
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wttoolbox.ui import icons  # noqa: E402

CELL = 108
COLS = 8
ICON_SIZE = 34


def main() -> int:
    app = QApplication.instance() or QApplication([])
    names = icons.available()
    rows = (len(names) + COLS - 1) // COLS
    width = COLS * CELL
    height = rows * CELL + 60

    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#EEF1F5"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # header
    painter.setPen(QColor("#1B2130"))
    font = QFont("Segoe UI", 11)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(12, 24, f"WTToolbox icon set - {len(names)} icons")

    label_font = QFont("Segoe UI", 7)
    for index, name in enumerate(names):
        col = index % COLS
        row = index // COLS
        x = col * CELL
        y = row * CELL + 44
        # checker the two backgrounds so light/dark icons are both checkable
        bg = QColor("#FFFFFF") if (row + col) % 2 == 0 else QColor("#171C24")
        painter.fillRect(x + 6, y + 2, CELL - 12, CELL - 34, bg)
        fg = "#1B2130" if (row + col) % 2 == 0 else "#E7EBF2"
        pixmap = icons.icon_pixmap(name, fg, ICON_SIZE, 1.7)
        painter.drawPixmap(
            int(x + (CELL - ICON_SIZE) / 2),
            int(y + 2 + (CELL - 34 - ICON_SIZE) / 2),
            pixmap,
        )
        painter.setPen(QColor("#6F7A8C") if (row + col) % 2 == 0 else QColor("#8E98A9"))
        painter.setFont(label_font)
        painter.drawText(
            QRectF(x + 4, y + CELL - 30, CELL - 8, 14),
            int(Qt.AlignHCenter | Qt.AlignVCenter),
            name,
        )

    # the brand mark, large
    painter.setFont(font)
    painter.setPen(QColor("#1B2130"))
    painter.drawText(12, height - 14, "logo_mark:")
    mark = icons.logo_mark("#FF9E1B", 34)
    painter.drawPixmap(110, height - 40, mark)
    painter.drawPixmap(150, height - 40, icons.logo_mark("#2563EB", 34))
    painter.end()

    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "icon_sheet.png")
    image.save(out)
    print("wrote", out, image.width(), "x", image.height())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
