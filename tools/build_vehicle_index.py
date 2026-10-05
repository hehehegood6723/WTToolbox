"""Build the bundled vehicle index from the official wiki.

Run:  python tools/build_vehicle_index.py [--sitemap FILE] [--cache DIR]

Sources:
  * ``sitemap-units.xml`` - the authoritative list of pages that really exist;
  * the five tech-tree routes (``/ground`` ``/aviation`` ``/helicopters``
    ``/ships`` ``/boats``) - they carry each vehicle's display name, nation,
    rank and class, and are the only place that data is available in bulk;
  * for vehicles the tech tree no longer lists (removed or hidden vehicles such
    as the Leopard I or the Panther II), the vehicle's own page.

Tech-tree folder nodes (``*_group``) are deliberately **dropped**: they are
links in the tree but have no wiki page, so picking one could only ever 404.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Tech-tree pages and the sitemap are cached here between runs.  Point
# WTTOOLBOX_WIKI_CACHE somewhere else (or just delete it) to refetch.
CACHE = os.environ.get("WTTOOLBOX_WIKI_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".wiki-cache"
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "wttoolbox", "assets", "vehicles.json")
SITEMAP_URL = "https://wiki.warthunder.com/sitemap-units.xml"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WTToolbox/1.1"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

ROUTES = {
    "ground": "tank",
    "aviation": "aircraft",
    "helicopters": "helicopter",
    "ships": "ship",
    "boats": "boat",
}

NATION_LABELS = {
    "usa": ("美国", "USA"),
    "germany": ("德国", "Germany"),
    "ussr": ("苏联", "USSR"),
    "britain": ("英国", "Great Britain"),
    "japan": ("日本", "Japan"),
    "china": ("中国", "China"),
    "italy": ("意大利", "Italy"),
    "france": ("法国", "France"),
    "sweden": ("瑞典", "Sweden"),
    "israel": ("以色列", "Israel"),
}

CLASS_LABELS = {
    "tank": ("坦克 / 装甲车辆", "Ground"),
    "aircraft": ("飞机", "Aviation"),
    "helicopter": ("直升机", "Helicopters"),
    "ship": ("远洋舰船", "Bluewater"),
    "boat": ("近岸舰艇", "Coastal"),
}

#: ``game-unit_nation`` text on a vehicle page -> our class key.
PAGE_CLASS = {
    "ground vehicles": "tank",
    "aviation": "aircraft",
    "helicopters": "helicopter",
    "bluewater fleet": "ship",
    "coastal fleet": "boat",
    "fleet": "ship",
}
#: country_svg file name -> our nation key.
COUNTRY_ALIASES = {
    "usa": "usa", "us": "usa", "germany": "germany", "ussr": "ussr", "russia": "ussr",
    "britain": "britain", "uk": "britain", "japan": "japan", "china": "china",
    "italy": "italy", "france": "france", "sweden": "sweden", "israel": "israel",
}

_NATION_SPLIT = re.compile(r'class="unit-tree"\s+data-tree-id="([a-z]+)"')
_RANK_RE = re.compile(r'wt-tree_r-header_label">Rank\s*<span>([IVXL]+)</span>')
_UNIT_RE = re.compile(
    r'data-unit-id="([^"]+)"(?:[^>]*data-unit-req="([^"]*)")?[^>]*>'
    r'.{0,600}?wt-tree_item-text"><span>([^<]*)</span>',
    re.S,
)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_PAGE_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_PAGE_NATION_RE = re.compile(r'game-unit_nation">\s*(.*?)\s*</div>', re.S)
_PAGE_COUNTRY_RE = re.compile(r"country_svg/country_([a-z_]+)\.svg", re.S)
_PAGE_RANK_RE = re.compile(
    r'game-unit_card-info_item game-unit_rank">\s*<div class="game-unit_card-info_value">\s*([IVXL]+)\s*</div>',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def clean(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text).replace("\xa0", " ")).strip()


def fetch(url: str, timeout: float = 45.0) -> str | None:
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
        except Exception:
            pass
        time.sleep(2)
    return None


def load_sitemap(path: str | None) -> list[str]:
    xml = ""
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            xml = fh.read()
    if not xml:
        cached = os.path.join(CACHE, "sitemap-units.xml")
        if os.path.isfile(cached):
            with open(cached, "r", encoding="utf-8", errors="replace") as fh:
                xml = fh.read()
    if not xml:
        print(f"  fetching {SITEMAP_URL}")
        xml = fetch(SITEMAP_URL) or ""
        if xml:
            os.makedirs(CACHE, exist_ok=True)
            with open(os.path.join(CACHE, "sitemap-units.xml"), "w", encoding="utf-8") as fh:
                fh.write(xml)
    slugs: list[str] = []
    for match in re.finditer(r"/unit/([^<\s]+)", xml):
        slug = match.group(1).strip("/")
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def parse_route(text: str, vehicle_class: str) -> list[dict]:
    """Walk a tech-tree page in order, tracking the current nation and rank."""
    marks = list(_NATION_SPLIT.finditer(text))
    out: list[dict] = []
    for index, mark in enumerate(marks):
        nation = mark.group(1)
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        chunk = text[start:end]

        events: list[tuple[int, str, object]] = []
        for match in _RANK_RE.finditer(chunk):
            events.append((match.start(), "rank", match.group(1)))
        for match in _UNIT_RE.finditer(chunk):
            events.append((match.start(), "unit", match.groups()))
        events.sort(key=lambda item: item[0])

        rank: int | None = None
        for _position, kind, payload in events:
            if kind == "rank":
                rank = _ROMAN.get(str(payload).upper())
                continue
            slug, parent, name = payload  # type: ignore[misc]
            name = clean(str(name))
            if not slug or not name:
                continue
            out.append(
                {
                    "slug": slug,
                    "name": name,
                    "class": vehicle_class,
                    "nation": nation,
                    "rank": rank,
                    "parent": parent or "",
                }
            )
    return out


def tree_index() -> dict[str, dict]:
    """Every vehicle the tech-tree pages describe, keyed by lower-case slug."""
    out: dict[str, dict] = {}
    for route, vehicle_class in ROUTES.items():
        path = os.path.join(CACHE, f"{route}.html")
        if not os.path.isfile(path):
            print(f"  !! missing tech-tree cache for {route}: {path}")
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        parsed = parse_route(text, vehicle_class)
        added = 0
        for entry in parsed:
            slug = entry["slug"]
            # Folder nodes are links in the tree but have no page of their own.
            if slug.endswith("_group"):
                continue
            key = slug.lower()
            if key in out:
                if out[key].get("rank") is None and entry.get("rank") is not None:
                    out[key] = entry
                continue
            out[key] = entry
            added += 1
        print(f"  {route:12s} parsed={len(parsed):5d}  kept={added:5d}")
    return out


def page_facts(slug: str) -> dict | None:
    """Name / class / nation / rank straight from the vehicle's own page."""
    cache_path = os.path.join(CACHE, "units", f"{slug}.html")
    page = ""
    if os.path.isfile(cache_path):
        with open(cache_path, "r", encoding="utf-8", errors="replace") as fh:
            page = fh.read()
    if not page:
        page = fetch(f"https://wiki.warthunder.com/unit/{slug}") or ""
        if page:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                fh.write(page)
    if not page:
        return None

    title = _PAGE_TITLE_RE.search(page)
    name = clean(title.group(1)) if title else ""
    name = re.sub(r"\s*\|\s*War Thunder Wiki\s*$", "", name).strip()
    nation_text = _PAGE_NATION_RE.search(page)
    class_key = PAGE_CLASS.get(clean(nation_text.group(1)).lower(), "") if nation_text else ""
    country = _PAGE_COUNTRY_RE.search(page)
    nation = COUNTRY_ALIASES.get((country.group(1) if country else "").lower(), "")
    rank_match = _PAGE_RANK_RE.search(page)
    rank = _ROMAN.get(rank_match.group(1).upper()) if rank_match else None
    if not name or not class_key:
        return None
    return {"slug": slug, "name": name, "class": class_key, "nation": nation,
            "rank": rank, "parent": ""}


def main() -> int:
    sitemap_arg = ""
    for index, arg in enumerate(sys.argv):
        if arg == "--sitemap" and index + 1 < len(sys.argv):
            sitemap_arg = sys.argv[index + 1]

    print("== sources ==")
    slugs = load_sitemap(sitemap_arg)
    print(f"  sitemap pages : {len(slugs)}")
    tree = tree_index()
    print(f"  tech tree     : {len(tree)} (folder nodes excluded)")

    known = 0
    missing: list[str] = []
    for slug in slugs:
        if slug.lower() in tree:
            known += 1
        else:
            missing.append(slug)
    print(f"  covered by the tree : {known}")
    print(f"  need their own page : {len(missing)}")

    print()
    print("== fetching the ones the tree no longer lists ==")
    for slug in missing:
        facts = page_facts(slug)
        if facts is None:
            print(f"  {slug:28s} UNRESOLVED (no page / unparsable)")
            continue
        tree[slug.lower()] = facts
        print(f"  {slug:28s} {facts['name']!r} {facts['class']} {facts['nation']} rank={facts['rank']}")

    # Keep the sitemap's canonical casing for the URLs we hand to the wiki.
    vehicles: list[dict] = []
    for slug in slugs:
        entry = tree.get(slug.lower())
        if entry is None:
            continue
        vehicles.append({**entry, "slug": slug})

    by_class: dict[str, int] = {}
    by_nation: dict[str, int] = {}
    for entry in vehicles:
        by_class[entry["class"]] = by_class.get(entry["class"], 0) + 1
        by_nation[entry["nation"]] = by_nation.get(entry["nation"], 0) + 1

    print()
    print(f"  total vehicles : {len(vehicles)}")
    print(f"  by class       : {by_class}")
    print(f"  by nation      : {by_nation}")
    if "" in by_nation:
        unresolved = [v["slug"] for v in vehicles if not v["nation"]]
        print(f"  !! vehicles with no nation: {unresolved[:10]}")

    vehicles.sort(key=lambda e: (e["class"], e["nation"], e.get("rank") or 0, e["name"].lower()))
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": (
            "https://wiki.warthunder.com/sitemap-units.xml + the /ground /aviation "
            "/helicopters /ships /boats tech-tree routes + individual /unit/<slug> pages"
        ),
        "license_note": (
            "Vehicle names, nations, ranks and classes are read from the public War Thunder "
            "wiki. WTToolbox only links to that data; it is not redistributed as a game asset."
        ),
        "classes": {key: {"zh": value[0], "en": value[1]} for key, value in CLASS_LABELS.items()},
        "nations": {key: {"zh": value[0], "en": value[1]} for key, value in NATION_LABELS.items()},
        "count": len(vehicles),
        "vehicles": vehicles,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"\n  wrote {OUT}  ({os.path.getsize(OUT):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
