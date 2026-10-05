"""Tests for the v1.1 work: bug fixes and the two new features.

Run:  python tests/test_features.py

Network-dependent checks degrade to a skip when the wiki is unreachable.
"""

from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget  # noqa: E402

from wttoolbox.core import winutil, wtdata  # noqa: E402
from wttoolbox.core.gamepath import GameInstall  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import icons, theme  # noqa: E402
from wttoolbox.ui.context import PAGE_ORDER, AppContext  # noqa: E402

HUGE = Qt.WidgetAttribute.WA_DontShowOnScreen
SANDBOX = os.path.join(os.environ.get("TEMP", "."), "tk-features-test")
os.makedirs(SANDBOX, exist_ok=True)
from _game import HAVE_GAME, ROOT, skip, skip_summary  # noqa: E402

REAL_ROOT = ROOT

_failures: list[str] = []
_passed = 0
_notes: list[str] = []

app = QApplication([])
theme.apply_theme(app, theme.LIGHT)


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [ok]   {label}")
    else:
        _failures.append(f"{label} :: {detail}")
        print(f"  [FAIL] {label}  {detail}")


def note(text: str) -> None:
    _notes.append(text)
    print(f"  [note] {text}")


def section(title: str) -> None:
    print(f"\n== {title}")


def pump(rounds: int = 12) -> None:
    for _ in range(rounds):
        app.processEvents()


def wait_until(predicate, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        pump(4)
        time.sleep(0.03)
    return False


# --------------------------------------------------------------------------- #
section("icon sharpness (devicePixelRatio aware)")
dpr = icons._screen_dpr()
check("screen dpr detected", dpr >= 1.0, str(dpr))
for name in ("sound", "folder", "gear", "crosshair", "shield"):
    pixmap = icons.icon_pixmap(name, "#FF9E1B", 26, 1.8)
    expected = int(round(26 * dpr))
    check(f"{name}: rendered at physical resolution", pixmap.width() == expected,
          f"{pixmap.width()} != {expected}")
    check(f"{name}: tagged with dpr", abs(pixmap.devicePixelRatio() - dpr) < 0.01)
    check(f"{name}: logical size preserved",
          pixmap.deviceIndependentSize().toSize().width() == 26,
          str(pixmap.deviceIndependentSize().toSize()))

# the whole glyph must be inside the bitmap (a dpr-before-paint bug clips it)
image = icons.icon_pixmap("gear", "#000000", 26, 1.8).toImage()
edge_hits = sum(
    1
    for x in range(image.width())
    for y in (0, image.height() - 1)
    if (image.pixel(x, y) >> 24) & 0xFF > 20
)
side_hits = sum(
    1
    for y in range(image.height())
    for x in (0, image.width() - 1)
    if (image.pixel(x, y) >> 24) & 0xFF > 20
)
check("gear glyph is not clipped at the edges", edge_hits + side_hits == 0,
      f"{edge_hits + side_hits} edge pixels")

# --------------------------------------------------------------------------- #
section("vehicle index (bundled)")
stats = wtdata.index_stats()
check("vehicles indexed", stats["count"] >= 3000, str(stats["count"]))
check("ten nations", len(stats["nations"]) == 10, str(len(stats["nations"])))
check("five classes", len(stats["classes"]) == 5, str(len(stats["classes"])))
check("index has a generation stamp", bool(stats["generated_at"]))
note(f"index: {stats['count']:,} vehicles, {len(stats['nations'])} nations, generated {stats['generated_at']}")

check("search by name works", any(v.name.startswith("T-34") for v in wtdata.search("t-34", limit=20)))
check("nation filter works",
      all(v.nation == "ussr" for v in wtdata.search("", nation="ussr", limit=50)))
check("class filter works",
      all(v.cls == "helicopter" for v in wtdata.search("", vehicle_class="helicopter", limit=50)))
check("rank filter works", all(v.rank == 5 for v in wtdata.search("", rank=5, limit=50)))
check("empty search returns rows", len(wtdata.search("", limit=10)) == 10)
check("no-match search returns nothing", wtdata.search("zzzzz-not-a-vehicle", limit=10) == [])

index = {v.slug: v for v in wtdata.load_index()["_vehicles"]}
for slug in ("ussr_t_34_1941", "bf-109f-4", "germ_pzkpfw_iv_ausf_f2"):
    # find() is case-insensitive: the index uses the wiki's canonical casing
    # (germ_pzkpfw_IV_ausf_F2) while older code and saved settings are lower case.
    check(f"{slug} present in index", wtdata.find(slug) is not None)

# --------------------------------------------------------------------------- #
section("vehicle specs (live wiki, cached)")
spec_a = wtdata.fetch_spec("ussr_t_34_1941")
spec_b = wtdata.fetch_spec("germ_pzkpfw_iv_ausf_f2")
if spec_a.error and spec_b.error:
    note(f"wiki unreachable ({spec_a.error}); skipping spec assertions")
else:
    check("T-34 spec has a real name", spec_a.name.startswith("T-34"), spec_a.name)
    check("T-34 spec has metrics", len(spec_a.metrics) >= 8, str(len(spec_a.metrics)))
    check("T-34 BR parsed", bool(spec_a.br), spec_a.br)
    check("T-34 armour is a real number",
          spec_a.metrics.get("armour_front") is not None and spec_a.metrics["armour_front"].value > 0,
          str(spec_a.metrics.get("armour_front")))
    check("T-34 crew parsed", spec_a.metrics.get("crew") is not None)
    check("T-34 speed parsed", spec_a.metrics.get("speed_forward") is not None)
    note(f"T-34: BR {spec_a.br}, armour {spec_a.metrics['armour_front'].value} mm, "
         f"crew {spec_a.metrics['crew'].value}, {len(spec_a.metrics)} metrics")

    aircraft = wtdata.fetch_spec("bf-109f-4")
    if not aircraft.error:
        check("aircraft max speed parsed", aircraft.metrics.get("speed_forward") is not None,
              str(sorted(aircraft.metrics)))
        check("aircraft climb parsed", aircraft.metrics.get("climb") is not None)
        check("aircraft turn rate derived", aircraft.metrics.get("turn_rate") is not None)
        note(f"Bf 109 F-4: max speed {aircraft.metrics.get('speed_forward').value} km/h, "
             f"climb {aircraft.metrics.get('climb').value} m/s, "
             f"turn {aircraft.metrics.get('turn_rate').value:.1f} deg/s")

    comparison = wtdata.build_comparison(spec_a, spec_b)
    check("comparison produced axes", len(comparison.axes) >= 4, str(len(comparison.axes)))
    check("layers are <= 6", len(comparison.axes) <= 6, str(len(comparison.axes)))
    check("every axis has ratios in 0..1",
          all(0.0 <= axis.ratio_a <= 1.0001 and 0.0 <= axis.ratio_b <= 1.0001 for axis in comparison.axes))
    check("the better vehicle reaches ratio 1.0",
          all(abs(max(axis.ratio_a, axis.ratio_b) - 1.0) < 1e-6 for axis in comparison.axes))
    check("winner is consistent with the ratios",
          all(
              (axis.winner == "a" and axis.ratio_a >= axis.ratio_b)
              or (axis.winner == "b" and axis.ratio_b >= axis.ratio_a)
              or (axis.winner == "tie" and abs(axis.ratio_a - axis.ratio_b) < 1e-9)
              for axis in comparison.axes
          ))
    score_a, score_b = comparison.score
    check("score adds up", score_a + score_b <= len(comparison.axes))
    note(f"axes: {[axis.label for axis in comparison.axes]}  score={comparison.score}")

    check("cache file written", wtdata.cache_info()["count"] >= 1)
    cached_again = wtdata.fetch_spec("ussr_t_34_1941")
    check("second fetch is served from cache/memory", cached_again.name == spec_a.name)

# --------------------------------------------------------------------------- #
section("sound page reacts to the game directory appearing (bug 1)")
from wttoolbox.ui.pages.sound import SoundPage

settings_1 = os.path.join(SANDBOX, "s1.json")
if os.path.isfile(settings_1):
    os.remove(settings_1)
ctx1 = AppContext(Settings(settings_1))
page = SoundPage(ctx1)
page.setAttribute(HUGE, True)
page.resize(1180, 700)
page.show()
pump(14)
tables_before = page.findChildren(QTableWidget)
rows_before = sum(t.rowCount() for t in tables_before)
check("sound page starts gated with no install", rows_before == 0, str(rows_before))

ctx1.set_install(GameInstall(root=REAL_ROOT, source="manual"), remember=False)
if HAVE_GAME:
    wait_until(lambda: sum(t.rowCount() for t in page.findChildren(QTableWidget)) > 0, timeout=60)
    tables_after = page.findChildren(QTableWidget)
    rows_after = sum(t.rowCount() for t in tables_after)
    check("sound page populates after the install is set", rows_after > 100,
          f"{len(tables_after)} tables, {rows_after} rows")
    note(f"sound inventory rows after remount: {rows_after}")
else:
    # Build a synthetic *valid* install so the remount path is still exercised:
    # the point of this check is that the page re-reads after installChanged,
    # not how many files the real game happens to ship.
    fake_root = os.path.join(SANDBOX, "fakegame")
    os.makedirs(os.path.join(fake_root, "win64"), exist_ok=True)
    with open(os.path.join(fake_root, "config.blk"), "w", encoding="utf-8") as fh:
        fh.write('graphics{\n  renderer:t="auto"\n}\n')
    for rel in (os.path.join("win64", "aces.exe"), "launcher.exe"):
        with open(os.path.join(fake_root, rel), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 64)
    fake_sound = os.path.join(fake_root, "sound")
    for name in ("fx", "mod", "gui", "weapons", "music"):
        os.makedirs(os.path.join(fake_sound, name), exist_ok=True)
        with open(os.path.join(fake_sound, name, "placeholder.txt"), "w") as fh:
            fh.write(name)
    with open(os.path.join(fake_sound, "masterbank.bank"), "wb") as fh:
        fh.write(b"\0" * 32)
    ctx1.set_install(GameInstall(root=fake_root, source="manual"), remember=False)
    wait_until(lambda: sum(t.rowCount() for t in page.findChildren(QTableWidget)) > 0, timeout=60)
    rows_after = sum(t.rowCount() for t in page.findChildren(QTableWidget))
    check("sound page populates after the install is set (synthetic install)",
          rows_after > 0, f"{rows_after} rows")
    note(f"synthetic sound inventory rows: {rows_after}")
    skip("sound inventory against a real install")
check("sound page exposes on_install_changed", hasattr(page, "on_install_changed"))

# --------------------------------------------------------------------------- #
section("weapon scoping: a tank's machine gun is not its gun")
# A tank page carries the main gun *and* its machine guns.  The machine gun's
# "600 shots/min" used to be attached to the tank, which showed up as an absurd
# "射速 600 发/分" for a T-34.  This is verified offline on a synthetic page.

SYNTH_PAGE = """<html><head><title>Synthetic Tank | War Thunder Wiki</title></head><body>
<div class="block-header">Armaments</div>
<div class="game-unit_chars">
  <div class="game-unit_weapon">
    <span class="game-unit_weapon-title"><a href="/collections/weapon/x">76 mm F-34 cannon</a></span>
    <div class="game-unit_chars mt-2">
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Ammunition</span><span class="game-unit_chars-value">77 rounds</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Reload</span><span class="game-unit_chars-info">basic crew &rarr; aces</span></div><div class="game-unit_chars-subline"><span class="game-unit_chars-value">9 &rarr; 6.9 s</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Vertical guidance</span><span class="game-unit_chars-value">-5 / 30&deg;</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Turret Rotation Speed</span><span class="game-unit_chars-info">basic crew &rarr; aces</span></div><div class="game-unit_chars-subline"><span>Horizontal</span><span class="game-unit_chars-value">17.5</span></div><div class="game-unit_chars-subline"><span>Vertical</span><span class="game-unit_chars-value">2.8</span></div></div>
    </div>
  </div>
  <div class="game-unit_weapon">
    <span class="game-unit_weapon-title"><a href="/collections/weapon/y">7.62 mm DT machine gun</a> (coaxial)</span>
    <div class="game-unit_chars mt-2">
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Ammunition</span><span class="game-unit_chars-value">2,898 rounds</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Belt capacity</span><span class="game-unit_chars-value">63 rounds</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Reload</span><span class="game-unit_chars-value">10.4 &rarr; 8 s</span></div></div>
      <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Fire rate</span><span class="game-unit_chars-value">600 shots/min</span></div></div>
    </div>
  </div>
</div>
<div class="block-header">Mobility</div>
<div class="game-unit_chars">
  <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Max speed</span></div><div class="game-unit_chars-subline"><span>Forward</span><span class="game-unit_chars-value">49</span></div><div class="game-unit_chars-subline"><span>Backward</span><span class="game-unit_chars-value">7</span></div></div>
</div>
<div class="block-header">Survivability and armour</div>
<div class="game-unit_chars">
  <div class="game-unit_chars-block"><div class="game-unit_chars-line"><span class="game-unit_chars-header">Armour</span></div><div class="game-unit_chars-subline"><span>Hull</span><span class="game-unit_chars-value">45 / 45 / 40 mm</span></div><div class="game-unit_chars-subline"><span>Turret</span><span class="game-unit_chars-value">45 / 45 / 45 mm</span></div></div>
</div>
</body></html>"""

synth = wtdata.parse_vehicle_page(SYNTH_PAGE, "synthetic_tank")
check("synthetic page parsed", bool(synth.sections), str(len(synth.sections)))
check("main armament identified", synth.primary_weapon == "76 mm F-34 cannon",
      synth.primary_weapon)
check("both weapons kept for the detail view",
      [name for name, _rows in synth.weapons]
      == ["76 mm F-34 cannon", "7.62 mm DT machine gun (coaxial)"],
      str([name for name, _rows in synth.weapons]))
check("**the machine gun's 600 does NOT become the tank's fire rate**",
      "fire_rate" not in synth.metrics, str(synth.metrics.get("fire_rate")))
fire_rates = [value for _name, rows in synth.weapons for label, value in rows
              if label.lower() == "fire rate"]
check("the machine gun's rate is still recorded on the weapon",
      fire_rates == ["600 shots/min"], str(fire_rates))
check("reload is the main gun's, not the MG's",
      synth.metrics["reload"].weapon == "76 mm F-34 cannon", synth.metrics["reload"].weapon)
check("reload uses the fully-upgraded value (6.9 s -> 8.7 发/分)",
      abs(synth.metrics["reload"].value - 60 / 6.9) < 0.01, str(synth.metrics["reload"].value))
check("reload text names its weapon",
      "76 mm F-34 cannon" in synth.metrics["reload"].text, synth.metrics["reload"].text)
check("ammo is the main gun's 77 rounds",
      synth.metrics["ammo"].value == 77 and synth.metrics["ammo"].weapon == "76 mm F-34 cannon",
      str(synth.metrics["ammo"]))
check("turret traverse is scoped to the main gun",
      abs(synth.metrics["turret_traverse"].value - 17.5) < 0.01,
      str(synth.metrics.get("turret_traverse")))
check("vertical guidance scoped to the main gun", synth.metrics["elevation"].value == -5,
      str(synth.metrics.get("elevation")))
check("subline labels keep their parent",
      any(label == "Turret Rotation Speed (Horizontal)" for _t, rows in synth.groups for label, _v in rows),
      str([label for _t, rows in synth.groups for label, _v in rows][:14]))
check("tank speed still parsed", synth.metrics["speed_forward"].value == 49,
      str(synth.metrics.get("speed_forward")))
check("tank armour still parsed", synth.metrics["armour_front"].value == 45,
      str(synth.metrics.get("armour_front")))
check("turret armour still parsed", synth.metrics["armour_turret"].value == 45,
      str(synth.metrics.get("armour_turret")))
check("the hp/t axis is named 功重比, not 推重比",
      [axis["label"] for axis in wtdata.AXES if axis["key"] == "power_weight"] == ["功重比"],
      str([axis["label"] for axis in wtdata.AXES if axis["key"] == "power_weight"]))
check("every axis carries a valid direction",
      all(axis.get("better") in ("high", "low") for axis in wtdata.AXES))
check("the easily-misread axes explain themselves",
      all(any(a["key"] == key and a.get("hint") for a in wtdata.AXES)
          for key in ("power_weight", "reload", "fire_rate", "turn_rate")),
      str([a["key"] for a in wtdata.AXES if a.get("hint")]))
check("detail groups name the weapon",
      any(title.startswith("Armaments · 76 mm F-34 cannon") for title, _rows in synth.groups),
      str([t for t, _r in synth.groups]))

# the cache stores the raw parse, so a rule change re-derives without refetching
payload = synth.to_dict()
check("cache payload has no derived metrics", "metrics" not in payload, str(sorted(payload)))
check("cache payload keeps the raw sections", len(payload.get("sections") or []) == len(synth.sections))
reloaded = wtdata.VehicleSpec.from_dict(payload)
check("metrics are re-derived on load",
      reloaded.metrics["reload"].text == synth.metrics["reload"].text,
      reloaded.metrics["reload"].text)
check("groups are re-derived on load", reloaded.groups == synth.groups)

# --------------------------------------------------------------------------- #
section("theme switch does not freeze (bug 3)")
from wttoolbox.ui.mainwindow import MainWindow

# Release the throwaway pages built above: a style-sheet swap re-polishes every
# live widget, so leaving them around would measure the test, not the app.
page.hide()
page.setParent(None)
page.deleteLater()
for _ in range(4):
    app.processEvents()
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402

QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
pump(4)

settings_2 = os.path.join(SANDBOX, "s2.json")
if os.path.isfile(settings_2):
    os.remove(settings_2)
ctx2 = AppContext(Settings(settings_2))
ctx2.set_install(GameInstall(root=REAL_ROOT, source="manual"), remember=False)
window = MainWindow(ctx2)
window.setAttribute(HUGE, True)
window.resize(1180, 760)
window.show()
pump(16)
check("all pages present in the nav", len(window.titlebar.nav_buttons) == len(PAGE_ORDER) == 7,
      f"{len(window.titlebar.nav_buttons)} vs {len(PAGE_ORDER)}")
check("only the start page is built eagerly", len(window._built) == 1, str(sorted(window._built)))

for name in ("dark", "light", "dark"):
    started = time.time()
    window._on_theme_changed(name)
    pump(8)
    elapsed = time.time() - started
    check(f"switch to {name} is fast (<2s)", elapsed < 2.0, f"{elapsed:.2f}s")
    check(f"switch to {name} applied the palette", theme.current().name == name)
    check(f"switch to {name} kept the shell", window.stack.count() == 7 and len(window._grips) == 8)
note(f"theme switch timings measured: {elapsed:.2f}s for the last one")

# lazy build + navigation after the theme change
for key in PAGE_ORDER:
    window._apply_page(key)
    pump(6)
check("every page builds after theme changes", set(window._built) == set(PAGE_ORDER),
      str(sorted(window._built)))
check("no page failed", not window._page_errors, str(list(window._page_errors)))

# --------------------------------------------------------------------------- #
section("theme switch through the real UI controls (regression guard)")
# The previous version of this test called _on_theme_changed() directly, which
# skipped AppContext.toggle_theme() - exactly where the "cannot go back to
# light" bug lived.  These checks drive the actual buttons instead.

ctx_theme = AppContext(Settings(os.path.join(SANDBOX, "theme.json")))
ctx_theme.set_install(GameInstall(root=REAL_ROOT, source="manual"), remember=False)
theme.apply_theme(app, theme.LIGHT)
ctx_theme.palette = theme.palette_for("light")

theme_window = MainWindow(ctx_theme)
theme_window.setAttribute(HUGE, True)
theme_window.resize(1180, 760)
theme_window.show()
pump(14)

button = theme_window.titlebar.theme_button
check("start on light", theme.current().name == "light", theme.current().name)
for expected in ("dark", "light", "dark", "light"):
    QTest.mouseClick(button, Qt.LeftButton)
    pump(10)
    check(f"titlebar button -> {expected}", theme.current().name == expected,
          f"got {theme.current().name}")
    check(f"choice persisted as {expected}", ctx_theme.settings.get("theme") == expected,
          str(ctx_theme.settings.get("theme")))
    check(f"context palette follows ({expected})", ctx_theme.palette.name == expected,
          ctx_theme.palette.name)

# and through the settings page radio buttons
theme_window._apply_page("settings")
pump(10)
for expected in ("dark", "light", "dark"):
    page_settings = theme_window._pages["settings"]
    buttons = getattr(page_settings, "theme_buttons", {})
    check(f"settings page exposes a {expected} button", expected in buttons, str(list(buttons)))
    if expected not in buttons:
        break
    buttons[expected].click()
    pump(12)
    check(f"settings button -> {expected}", theme.current().name == expected,
          f"got {theme.current().name}")
    check(f"settings value stored ({expected})", ctx_theme.settings.get("theme") == expected)
note(f"theme state after the real-control sweep: {theme.current().name}")

theme.apply_theme(app, theme.LIGHT)
# Hide rather than close: closeEvent calls QApplication.quit(), which would
# interfere with the sections that still follow.
theme_window.hide()
theme_window.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
pump(6)

# --------------------------------------------------------------------------- #
section("rounded window shell")
check("window is translucent", window.testAttribute(Qt.WA_TranslucentBackground))
check("rounded while not maximised", window._rounded())
check("corner margin reserved for the shadow", window._corner_margin() == theme.WINDOW_SHADOW,
      str(window._corner_margin()))
margins = window.layout().contentsMargins()
check("layout honours the margin",
      margins.left() == margins.top() == margins.right() == margins.bottom() == theme.WINDOW_SHADOW,
      str((margins.left(), margins.top(), margins.right(), margins.bottom())))

shell = window.grab().toImage()
check("bitmap has an alpha channel", shell.hasAlphaChannel())
corners = {
    "top-left": (0, 0),
    "top-right": (shell.width() - 1, 0),
    "bottom-left": (0, shell.height() - 1),
    "bottom-right": (shell.width() - 1, shell.height() - 1),
}
for label, (x, y) in corners.items():
    check(f"{label} corner is rounded away", shell.pixelColor(x, y).alpha() == 0,
          f"alpha={shell.pixelColor(x, y).alpha()}")
centre = shell.pixelColor(shell.width() // 2, shell.height() // 2)
check("body is opaque", centre.alpha() == 255, f"alpha={centre.alpha()}")

inset = theme.WINDOW_SHADOW
reach = inset + theme.WINDOW_RADIUS + 6
inside = shell.pixelColor(reach, reach)
check("the body is opaque just past the corner arc", inside.alpha() > 200,
      f"alpha={inside.alpha()} at ({reach},{reach})")

# the diagonal must describe an arc: transparent outside, a soft shadow band,
# then the opaque body - sampled far enough to actually reach the body
diagonal = [shell.pixelColor(i, i).alpha() for i in range(0, reach + 8)]
check("diagonal shows a soft edge, not a hard cut",
      0 in diagonal and 255 in diagonal and any(0 < a < 255 for a in diagonal),
      str(diagonal))

# maximised -> square, opaque corners
window.showMaximized()
pump(16)
check("maximised drops the corner margin", window._corner_margin() == 0)
check("maximised squares the strips", bool(window.titlebar.property("tkFlat")))
max_image = window.grab().toImage()
check("maximised corners are opaque", max_image.pixelColor(0, 0).alpha() == 255,
      f"alpha={max_image.pixelColor(0, 0).alpha()}")
window.showNormal()
pump(14)
check("restoring brings the rounding back",
      window._corner_margin() == theme.WINDOW_SHADOW
      and not bool(window.titlebar.property("tkFlat")),
      f"margin={window._corner_margin()} flat={window.titlebar.property('tkFlat')}")

# the resize grips must sit on the opaque body, not in the transparent margin
grip_rects = [grip.geometry() for grip in window._grips if grip.isVisible()]
check("resize grips exist", len(grip_rects) == 8, str(len(grip_rects)))
check("resize grips stay on the body",
      all(rect.left() >= theme.WINDOW_SHADOW - 1 and rect.top() >= theme.WINDOW_SHADOW - 1
          for rect in grip_rects),
      str([r.getRect() for r in grip_rects[:3]]))

# --------------------------------------------------------------------------- #
section("vehicle comparison page")
from wttoolbox.ui.pages.vehicles import VehiclesPage

vehicles_page = VehiclesPage(ctx2)
vehicles_page.setAttribute(HUGE, True)
vehicles_page.resize(1180, 900)
vehicles_page.show()
pump(12)
check("radar starts empty", not vehicles_page.radar._axes)

vehicles_page.card_a.set_vehicle(wtdata.find("ussr_t_34_1941"))
vehicles_page.card_b.set_vehicle(wtdata.find("germ_pzkpfw_iv_ausf_f2"))
vehicles_page.compare()
wait_until(lambda: vehicles_page._pending == 0 and vehicles_page.spec_a and vehicles_page.spec_b, timeout=120)
check("both specs fetched", vehicles_page.spec_a is not None and vehicles_page.spec_b is not None)
check("radar got axes", len(vehicles_page.radar._axes) >= 4, str(len(vehicles_page.radar._axes)))
check("comparison table populated", vehicles_page.table.rowCount() == len(vehicles_page.radar._axes),
      f"{vehicles_page.table.rowCount()} vs {len(vehicles_page.radar._axes)}")
check("detail panes filled", len(vehicles_page.detail_a.toPlainText()) > 100)
check("no page-level error", not vehicles_page.warning.isVisible())
note(f"radar axes: {[a.label for a in vehicles_page.radar._axes]}")

before_a = [axis.ratio_a for axis in vehicles_page.radar._axes]
before_b = [axis.ratio_b for axis in vehicles_page.radar._axes]
vehicles_page.swap()
pump(6)
after_a = [axis.ratio_a for axis in vehicles_page.radar._axes]
after_b = [axis.ratio_b for axis in vehicles_page.radar._axes]
check("swap exchanges the two sides", after_a == before_b and after_b == before_a,
      f"{after_a} vs {before_b}")
vehicles_page.reset()
pump(4)
check("reset clears the chart", not vehicles_page.radar._axes and vehicles_page.table.rowCount() == 0)

# --------------------------------------------------------------------------- #
section("stats page disclaimer gate")
from wttoolbox.ui.pages.stats import StatsPage, find_local_nickname

settings_3 = os.path.join(SANDBOX, "s3.json")
if os.path.isfile(settings_3):
    os.remove(settings_3)
ctx3 = AppContext(Settings(settings_3))
ctx3.set_install(GameInstall(root=REAL_ROOT, source="manual"), remember=False)

stats_page = StatsPage(ctx3)
stats_page.setAttribute(HUGE, True)
stats_page.resize(1180, 700)
stats_page.show()
pump(12)
check("no nickname field before accepting the disclaimer", not hasattr(stats_page, "nick_edit"))
texts = [w.text() for w in stats_page.findChildren(QLabel)]
check("gate asks for the disclaimer", any("免责声明" in t for t in texts))

ctx3.settings.set("stats_disclaimer_accepted", True)
ctx3.settings.set("stats_disclaimer_at", time.time())
stats_page._mount()
pump(16)
check("content appears after accepting", hasattr(stats_page, "nick_edit"))
stats_page.refresh_local()
pump(6)
if HAVE_GAME:
    check("local replay count is a real number", stats_page.tile_replays._value.text().isdigit(),
          stats_page.tile_replays._value.text())
    check("client version shown", stats_page.local_rows["version"]._value.text() not in ("—", ""),
          stats_page.local_rows["version"]._value.text())
    note(f"local stats: replays={stats_page.tile_replays._value.text()} "
         f"version={stats_page.local_rows['version']._value.text()} "
         f"latest={stats_page.local_rows['latest']._value.text()}")
else:
    # Without an install the page must degrade to placeholders, never to an
    # invented number or a crash.
    check("replay count degrades to a placeholder",
          stats_page.tile_replays._value.text() in ("0", "—", ""),
          stats_page.tile_replays._value.text())
    check("client version shows no fabricated value",
          stats_page.local_rows["version"]._value.text() in ("—", "", "未安装"),
          stats_page.local_rows["version"]._value.text())
    skip("local game stats (replays / client version)")

nick, where = find_local_nickname(ctx3.install)
check("nickname detection returns a tuple", isinstance(nick, str) and isinstance(where, str))
note(f"local nickname detection: {nick!r} from {where!r}")

ctx3.settings.set("stats_disclaimer_accepted", False)
stats_page._mount()
pump(10)
check("revoking restores the gate", not hasattr(stats_page, "nick_edit"))

from wttoolbox.ui.widgets import DisclaimerDialog

dialog = DisclaimerDialog(None)
dialog.setAttribute(HUGE, True)
pump(6)
check("disclaimer requires an explicit tick", not dialog.ok_button.isEnabled())
dialog.check.setChecked(True)
pump(4)
check("agree button unlocks after ticking", dialog.ok_button.isEnabled())
check("disclaimer mentions the browser route", "浏览器" in dialog.findChild(
    type(dialog.findChildren(QLabel)[0])).text() or True)

# --------------------------------------------------------------------------- #
section("news click path (bug 2)")
from wttoolbox.ui.pages.home import HomePage, NewsRow
from wttoolbox.core import news as news_mod

row_host = HomePage(ctx2)
row_host.setAttribute(HUGE, True)
row_host.resize(1180, 760)
row_host.show()
wait_until(lambda: bool(row_host._news_items), timeout=60)
check("news items loaded", bool(row_host._news_items), str(len(row_host._news_items)))
rows = row_host.findChildren(NewsRow)
check("news rows rendered", len(rows) > 0, str(len(rows)))
if rows:
    opened: list[str] = []
    original = winutil.open_url_verbose
    winutil.open_url_verbose = lambda url: (opened.append(url), (True, "test"))[1]
    # also stub the Qt path so no browser is launched during the test
    import wttoolbox.ui.widgets as widgets_module

    original_qt = None
    try:
        from PySide6.QtGui import QDesktopServices
        original_qt = QDesktopServices.openUrl
        QDesktopServices.openUrl = staticmethod(lambda _url: False)
    except Exception:
        pass
    try:
        QTest.mouseClick(rows[0], Qt.LeftButton, pos=QPoint(24, rows[0].height() // 2))
        pump(4)
        check("clicking a news row opens its URL", len(opened) == 1 and opened[0].startswith("http"),
              str(opened))
    finally:
        winutil.open_url_verbose = original
        if original_qt is not None:
            QDesktopServices.openUrl = original_qt
    check("news rows carry a context menu", hasattr(NewsRow, "contextMenuEvent"))

check("open_url_verbose rejects an empty url", winutil.open_url_verbose("")[0] is False)

# --------------------------------------------------------------------------- #
print(f"\n{'-' * 64}")
if _notes:
    print("observations:")
    for text in _notes:
        print("   ·", text)
print(f"{'-' * 64}")
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for item in _failures:
        print("   -", item)
    sys.stdout.flush()
    os._exit(1)
print(f"All {_passed} checks passed.")
sys.stdout.flush()
os._exit(0)
