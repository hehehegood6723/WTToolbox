"""Crawl per-vehicle data from the wiki (slow; run only to refresh the assets).

Run:  python tools/crawl_vehicle_data.py [slug ...]

Fetches every vehicle page and stores a small record per vehicle: rank, battle
rating, research points, purchase price, armour and the published per-shell
penetration tables.  Records go to ``WTTOOLBOX_WIKIDATA`` (default
``<repo>/.wikidata``) and are then bundled by ``tools/build_vehicle_data.py``.

A full run is about 3,200 pages and takes roughly 25 minutes; then run the
bundler.  One-pass extractor for everything both new features need from a
vehicle page.

Emits a compact record per vehicle (a few hundred bytes) instead of keeping the
~180 KB page, so a full crawl of all 3,251 vehicles costs about 1 MB of disk.

Per vehicle:
  rank, br, research RP, purchase SL
  armour: hull / turret, front / side / back (mm)   <- for the penetration panel
  main gun + its shells with the published penetration table
"""

from __future__ import annotations

import html as htmlmod
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

TAG = re.compile(r"<[^>]+>")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("WTTOOLBOX_WIKIDATA") or os.path.join(ROOT, ".wikidata")
PAGE_CACHE = os.environ.get("WTTOOLBOX_PAGE_CACHE") or os.path.join(ROOT, ".wiki-cache", "units")
INDEX = os.path.join(ROOT, "src", "wttoolbox", "assets", "vehicles.json")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}


def clean(raw: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(TAG.sub(" ", raw or ""))).replace("\xa0", " ").strip()


def first(pattern: str, text: str, group: int = 1, flags: int = re.S) -> str:
    m = re.search(pattern, text, flags)
    return clean(m.group(group)) if m else ""


# --------------------------------------------------------------------------- #
# rank / br / costs
# --------------------------------------------------------------------------- #

def parse_basics(page: str) -> dict:
    out: dict = {}
    rank = re.search(r'game-unit_rank">\s*<div class="game-unit_card-info_value">\s*([IVXL]+)', page)
    out["rank"] = ROMAN.get(rank.group(1).upper()) if rank else None

    # Battle rating: "AB 4.3 / RB 4.0 / SB 4.0"
    br = re.search(r'game-unit_br">(.*?)game-unit_card-info_title', page, re.S)
    if br:
        values = re.findall(r">\s*(\d+\.\d+)\s*<", br.group(1))
        out["br"] = values[1] if len(values) > 1 else (values[0] if values else "")
    else:
        out["br"] = ""

    card = re.compile(
        r'<div class="game-unit_card-info_item">(.*?)'
        r'<div class="game-unit_card-info_title">(.*?)</div>',
        re.S,
    )
    for block, title in card.findall(page):
        label = clean(title)
        if label in ("Research", "Purchase"):
            number = re.search(r"<div>\s*([\d,]+)\s*</div>", block)
            if number:
                out["rp" if label == "Research" else "sl"] = int(number.group(1).replace(",", ""))
    out.setdefault("rp", None)
    out.setdefault("sl", None)
    return out


# --------------------------------------------------------------------------- #
# armour
# --------------------------------------------------------------------------- #

def parse_armour(page: str) -> dict:
    """Hull / Turret front-side-back for tanks (published as three numbers).

    Kept for the tank-style comparison; :func:`parse_armour_rows` covers the
    labels ships use instead.
    """
    out: dict = {}
    block = re.search(
        r'<span class="game-unit_chars-header">Armour</span>(.*?)(?=<div class="game-unit_chars-block">\s*<div class="game-unit_chars-line">\s*<span class="game-unit_chars-header">(?!Armour)|</div>\s*</div>\s*</div>)',
        page,
        re.S,
    )
    chunk = block.group(1) if block else page
    for name, pattern in (
        ("hull", r"<span>Hull</span>\s*<span class=\"game-unit_chars-value\">(.*?)</span>"),
        ("turret", r"<span>Turret</span>\s*<span class=\"game-unit_chars-value\">(.*?)</span>"),
    ):
        m = re.search(pattern, chunk, re.S)
        if not m:
            continue
        numbers = [int(n) for n in re.findall(r"(\d+)", clean(m.group(1)))]
        if len(numbers) >= 3:
            out[name] = {"front": numbers[0], "side": numbers[1], "back": numbers[2]}
    return out


def parse_armour_rows(page: str) -> list[dict]:
    """Every armour readout on the page, with its own label.

    Tanks publish ``Hull`` and ``Turret`` as "front / side / back"; ships publish
    ``Hull``, ``Superstructure`` and turret figures as "forward / backward" and
    "horizontal / vertical".  Reading the labels verbatim covers both instead of
    assuming the tank layout.
    """
    block = re.search(
        r'<span class="game-unit_chars-header">Armour</span>(.*?)(?=<div class="game-unit_chars-block">\s*<div class="game-unit_chars-line">\s*<span class="game-unit_chars-header">(?!Armour)|</div>\s*</div>\s*</div>)',
        page,
        re.S,
    )
    if not block:
        return []
    chunk = block.group(1)
    rows: list[dict] = []
    for match in re.finditer(
        r'<div class="game-unit_chars-subline">\s*<span>(.*?)</span>\s*'
        r'<span class="game-unit_chars-value">(.*?)</span>',
        chunk,
        re.S,
    ):
        label = clean(match.group(1))
        text = clean(match.group(2))
        numbers = [int(n) for n in re.findall(r"(\d+)", text)]
        if label and numbers:
            rows.append({"label": label, "text": text, "values": numbers})
    # the block's own header line can carry the values too (some pages)
    if not rows:
        header = re.search(
            r'<span class="game-unit_chars-header">Armour</span>\s*'
            r'<span class="game-unit_chars-value">(.*?)</span>',
            chunk,
            re.S,
        )
        if header:
            text = clean(header.group(1))
            numbers = [int(n) for n in re.findall(r"(\d+)", text)]
            if numbers:
                rows.append({"label": "Armour", "text": text, "values": numbers})
    return rows


# --------------------------------------------------------------------------- #
# main gun + shells with the published penetration table
# --------------------------------------------------------------------------- #

BELT_TABLE = re.compile(r'<table class="game-unit_belt-list">(.*?)</table>', re.S)
DISTANCES = ("10", "100", "500", "1000", "1500", "2000")


def parse_weapons(page: str) -> list[dict]:
    """Every ammunition table on the page, with its weapon title."""
    weapons: list[dict] = []
    # weapon blocks are delimited by game-unit_weapon / game-unit_weapon-title
    blocks = re.split(r'<div class="game-unit_weapon">', page)[1:]
    for block in blocks:
        title = first(r'game-unit_weapon-title">(.*?)</div>', block) or first(
            r'game-unit_weapon-title">(.*?)</', block
        )
        title = re.sub(r"^.*?>", "", title) if ">" in title else title
        for table in BELT_TABLE.findall(block):
            rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
            shells: list[dict] = []
            for row in rows:
                cells = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
                cells = [c for c in cells if c]
                if len(cells) < 3:
                    continue
                if cells[0] in DISTANCES or cells[0].startswith("Armor penetration"):
                    continue
                numbers = []
                for value in cells[2:]:
                    digits = re.findall(r"\d+", value)
                    numbers.append(int(digits[0]) if digits else None)
                if len(numbers) < 6 or numbers[0] is None:
                    continue
                shells.append(
                    {
                        "name": cells[0],
                        "type": cells[1],
                        "pen": dict(zip(DISTANCES, numbers[:6])),
                    }
                )
            if shells:
                weapons.append({"title": title[:80], "shells": shells})
    return weapons


def extract(slug: str, page: str) -> dict:
    record = {"slug": slug}
    record.update(parse_basics(page))
    record["armour"] = parse_armour(page)
    rows = parse_armour_rows(page)
    if rows:
        record["armour_rows"] = rows
    record["weapons"] = parse_weapons(page)
    return record


# --------------------------------------------------------------------------- #
# crawl
# --------------------------------------------------------------------------- #

def fetch(slug: str, timeout: float = 25.0) -> str:
    url = f"https://wiki.warthunder.com/unit/{slug}"
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ""
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0 + attempt)
    return ""


def main() -> int:
    only = sys.argv[1:] or None
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PAGE_CACHE, exist_ok=True)
    with open(INDEX, encoding="utf-8") as fh:
        index = json.load(fh)
    slugs = [v["slug"] for v in index["vehicles"]]
    if only:
        slugs = [s for s in slugs if s in only]

    print(f"  {len(slugs)} vehicles to extract", flush=True)
    done = failed = skipped = 0
    started = time.time()
    lock = threading.Lock()

    def work(queue: list[str]) -> None:
        nonlocal done, failed, skipped
        while True:
            with lock:
                if not queue:
                    return
                slug = queue.pop()
            target = os.path.join(OUT_DIR, f"{slug.lower()}.json")
            if os.path.isfile(target):
                with lock:
                    skipped += 1
                continue
            page = ""
            cached = os.path.join(PAGE_CACHE, f"{slug}.html")
            if os.path.isfile(cached):
                with open(cached, encoding="utf-8", errors="replace") as fh:
                    page = fh.read()
            if not page:
                page = fetch(slug)
                time.sleep(0.25)
            if not page:
                with lock:
                    failed += 1
                    done += 1
                continue
            try:
                record = extract(slug, page)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failed += 1
                    done += 1
                print(f"  !! {slug}: {type(exc).__name__} {exc}", flush=True)
                continue
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False, separators=(",", ":"))
            with lock:
                done += 1
                if done % 100 == 0:
                    rate = done / max(0.001, time.time() - started)
                    left = (len(slugs) - done - skipped) / max(0.01, rate)
                    print(
                        f"  {done + skipped}/{len(slugs)}  ok={done} fail={failed} "
                        f"cache={skipped}  {rate:.1f}/s  ~{left / 60:.0f} min left",
                        flush=True,
                    )

    queue = list(slugs)
    workers = [threading.Thread(target=work, args=(queue,), daemon=True) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    print(f"\n  finished: ok={done} failed={failed} pre-cached={skipped} "
          f"in {(time.time() - started) / 60:.1f} min", flush=True)
    print(f"  wrote {len(os.listdir(OUT_DIR))} records to {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
