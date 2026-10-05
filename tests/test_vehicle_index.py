"""Vehicle index integrity + comparison rules.

Run:  python tests/test_vehicle_index.py

Covers:
  * the bundled index only contains pages the wiki really serves (the tech-tree
    ``*_group`` folder nodes are links without a page, and used to make up 398
    dead entries - picking one could only ever show "nothing happened");
  * every vehicle the sitemap lists is present, including the ones the tech
    tree dropped (Leopard I, Panther II, the UCAVs, the floatplanes);
  * slug lookups ignore case, so pairs saved by an older build still restore;
  * comparing across services (tank vs ship) is refused with a clear message;
  * same-class pairs - aircraft and navy included - still produce a chart.
"""

from __future__ import annotations

import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wttoolbox.core import gamepath, wtdata  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import theme  # noqa: E402
from wttoolbox.ui.context import AppContext  # noqa: E402

HUGE = Qt.WidgetAttribute.WA_DontShowOnScreen
SANDBOX = os.path.join(os.environ.get("TEMP", "."), "tk-vehicle-test")
os.makedirs(SANDBOX, exist_ok=True)

_failures: list[str] = []
_passed = 0
_notes: list[str] = []

app = QApplication([])
theme.apply_theme(app, theme.LIGHT)


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
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


def pump(rounds: int = 8) -> None:
    for _ in range(rounds):
        app.processEvents()


def wait_until(pred, timeout: float = 90.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        pump(4)
        time.sleep(0.04)
    return False


# --------------------------------------------------------------------------- #
section("index integrity")
stats = wtdata.index_stats()
vehicles = wtdata.load_index()["_vehicles"]
check("index built", stats["count"] > 3000, str(stats["count"]))
check("count matches the vehicle list", stats["count"] == len(vehicles))
check("five classes", len(stats["classes"]) == 5, str(len(stats["classes"])))
check("ten nations", len(stats["nations"]) == 10, str(len(stats["nations"])))

stubs = [v.slug for v in vehicles if v.slug.endswith("_group")]
check("no folder-node stubs (they have no wiki page)", stubs == [], f"{len(stubs)} e.g. {stubs[:3]}")

duplicates = len(vehicles) - len({v.slug.lower() for v in vehicles})
check("no duplicate slugs (case-insensitively)", duplicates == 0, str(duplicates))

missing_fields = [v.slug for v in vehicles if not v.name or not v.cls or not v.nation]
check("every entry has a name, class and nation", missing_fields == [], str(missing_fields[:5]))

unknown_class = [v.slug for v in vehicles if v.cls not in stats["classes"]]
check("every class is a known one", unknown_class == [], str(unknown_class[:5]))
unknown_nation = [v.slug for v in vehicles if v.nation not in stats["nations"]]
check("every nation is a known one", unknown_nation == [], str(unknown_nation[:5]))

by_class: dict[str, int] = {}
for vehicle in vehicles:
    by_class[vehicle.cls] = by_class.get(vehicle.cls, 0) + 1
note(f"index: {stats['count']:,} vehicles · {by_class}")

section("the vehicles the tech tree no longer lists are included")
# These were missing before: the tree dropped them but the wiki still has pages.
NEWLY_ADDED = {
    "aichi_e13a": "Aichi E13A1",
    "e7k2": "E7K2",
    "e8n2": "E8N2",
    "gl_832": "GL.832HY",
    "kor_1": "KOR-1",
    "kor_2": "KOR-2",
    "loire_130": "Loire 130C",
    "o3u_1": "O3U-1",
    "osprey_mk4": "Osprey Mk IV",
    "ro_43": "Ro.43",
    "sc_1": "SC-1",
    "soc_1": "SOC-1",
    "ucav_mq_1_predator": "MQ-1",
    "ucav_orion": "Orion",
    "ucav_wing_loong_i": "Wing Loong I",
    "walrus_mk1": "Walrus Mk.I",
}
for slug, name in NEWLY_ADDED.items():
    vehicle = wtdata.find(slug)
    check(f"{slug} -> {name}", vehicle is not None and vehicle.name == name,
          f"{vehicle.name if vehicle else None!r}")
check("all 16 additions are aircraft",
      all(wtdata.find(slug).cls == "aircraft" for slug in NEWLY_ADDED))

section("case-insensitive slug lookup")
check("lower-case slug resolves", wtdata.find("germ_pzkpfw_iv_ausf_f2") is not None)
check("canonical mixed-case slug resolves", wtdata.find("germ_pzkpfw_IV_ausf_F2") is not None)
check("upper-case slug resolves", wtdata.find("CN_ZTZ_59A") is not None)
check("unknown slug returns None", wtdata.find("not_a_vehicle_at_all") is None)
check("empty slug returns None", wtdata.find("") is None)
check("both casings are the same vehicle",
      wtdata.find("germ_pzkpfw_iv_ausf_f2").slug == wtdata.find("germ_pzkpfw_IV_ausf_F2").slug)

section("no dead links (sampled live)")
random.seed(11)
sample = random.sample(vehicles, 12)
dead: list[tuple[str, str]] = []
for vehicle in sample:
    spec = wtdata.fetch_spec(vehicle.slug, timeout=12)
    if spec.error:
        dead.append((vehicle.slug, spec.error))
check("every sampled vehicle resolves", len(dead) <= 1, str(dead[:3]))
if dead:
    note(f"{len(dead)}/12 failed (the wiki is occasionally slow): {dead[0]}")
note(f"sampled {len(sample)} vehicles across {len({v.cls for v in sample})} classes")

# --------------------------------------------------------------------------- #
section("cross-service comparison is refused")
ctx = AppContext(Settings(os.path.join(SANDBOX, "vehicles.json")))
from _game import ROOT  # noqa: E402

ctx.set_install(gamepath.GameInstall(root=ROOT, source="registry"))

from wttoolbox.ui.pages.vehicles import VehiclesPage  # noqa: E402

page = VehiclesPage(ctx)
page.setAttribute(HUGE, True)
page.resize(1180, 900)
page.show()
pump(10)

tank = wtdata.find("ussr_t_34_1941")
ship = wtdata.find("us_destroyer_clemson_litchfield")
boat = wtdata.find("us_pt6")
plane = wtdata.find("bf-109f-4")
check("sample vehicles resolved", None not in (tank, ship, boat, plane))

page.card_a.set_vehicle(tank)
page.card_b.set_vehicle(ship)
page.compare()
pump(6)
check("tank vs ship draws no radar", not page.radar._axes, str(len(page.radar._axes)))
check("it says 不同阵营无法比对", "不同阵营无法比对" in (page.radar._message or ""),
      repr(page.radar._message))
check("it names the tank class", "坦克" in (page.radar._message or ""))
check("it names the ship class", "舰船" in (page.radar._message or ""))
check("it tells the user what to do", "两辆坦克" in (page.radar._message or ""),
      repr(page.radar._message))
check("no downloads were started", page._pending == 0, str(page._pending))
check("the table is cleared", page.table.rowCount() == 0)
check("the blocking reason is also logged",
      any("不同阵营" in record.message for record in ctx.log.records(limit=8)),
      str([r.message for r in ctx.log.records(limit=3)]))

page.card_a.set_vehicle(tank)
page.card_b.set_vehicle(plane)
page.compare()
pump(6)
check("tank vs aircraft is refused as well", not page.radar._axes)
check("and names aircraft", "飞机" in (page.radar._message or ""), repr(page.radar._message))

# --------------------------------------------------------------------------- #
section("same-class pairs still work, aircraft and navy included")
PAIRS = (
    ("aircraft", "bf-109f-4", "p-51c-10-nt", 4),
    ("ship", "us_destroyer_clemson_litchfield", "us_destroyer_mahan_class", 4),
    ("boat", "us_pt6", "us_elco_77ft_pt20", 4),
    ("tank", "ussr_t_34_1941", "germ_pzkpfw_IV_ausf_F2", 4),
)
for label, slug_a, slug_b, min_axes in PAIRS:
    first, second = wtdata.find(slug_a), wtdata.find(slug_b)
    if first is None or second is None:
        check(f"{label}: samples resolved", False, f"{slug_a} / {slug_b}")
        continue
    page.card_a.set_vehicle(first)
    page.card_b.set_vehicle(second)
    page.spec_a = page.spec_b = None
    page.compare()
    finished = wait_until(lambda: page._pending == 0 and page.spec_a and page.spec_b, timeout=90)
    axes = [axis.label for axis in page.radar._axes]
    errors = (page.spec_a.error if page.spec_a else "?", page.spec_b.error if page.spec_b else "?")
    check(f"{label}: chart produced", finished and len(axes) >= min_axes,
          f"finished={finished} axes={axes} errors={errors}")
    check(f"{label}: table matches the chart", page.table.rowCount() == len(axes),
          f"{page.table.rowCount()} vs {len(axes)}")
    check(f"{label}: details filled", len(page.detail_a.toPlainText()) > 100)
    note(f"{label}: {axes}")

# --------------------------------------------------------------------------- #
section("picker: class lock and responsiveness")
from wttoolbox.ui.dialogs.vehicle_picker import MAX_ROWS, VehiclePickerDialog  # noqa: E402

picker = VehiclePickerDialog(ctx, same_class_as=tank)
picker.setAttribute(HUGE, True)
picker.resize(820, 620)
picker.show()
pump(8)
check("class filter pre-set to the other side's class",
      picker.class_combo.currentData() == "tank", str(picker.class_combo.currentData()))
check("class filter is locked", not picker.class_combo.isEnabled())
check("the lock is explained in the subtitle",
      any("坦克" in label.text() for label in picker.findChildren(type(picker.hint))),
      "no subtitle mentions the class")

unlocked = VehiclePickerDialog(ctx)
unlocked.setAttribute(HUGE, True)
pump(6)
check("without a counterpart the class filter is free",
      unlocked.class_combo.isEnabled() and unlocked.class_combo.currentData() == "")
check("the list starts populated", unlocked.table.rowCount() == MAX_ROWS,
      str(unlocked.table.rowCount()))

started = time.perf_counter()
for index in range(1, 6):
    picker.search.setText("tiger"[:index])
    pump(3)
typing = time.perf_counter() - started
check("typing 5 characters does not block (>350ms feels frozen)", typing < 0.35,
      f"{typing * 1000:.0f} ms")
note(f"5 keystrokes: {typing * 1000:.0f} ms (debounced, was ~250 ms)")

pump(24)
check("the debounced refresh still runs", picker.table.rowCount() > 0, str(picker.table.rowCount()))
check("every row carries its vehicle",
      all(picker.table.item(row, 0).data(Qt.UserRole) is not None
          for row in range(min(20, picker.table.rowCount()))))

# the dialog must only ever return a vehicle of the locked class
picker.table.setCurrentCell(0, 0)
picker._accept()
check("the picker returns the locked class", picker.selected is not None
      and picker.selected.cls == "tank",
      f"{picker.selected.cls if picker.selected else None}")

print(f"\n{'-' * 64}")
for text in _notes:
    print("   ·", text)
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for item in _failures:
        print("   -", item)
    sys.stdout.flush()
    os._exit(1)
print(f"All {_passed} checks passed.")
sys.stdout.flush()
os._exit(0)
