"""War Thunder map / mission codename translation.

Replay headers only expose internal codenames such as ``avg_abandoned_factory``
or ``air_denmark``.  The default rendering simply prettifies the codename, which
for War Thunder is already the in-game name in the large majority of cases.

Only codenames that are *known* to differ appear in :data:`NAME_OVERRIDES`, and
:data:`NAME_ZH` carries Chinese names for the maps players see most often.  When
a name is unknown the UI falls back to the raw codename, so nothing is invented.
"""

from __future__ import annotations

import re

__all__ = ["prettify", "display_name", "mode_label", "map_code_from_level"]

# codename (without the avg_/air_ prefix) -> official English in-game name
NAME_OVERRIDES: dict[str, str] = {
    "hurtgen": "Hürtgen Forest",
    "karelia_forest_a": "Karelia",
    "maginot_rework": "Maginot Line",
    "container_port": "Cargo Port",
    "karpaty_passage": "Carpathians",
    "iberian_castle": "Iberian Castle",
    "red_desert": "Red Desert",
    "aral_sea": "Aral Sea",
    "stalingrad_factory": "Stalingrad",
    "soviet_suburban": "Eastern Europe",
    "soviet_suburban_snow": "Eastern Europe (Winter)",
    "eastern_europe": "Eastern Europe",
    "poland_snow": "Poland (Winter)",
    "ardennes_snow": "Ardennes (Winter)",
    "sector_montmedy": "Sector Montmedy",
    "sector_montmedy_snow": "Sector Montmedy (Winter)",
    "northern_india": "Northern India",
    "egypt_sinai": "Sands of Sinai",
    "tunisia_desert": "Tunisia",
    "africa_desert": "Africa Desert",
    "american_valley": "American Desert",
    "alaska_town": "Alaska",
    "nuclear_incident": "Nuclear Incident",
    "abandoned_factory": "Abandoned Factory",
    "abandoned_town": "Abandoned Town",
    "european_fortress": "European Fortress",
    "port_novorossiysk": "Novorossiysk Port",
    "football_field": "Football Field",
    "snow_alps": "Snowy Alps",
    "northern_valley": "Northern Valley",
    "training_ground": "Training Ground",
    "soviet_range": "Soviet Test Range",
    "lazzaro_italy_new_city": "Lazzaro",
    "korea_lake": "Korea",
    "vietnam_hills": "Vietnam",
    "karantan": "Karantan",
    "krymsk": "Krymsk",
    "mozdok": "Mozdok",
    "fulda": "Fulda",
    "rheinland": "Rheinland",
    "berlin": "Berlin",
    "normandy": "Normandy",
    "ardennes": "Ardennes",
    "poland": "Poland",
    "finland": "Finland",
    "sweden": "Sweden",
    "arctic": "Arctic",
    "greece": "Greece",
    "syria": "Syria",
    "israel": "Israel",
    "falklands": "Falklands",
    "japan": "Japan",
    "guadalcanal": "Guadalcanal",
    "netherlands": "Netherlands",
    "ireland": "Ireland",
    "vlaanderen": "Flanders",
    "volokolamsk": "Volokolamsk",
    "kursk_villages": "Kursk",
    "breslau": "Breslau",
    # air battle maps
    "afghan": "Afghanistan",
    "africa_desert_air": "Africa Desert",
    "archipelago": "Archipelago",
    "denmark": "Denmark",
    "equatorial_island": "Equatorial Island",
    "grand_canyon": "Grand Canyon",
    "kamchatka": "Kamchatka",
    "ladoga": "Ladoga",
    "mysterious_valley": "Mysterious Valley",
    "pyrenees": "Pyrenees",
    "skyscraper_city": "Skyscraper City",
    "race_phiphi_islands": "Phi Phi Islands",
}

# Chinese names for the maps seen most often.  Unknown -> fall back to English.
NAME_ZH: dict[str, str] = {
    "abandoned_factory": "废弃工厂",
    "abandoned_town": "废弃城镇",
    "afghan": "阿富汗",
    "arctic": "北极",
    "ardennes": "阿登",
    "ardennes_snow": "阿登（冬季）",
    "berlin": "柏林",
    "breslau": "布雷斯劳",
    "container_port": "货运港口",
    "denmark": "丹麦",
    "eastern_europe": "东欧",
    "egypt_sinai": "西奈半岛",
    "falklands": "福克兰群岛",
    "finland": "芬兰",
    "fulda": "富尔达",
    "greece": "希腊",
    "guadalcanal": "瓜达尔卡纳尔",
    "hurtgen": "许特根森林",
    "iberian_castle": "伊比利亚城堡",
    "ireland": "爱尔兰",
    "israel": "以色列",
    "japan": "日本",
    "karpaty_passage": "喀尔巴阡山脉",
    "karelia_forest_a": "卡累利阿",
    "korea_lake": "朝鲜",
    "krymsk": "克雷姆斯克",
    "kursk_villages": "库尔斯克",
    "maginot_rework": "马奇诺防线",
    "mozdok": "莫兹多克",
    "netherlands": "荷兰",
    "normandy": "诺曼底",
    "poland": "波兰",
    "poland_snow": "波兰（冬季）",
    "red_desert": "红色沙漠",
    "rheinland": "莱茵兰",
    "sector_montmedy": "蒙梅迪地区",
    "snow_alps": "雪山",
    "stalingrad_factory": "斯大林格勒",
    "sweden": "瑞典",
    "syria": "叙利亚",
    "tunisia_desert": "突尼斯",
    "vietnam_hills": "越南",
    "vlaanderen": "佛兰德斯",
    "volokolamsk": "沃洛科拉姆斯克",
    "aral_sea": "咸海",
}

_PREFIX_RE = re.compile(r"^(?:avg|air|levels|avg_|air_)+", re.IGNORECASE)
_MODE_SUFFIX = {
    "dom": ("Domination", "统治"),
    "dom1": ("Domination", "统治"),
    "dom2": ("Domination", "统治"),
    "conq": ("Conquest", "征服"),
    "conq1": ("Conquest", "征服"),
    "conq2": ("Conquest", "征服"),
    "conq3": ("Conquest", "征服"),
    "br": ("Battle", "遭遇战"),
    "br1": ("Battle", "遭遇战"),
    "br2": ("Battle", "遭遇战"),
    "brk": ("Break", "突破"),
    "assault": ("Assault", "突击"),
    "air": ("Air Battle", "空战"),
    "ctf": ("Capture the Flag", "夺旗"),
    "skirmish": ("Skirmish", "遭遇战"),
    "duel": ("Duel", "决斗"),
    "event": ("Event", "活动"),
    "test": ("Test", "测试"),
    "defense": ("Defense", "防守"),
    "ground": ("Ground Strike", "对地攻击"),
}


def map_code_from_level(level_path: str) -> str:
    """``levels/avg_abandoned_factory.bin`` -> ``abandoned_factory``."""
    if not level_path:
        return ""
    name = level_path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    lowered = name.lower()
    for prefix in ("avg_", "air_"):
        if lowered.startswith(prefix):
            return name[len(prefix):]
    return name


def prettify(code: str) -> str:
    """Humanise a raw codename: ``abandoned_factory`` -> ``Abandoned Factory``."""
    if not code:
        return "未知"
    if code in NAME_OVERRIDES:
        return NAME_OVERRIDES[code]
    text = code.replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.title() if text else "未知"


def display_name(code: str, *, prefer_chinese: bool = True) -> str:
    """Best-effort display name; never fabricates, always derived from the code."""
    if not code:
        return "未知地图"
    if prefer_chinese and code in NAME_ZH:
        return NAME_ZH[code]
    return prettify(code)


def mode_label(mission_file: str) -> tuple[str, str]:
    """Derive ``(english, chinese)`` mode name from a mission file stem.

    ``poland_dom`` -> ``("Domination", "统治")``.  Unknown suffixes yield
    ``("", "")`` rather than a guess.
    """
    if not mission_file:
        return "", ""
    stem = mission_file.rsplit(".", 1)[0]
    stem = stem.lower()
    parts = re.split(r"[_\-]", stem)
    for token in reversed(parts):
        token = re.sub(r"\d+$", lambda m: m.group(0), token)
        if token in _MODE_SUFFIX:
            return _MODE_SUFFIX[token]
        stripped = re.sub(r"\d+$", "", token)
        if stripped in _MODE_SUFFIX:
            return _MODE_SUFFIX[stripped]
    return "", ""
