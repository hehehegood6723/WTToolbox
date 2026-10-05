"""Render a page (or the whole window) to a PNG without showing it.

Uses the real Windows platform plugin with ``WA_DontShowOnScreen`` so system
fonts resolve correctly, then ``grab()``s the widget.  This is how the UI gets
visually verified during development.

Usage:
    python tools/render_page.py home  out/home.png  [--dark] [--size 1180x760]
    python tools/render_page.py window out/win.png
    python tools/render_page.py list
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
sys.path.insert(0, _SRC)

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

PAGE_MODULES = {
    "home": ("wttoolbox.ui.pages.home", "HomePage"),
    "sound": ("wttoolbox.ui.pages.sound", "SoundPage"),
    "tools": ("wttoolbox.ui.pages.tools", "ToolsPage"),
    "library": ("wttoolbox.ui.pages.library", "LibraryPage"),
    "vehicles": ("wttoolbox.ui.pages.vehicles", "VehiclesPage"),
    "stats": ("wttoolbox.ui.pages.stats", "StatsPage"),
    "settings": ("wttoolbox.ui.pages.settings", "SettingsPage"),
    "config_editor": ("wttoolbox.ui.dialogs.config_editor", "ConfigEditorDialog"),
}


def build_context(theme_name: str):
    from wttoolbox.core import gamepath
    from wttoolbox.core.gamepath import GameInstall
    from wttoolbox.core.settings import Settings
    from wttoolbox.ui import theme
    from wttoolbox.ui.context import AppContext

    theme.apply_theme(QApplication.instance(), theme.palette_for(theme_name))
    ctx = AppContext(Settings())
    # An install is optional: without one the pages render their empty state,
    # which is what the "no game installed" screenshots want anyway.
    candidate = (
        ctx.settings.get("game_path")
        or os.environ.get("WTTOOLBOX_GAME", "")
        or next((i.root for i in gamepath.detect_all(include_scan=False)), "")
    )
    if os.path.isdir(candidate):
        ctx.set_install(GameInstall(root=candidate, source="manual"), remember=False)
    return ctx


def render(target: str, out: str, *, theme_name: str, size: tuple[int, int], settle: int = 900) -> int:
    app = QApplication.instance() or QApplication([])
    ctx = build_context(theme_name)

    if target in ("window", "main"):
        from wttoolbox.ui.mainwindow import MainWindow

        widget: QWidget = MainWindow(ctx)
    elif target == "config_editor":
        from wttoolbox.ui.dialogs.config_editor import ConfigEditorDialog

        widget = ConfigEditorDialog(ctx, ctx.install)
    else:
        if target not in PAGE_MODULES:
            print(f"unknown target {target!r}; known: {', '.join(sorted(PAGE_MODULES))}, window")
            return 2
        module_name, class_name = PAGE_MODULES[target]
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
        widget = cls(ctx)

    widget.resize(*size)
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.show()

    # Let layouts, timers and any async population settle.
    for _ in range(6):
        app.processEvents()

    def finish() -> None:
        try:
            pixmap = widget.grab()
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            pixmap.save(out)
            print(f"wrote {out} ({pixmap.width()}x{pixmap.height()})")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            app.quit()

    QTimer.singleShot(settle, finish)
    app.exec()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="list")
    parser.add_argument("out", nargs="?", default="")
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--size", default="1180x760")
    parser.add_argument("--settle", type=int, default=900)
    args = parser.parse_args()

    if args.target == "list":
        for key, (module, cls) in sorted(PAGE_MODULES.items()):
            print(f"{key:16s} {module}.{cls}")
        print(f"{'window':16s} wttoolbox.ui.mainwindow.MainWindow")
        return 0

    width, _, height = args.size.partition("x")
    out = args.out or os.path.join(_HERE, f"shot_{args.target}.png")
    return render(
        args.target,
        out,
        theme_name="dark" if args.dark else "light",
        size=(int(width), int(height or 760)),
        settle=args.settle,
    )


if __name__ == "__main__":
    raise SystemExit(main())
