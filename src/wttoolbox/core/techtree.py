"""Tech trees, research paths and research costs.

All of it comes from data published by the War Thunder wiki:

* ``assets/tech_trees.json`` - the grid each nation's tree is drawn on, built by
  ``tools/build_tech_trees.py``: per nation and rank, rows holding a
  ``research`` half and a ``premium`` half, with empty cells kept so that a
  column index means the same thing in every rank.
* ``assets/vehicle_data.json`` - per vehicle: rank, battle rating, research
  points, purchase price, armour and the published penetration tables, built by
  ``tools/build_vehicle_data.py``.

Prerequisites
-------------
``data-unit-req`` gives the vehicle that must be researched first.  Two gaps in
that field are filled here, and the app says so in the UI:

* it can name a **folder** (``germ_prewar_pz_iii_group``); the real requirement
  is that folder's vehicles in order, so the whole folder joins the path;
* it is **not repeated on the first vehicle of a column in a new rank**.  Those
  links are reconstructed from the column index, which is why the builder keeps
  empty cells instead of compacting rows.

What the cost model does and does not claim
-------------------------------------------
The totals are a **lower bound computed from the wiki's own numbers**, and the
plan lists every vehicle it counted so the figure can be checked in the game.
Two assumptions are stated in the result rather than hidden: silver lions are
the purchase price of every vehicle on the path, and vehicles added only to
satisfy a rank gate are chosen cheapest-first (a greedy minimum, not a proven
global one).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field

from . import appdirs

# Re-entrant: building the World calls tech_trees()/index()/vehicle_data(),
# which take this same lock.  A plain Lock deadlocks here.
_LOCK = threading.RLock()
_TREES: dict | None = None
_DATA: dict | None = None
_INDEX: dict | None = None
_WORLD: "World | None" = None

#: How many vehicles of the previous rank must be researched to open a rank.
#: The wiki's explanation page is rendered client-side and cannot be scraped, so
#: the numbers come from the game's own requirement dialogs (supplied by the
#: project owner) and are kept as an editable setting.
#:
#: Ground  : rank II 4, rank III 5, ranks IV-V 6, rank VI and up 5
#: Aviation: rank II 3, ranks III-V 6, ranks VI-VIII 5, rank IX 3
#: Navy    : every vehicle of the previous rank, capped at six
#: Helicopter trees start at rank V, so only ranks VI and VII have a gate; they
#:          follow the aviation numbers and the app labels that as an assumption.
RANK_RULES: dict[str, object] = {
    "tank": {2: 4, 3: 5, 4: 6, 5: 6, 6: 5, 7: 5, 8: 5, 9: 5, 10: 5},
    "aircraft": {2: 3, 3: 6, 4: 6, 5: 6, 6: 5, 7: 5, 8: 5, 9: 3, 10: 5},
    "helicopter": {6: 5, 7: 5},
    "ship": "all_up_to_6",
    "boat": "all_up_to_6",
}

#: The navy rule researches everything in the previous rank up to this many.
NAVY_CAP = 6

RANK_LABELS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII",
               8: "VIII", 9: "IX", 10: "X"}

RULE_SOURCES = {
    "tank": "陆战：II 级 4 辆、III 级 5 辆、IV–V 级 6 辆、VI 级及以后 5 辆",
    "aircraft": "空军：II 级 3 辆、III–V 级 6 辆、VI–VIII 级 5 辆、IX 级 3 辆",
    "helicopter": "直升机：树从 V 级开始，VI/VII 级沿用空军的 5 辆（规则未单独给出）",
    "ship": f"海军：需研发上一级全部载具，超过 {NAVY_CAP} 辆时只需 {NAVY_CAP} 辆",
    "boat": f"海军：需研发上一级全部载具，超过 {NAVY_CAP} 辆时只需 {NAVY_CAP} 辆",
}


def rank_rule_of(vehicle_class: str) -> object:
    return RANK_RULES.get(vehicle_class, {})


def gate_requirement(vehicle_class: str, rank: int, available: int) -> int:
    """Vehicles of ``rank - 1`` needed to open ``rank``.

    ``available`` is how many researchable vehicles the previous rank has in
    this nation's tree, which is what the navy rule counts.
    """
    rule = RANK_RULES.get(vehicle_class)
    if rule == "all_up_to_6":
        return min(available, NAVY_CAP)
    if isinstance(rule, dict):
        try:
            return int(rule.get(rank, 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
    return 0

REASON_PATH = "路径"
REASON_GATE = "阶级门槛"
REASON_TARGET = "目标"
REASON_INFERRED = "列连接"


def _asset(name: str) -> str:
    return appdirs.resource_path("assets", name)


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def tech_trees() -> dict:
    global _TREES
    with _LOCK:
        if _TREES is None:
            _TREES = _load_json(_asset("tech_trees.json")).get("trees", {})
        return _TREES


def vehicle_data() -> dict:
    """``{lower-case slug: record}`` - costs, armour and penetration tables."""
    global _DATA
    with _LOCK:
        if _DATA is None:
            raw = _load_json(_asset("vehicle_data.json")).get("vehicles", {})
            _DATA = {key.lower(): value for key, value in raw.items()}
        return _DATA


def index() -> dict:
    """``{lower-case slug: {name, class, nation, rank}}`` from the bundled index."""
    global _INDEX
    with _LOCK:
        if _INDEX is None:
            raw = _load_json(_asset("vehicles.json")).get("vehicles", [])
            _INDEX = {v["slug"].lower(): v for v in raw}
        return _INDEX


# --------------------------------------------------------------------------- #
# the whole tree, resolved once
# --------------------------------------------------------------------------- #

@dataclass
class Node:
    slug: str
    name: str
    cls: str
    nation: str
    rank: int
    half: str = "research"          # "research" | "premium"
    row: int = 0
    col: int = 0
    req: str = ""                   # explicit prerequisite from the wiki
    linked: str = ""                # prerequisite after filling the gaps
    origin: str = ""                # "wiki" | "column" | "folder" | ""
    premium: bool = False
    folder: str = ""
    folder_label: str = ""
    order: int = 0

    @property
    def researchable(self) -> bool:
        return self.half == "research" and not self.premium


class World:
    """Every tree, with prerequisites resolved and columns linked."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.order: list[str] = []
        self.folders: dict[str, list[str]] = {}
        self.by_class_nation: dict[tuple[str, str], list[Node]] = {}
        self.researchable_slugs: list[str] = []
        self._build()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        trees = tech_trees()
        counter = 0
        for cls, nations in trees.items():
            for nation, ranks in nations.items():
                bucket = self.by_class_nation.setdefault((cls, nation), [])
                # Remember the last vehicle seen in each column so a column can
                # continue into the next rank.
                column_tail: dict[str, str] = {}
                for rank_block in ranks:
                    rank = rank_block.get("rank") or 0
                    for r_index, row in enumerate(rank_block.get("rows", [])):
                        for half in ("research", "premium"):
                            for c_index, cell in enumerate(row.get(half) or []):
                                if cell is None:
                                    continue
                                units = cell["units"] if cell["type"] == "folder" else [cell]
                                folder = cell.get("group", "")
                                label = cell.get("label", "")
                                if folder:
                                    self.folders.setdefault(folder.lower(), [])
                                for unit in units:
                                    slug = unit["slug"]
                                    key = slug.lower()
                                    counter += 1
                                    node = Node(
                                        slug=slug,
                                        name=unit.get("name") or "",
                                        cls=cls,
                                        nation=nation,
                                        rank=rank,
                                        half=half,
                                        row=r_index,
                                        col=c_index,
                                        req=(unit.get("req") or "").lower(),
                                        premium=bool(unit.get("premium")),
                                        folder=folder,
                                        folder_label=label,
                                        order=counter,
                                    )
                                    # first occurrence wins (the wiki repeats a
                                    # slug once, in rank VIII)
                                    if key not in self.nodes:
                                        self.nodes[key] = node
                                        self.order.append(key)
                                        bucket.append(node)
                                    if folder:
                                        self.folders.setdefault(folder.lower(), []).append(key)

                # second pass: link columns across ranks
                for node in bucket:
                    if node.premium or node.half != "research":
                        continue
                    column = f"{node.half}:{node.col}"
                    head = column_tail.get(column)
                    if node.req:
                        node.linked = node.req
                        node.origin = "wiki"
                    elif head:
                        node.linked = head
                        node.origin = "column"
                    if self._tail_of(node):
                        column_tail[column] = self._tail_of(node)

    def _tail_of(self, node: Node) -> str:
        """The vehicle that carries a column forward: a folder's last member."""
        if node.folder:
            members = self.folders.get(node.folder.lower(), [])
            return members[-1] if members else node.slug.lower()
        return node.slug.lower()

    # -- lookups ----------------------------------------------------------- #
    def get(self, slug: str) -> Node | None:
        return self.nodes.get((slug or "").strip().lower())

    def name_of(self, slug: str) -> str:
        record = index().get((slug or "").lower())
        if record and record.get("name"):
            return record["name"]
        node = self.get(slug)
        return (node.name if node and node.name else slug) if node else slug

    def rank_of(self, slug: str) -> int:
        record = vehicle_data().get((slug or "").lower(), {})
        if record.get("rank"):
            return int(record["rank"])
        node = self.get(slug)
        return node.rank if node else 0

    def cost_of(self, slug: str) -> tuple[int | None, int | None]:
        record = vehicle_data().get((slug or "").lower(), {})
        return record.get("rp"), record.get("sl")

    def researchable(self, slug: str) -> bool:
        """True when the vehicle is earned by playing the tree rather than bought.

        Reserve / starter vehicles have no published research cost (they are
        free), but they are still vehicles of their rank, so they count towards
        a rank gate.  Only premiums are excluded - those cannot be researched
        with research points at all.
        """
        node = self.get(slug)
        if node is None:
            return False
        return not node.premium

    def chain(self, slug: str) -> list[str]:
        """Prerequisites of ``slug``, oldest first, excluding ``slug`` itself.

        Folder prerequisites expand to the folder's members so the whole folder
        ends up on the path.
        """
        chain: list[str] = []
        seen: set[str] = set()

        def push(key: str) -> None:
            if not key or key in seen:
                return
            seen.add(key)
            members = self.folders.get(key)
            if members:
                for member in members:
                    push(member)
                return
            chain.append(key)
            node = self.nodes.get(key)
            if node and node.linked:
                push(node.linked)
            elif node and node.folder:
                members = self.folders.get(node.folder.lower(), [])
                for member in members:
                    if member != key:
                        push(member)

        node = self.get(slug)
        if node and node.linked:
            push(node.linked)
        chain.reverse()
        return chain


def world() -> World:
    global _WORLD
    with _LOCK:
        if _WORLD is None:
            _WORLD = World()
        return _WORLD


# --------------------------------------------------------------------------- #
# convenience wrappers used by the UI
# --------------------------------------------------------------------------- #

def classes() -> list[str]:
    order = ("tank", "aircraft", "helicopter", "ship", "boat")
    present = list(tech_trees())
    return [c for c in order if c in present]


def nations_for(vehicle_class: str) -> list[str]:
    return sorted(tech_trees().get(vehicle_class, {}))


def find_node(slug: str) -> Node | None:
    return world().get(slug)


def ranks_for(vehicle_class: str, nation: str) -> list[dict]:
    return tech_trees().get(vehicle_class, {}).get(nation, [])


def nodes_for(vehicle_class: str, nation: str) -> list[Node]:
    return world().by_class_nation.get((vehicle_class, nation), [])


# --------------------------------------------------------------------------- #
# research plan
# --------------------------------------------------------------------------- #

@dataclass
class Step:
    slug: str
    name: str
    rank: int
    rp: int
    sl: int
    reason: str
    origin: str = ""

    @property
    def known(self) -> bool:
        return self.rp > 0


@dataclass
class Plan:
    slug: str
    name: str
    cls: str
    nation: str
    rank: int
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    free_vehicles: list[str] = field(default_factory=list)
    missing_costs: list[str] = field(default_factory=list)
    inferred_links: list[str] = field(default_factory=list)
    rank_rules: dict[int, int] = field(default_factory=dict)
    rule_source: str = ""
    premium: bool = False

    @property
    def research_points(self) -> int:
        return sum(step.rp for step in self.steps)

    @property
    def silver_lions(self) -> int:
        return sum(step.sl for step in self.steps)

    @property
    def node_count(self) -> int:
        return len(self.steps)

    @property
    def gate_vehicles(self) -> list[Step]:
        return [step for step in self.steps if step.reason == REASON_GATE]

    @property
    def complete(self) -> bool:
        return not self.missing_costs


def _candidates_for_rank(nation: str, vehicle_class: str, rank: int) -> list[tuple[int, str]]:
    """Researchable vehicles of ``rank``, cheapest first.

    Reserve vehicles (no published cost) sort first at 0 points, which is what
    a minimum-research-points path wants.
    """
    out: list[tuple[int, str]] = []
    for node in nodes_for(vehicle_class, nation):
        if node.rank != rank or not world().researchable(node.slug):
            continue
        rp, _sl = world().cost_of(node.slug)
        out.append((int(rp or 0), node.slug.lower()))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def research_plan(slug: str, rank_rules: dict | None = None) -> Plan | None:
    """Minimum research points and silver lions to research ``slug``.

    ``rank_rules`` optionally overrides :data:`RANK_RULES` for this one call,
    which is how the settings page lets the numbers be corrected.
    """
    w = world()
    node = w.get(slug)
    if node is None:
        return None
    rules = rank_rules if rank_rules is not None else RANK_RULES
    plan = Plan(
        slug=node.slug, name=w.name_of(node.slug), cls=node.cls, nation=node.nation,
        rank=node.rank, rank_rules={}, premium=node.premium,
        rule_source=RULE_SOURCES.get(node.cls, ""),
    )

    if not w.researchable(node.slug):
        plan.notes.append(
            "这辆车是礼包 / 金币 / 活动载具，不能用研发点解锁，所以没有研发成本。"
        )
        plan.steps.append(Step(node.slug, plan.name, node.rank, 0, 0, REASON_TARGET))
        return plan

    counted: dict[str, str] = {}

    for item in w.chain(node.slug):
        counted.setdefault(item, REASON_PATH)

    # Rank gates.  The navy rule counts the previous rank's researchable
    # vehicles, so gather them per rank first.
    def researchable_slugs_in(rank: int) -> list[str]:
        return [
            n.slug.lower()
            for n in nodes_for(node.cls, node.nation)
            if n.rank == rank and w.researchable(n.slug)
        ]

    for rank in range(2, node.rank + 1):
        available = researchable_slugs_in(rank - 1)
        needed = gate_requirement(node.cls, rank, len(available))
        plan.rank_rules[rank] = needed
        if needed <= 0:
            continue
        have = [s for s in counted if w.rank_of(s) == rank - 1]
        if len(have) >= needed:
            continue
        for _rp, candidate in _candidates_for_rank(node.nation, node.cls, rank - 1):
            if len([s for s in counted if w.rank_of(s) == rank - 1]) >= needed:
                break
            if candidate in counted:
                continue
            for extra in w.chain(candidate):
                counted.setdefault(extra, REASON_GATE)
            counted.setdefault(candidate, REASON_GATE)
        have = [s for s in counted if w.rank_of(s) == rank - 1]
        if len(have) < needed:
            plan.notes.append(
                f"第 {RANK_LABELS.get(rank, rank)} 级需要 {needed} 辆上一级载具，"
                f"本树里只有 {len(have)} 辆可研发，已按实际数量计算。"
            )

    counted[node.slug] = REASON_TARGET

    steps: list[Step] = []
    order = {key: w.nodes[key].order for key in w.order}
    for key, reason in counted.items():
        rp, sl = w.cost_of(key)
        entry = w.get(key)
        if rp is None and sl is None and not (entry and entry.premium):
            # Reserve / starter vehicles have no Research card on the wiki at
            # all: they are unlocked from the start and cost nothing.  That is
            # different from a missing value, so do not report it as one.
            plan.free_vehicles.append(w.name_of(key))
        elif rp is None:
            plan.missing_costs.append(w.name_of(key))
        elif rp == 0:
            plan.free_vehicles.append(w.name_of(key))
        if entry is not None and entry.origin == "column" and reason == REASON_PATH:
            plan.inferred_links.append(f"{entry.name or w.name_of(key)} ← {w.name_of(entry.linked)}")
        steps.append(
            Step(
                slug=key, name=w.name_of(key), rank=w.rank_of(key),
                rp=int(rp or 0), sl=int(sl or 0), reason=reason,
                origin=(entry.origin if entry else ""),
            )
        )
    steps.sort(key=lambda step: (step.rank or 99, order.get(step.slug, 10 ** 6)))
    plan.steps = steps

    if plan.inferred_links:
        plan.notes.append(
            "其中 " + str(len(plan.inferred_links)) + " 处前置由「同一列上下相邻」推导"
            "（Wiki 未显式标注这类跨阶级连接）：" + "；".join(plan.inferred_links[:4])
            + ("…" if len(plan.inferred_links) > 4 else "")
        )
    if plan.free_vehicles:
        plan.notes.append(
            "备用 / 初始载具（Wiki 未公布研发成本，按免费计）："
            + "、".join(sorted(set(plan.free_vehicles))[:8])
        )
    if plan.missing_costs:
        plan.notes.append(
            "以下载具 Wiki 未公布研发点，未计入合计："
            + "、".join(sorted(set(plan.missing_costs))[:8])
        )
    plan.notes.append("银狮按「沿途每辆车都购买」合计；若只要求研发不要求购买，银狮会少于该值。")
    if plan.gate_vehicles:
        plan.notes.append(
            f"其中 {len(plan.gate_vehicles)} 辆是为了解锁阶级而补的"
            "（按研发点从低到高选取，是贪心最小值，不保证全局最优）。"
        )
    return plan


def vehicle_summary(slug: str) -> dict:
    """Everything the UI needs about one vehicle."""
    w = world()
    record = vehicle_data().get((slug or "").lower(), {})
    node = w.get(slug)
    entry = index().get((slug or "").lower(), {})
    return {
        "slug": slug,
        "name": w.name_of(slug),
        "class": entry.get("class") or (node.cls if node else ""),
        "nation": entry.get("nation") or (node.nation if node else ""),
        "rank": record.get("rank") or (node.rank if node else None),
        "br": record.get("br", ""),
        "rp": record.get("rp"),
        "sl": record.get("sl"),
        "armour": record.get("armour", {}),
        "weapons": record.get("weapons", []),
        "premium": bool(node.premium) if node else False,
        "req": node.req if node else "",
        "linked": node.linked if node else "",
        "link_origin": node.origin if node else "",
        "folder": node.folder if node else "",
        "folder_label": node.folder_label if node else "",
        "in_tree": node is not None,
    }


def stats() -> dict:
    w = world()
    per_class = {}
    for cls, nations in tech_trees().items():
        total = research = premium = 0
        for rank_block_list in nations.values():
            for rank_block in rank_block_list:
                for row in rank_block.get("rows", []):
                    for half, label in (("research", "r"), ("premium", "p")):
                        for cell in row.get(half) or []:
                            if cell is None:
                                continue
                            count = len(cell["units"]) if cell["type"] == "folder" else 1
                            total += count
                            if label == "r":
                                research += count
                            else:
                                premium += count
        per_class[cls] = {
            "vehicles": total, "researchable": research, "premium": premium,
            "nations": len(nations),
        }
    data = vehicle_data()
    return {
        "per_class": per_class,
        "data_records": len(data),
        "vehicles_with_cost": sum(1 for r in data.values() if r.get("rp") is not None),
        "vehicles_with_armour": sum(1 for r in data.values() if r.get("armour")),
        "vehicles_with_shells": sum(1 for r in data.values() if r.get("weapons")),
        "inferred_links": sum(1 for key in w.order if w.nodes[key].origin == "column"),
        "folders": len(w.folders),
    }
