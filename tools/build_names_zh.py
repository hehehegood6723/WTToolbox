"""Build the Chinese vehicle-name table from the Chinese wiki's translation DB.

Run:  python tools/build_names_zh.py

Source: ``模块:Tr/db`` on wiki.biligame.com/warthunder - a community mirror of the
game's own localisation, keyed by the same vehicle codes the rest of this project
uses (``ct-<slug>``), including War Thunder's nation markers such as the ``▀``
prefix on captured vehicles.  That makes the match exact rather than a guess.

Output: ``src/wttoolbox/assets/vehicle_names_zh.json``
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "wttoolbox", "assets", "vehicle_names_zh.json")
INDEX = os.path.join(ROOT, "src", "wttoolbox", "assets", "vehicles.json")

API = "https://wiki.biligame.com/warthunder/api.php"
PAGE = "模块:Tr/db"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

#: The module is a Lua table split into sections; the vehicle names live in
#: ``main = { ["<slug>"]="<name>", ... }`` with the bare vehicle code as the key
#: (the ``ct-`` prefix is only used by the older template form).
LUA_ENTRY = re.compile(r'\[\s*"([^"]+?)"\s*\]\s*=\s*"((?:[^"\\]|\\.)*)"')
MAIN_SECTION = re.compile(r"--\s*BEGIN\s+main(.*?)--\s*END\s+main", re.S)


def fetch_wikitext() -> str:
    url = API + "?" + urllib.parse.urlencode(
        {"action": "query", "titles": PAGE, "prop": "revisions",
         "rvprop": "content", "rvslots": "main", "format": "json"}
    )
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    for page in data.get("query", {}).get("pages", {}).values():
        try:
            return page["revisions"][0]["slots"]["main"]["*"]
        except (KeyError, IndexError):
            continue
    return ""


def clean(name: str) -> str:
    text = html.unescape(name).replace("\\n", " ").replace("\\/", "/")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    print(f"  fetching {PAGE} ...")
    wikitext = fetch_wikitext()
    if not wikitext:
        print("  !! could not fetch the translation database")
        return 1
    print(f"  got {len(wikitext):,} chars")

    names: dict[str, str] = {}
    long_names: dict[str, str] = {}
    section = MAIN_SECTION.search(wikitext)
    body = section.group(1) if section else wikitext
    for match in LUA_ENTRY.finditer(body):
        slug, value = match.group(1), clean(match.group(2))
        if not slug or not value:
            continue
        # "<slug>/long" is the full official designation ("105 毫米火炮履带式战斗
        # 坦克 M1"); the bare key is the short name the wiki - and the game's own
        # lists - use ("M1").  The short one goes in the UI and both go in the
        # tooltip, so a compact label never hides the full name.
        if slug.endswith("/long"):
            long_names[slug[: -len("/long")].lower()] = value
            continue
        names[slug.lower()] = value
    print(f"  {len(names)} short names, {len(long_names)} full designations")

    with open(INDEX, encoding="utf-8") as fh:
        vehicles = json.load(fh)["vehicles"]
    known = {v["slug"].lower(): v["name"] for v in vehicles}

    matched = {slug: names[slug] for slug in known if slug in names}
    missing = [known[slug] for slug in known if slug not in names]
    full = {slug: long_names[slug] for slug in matched if slug in long_names}
    changed = [
        slug for slug in matched
        if matched[slug].strip().lower() != known[slug].strip().lower()
    ]

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": (
            "https://wiki.biligame.com/warthunder/ 模块:Tr/db - the community Chinese wiki's "
            "mirror of the game's own localisation, keyed by vehicle code"
        ),
        "note": (
            "Names are the game's Chinese localisation, including War Thunder's nation "
            "marker prefixes (for example the ▀ on captured vehicles). They are shown next "
            "to the English name, never instead of it, so nothing is lost."
        ),
        "count": len(matched),
        "names": matched,
        "full_names": full,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"  {len(full)} of them also have a full designation")
    print(f"  matched {len(matched)}/{len(known)} vehicles "
          f"({100 * len(matched) / max(1, len(known)):.1f}%)")
    print(f"  {len(changed)} differ from the English name")
    print(f"  {len(missing)} have no Chinese entry, e.g. {missing[:6]}")
    print(f"  wrote {OUT} ({os.path.getsize(OUT):,} bytes)")
    for slug in list(matched)[:8]:
        print(f"    {slug:34s} {known[slug][:22]:24s} -> {matched[slug]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
