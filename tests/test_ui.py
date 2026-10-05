"""End-to-end tests for the config editor's write path and the main window.

Run:  python tests/test_ui.py

Destructive operations only touch a sandbox copy of ``config.blk`` - the real
game configuration is never written to.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wttoolbox.core import appdirs, blk, config_backup  # noqa: E402
from wttoolbox.core.gamepath import GameInstall  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import theme, widgets  # noqa: E402
from wttoolbox.ui.context import PAGE_ORDER, AppContext  # noqa: E402

from _game import HAVE_GAME, ROOT, skip, skip_summary  # noqa: E402

REAL_ROOT = ROOT

_failures: list[str] = []
_passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [ok]   {label}")
    else:
        _failures.append(f"{label} :: {detail}")
        print(f"  [FAIL] {label}  {detail}")


def section(title: str) -> None:
    print(f"\n== {title}")


def note(text: str) -> None:
    print(f"  [note] {text}")


def pump(app, rounds: int = 6) -> None:
    for _ in range(rounds):
        app.processEvents()


def make_synthetic_install(root: str) -> str:
    """A minimal but *valid* install, for machines without War Thunder.

    The page builders and the window refuse to mount against an install that
    fails validation, so the suite needs a real-looking directory rather than a
    non-existent path: config.blk plus the two executables are what
    GameInstall.is_valid checks.
    """
    os.makedirs(os.path.join(root, "win64"), exist_ok=True)
    shutil.copy2(os.path.join(_HERE, "fixtures", "sample_config.blk"),
                 os.path.join(root, "config.blk"))
    for rel in (os.path.join("win64", "aces.exe"), "launcher.exe"):
        with open(os.path.join(root, rel), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 128)
    os.makedirs(os.path.join(root, "sound", "fx"), exist_ok=True)
    with open(os.path.join(root, "sound", "fx", "placeholder.txt"), "w") as fh:
        fh.write("synthetic\n")
    return root


def main() -> int:
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app, theme.LIGHT)

    sandbox = tempfile.mkdtemp(prefix="tk-ui-test-")
    install_root = REAL_ROOT
    if not HAVE_GAME:
        install_root = make_synthetic_install(os.path.join(sandbox, "fake-game"))
        note("no War Thunder install: using a synthetic one")
        skip("main window against a real install")
    ctx = AppContext(Settings(os.path.join(sandbox, "settings.json")))
    install = GameInstall(root=install_root, source="manual")
    ctx.set_install(install)

    # ------------------------------------------------------------------ #
    section("main window assembly")
    from wttoolbox.ui.mainwindow import MainWindow

    window = MainWindow(ctx)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.resize(1180, 760)
    window.show()
    pump(app, 10)

    check("no page failed to load", not window._page_errors,
          "; ".join(window._page_errors.keys()))
    for key, detail in window._page_errors.items():
        print(f"    --- {key} ---\n{detail}")

    # Pages are built lazily: only the start page exists until it is visited.
    check("start page built eagerly", set(window._built) == {"home"}, str(sorted(window._built)))
    check("one stack slot per page", window.stack.count() == len(PAGE_ORDER),
          str(window.stack.count()))

    for key in PAGE_ORDER:
        window._apply_page(key)
        pump(app, 6)
        page = window._pages.get(key)
        check(f"page {key} switches and refreshes", page is not None and window.current_page_key == key)

    check(f"all {len(PAGE_ORDER)} pages built after visiting each",
          set(window._built) == set(PAGE_ORDER), str(sorted(window._built)))
    check("stack still has one slot per page", window.stack.count() == len(PAGE_ORDER),
          str(window.stack.count()))

    check("titlebar exposes all nav buttons",
          set(window.titlebar.nav_buttons) == set(PAGE_ORDER),
          str(sorted(window.titlebar.nav_buttons)))
    check("8 resize grips created", len(window._grips) == 8, str(len(window._grips)))
    check("status strip reflects the install",
          install_root.lower() in window.status_left.text().lower(),
          window.status_left.text())
    check("tray icon created", window.tray is not None)

    window.titlebar.set_maximized(False)
    check("window has minimum size", window.minimumWidth() >= 1000 and window.minimumHeight() >= 600)

    # ------------------------------------------------------------------ #
    section("config editor write path (sandbox)")
    config_copy = os.path.join(sandbox, "config.blk")
    # The player's own config.blk when there is one, otherwise the bundled
    # fixture, which has the same shape (comments, nested blocks, every value
    # type) and the keys the assertions below look for.
    if HAVE_GAME:
        source = os.path.join(REAL_ROOT, "config.blk")
        note("driving the editor against the player's own config.blk")
    else:
        source = os.path.join(_HERE, "fixtures", "sample_config.blk")
        note("driving the editor against the bundled fixture")
        skip("config editor against a real config.blk")
    shutil.copy2(source, config_copy)
    with open(config_copy, "rb") as fh:
        original_bytes = fh.read()

    sandbox_install = GameInstall(root=sandbox, source="manual")

    from wttoolbox.ui.dialogs import config_editor as ce

    # The dialog reads install.config; point a GameInstall at the sandbox by
    # overriding the two accessors on a throwaway subclass.
    class SandboxInstall(GameInstall):
        @property
        def config(self) -> str:
            return config_copy

        def is_running(self) -> bool:
            return False

    # Auto-accept every confirmation so the test can drive the real save path.
    original_confirm = widgets.confirm
    widgets.confirm = lambda *a, **k: (True, False)
    try:
        dialog = ce.ConfigEditorDialog(ctx, SandboxInstall(root=sandbox))
        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.resize(1020, 700)
        dialog.show()
        pump(app, 8)

        check("block list populated", dialog.block_list.count() >= 4,
              str(dialog.block_list.count()))
        check("rows built for 顶层设置", len(dialog._rows) > 0, str(len(dialog._rows)))
        check("save disabled with no changes", not dialog.save_button.isEnabled())

        # 1) change an existing boolean
        row = dialog._rows.get(("video", "vsync"))
        if row is None:
            dialog._select_block("video")
            pump(app, 4)
            row = dialog._rows.get(("video", "vsync"))
        check("video.vsync row exists", row is not None)
        if row is not None:
            row.set_raw("yes")
            dialog._pending[("video", "vsync")] = "yes"
            dialog._types.setdefault(("video", "vsync"), "b")
            row.set_changed(True)
            dialog._refresh_change_state()
        check("save enabled after edit", dialog.save_button.isEnabled())

        # 2) apply a preset (writes graphicsQuality)
        dialog._apply_preset("high", "高画质")
        check("preset staged", dialog._pending.get(("", "graphicsQuality")) == '"high"',
              str(dialog._pending.get(("", "graphicsQuality"))))

        # 3) a key that does not exist yet must be created via ensure()
        dialog._pending[("video", "tkProbeKey")] = "7"
        dialog._types[("video", "tkProbeKey")] = "i"
        check("staged a brand new key", ("video", "tkProbeKey") in dialog._pending)

        before_backups = config_backup.list_backups()
        newest_before = max((b.stamp for b in before_backups), default=0.0)
        dialog._save()
        pump(app, 6)

        # Backups are pruned to ``backup_keep``, so once the cap is reached the
        # count stays put - the fresh backup must still be there though.
        keep = int(ctx.settings.get("backup_keep", 20) or 20)
        after_backups = config_backup.list_backups()
        grew = len(after_backups) == len(before_backups) + 1
        capped = len(after_backups) == keep and len(before_backups) == keep
        newest_after = max((b.stamp for b in after_backups), default=0.0)
        check("backup created",
              (grew or capped) and newest_after >= newest_before,
              f"{len(before_backups)} -> {len(after_backups)} (keep={keep}), "
              f"newest {newest_before:.0f} -> {newest_after:.0f}")

        with open(config_copy, "rb") as fh:
            written = fh.read()
        check("file changed", written != original_bytes)

        doc = blk.BlkDocument.load(config_copy)
        check("vsync written", doc.value("video", "vsync") is True, str(doc.value("video", "vsync")))
        check("graphicsQuality written", doc.value([], "graphicsQuality") == "high",
              str(doc.value([], "graphicsQuality")))
        check("new key created inside the right block",
              doc.value("video", "tkProbeKey") == 7, str(doc.value("video", "tkProbeKey")))
        check("parameters preserved",
              sum(1 for _ in doc.walk_params())
              == sum(1 for _ in blk.BlkDocument(original_bytes.decode("utf-8")).walk_params()) + 1)

        # Only the intended byte ranges may differ.
        old_text = original_bytes.decode("utf-8")
        new_text = written.decode("utf-8")
        check("no comments lost", old_text.count("//") == new_text.count("//"))
        check("block count unchanged",
              old_text.count("{") == new_text.count("{"),
              f"{old_text.count('{')} -> {new_text.count('{')}")
        check("other keys untouched",
              'antialiasing_mode:t="off"' in new_text and 'language:t="Chinese"' in new_text)
        check("no stray temp file",
              not os.path.exists(config_copy + ".tk-tmp") and not os.path.exists(config_copy + ".tmp"))

        # 4) an untouched key must remain byte-identical
        check("unchanged value preserved verbatim",
              'ssaa:r=1' in new_text and 'grassRadiusMul:r=0.1' in new_text)
    finally:
        widgets.confirm = original_confirm

    # ------------------------------------------------------------------ #
    section("render smoke: every page grabs")
    for key in PAGE_ORDER:
        window._apply_page(key)
        pump(app, 4)
        pixmap = window._pages[key].grab()
        check(f"page {key} renders", not pixmap.isNull() and pixmap.width() > 100,
              f"{pixmap.width()}x{pixmap.height()}")

    window._force_quit = True
    window.close()
    pump(app, 4)

    shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n{'-' * 64}")
    if _failures:
        print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
        for item in _failures:
            print("   -", item)
        _finish(1)
    print(f"All {_passed} checks passed.")
    _finish(0)


def _finish(code: int) -> None:
    """Leave immediately: a live worker thread would deadlock shutdown."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
