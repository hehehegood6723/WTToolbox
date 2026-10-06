"""Build the bundled tech-tree layout from the wiki's tree pages.

Run:  python tools/build_tech_trees.py [--cache DIR]

The wiki draws each nation's tree on a grid, exactly like the game.  Every rank
is one or more row-divs, and each row-div holds two tables:

    | grid-column 1-2 .. | divider | grid-column 4-6 .. |
    | researchable half  |         | premium half       |

so a cell is a vehicle, a folder of vehicles, or empty, and its column index
within the half is what makes a column continue from one rank to the next.

``data-unit-req`` is the vehicle that must be researched first.  Two things are
worth knowing about it:

* it may name a **folder** (``germ_prewar_pz_iii_group``), which really means
  the last vehicle inside that folder;
* it is **absent on the first vehicle of a column in a new rank**, even though
  the game does chain those.  The column index is preserved here so the app can
  reconstruct that link and say that it did.

Output: ``src/wttoolbox/assets/tech_trees.json``
"""

from __future__ import annotations

import html as htmlmod
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "wttoolbox", "assets", "tech_trees.json")

ROUTES = {
    "ground": "tank",
    "aviation": "aircraft",
    "helicopters": "helicopter",
    "ships": "ship",
    "boats": "boat",
}

TAG = re.compile(r"<[^>]+>")
_ROMAN_VALUES = (("X", 10), ("IX", 9), ("VIII", 8), ("VII", 7), ("VI", 6), ("V", 5),
                 ("IV", 4), ("III", 3), ("II", 2), ("I", 1))


def roman_to_int(text: str) -> int | None:
    """Parse a rank numeral.

    Written out rather than looked up in a table because aircraft have a rank
    **IX**: a table stopping at VIII silently dropped that header and filed the
    rank-IX jets under rank VIII.
    """
    value = (text or "").strip().upper()
    if not value:
        return None
    total = 0
    index = 0
    for numeral, amount in _ROMAN_VALUES:
        while value.startswith(numeral, index):
            total += amount
            index += len(numeral)
    return total if index == len(value) and total > 0 else None

_NATION = re.compile(r'<div class="unit-tree"\s+data-tree-id="([a-z]+)"')
_RANK_HEADER = re.compile(r'class="wt-tree_r-header[^"]*"')
_RANK_LABEL = re.compile(r'wt-tree_r-header_label">Rank\s*<span>([IVXL]+)</span>')
_ROW_DIV = re.compile(r'<div class="wt-tree_rank wt-tree_row">')
_TABLE = re.compile(r'<table class="wt-tree_rank-instance"[^>]*>(.*?)</table>', re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_GROUP = re.compile(r'<div class="wt-tree_group"(?P<attrs>[^>]*)>(?P<body>.*)', re.S)
_GROUP_ITEMS = re.compile(r'<div class="wt-tree_group-items">(.*)', re.S)
_ITEM = re.compile(r'<div class="wt-tree_item(?P<prem> wt-tree_item--prem)?"(?P<attrs>[^>]*)>')
_UNIT_ID = re.compile(r'data-unit-id="([^"]+)"')
_REQ = re.compile(r'data-unit-req="([^"]+)"')
_LABEL = re.compile(r'wt-tree_item-text">\s*<span>(.*?)</span>', re.S)


def clean(raw: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(TAG.sub(" ", raw or ""))).replace("\xa0", " ").strip()


def _unit(chunk: str, premium: bool, fallback_req: str = "") -> dict | None:
    slug_match = _UNIT_ID.search(chunk)
    if not slug_match:
        return None
    label = _LABEL.search(chunk)
    req = _REQ.search(chunk)
    return {
        "slug": slug_match.group(1),
        "name": clean(label.group(1)) if label else "",
        "req": (req.group(1) if req else "") or fallback_req,
        "premium": premium,
    }


def parse_cell(cell_html: str) -> dict | None:
    group_match = _GROUP.search(cell_html)
    if group_match:
        attrs = group_match.group("attrs")
        body = group_match.group("body")
        group_slug = _UNIT_ID.search(attrs)
        group_req = _REQ.search(attrs)
        label = _LABEL.search(body)
        items_html = body
        items_match = _GROUP_ITEMS.search(body)
        if items_match:
            items_html = items_match.group(1)
        units = []
        for item in _ITEM.finditer(items_html):
            start = item.start()
            nxt = items_html.find('<div class="wt-tree_item', item.end())
            piece = items_html[start: nxt if nxt != -1 else len(items_html)]
            unit = _unit(piece, bool(item.group("prem")), group_req.group(1) if group_req else "")
            if unit:
                units.append(unit)
        if not units:
            return None
        return {
            "type": "folder",
            "group": group_slug.group(1) if group_slug else "",
            "label": clean(label.group(1)) if label else "",
            "units": units,
        }

    for item in _ITEM.finditer(cell_html):
        unit = _unit(cell_html[item.start():], bool(item.group("prem")))
        if unit:
            return {"type": "unit", **unit}
    return None


def _table_rows(table_html: str) -> list[list[dict | None]]:
    """Rows of a table, one entry per column, ``None`` for an empty cell."""
    rows: list[list[dict | None]] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_html, re.S):
        cells = [parse_cell(cell) for cell in _TD.findall(row_html)]
        if any(cell is not None for cell in cells):
            rows.append(cells)
    return rows


def parse_route(page: str) -> dict[str, list[dict]]:
    """``{nation: [ {rank, rows: [ {research: [...], premium: [...] } ]} ]}``."""
    nations: dict[str, list[dict]] = {}
    marks = list(_NATION.finditer(page))
    for index, mark in enumerate(marks):
        nation = mark.group(1)
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(page)
        chunk = page[start:end]

        events: list[tuple[int, str, object]] = []
        for match in _RANK_HEADER.finditer(chunk):
            events.append((match.start(), "header", match))
        for match in _ROW_DIV.finditer(chunk):
            events.append((match.start(), "row", match))
        events.sort(key=lambda item: item[0])

        ranks: list[dict] = []
        current: dict | None = None
        for position, kind, match in events:
            if kind == "header":
                label = _RANK_LABEL.search(chunk[match.start(): match.start() + 400])
                rank = roman_to_int(label.group(1)) if label else None
                if rank is not None:
                    current = {"rank": rank, "rows": []}
                    ranks.append(current)
                continue
            if current is None:
                current = {"rank": None, "rows": []}
                ranks.append(current)
            # the row div runs to the next row div / header
            stop = min((p for p, _k, _m in events if p > position), default=len(chunk))
            row_html = chunk[position:stop]
            tables = _TABLE.findall(row_html)
            if not tables:
                continue
            # left table = researchable, right table = premium; merge them row
            # by row so the grid the user sees is reproduced exactly.
            research = _table_rows(tables[0])
            premium = _table_rows(tables[1]) if len(tables) > 1 else []
            for i in range(max(len(research), len(premium))):
                current["rows"].append(
                    {
                        "research": research[i] if i < len(research) else [],
                        "premium": premium[i] if i < len(premium) else [],
                    }
                )

        ranks = [rank for rank in ranks if rank["rows"]]
        if ranks:
            nations[nation] = ranks
    return nations


def main() -> int:
    cache = os.path.join(ROOT, ".wiki-cache")
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == "--cache" and index + 1 < len(args):
            cache = args[index + 1]

    trees: dict[str, dict] = {}
    problems: list[str] = []
    for route, vehicle_class in ROUTES.items():
        path = os.path.join(cache, f"{route}.html")
        if not os.path.isfile(path):
            problems.append(f"missing tech-tree cache: {path}")
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            page = fh.read()
        nations = parse_route(page)
        trees[vehicle_class] = nations

        units = folders = reqs = 0
        ranks_seen = 0
        for ranks in nations.values():
            ranks_seen += len(ranks)
            for rank_block in ranks:
                for row in rank_block["rows"]:
                    for cell in list(row["research"]) + list(row["premium"]):
                        if cell is None:
                            continue
                        if cell["type"] == "folder":
                            folders += 1
                            units += len(cell["units"])
                            reqs += sum(1 for u in cell["units"] if u.get("req"))
                        else:
                            units += 1
                            reqs += 1 if cell.get("req") else 0
        print(
            f"  {route:12s} -> {vehicle_class:11s} nations={len(nations):2d} ranks={ranks_seen:3d} "
            f"units={units:5d} folders={folders:4d} with-req={reqs:5d}"
        )

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "https://wiki.warthunder.com/ (" + ", ".join("/" + r for r in ROUTES) + ")",
        "layout": (
            "Per nation and rank: rows of {research: [...], premium: [...]}. Each list is "
            "one grid row of the corresponding half; a slot is a vehicle, a folder of "
            "vehicles, or null for an empty cell, so the column index is meaningful."
        ),
        "layout_note": (
            "'req' is the vehicle that must be researched first and may name a folder. "
            "The wiki does not repeat that field on the first vehicle of a column in a "
            "new rank, so the app links columns across ranks itself."
        ),
        "classes": dict(ROUTES),
        "trees": trees,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"\n  wrote {OUT} ({os.path.getsize(OUT):,} bytes)")
    for problem in problems:
        print(f"  !! {problem}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
