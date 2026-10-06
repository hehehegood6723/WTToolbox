"""Bundle the crawled per-vehicle wiki data into one asset.

Run:  python tools/build_vehicle_data.py [--in DIR] [--out FILE]

Input is the directory produced by the crawler (one small JSON per vehicle):
rank, battle rating, research points, purchase price, armour and the published
per-shell penetration tables.  Output is a single compact file the app ships
with, so the research-cost and penetration panels work offline.

Refresh it when the game's economy or vehicle list changes:

    python tools/crawl_vehicle_data.py     # downloads (slow, see the docstring)
    python tools/build_vehicle_data.py     # bundles
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.environ.get("WTTOOLBOX_WIKIDATA") or os.path.join(ROOT, ".wikidata")
OUT = os.path.join(ROOT, "src", "wttoolbox", "assets", "vehicle_data.json")


def main() -> int:
    source = DEFAULT_IN
    out = OUT
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == "--in" and index + 1 < len(args):
            source = args[index + 1]
        if arg == "--out" and index + 1 < len(args):
            out = args[index + 1]

    if not os.path.isdir(source):
        print(f"  !! no crawled data at {source}")
        print("     run tools/crawl_vehicle_data.py first, or pass --in DIR")
        return 1

    vehicles: dict[str, dict] = {}
    skipped = 0
    for name in sorted(os.listdir(source)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(source, name), "r", encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            skipped += 1
            continue
        slug = record.get("slug")
        if not slug:
            skipped += 1
            continue
        # keep the payload lean: drop empty collections
        clean = {
            "rank": record.get("rank"),
            "br": record.get("br") or "",
            "rp": record.get("rp"),
            "sl": record.get("sl"),
        }
        armour = record.get("armour") or {}
        if armour:
            clean["armour"] = armour
        # Ships publish different armour labels than tanks (Hull /
        # Superstructure / forward-backward), so the generic rows are kept too
        # and the penetration panel reads those when they exist.
        rows = record.get("armour_rows") or []
        if rows:
            clean["armour_rows"] = rows
        weapons = []
        for weapon in record.get("weapons") or []:
            shells = [
                {
                    "name": s.get("name", ""),
                    "type": s.get("type", ""),
                    "pen": {k: v for k, v in (s.get("pen") or {}).items() if v is not None},
                }
                for s in weapon.get("shells", [])
                if s.get("pen")
            ]
            if shells:
                weapons.append({"title": weapon.get("title", ""), "shells": shells})
        if weapons:
            clean["weapons"] = weapons
        vehicles[slug] = clean

    with_cost = sum(1 for v in vehicles.values() if v.get("rp"))
    with_armour = sum(1 for v in vehicles.values() if v.get("armour"))
    with_rows = sum(1 for v in vehicles.values() if v.get("armour_rows"))
    with_shells = sum(1 for v in vehicles.values() if v.get("weapons"))

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "https://wiki.warthunder.com/unit/<slug> (vehicle cards, armour block, ammunition tables)",
        "fields": {
            "rank": "rank number 1-8",
            "br": "realistic battle rating as published",
            "rp": "research points needed to research the vehicle",
            "sl": "silver lions needed to purchase it",
            "armour": "{hull|turret: {front, side, back}} in mm, as published",
            "weapons": "[{title, shells: [{name, type, pen: {distance: mm}}]}]",
        },
        "count": len(vehicles),
        "vehicles": vehicles,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"  read {len(vehicles)} records from {source}  (skipped {skipped})")
    print(f"    with research cost : {with_cost}")
    print(f"    with tank armour   : {with_armour}")
    print(f"    with armour rows   : {with_rows}")
    print(f"    with shell tables  : {with_shells}")
    print(f"  wrote {out} ({os.path.getsize(out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
