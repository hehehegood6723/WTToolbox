"""Tech tree, research plan and penetration comparison.

Run:  python tests/test_techtree.py

Everything asserted here is derived from the bundled wiki crawl, so the checks
are about the *logic*: that the tree grid is complete, that prerequisites resolve
through folders and across ranks, that the rank-unlock numbers the game uses are
applied per vehicle class, and that the penetration comparison only ever reports
what the published tables support.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wttoolbox.core import penetration, techtree  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import theme  # noqa: E402
from wttoolbox.ui.context import PAGE_ORDER, AppContext  # noqa: E402

HUGE = Qt.WidgetAttribute.WA_DontShowOnScreen
SANDBOX = os.path.join(os.environ.get("TEMP", "."), "tk-techtree-suite")
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


def pump(rounds: int = 10) -> None:
    for _ in range(rounds):
        app.processEvents()


world = techtree.world()

# --------------------------------------------------------------------------- #
section("the bundled tree is complete")
stats = techtree.stats()
check("five vehicle classes", len(techtree.classes()) == 5, str(techtree.classes()))
check("all ten nations have a ground tree", len(techtree.nations_for("tank")) == 10,
      str(techtree.nations_for("tank")))
check("seven nations have a navy", len(techtree.nations_for("ship")) == 7,
      str(techtree.nations_for("ship")))
check("tree holds more than 3,000 vehicles", len(world.nodes) > 3000, str(len(world.nodes)))
check("folders were parsed", stats["folders"] > 300, str(stats["folders"]))
check("aircraft reach rank IX", 9 in {n.rank for n in world.nodes.values() if n.cls == "aircraft"})
check("tanks stop at rank VIII", 9 not in {n.rank for n in world.nodes.values() if n.cls == "tank"})
note(f"tree: {len(world.nodes)} vehicles, {stats['folders']} folders, "
     f"{stats['inferred_links']} column links inferred")

check("every node resolves to a display name",
      not [k for k in world.nodes if not world.name_of(k) or world.name_of(k) == k],
      str([k for k in world.nodes if world.name_of(k) == k][:5]))
check("every node has a rank", not [k for k, n in world.nodes.items() if not n.rank])
check("no duplicate slugs", len(world.nodes) == len(set(world.nodes)))

# the layout must match the game for a tree we can verify by hand
usa_rank1 = [
    node.name
    for node in techtree.nodes_for("tank", "usa")
    if node.rank == 1 and node.half == "research"
]
check("USA rank I starts with the known first row",
      usa_rank1[:5] == ["M2A4", "M2", "LVT(A)(1)", "M13 MGMC", "M2A2"],
      str(usa_rank1[:6]))
check("premium vehicles are a separate half",
      any(node.half == "premium" for node in techtree.nodes_for("tank", "germany")),
      "no premium half found")

# --------------------------------------------------------------------------- #
section("prerequisites")
check("most vehicles carry a wiki prerequisite",
      sum(1 for n in world.nodes.values() if n.req) > 700,
      str(sum(1 for n in world.nodes.values() if n.req)))
check("cross-rank column links were inferred",
      stats["inferred_links"] > 50, str(stats["inferred_links"]))

# a folder's members chain inside the folder, and a later vehicle requires the
# folder - the chain must expand to the members rather than stopping at the
# group slug
m4 = world.get("us_m4_sherman")
check("the M4 Sherman is in a folder", m4 is not None and m4.folder == "us_sherman_group",
      str(m4.folder if m4 else None))
chain = world.chain("us_m4_sherman")
check("the chain reaches back to rank I",
      any(world.rank_of(s) == 1 for s in chain),
      str([(s, world.rank_of(s)) for s in chain]))
check("no folder slug leaks into the chain",
      not [s for s in chain if s.endswith("_group")],
      str([s for s in chain if s.endswith("_group")]))
check("the chain is ordered oldest first",
      [world.rank_of(s) for s in chain] == sorted(world.rank_of(s) for s in chain),
      str([world.rank_of(s) for s in chain]))
check("the chain never contains the target",
      "us_m4_sherman" not in chain)

# --------------------------------------------------------------------------- #
section("rank-unlock rules the game uses")
check("ground: II 4, III 5, IV 6, V 6, VI 5",
      [techtree.gate_requirement("tank", r, 99) for r in (2, 3, 4, 5, 6)] == [4, 5, 6, 6, 5],
      str([techtree.gate_requirement("tank", r, 99) for r in (2, 3, 4, 5, 6)]))
check("aviation: II 3, III-V 6, VI-VIII 5, IX 3",
      [techtree.gate_requirement("aircraft", r, 99) for r in range(2, 10)]
      == [3, 6, 6, 6, 5, 5, 5, 3],
      str([techtree.gate_requirement("aircraft", r, 99) for r in range(2, 10)]))
for count, expected in ((3, 3), (6, 6), (10, 6)):
    check(f"navy: {count} researchable -> {expected}",
          techtree.gate_requirement("ship", 3, count) == expected,
          str(techtree.gate_requirement("ship", 3, count)))
check("helicopter rank V is not gated by another helicopter rank",
      techtree.gate_requirement("helicopter", 5, 99) == 0,
      str(techtree.gate_requirement("helicopter", 5, 99)))
check("each later helicopter rank needs one of the rank below",
      [techtree.gate_requirement("helicopter", r, 99) for r in (6, 7)] == [1, 1],
      str([techtree.gate_requirement("helicopter", r, 99) for r in (6, 7)]))
check("the helicopter entry rank and classes are the documented ones",
      techtree.HELICOPTER_ENTRY_RANK == 5
      and techtree.HELICOPTER_ENTRY_CLASSES == ("tank", "aircraft"),
      f"{techtree.HELICOPTER_ENTRY_RANK} {techtree.HELICOPTER_ENTRY_CLASSES}")

# --------------------------------------------------------------------------- #
section("research plan")
for slug, rank in (("ussr_t_34_1941", 2), ("germ_pzkpfw_IV_ausf_F2", 2)):
    plan = techtree.research_plan(slug)
    check(f"{slug} plan exists", plan is not None)
    if plan:
        check(f"{slug} totals are positive", plan.research_points > 0 and plan.silver_lions > 0,
              f"{plan.research_points} / {plan.silver_lions}")
        check(f"{slug} totals match its steps",
              plan.research_points == sum(s.rp for s in plan.steps)
              and plan.silver_lions == sum(s.sl for s in plan.steps))
        check(f"{slug} steps are ordered by rank",
              [s.rank or 0 for s in plan.steps] == sorted(s.rank or 0 for s in plan.steps))
        check(f"{slug} target is last or present",
              any(s.slug.lower() == slug.lower() and s.reason == techtree.REASON_TARGET
                  for s in plan.steps))
        note(f"{slug}: {plan.node_count} vehicles, {plan.research_points:,} RP, "
             f"{plan.silver_lions:,} SL")

deep = techtree.research_plan("us_m1_abrams")
check("a rank VII tank needs many vehicles", deep is not None and deep.node_count > 20,
      str(deep.node_count if deep else None))
if deep:
    check("rank gates contributed vehicles", len(deep.gate_vehicles) > 0,
          str(len(deep.gate_vehicles)))
    check("the plan explains its assumptions",
          any("银狮" in n for n in deep.notes) and any("贪心" in n for n in deep.notes),
          str(deep.notes))

nine = next((n for n in world.nodes.values() if n.cls == "aircraft" and n.rank == 9), None)
check("a rank IX aircraft exists", nine is not None)
if nine:
    plan = techtree.research_plan(nine.slug)
    check("its plan resolves the IX gate", plan is not None and plan.rank_rules.get(9) == 3,
          str(plan.rank_rules.get(9) if plan else None))

# a premium must be reported as unbuyable with research points, not silently free
premium = next((n for n in world.nodes.values() if n.premium), None)
if premium:
    plan = techtree.research_plan(premium.slug)
    check("a premium explains itself",
          plan is not None and any("金币" in n for n in plan.notes), str(plan.notes if plan else None))
    check("a premium costs nothing in research points",
          plan is not None and plan.research_points == 0)

check("an unknown slug returns None", techtree.research_plan("not_a_vehicle") is None)

# --------------------------------------------------------------------------- #
section("helicopter entry rule")
# Helicopter trees open at rank V and are opened by a rank V ground or air
# vehicle of the same nation, not by a lower helicopter rank.
helis = [n for n in world.nodes.values() if n.cls == "helicopter" and n.rank == 5]
check("rank V helicopters exist in the tree", len(helis) > 0, str(len(helis)))
if helis:
    plan = techtree.research_plan(helis[0].slug)
    check("a rank V helicopter plan resolves", plan is not None)
    if plan:
        check("its own ranks contribute no helicopter gate",
              all(count == 0 for rank, count in plan.rank_rules.items() if rank <= 5),
              str(plan.rank_rules))
        check("it names a rank V ground or air vehicle as the entry",
              plan.entry_class in ("tank", "aircraft") and bool(plan.entry_name),
              f"{plan.entry_class!r} {plan.entry_name!r}")
        check("the entry cost is reported separately",
              plan.entry_rp > 0 and plan.entry_rp <= plan.research_points,
              f"{plan.entry_rp} of {plan.research_points}")
        check("the entry vehicle is counted in the steps",
              any(step.reason == techtree.REASON_ENTRY for step in plan.steps),
              str(sorted({s.reason for s in plan.steps})))
        check("the entry rule is explained to the user",
              any("五级" in note and "减去" in note for note in plan.notes),
              str(plan.notes)[:160])
        note(f"helicopter entry: {plan.entry_name} ({plan.entry_class}), "
             f"{plan.entry_rp:,} of {plan.research_points:,} RP")

    higher = [n for n in world.nodes.values() if n.cls == "helicopter" and n.rank >= 6]
    if higher:
        plan = techtree.research_plan(higher[0].slug)
        check("a later rank needs one helicopter of the rank below",
              plan is not None and all(
                  count == 1 for rank, count in plan.rank_rules.items() if rank >= 6
              ),
              str(plan.rank_rules if plan else None))
        if plan:
            check("the later rank also carries the entry cost", plan.entry_rp > 0,
                  str(plan.entry_rp))
            check("its own cost is the remainder",
                  plan.research_points - plan.entry_rp > 0,
                  str(plan.research_points - plan.entry_rp))

# --------------------------------------------------------------------------- #
section("penetration comparison uses only published data")
coverage = penetration.coverage()
check("the crawl produced armour and shell data",
      coverage["armour"] > 1000 and coverage["shells"] > 3000, str(coverage))
note(f"penetration coverage: {coverage}")

t34_shells = penetration.all_shells("ussr_t_34_1941")
check("T-34 shells were parsed", len(t34_shells) >= 4, str(len(t34_shells)))
aphe = next((s for s in t34_shells if s["name"].startswith("BR-350A")), None)
check("the BR-350A table is the published one",
      aphe is not None and aphe["pen"].get(10) == 87 and aphe["pen"].get(500) == 77,
      str(aphe["pen"] if aphe else None))
check("the nearest tabulated distance is used",
      penetration.penetration_at(aphe, 400) == 77 if aphe else False,
      str(penetration.penetration_at(aphe, 400) if aphe else None))

verdict = penetration.compare("ussr_t_34_1941", "germ_pzkpfw_IV_ausf_F2", distance=500)
check("comparison produced plates", verdict is not None and len(verdict.plates) == 6,
      str(len(verdict.plates) if verdict else None))
check("a 77 mm shell defeats 50 mm of armour",
      verdict is not None and verdict.outcome == "pen", str(verdict.outcome if verdict else None))
front = next((p for p in verdict.plates if p["label"] == "车体·正面"), None)
check("the front plate is reported with its thickness",
      front is not None and front["thickness"] == 50 and front["margin"] == 27,
      str(front))

thin = penetration.compare("germ_pzkpfw_IV_ausf_F2", "us_m1_abrams", distance=2000)
check("a weak round against heavy armour is a miss or a partial",
      thin is not None and thin.outcome in ("no", "partial"), str(thin.outcome if thin else None))

angled = penetration.compare("ussr_t_34_1941", "germ_pzkpfw_IV_ausf_F2", distance=500, angle=60)
check("an angled shot reports a larger effective thickness",
      angled is not None and angled.plates[0]["effective"] > angled.plates[0]["thickness"],
      str(angled.plates[0] if angled else None))
check("the angled result warns that the game reduces penetration",
      angled is not None and any("偏乐观" in n for n in angled.notes), str(angled.notes))

aircraft = penetration.compare("ussr_t_34_1941", "bf-109f-4", distance=500)
check("a target with no published armour reports unknown rather than guessing",
      aircraft is not None and aircraft.outcome == "unknown", str(aircraft.outcome if aircraft else None))

duel = penetration.duel("ussr_t_34_1941", "germ_pzkpfw_IV_ausf_F2", distance=500)
check("the duel covers both directions",
      duel["a_to_b"] is not None and duel["b_to_a"] is not None)
if duel["b_to_a"]:
    check("the reverse direction picks the strongest round",
          duel["b_to_a"]["verdict"].penetration >= 100,
          str(duel["b_to_a"]["verdict"].penetration))

check("shell types get a readable label",
      penetration.shell_label("APHEBC") != "APHEBC" and penetration.shell_label("HEATFS") != "HEATFS",
      penetration.shell_label("APHEBC"))

# --------------------------------------------------------------------------- #
section("the pages build and respond")
ctx = AppContext(Settings(os.path.join(SANDBOX, "ctx.json")))
check("the tech tree page is registered", "techtree" in PAGE_ORDER, str(PAGE_ORDER))

from wttoolbox.ui.pages.techtree import TechTreePage  # noqa: E402

tree_page = TechTreePage(ctx)
tree_page.setAttribute(HUGE, True)
tree_page.resize(1180, 860)
tree_page.show()
pump(16)
check("the tree renders cells", len(tree_page._cells) > 50, str(len(tree_page._cells)))
check("the nation list matches the class",
      tree_page.nation_combo.count() == len(techtree.nations_for("tank")),
      str(tree_page.nation_combo.count()))

target_node = next(n for n in world.nodes.values() if n.cls == "tank" and n.rank == 4)
tree_page._select(target_node)
pump(10)
check("selecting a vehicle fills the plan table",
      tree_page.plan_table.rowCount() == techtree.research_plan(target_node.slug).node_count,
      str(tree_page.plan_table.rowCount()))
check("the tiles show the totals",
      tree_page.tile_rp._value.text() != "—" and tree_page.tile_count._value.text() != "—",
      f"{tree_page.tile_rp._value.text()} / {tree_page.tile_count._value.text()}")
check("the rules are shown to the user", bool(tree_page.rule_label.text()))
check("the assumptions are shown to the user", bool(tree_page.notes.text()))

from wttoolbox.ui.pages.vehicles import VehiclesPage  # noqa: E402

vehicles_page = VehiclesPage(ctx)
vehicles_page.setAttribute(HUGE, True)
vehicles_page.resize(1180, 860)
vehicles_page.show()
pump(14)
check("the vehicles page now has both tabs", vehicles_page.subnav.nav.count() == 2,
      str(vehicles_page.subnav.nav.count()))

pen = vehicles_page.penetration
pen.shooter = "ussr_t_34_1941"
pen.target = "germ_pzkpfw_IV_ausf_F2"
pen._reload_shells()
pump(6)
check("the shell list is populated", pen.shell_combo.count() >= 4, str(pen.shell_combo.count()))
pen._compare()
pump(8)
check("the penetration table fills", pen.table.rowCount() == 6, str(pen.table.rowCount()))
check("the verdict is stated in words",
      "击穿" in pen.summary.text() and "反向" in pen.summary.text(), pen.summary.text()[:80])
check("the caveat is shown", "不是游戏的弹道模拟" in pen.notes.text(), pen.notes.text()[:60])

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
