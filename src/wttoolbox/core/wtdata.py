"""War Thunder vehicle data for the comparison radar.

Two sources, both real and both verifiable:

* a **bundled index** (``assets/vehicles.json``) built by
  ``tools/build_vehicle_index.py`` from the public wiki's tech-tree pages - it
  carries every vehicle's display name, nation, rank and class;
* the **vehicle's own wiki page**, fetched on demand and parsed for its
  characteristics (armour, mobility, armament, economy).

Nothing here invents numbers.  If a metric cannot be read from the page it is
simply absent, and the radar drops that axis.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable

from . import appdirs
from .applog import log

__all__ = [
    "Vehicle",
    "VehicleSpec",
    "Axis",
    "Comparison",
    "load_index",
    "index_stats",
    "search",
    "find",
    "fetch_spec",
    "build_comparison",
    "clear_cache",
    "cache_info",
    "AXES",
    "WIKI_BASE",
]

WIKI_BASE = "https://wiki.warthunder.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WTToolbox/1.1"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}
CACHE_TTL_DAYS = 30
#: Bumped when the *structure* parsing changes.  Metric derivation is redone on
#: every load from the cached sections, so adjusting the metric rules (or their
#: display text) never needs the page to be downloaded again.
CACHE_SCHEMA = 3

_index_cache: dict | None = None
_spec_memory: dict[str, "VehicleSpec"] = {}


# --------------------------------------------------------------------------- #
#  Index
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Vehicle:
    slug: str
    name: str
    cls: str
    nation: str
    rank: int | None = None
    parent: str = ""

    @property
    def url(self) -> str:
        return f"{WIKI_BASE}/unit/{self.slug}"

    @property
    def search_text(self) -> str:
        return f"{self.name} {self.slug} {self.nation} {self.cls}".lower()

    @property
    def label(self) -> str:
        return self.name

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.name} ({self.cls}/{self.nation})"


def index_path() -> str:
    return appdirs.resource_path("assets", "vehicles.json")


def load_index() -> dict:
    """Load the bundled vehicle index (cached in memory)."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    path = index_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.error(f"载具索引读取失败：{exc}", "载具")
        data = {"vehicles": [], "count": 0, "nations": {}, "classes": {}}
    data["_vehicles"] = [
        Vehicle(
            slug=item["slug"],
            name=item["name"],
            cls=item.get("class", ""),
            nation=item.get("nation", ""),
            rank=item.get("rank"),
            parent=item.get("parent", ""),
        )
        for item in data.get("vehicles", [])
    ]
    _index_cache = data
    return data


def _vehicle_map() -> dict[str, "Vehicle"]:
    """``{lower-case slug: Vehicle}``, cached next to the index.

    The wiki's canonical URLs use mixed case (``germ_pzkpfw_IV_ausf_F2``) while
    the tech-tree ids and anything saved earlier are lower case, and the wiki
    itself answers both.  Every lookup therefore goes through this map.
    """
    data = load_index()
    mapping = data.get("_by_lower")
    if mapping is None:
        mapping = {vehicle.slug.lower(): vehicle for vehicle in data["_vehicles"]}
        data["_by_lower"] = mapping
    return mapping


def find(slug: str) -> "Vehicle | None":
    """Look a vehicle up by slug, ignoring case."""
    if not slug:
        return None
    return _vehicle_map().get(str(slug).strip().lower())


def index_stats() -> dict:
    data = load_index()
    return {
        "count": len(data["_vehicles"]),
        "nations": data.get("nations", {}),
        "classes": data.get("classes", {}),
        "generated_at": data.get("generated_at", ""),
    }


def search(
    query: str = "",
    *,
    nation: str = "",
    vehicle_class: str = "",
    rank: int | None = None,
    limit: int = 400,
) -> list[Vehicle]:
    """Filter the index.  An empty query returns everything (up to *limit*)."""
    vehicles: list[Vehicle] = load_index()["_vehicles"]
    needle = (query or "").strip().lower()
    out: list[Vehicle] = []
    for vehicle in vehicles:
        if nation and vehicle.nation != nation:
            continue
        if vehicle_class and vehicle.cls != vehicle_class:
            continue
        if rank is not None and vehicle.rank != rank:
            continue
        if needle and needle not in vehicle.search_text:
            continue
        out.append(vehicle)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
#  Specs
# --------------------------------------------------------------------------- #
@dataclass
class Section:
    """One block of a vehicle page, optionally scoped to a single weapon."""

    section: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    weapon: str = ""
    #: True for the main armament (the first weapon block on the page).
    primary: bool = False

    @property
    def title(self) -> str:
        return f"{self.section} · {self.weapon}" if self.weapon else self.section

    @property
    def is_weapon(self) -> bool:
        return bool(self.weapon)


@dataclass
class Metric:
    key: str
    label: str
    value: float
    text: str
    unit: str = ""
    weapon: str = ""


@dataclass
class VehicleSpec:
    slug: str
    name: str = ""
    nation: str = ""
    cls: str = ""
    rank: int | None = None
    br: str = ""
    country: str = ""
    image_url: str = ""
    #: Name of the main armament, e.g. "76 mm F-34 cannon".
    primary_weapon: str = ""
    #: Every weapon block on the page, main armament first.
    weapons: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    #: The raw parsed page.  ``groups`` and ``metrics`` are derived from it.
    sections: list[Section] = field(default_factory=list)
    fetched_at: float = 0.0
    source_url: str = ""
    metrics: dict[str, Metric] = field(default_factory=dict)
    groups: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.metrics) and not self.error

    @property
    def age_text(self) -> str:
        if not self.fetched_at:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.fetched_at))

    def metric(self, key: str) -> Metric | None:
        return self.metrics.get(key)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "nation": self.nation,
            "cls": self.cls,
            "rank": self.rank,
            "br": self.br,
            "country": self.country,
            "image_url": self.image_url,
            "primary_weapon": self.primary_weapon,
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
            # Only the raw parse is stored; groups/metrics are re-derived on
            # load so a rule change never invalidates a downloaded page.
            "sections": [
                {
                    "section": s.section,
                    "weapon": s.weapon,
                    "primary": s.primary,
                    "rows": [list(row) for row in s.rows],
                }
                for s in self.sections
            ],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VehicleSpec":
        spec = cls(slug=data.get("slug", ""))
        spec.name = data.get("name", "")
        spec.nation = data.get("nation", "")
        spec.cls = data.get("cls", "")
        spec.rank = data.get("rank")
        spec.br = data.get("br", "")
        spec.country = data.get("country", "")
        spec.image_url = data.get("image_url", "")
        spec.primary_weapon = data.get("primary_weapon", "")
        spec.fetched_at = float(data.get("fetched_at") or 0)
        spec.source_url = data.get("source_url", "")
        spec.error = data.get("error", "")
        spec.sections = [
            Section(
                section=item.get("section", ""),
                weapon=item.get("weapon", ""),
                primary=bool(item.get("primary")),
                rows=[tuple(row) for row in item.get("rows", [])],
            )
            for item in (data.get("sections") or [])
        ]
        spec.derive()
        return spec

    def derive(self) -> None:
        """(Re)build the display groups and the metrics from ``sections``."""
        self.groups = [(section.title, list(section.rows)) for section in self.sections]
        self.weapons = [(s.weapon, list(s.rows)) for s in self.sections if s.is_weapon]
        primary = next((s for s in self.sections if s.is_weapon and s.primary), None)
        if primary is not None:
            self.primary_weapon = primary.weapon
        _metrics_from_groups(self.sections, self)


# --- HTML parsing ---------------------------------------------------------- #
_BLOCK_RE = re.compile(r'<div class="block-header">(.*?)</div>', re.S)
_ROW_RE = re.compile(r'<div class="game-unit_chars-(line|subline)">(.*?)</div>', re.S)
_HEADER_RE = re.compile(r'game-unit_chars-header">(.*?)</span>', re.S)
_INFO_RE = re.compile(r'game-unit_chars-info">(.*?)</span>', re.S)
_LABEL_RE = re.compile(r"<span>([^<]{1,60})</span>", re.S)
_VALUE_RE = re.compile(r'game-unit_chars-value">(.*?)</span>', re.S)
# A weapon sub-block: "<span class=game-unit_weapon-title>76 mm F-34 cannon</span>"
# followed by that weapon's own characteristics.  A tank page carries the main
# gun *and* its machine guns here, so the fields must stay scoped to their
# weapon - otherwise the coaxial MG's "600 shots/min" ends up on the gun.
_WEAPON_SPLIT_RE = re.compile(r'<div class="game-unit_weapon">')
_WEAPON_TITLE_RE = re.compile(r'game-unit_weapon-title">(.*?)</span>', re.S)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_BR_RE = re.compile(
    r'game-unit_br-item">\s*<div class="mode">([A-Z]{2})</div>\s*<div class="value">([\d.]+)</div>',
    re.S,
)
_RANK_CARD_RE = re.compile(
    r"game-unit_rank\">\s*<div class=\"game-unit_card-info_value\">\s*([IVXL]+)\s*</div>",
    re.S,
)
_COUNTRY_RE = re.compile(r"country_svg/country_([a-z_]+)\.svg", re.S)
_IMAGE_RE = re.compile(r'game-unit_template-image"\s+src="([^"]+)"', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
}


def _clean(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html_module.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            out.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _first_number(text: str, default: float = 0.0) -> float:
    values = _numbers(text)
    return values[0] if values else default


def _is_numeric_label(text: str) -> bool:
    """True for subline labels like ``6,000`` (an altitude, not a field name)."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    return bool(re.fullmatch(r"[0-9][0-9,.\s]*", stripped))


def _rows_in(chunk: str) -> list[tuple[str, str]]:
    """``[(label, value), ...]`` for the characteristic rows inside *chunk*."""
    rows: list[tuple[str, str]] = []
    pending_header = ""
    pending_info = ""
    for row in _ROW_RE.finditer(chunk):
        body = row.group(2)
        header = _HEADER_RE.search(body)
        info = _INFO_RE.search(body)
        values = [_clean(v) for v in _VALUE_RE.findall(body)]
        values = [v for v in values if v]
        label_match = _LABEL_RE.search(body)
        label = _clean(label_match.group(1)) if label_match else ""
        if header:
            pending_header = _clean(header.group(1))
            pending_info = _clean(info.group(1)) if info else ""
            if values:
                rows.append((pending_header, " / ".join(values)))
                pending_header = ""
            continue
        if not values:
            continue
        # A subline whose label is just a number ("6,000" under "Max speed")
        # belongs to the enclosing line, so use that line's header.  A named
        # subline keeps its parent too ("Turret Rotation Speed (Horizontal)").
        key = label
        if _is_numeric_label(key):
            key = pending_header or label
        elif pending_header and label:
            key = f"{pending_header} ({label})"
        if not key:
            key = pending_header or "值"
        if pending_info and key == pending_header:
            key = f"{key} ({pending_info})"
        rows.append((key, " / ".join(values)))
    return rows


def _parse_sections(page: str) -> list["Section"]:
    """Split a wiki page into sections, with weapon sub-blocks kept apart.

    Returns one :class:`Section` per section; sections that contain per-weapon
    sub-blocks (``Armaments``, ``Offensive armament``, ...) yield one Section per
    weapon, flagged so the metric extractor can use only the main armament.
    """
    marks = list(_BLOCK_RE.finditer(page))
    sections: list[Section] = []
    for index, mark in enumerate(marks):
        title = _clean(mark.group(1))
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else min(len(page), start + 40000)
        chunk = page[start:end]

        pieces = _WEAPON_SPLIT_RE.split(chunk)
        if len(pieces) == 1:
            rows = _rows_in(chunk)
            if rows:
                sections.append(Section(section=title, rows=rows))
            continue

        # Anything before the first weapon still belongs to the section.
        prefix_rows = _rows_in(pieces[0])
        if prefix_rows:
            sections.append(Section(section=title, rows=prefix_rows))
        first_weapon = True
        for piece in pieces[1:]:
            title_match = _WEAPON_TITLE_RE.search(piece)
            weapon = _clean(title_match.group(1)) if title_match else "武器"
            # The rows of this weapon stop at the next weapon, already split.
            rows = _rows_in(piece)
            if not rows:
                continue
            sections.append(
                Section(section=title, weapon=weapon, rows=rows, primary=first_weapon)
            )
            first_weapon = False
    return sections


def _parse_groups(page: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """``[(section, [(label, value), ...]), ...]`` for the detail view.

    Weapon sub-blocks are shown as their own group (``Armaments · 76 mm F-34
    cannon``) so main and secondary armament never blur together.
    """
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for section in _parse_sections(page):
        out.append((section.title, section.rows))
    return out


# Canonical metrics the radar can use, in priority order: the comparison takes
# the first six that *both* vehicles actually have, so the order decides which
# six show up.  Tanks end up with armour / power-to-weight / speed / reload /
# turret traverse / ammo, aircraft with speed / climb / turn / ceiling / burst
# mass / gun rate.
#
# "hint" explains a metric in the tooltip; several of them are easy to misread
# (power-to-weight is not thrust-to-weight, and a tank's reload rate is not the
# rate of its machine gun).
AXES: tuple[dict, ...] = (
    # key,            zh label,      unit,    higher is better
    {"key": "armour_front", "label": "正面装甲", "unit": "mm", "better": "high",
     "hint": "Wiki 的 Hull 一栏，取三个方向里最厚的（mm）"},
    {"key": "power_weight", "label": "功重比", "unit": "hp/t", "better": "high",
     "hint": "发动机功率 ÷ 重量（Wiki: Power-to-weight ratio）。"
             "这是功重比，不是喷气机的推重比——Wiki 的飞机页没有推重比字段。"},
    {"key": "speed_forward", "label": "最大速度", "unit": "km/h", "better": "high",
     "hint": "前进最大速度；有多个模式时会取完全体 · RB 那一列"},
    {"key": "reload", "label": "主炮装填", "unit": "发/分", "better": "high",
     "hint": "由主炮的完全体装填时间换算：60 ÷ 装填秒数。"
             "并列机枪的射速不会算进来。"},
    {"key": "turret_traverse", "label": "炮塔转速", "unit": "°/s", "better": "high",
     "hint": "炮塔水平转速（Wiki: Turret Rotation Speed · Horizontal）"},
    {"key": "climb", "label": "爬升率", "unit": "m/s", "better": "high"},
    {"key": "turn_rate", "label": "盘旋性能", "unit": "°/s", "better": "high",
     "hint": "由盘旋时间换算：360 ÷ 盘旋秒数，越大转得越快"},
    {"key": "ceiling", "label": "实用升限", "unit": "m", "better": "high"},
    {"key": "burst_mass", "label": "点射质量", "unit": "kg/s", "better": "high",
     "hint": "主武器每秒投射的弹丸质量（Wiki: One-second Burst Mass）"},
    {"key": "fire_rate", "label": "主武器射速", "unit": "发/分", "better": "high",
     "hint": "主武器自身标明的射速；只有机关炮/机枪类才有这个字段，"
             "坦克主炮没有，会改用装填时间换算"},
    {"key": "ammo", "label": "主武器弹药", "unit": "发", "better": "high",
     "hint": "主武器的弹药基数（不含并列机枪等次武器）"},
    {"key": "crew", "label": "乘员", "unit": "人", "better": "high"},
    {"key": "penetration", "label": "穿深", "unit": "mm", "better": "high"},
    {"key": "caliber", "label": "主炮口径", "unit": "mm", "better": "high"},
    {"key": "weight", "label": "重量", "unit": "t", "better": "low"},
    {"key": "concealment", "label": "隐蔽性", "unit": "%", "better": "high",
     "hint": "可见度越低越好；这里取倒数便于与其它轴一起比较"},
    {"key": "engine_power", "label": "发动机功率", "unit": "hp", "better": "high"},
)

_AXIS_BY_KEY = {axis["key"]: axis for axis in AXES}

# label (lower-cased, from the page) -> (metric key, transform)
_LABEL_MAP: dict[str, tuple[str, str]] = {
    "hull": ("armour_front", "max_of_slash"),
    "turret": ("armour_turret", "max_of_slash"),
    "visibility": ("visibility", "number"),
    "crew": ("crew", "number"),
    "forward": ("speed_forward", "number"),
    "backward": ("speed_backward", "number"),
    # Named sublines keep their parent in the label.
    "max speed (forward)": ("speed_forward", "number"),
    "max speed (backward)": ("speed_backward", "number"),
    "turret rotation speed (horizontal)": ("turret_traverse", "number"),
    "turret rotation speed (vertical)": ("turret_traverse_v", "number"),
    "power-to-weight ratio": ("power_weight", "number"),
    "engine power": ("engine_power", "number"),
    "weight": ("weight", "number"),
    "base weight": ("weight", "number"),
    "rate of climb": ("climb", "number"),
    "turn time": ("turn_time", "number"),
    "max altitude": ("ceiling", "number"),
    "wing loading": ("wing_loading", "number"),
    "length": ("length", "number"),
    "wingspan": ("wingspan", "number"),
    "ammunition": ("ammo", "number"),
    "reload": ("reload_time", "last_number"),
    "fire rate": ("fire_rate", "number"),
    "one-second burst mass": ("burst_mass", "number"),
    "turret rotation speed": ("turret_traverse", "number"),
    "vertical guidance": ("elevation", "number"),
    "max speed limit (ias)": ("speed_limit", "number"),
    "max speed": ("speed_generic", "number"),
}

_UNIT_HINTS = {
    "mm": "mm",
    "%": "%",
    "km/h": "km/h",
    "m/s": "m/s",
    "hp/t": "hp/t",
    "hp": "hp",
    "persons": "人",
    "person": "人",
    "rounds": "发",
    "shots/min": "发/分",
    "kg": "kg",
    "kg/m²": "kg/m²",
    "t": "t",
    "m": "m",
    "s": "s",
}


def _unit_of(text: str) -> str:
    lowered = (text or "").lower()
    for hint, unit in sorted(_UNIT_HINTS.items(), key=lambda kv: -len(kv[0])):
        if hint in lowered:
            return unit
    return ""


def _lookup_label(label: str) -> tuple[str, str] | None:
    """Resolve a page label to ``(metric key, transform)``.

    Labels come in several shapes and each needs a different fallback:
    ``Hull`` (bare), ``Armour (Hull)`` (parent + child), ``Max speed (Forward)``
    (parent + named child) and ``Reload (basic crew → aces)`` (parent + a note).
    """
    full = (label or "").strip().lower()
    if not full:
        return None
    direct = _LABEL_MAP.get(full)
    if direct is not None:
        return direct
    parent, sep, rest = full.partition("(")
    if not sep:
        return None
    # "Reload (basic crew → aces)" -> the parent is the field.
    parent = parent.strip()
    child = rest.rstrip(")").strip()
    for candidate in (parent, child):
        mapped = _LABEL_MAP.get(candidate)
        if mapped is not None:
            return mapped
    return None


def _metrics_from_groups(sections: list[Section], spec: VehicleSpec) -> None:
    """Pull the canonical metrics out of *sections*.

    Only the **main armament** and the non-weapon sections feed the metrics.
    Secondary weapons (coaxial/AA machine guns, defensive guns) are kept in the
    detail groups but must never supply a number like "fire rate", because that
    is how a T-34 ended up credited with its machine gun's 600 rounds/min.
    """
    raw: dict[str, Metric] = {}

    def put(key: str, label: str, value: float, text: str, unit: str, weapon: str = "") -> None:
        if value is None or key in raw:
            return
        # Weapon-sourced numbers always carry the weapon's name, so a table row
        # can never be read as belonging to the wrong gun.
        shown = f"{text}（{weapon}）" if weapon and weapon not in text else text
        raw[key] = Metric(
            key=key, label=label, value=float(value), text=shown, unit=unit, weapon=weapon
        )

    for item in sections:
        if item.is_weapon and not item.primary:
            continue
        for label, value in item.rows:
            mapped = _lookup_label(label)
            if mapped is None:
                continue
            key, transform = mapped
            unit = _unit_of(value)
            numbers = _numbers(value)
            if not numbers:
                continue
            if transform == "max_of_slash":
                put(key, label, max(numbers), value, unit or "mm", item.weapon)
            elif transform == "last_number":
                # e.g. "9 → 6.9 s" (basic crew → aces): the last value is the
                # fully upgraded one, matching the "spaded" reading elsewhere.
                put(key, label, numbers[-1], value, unit, item.weapon)
            else:
                put(key, label, numbers[0], value, unit, item.weapon)

    # Derived metrics used by the radar.
    turn_time = raw.get("turn_time")
    if turn_time and turn_time.value > 0.1:
        rate = 360.0 / turn_time.value  # a full circle per turn time
        raw["turn_rate"] = Metric("turn_rate", "盘旋性能", rate, f"{rate:.1f} °/s", "°/s")

    visibility = raw.get("visibility")
    if visibility is not None and visibility.value > 0:
        # Lower visibility is better; expose the reciprocal as "concealment"
        # but keep the raw percentage in the displayed text.
        conceal = 100.0 / visibility.value
        raw["concealment"] = Metric(
            "concealment", "隐蔽性", conceal, f"可见度 {visibility.text}", "%"
        )

    reload_time = raw.pop("reload_time", None)
    if reload_time and reload_time.value > 0.05:
        rate = 60.0 / reload_time.value
        weapon = f"（{reload_time.weapon}）" if reload_time.weapon else ""
        raw["reload"] = Metric(
            "reload", "主炮装填", rate,
            f"{reload_time.value:g} s → {rate:.1f} 发/分{weapon}",
            "发/分", weapon=reload_time.weapon,
        )

    speed_generic = raw.pop("speed_generic", None)
    if speed_generic is not None and "speed_forward" not in raw:
        raw["speed_forward"] = Metric(
            "speed_forward", "最大速度", speed_generic.value, speed_generic.text, speed_generic.unit
        )

    spec.metrics = raw


def _extract_br(page: str) -> str:
    """``AB 4.3 / RB 4.0 / SB 4.0`` from the vehicle card."""
    found = _BR_RE.findall(page)
    if not found:
        return ""
    order = {"AB": 0, "RB": 1, "SB": 2}
    pairs = sorted({(mode, value) for mode, value in found}, key=lambda item: order.get(item[0], 9))
    return " / ".join(f"{mode} {value}" for mode, value in pairs)


def parse_vehicle_page(page: str, slug: str) -> VehicleSpec:
    spec = VehicleSpec(slug=slug, source_url=f"{WIKI_BASE}/unit/{slug}")
    title_match = _TITLE_RE.search(page)
    if title_match:
        title = _clean(title_match.group(1))
        spec.name = re.sub(r"\s*\|\s*War Thunder Wiki\s*$", "", title).strip()

    index = {v.slug: v for v in load_index()["_vehicles"]}
    known = index.get(slug) or find(slug)
    if known is not None:
        spec.nation = known.nation
        spec.cls = known.cls
        spec.rank = known.rank
        spec.name = spec.name or known.name

    spec.sections = _parse_sections(page)
    spec.derive()
    spec.br = _extract_br(page)

    rank_match = _RANK_CARD_RE.search(page)
    if rank_match:
        roman = _ROMAN.get(rank_match.group(1).strip().upper())
        if roman:
            spec.rank = roman
    country_match = _COUNTRY_RE.search(page)
    if country_match:
        spec.country = country_match.group(1)
    image_match = _IMAGE_RE.search(page)
    if image_match:
        spec.image_url = image_match.group(1)

    spec.fetched_at = time.time()
    if not spec.groups and not spec.metrics:
        spec.error = "页面结构无法识别（wiki 可能已改版）"
    return spec


# --------------------------------------------------------------------------- #
#  Cache + fetch
# --------------------------------------------------------------------------- #
def cache_dir() -> str:
    path = os.path.join(appdirs.cache_dir(), "vehicles")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _cache_file(slug: str) -> str:
    # Lower-cased so the same vehicle never ends up cached under two casings.
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", slug).lower()
    return os.path.join(cache_dir(), f"{safe}.json")


def cache_info() -> dict:
    folder = cache_dir()
    count = 0
    size = 0
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.endswith(".json"):
                count += 1
                try:
                    size += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return {"count": count, "bytes": size, "folder": folder}


def clear_cache() -> int:
    removed = 0
    try:
        for entry in os.scandir(cache_dir()):
            if entry.is_file() and entry.name.endswith(".json"):
                try:
                    os.remove(entry.path)
                    removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    _spec_memory.clear()
    return removed


def fetch_spec(slug: str, *, force: bool = False, timeout: float = 15.0) -> VehicleSpec:
    """Return a vehicle's specs, from memory, disk cache, or the wiki."""
    if not force and slug in _spec_memory:
        return _spec_memory[slug]

    path = _cache_file(slug)
    if not force and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            age_days = (time.time() - float(data.get("fetched_at") or 0)) / 86400.0
            if int(data.get("schema") or 0) == CACHE_SCHEMA and age_days <= CACHE_TTL_DAYS:
                spec = VehicleSpec.from_dict(data)
                _spec_memory[slug] = spec
                return spec
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    url = f"{WIKI_BASE}/unit/{slug}"
    # The wiki is usually quick (~2 s) but occasionally stalls; a shorter
    # timeout plus one retry keeps the UI responsive instead of leaving the
    # user staring at "正在获取详情…" for half a minute.
    attempts = 2
    last_error = ""
    page = ""
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                page = response.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                spec = VehicleSpec(
                    slug=slug, error="HTTP 404（wiki 上可能没有这辆载具的页面）"
                )
                _spec_memory[slug] = spec
                return spec
            last_error = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__} {exc}"
        if attempt + 1 < attempts:
            log.warn(f"读取载具页面失败，正在重试：{slug} · {last_error}", "载具")
            time.sleep(1.5)

    if not page:
        spec = VehicleSpec(slug=slug, error=f"网络请求失败：{last_error}")
        _spec_memory[slug] = spec
        return spec

    spec = parse_vehicle_page(page, slug)
    _spec_memory[slug] = spec
    if not spec.error:
        try:
            payload = spec.to_dict()
            payload["schema"] = CACHE_SCHEMA
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        except OSError:
            pass
        log.info(f"已获取载具数据：{spec.name or slug}（{len(spec.metrics)} 项指标）", "载具")
    else:
        log.warn(f"载具页面解析失败：{slug} · {spec.error}", "载具")
    return spec


# --------------------------------------------------------------------------- #
#  Comparison
# --------------------------------------------------------------------------- #
@dataclass
class Axis:
    key: str
    label: str
    unit: str
    better: str
    value_a: float
    value_b: float
    text_a: str
    text_b: str
    ratio_a: float
    ratio_b: float
    winner: str  # "a" | "b" | "tie"
    hint: str = ""

    @property
    def a_wins(self) -> bool:
        return self.winner == "a"

    @property
    def b_wins(self) -> bool:
        return self.winner == "b"


@dataclass
class Comparison:
    a: VehicleSpec
    b: VehicleSpec
    axes: list[Axis] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.axes)

    @property
    def score(self) -> tuple[int, int]:
        a = sum(1 for axis in self.axes if axis.winner == "a")
        b = sum(1 for axis in self.axes if axis.winner == "b")
        return a, b

    @property
    def same_class(self) -> bool:
        return bool(self.a.cls) and self.a.cls == self.b.cls


def build_comparison(a: VehicleSpec, b: VehicleSpec, *, max_axes: int = 6) -> Comparison:
    """Pick the axes both vehicles share, best-first, and normalise them."""
    result = Comparison(a=a, b=b)
    if a.error:
        result.warnings.append(f"{a.name or a.slug}：{a.error}")
    if b.error:
        result.warnings.append(f"{b.name or b.slug}：{b.error}")
    if a.cls and b.cls and a.cls != b.cls:
        result.warnings.append("两者载具类型不同，只比较双方都有的指标。")

    for axis_def in AXES:
        key = axis_def["key"]
        metric_a = a.metrics.get(key)
        metric_b = b.metrics.get(key)
        if metric_a is None or metric_b is None:
            continue
        va, vb = metric_a.value, metric_b.value
        if va <= 0 and vb <= 0:
            continue

        # "better" direction -> a normalised score where bigger is better.
        if axis_def["better"] == "low":
            score_a = (1.0 / va) if va > 0 else 0.0
            score_b = (1.0 / vb) if vb > 0 else 0.0
        else:
            score_a, score_b = va, vb
        peak = max(score_a, score_b)
        if peak <= 0:
            continue
        ratio_a, ratio_b = score_a / peak, score_b / peak
        if abs(va - vb) < 1e-9:
            winner = "tie"
        elif score_a > score_b:
            winner = "a"
        else:
            winner = "b"
        result.axes.append(
            Axis(
                key=key,
                label=axis_def["label"],
                unit=metric_a.unit or metric_b.unit or axis_def["unit"],
                better=axis_def["better"],
                value_a=va,
                value_b=vb,
                text_a=metric_a.text,
                text_b=metric_b.text,
                ratio_a=ratio_a,
                ratio_b=ratio_b,
                winner=winner,
                hint=axis_def.get("hint", ""),
            )
        )
        if len(result.axes) >= max_axes:
            break
    return result
