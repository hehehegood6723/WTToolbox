"""Armour penetration comparison.

Uses only what the wiki publishes:

* per shell, penetration in mm at 10 / 100 / 500 / 1000 / 1500 / 2000 m,
  measured at 0 degrees (the ammunition tables on every vehicle page);
* per vehicle, armour thickness in mm for hull and turret,
  front / side / back.

What this module deliberately does **not** do
---------------------------------------------
It does not reimplement the game's ballistics.  War Thunder's slope effects,
normalisation, ricochet rules and overmatch behaviour are not published, so any
formula invented here would produce confident numbers that do not match the
game - the opposite of useful.  Two consequences are surfaced to the user rather
than hidden:

* the verdict is a straight comparison of the published penetration against the
  plate thickness, which is what the wiki's own tables support;
* an angled shot can be shown, but its effective thickness is plain geometry
  (``thickness / cos(angle)``) and the result says the real requirement is
  higher, because the game also reduces penetration at an angle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import techtree

DISTANCES = (10, 100, 500, 1000, 1500, 2000)
FACINGS = ("front", "side", "back")
FACING_LABELS = {"front": "正面", "side": "侧面", "back": "后面"}
PLATE_LABELS = {"hull": "车体", "turret": "炮塔"}
SHELL_KINDS = {
    "AP": "穿甲弹",
    "APBC": "被帽穿甲弹",
    "APC": "被帽穿甲弹",
    "APCBC": "风帽被帽穿甲弹",
    "APHE": "穿甲高爆弹",
    "APHEBC": "风帽被帽穿甲高爆弹",
    "APCR": "硬芯穿甲弹",
    "APDS": "脱壳穿甲弹",
    "APFSDS": "尾翼稳定脱壳穿甲弹",
    "HEAT": "破甲弹",
    "HEATFS": "尾翼稳定破甲弹",
    "HESH": "碎甲弹",
    "HE": "榴弹",
    "HE-TF": "榴弹（定时引信）",
    "Shrapnel": "榴霰弹",
    "Smoke": "烟雾弹",
    "Rocket": "火箭弹",
    "ATGM": "反坦克导弹",
    "AAM": "空空导弹",
    "AP-I": "穿甲燃烧弹",
}


def shell_label(shell_type: str) -> str:
    """A Chinese label for a shell type, falling back to the raw string."""
    key = (shell_type or "").strip()
    if key in SHELL_KINDS:
        return SHELL_KINDS[key]
    upper = key.upper()
    for prefix in ("APFSDS", "APHEBC", "APCBC", "APCR", "APDS", "APHE", "APBC", "APC", "HEATFS", "HEAT", "HESH", "AP", "HE"):
        if upper.startswith(prefix):
            return f"{SHELL_KINDS.get(prefix, prefix)}（{key}）"
    return key or "—"


def weapons_of(slug: str) -> list[dict]:
    record = techtree.vehicle_data().get((slug or "").lower(), {})
    return record.get("weapons", []) or []


def main_weapon(slug: str) -> dict | None:
    """The vehicle's main gun: the first weapon that is not a machine gun."""
    weapons = weapons_of(slug)
    for weapon in weapons:
        title = (weapon.get("title") or "").lower()
        if "machine gun" in title or "mg" == title[:2].strip():
            continue
        return weapon
    return weapons[0] if weapons else None


def all_shells(slug: str) -> list[dict]:
    """``[ {weapon, name, type, pen} ]`` for every shell the vehicle carries."""
    out: list[dict] = []
    for weapon in weapons_of(slug):
        for shell in weapon.get("shells", []) or []:
            out.append(
                {
                    "weapon": weapon.get("title", ""),
                    "name": shell.get("name", ""),
                    "type": shell.get("type", ""),
                    "pen": {int(k): v for k, v in (shell.get("pen") or {}).items() if v is not None},
                }
            )
    return out


def armour_of(slug: str) -> dict:
    record = techtree.vehicle_data().get((slug or "").lower(), {})
    return record.get("armour", {}) or {}


def penetration_at(shell: dict, distance: int) -> int | None:
    """Published penetration in mm, at the nearest tabulated distance."""
    table = shell.get("pen") or {}
    if not table:
        return None
    if distance in table:
        return table[distance]
    nearest = min(table, key=lambda d: abs(d - distance))
    return table[nearest]


@dataclass
class Plate:
    """One armour readout that a shell can be compared against."""

    label: str
    thickness: int
    kind: str = ""       # hull / turret / superstructure ...
    facing: str = ""     # front / side / back, when the source has them

    @property
    def display(self) -> str:
        return self.label


PLATE_TERMS = {
    "hull": "船体", "superstructure": "上层建筑", "turret": "炮塔",
    "belt": "舷侧装甲带", "deck": "甲板", "conning tower": "指挥塔",
    "barbette": "炮座", "citadel": "核心舱", "bulkhead": "舱壁",
    "front": "正面", "side": "侧面", "back": "后面",
    "forward": "前部", "backward": "后部",
    "horizontal": "水平", "vertical": "垂直",
}

#: Tanks publish Hull/Turret; calling a tank's hull a ship's hull reads wrong.
TANK_TERMS = {"hull": "车体", "turret": "炮塔"}


def _term(word: str) -> str:
    return PLATE_TERMS.get(word.strip().lower(), word.strip())


def plates_of(slug: str) -> list[Plate]:
    """Every armour readout the vehicle publishes.

    Tanks give ``Hull``/``Turret`` as front/side/back, so each combination is a
    plate.  Ships give ``Hull``, ``Superstructure`` and turret figures with
    different wording, so those rows are used verbatim.
    """
    record = techtree.vehicle_data().get((slug or "").lower(), {})
    out: list[Plate] = []

    armour = record.get("armour") or {}
    for kind in ("hull", "turret"):
        section = armour.get(kind) or {}
        for facing in FACINGS:
            value = section.get(facing)
            if isinstance(value, int):
                out.append(
                    Plate(
                        label=f"{TANK_TERMS.get(kind, kind)}·{FACING_LABELS.get(facing, facing)}",
                        thickness=value,
                        kind=kind,
                        facing=facing,
                    )
                )
    if out:
        return out

    # ships and anything else: use the published labels as-is
    for row in record.get("armour_rows") or []:
        values = [v for v in (row.get("values") or []) if isinstance(v, int)]
        if not values:
            continue
        label = _term(row.get("label", "")) or "装甲"
        if len(values) == 1:
            out.append(Plate(label=label, thickness=values[0]))
        else:
            # "a / b / c" keeps its own ordering; name the parts positionally
            for index, value in enumerate(values):
                out.append(Plate(label=f"{label} #{index + 1}", thickness=value))
    return out


@dataclass
class Verdict:
    shooter: str
    shooter_name: str
    target: str
    target_name: str
    shell_name: str
    shell_type: str
    weapon: str
    distance: int
    angle: float
    penetration: int
    plates: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: "pen" | "no" | "partial" | "unknown"
    outcome: str = "unknown"

    @property
    def penetrated(self) -> list[dict]:
        return [p for p in self.plates if p["verdict"] == "pen"]

    @property
    def blocked(self) -> list[dict]:
        return [p for p in self.plates if p["verdict"] == "no"]


def _effective(thickness: int, angle: float) -> float:
    """Geometric line-of-sight thickness at ``angle`` degrees from the normal."""
    if angle <= 0:
        return float(thickness)
    clamped = min(angle, 85.0)
    return thickness / max(0.087, math.cos(math.radians(clamped)))


def compare(
    shooter: str,
    target: str,
    *,
    shell_index: int = 0,
    distance: int = 500,
    angle: float = 0.0,
) -> Verdict | None:
    """Compare one shell of ``shooter`` against every published plate of ``target``."""
    shells = all_shells(shooter)
    if not shells:
        return None
    shell = shells[max(0, min(shell_index, len(shells) - 1))]
    pen = penetration_at(shell, distance)
    verdict = Verdict(
        shooter=shooter,
        shooter_name=techtree.world().name_of(shooter),
        target=target,
        target_name=techtree.world().name_of(target),
        shell_name=shell["name"],
        shell_type=shell["type"],
        weapon=shell["weapon"],
        distance=distance,
        angle=angle,
        penetration=pen or 0,
    )

    if pen is None:
        verdict.notes.append("这发弹在 Wiki 上没有穿深数据，无法判定。")
        verdict.outcome = "unknown"
        return verdict

    plates = plates_of(target)
    if not plates:
        verdict.notes.append("目标载具在 Wiki 上没有公布装甲厚度，无法判定。")
        verdict.outcome = "unknown"
        return verdict

    for plate in plates:
        effective = _effective(plate.thickness, angle)
        state = "pen" if effective <= pen else "no"
        verdict.plates.append(
            {
                "kind": plate.kind,
                "facing": plate.facing,
                "label": plate.display,
                "thickness": plate.thickness,
                "effective": round(effective, 1),
                "verdict": state,
                "margin": round(pen - effective, 1),
            }
        )

    front = [p for p in verdict.plates if p["facing"] == "front"] or verdict.plates
    if all(p["verdict"] == "pen" for p in front):
        verdict.outcome = "pen"
    elif all(p["verdict"] == "no" for p in front):
        verdict.outcome = "no"
    else:
        verdict.outcome = "partial"

    verdict.notes.append(
        "穿深取自 Wiki 公布的弹药表（0° 入射，"
        f"{distance} m 处 {pen} mm）；装甲厚度取自 Wiki 的装甲栏。"
        "这是**数据对照**，不是游戏的弹道模拟。"
    )
    if angle > 0:
        verdict.notes.append(
            f"已按 {angle:g}° 入射角计算几何等效厚度（厚度 ÷ cos 角）。"
            "游戏还会对斜靶降低穿深并有转正/跳弹规则，这些**没有公开公式**，"
            "所以斜角结果偏乐观，请以 0° 结果为主要参考。"
        )
    if verdict.outcome == "partial":
        verdict.notes.append("正面有的部位能穿、有的不能，具体看下面的部位表。")
    return verdict


def duel(slug_a: str, slug_b: str, *, distance: int = 500) -> dict:
    """Both directions at once: what each vehicle's best round does to the other."""
    def best(shooter: str, target: str) -> dict | None:
        shells = all_shells(shooter)
        if not shells:
            return None
        scored = []
        for index, shell in enumerate(shells):
            pen = penetration_at(shell, distance)
            if pen is None:
                continue
            scored.append((pen, index))
        if not scored:
            return None
        scored.sort(reverse=True)
        pen, index = scored[0]
        result = compare(shooter, target, shell_index=index, distance=distance)
        return {"shell": shells[index], "verdict": result} if result else None

    return {
        "distance": distance,
        "a_to_b": best(slug_a, slug_b),
        "b_to_a": best(slug_b, slug_a),
        "a_name": techtree.world().name_of(slug_a),
        "b_name": techtree.world().name_of(slug_b),
    }


def coverage() -> dict:
    """How many vehicles have the data both features need."""
    data = techtree.vehicle_data()
    with_armour = sum(1 for r in data.values() if r.get("armour"))
    with_shells = sum(1 for r in data.values() if r.get("weapons"))
    both = sum(1 for r in data.values() if r.get("armour") and r.get("weapons"))
    return {
        "records": len(data),
        "armour": with_armour,
        "shells": with_shells,
        "both": both,
    }
