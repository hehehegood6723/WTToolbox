"""Render every page (light + dark) to PNGs for review.

Usage: python tools/shot_all.py [outdir]
"""

from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

PAGES = [
    ("home", "wttoolbox.ui.pages.home", "HomePage"),
    ("sound", "wttoolbox.ui.pages.sound", "SoundPage"),
    ("tools", "wttoolbox.ui.pages.tools", "ToolsPage"),
    ("library", "wttoolbox.ui.pages.library", "LibraryPage"),
    ("settings", "wttoolbox.ui.pages.settings", "SettingsPage"),
]


def build_ctx(theme_name):
    from wttoolbox.core.gamepath import GameInstall
    from wttoolbox.core.settings import Settings
    from wttoolbox.ui import theme
    from wttoolbox.ui.context import AppContext

    theme.apply_theme(QApplication.instance(), theme.palette_for(theme_name))
    ctx = AppContext(Settings())
    # WTTOOLBOX_GAME=<install> to shoot the pages with a game attached; the
# pages fall back to their empty states when it is unset.
GAME = os.environ.get("WTTOOLBOX_GAME", "")
if GAME:
    ctx.set_install(GameInstall(root=GAME, source="manual"), remember=False)
    return ctx


def run(theme_name: str, outdir: str, size=(1180, 760), settle=1400) -> int:
    app = QApplication.instance() or QApplication([])
    ctx = build_ctx(theme_name)
    failures = []
    widgets = []

    for key, module_name, class_name in PAGES:
        try:
            module = __import__(module_name, fromlist=[class_name])
            page = getattr(module, class_name)(ctx)
        except Exception:
            failures.append((key, traceback.format_exc()))
            print(f"!! {key} failed to build")
            continue
        page.resize(*size)
        page.setAttribute(Qt.WA_DontShowOnScreen, True)
        page.show()
        widgets.append((key, page))

    for _ in range(8):
        app.processEvents()

    def finish():
        for key, page in widgets:
            try:
                pixmap = page.grab()
                path = os.path.join(outdir, f"page_{key}_{theme_name}.png")
                pixmap.save(path)
                print(f"wrote {path} ({pixmap.width()}x{pixmap.height()})")
            except Exception:
                failures.append((key, traceback.format_exc()))
        for key, detail in failures:
            print(f"--- {key} ---\n{detail}")
        app.quit()

    QTimer.singleShot(settle, finish)
    app.exec()
    return 0


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shots"
)
    os.makedirs(outdir, exist_ok=True)
    run("light", outdir)
    run("dark", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
