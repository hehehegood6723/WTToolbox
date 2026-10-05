"""Functional tests for the whole non-GUI core.

Run:  python tests/test_core.py

Destructive operations only ever touch sandbox folders created under the system
temp directory - never the real game install.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from wttoolbox.core import appdirs, cleaner, gamelaunch, gamelog, gamepath, library, news
from wttoolbox.core import mapnames, replays, settings as settings_mod, soundmods, trash
from wttoolbox.core import winutil

from _game import HAVE_GAME, ROOT, skip, skip_summary  # noqa: E402

#: A real install when there is one, otherwise a path that cannot exist,
#: so the os.path.join() checks below stay well formed.
REAL_ROOT = ROOT

_failures: list[str] = []
_passed = 0
_notes: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
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


SANDBOX = tempfile.mkdtemp(prefix="tk-core-test-")


def make_zip(path: str, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


# --------------------------------------------------------------------------- #
section("winutil")
check("human_size 1536", winutil.human_size(1536) == "1.50 KB", winutil.human_size(1536))
check("human_size 0", winutil.human_size(0) == "0 B", winutil.human_size(0))
check("human_size GB", winutil.human_size(2_578_400_000).endswith("GB"), winutil.human_size(2_578_400_000))
if os.path.isfile(os.path.join(REAL_ROOT, "win64", "aces.exe")):
    version = winutil.file_version(os.path.join(REAL_ROOT, "win64", "aces.exe"))
    check("file_version(aces.exe) readable", bool(version and version.count(".") == 3), str(version))
    note(f"aces.exe 版本 = {version}")
procs = winutil.find_processes()
check("process enumeration works", len(procs) > 10, f"{len(procs)} processes")

# --------------------------------------------------------------------------- #
section("settings")
tmp_settings = os.path.join(SANDBOX, "settings.json")
store = settings_mod.Settings(tmp_settings)
check("defaults loaded", store.get("theme") == "light")
store.set("theme", "dark")
store.remember_game_path(REAL_ROOT)
reopened = settings_mod.Settings(tmp_settings)
check("persisted theme", reopened.get("theme") == "dark")
check("persisted game path", reopened.get("game_path") == REAL_ROOT)
check("known paths recorded", REAL_ROOT in reopened.get("known_paths", []))

# --------------------------------------------------------------------------- #
section("game path detection")
install = None
if os.path.isdir(REAL_ROOT):
    reg = gamepath.detect_registry()
    check("registry detection finds the install", REAL_ROOT in reg, str(reg))
    install = gamepath.GameInstall(root=REAL_ROOT, source="manual")
    validation = install.validate()
    check("validation passes", validation.ok, validation.summary)
    check("config.blk reachable", os.path.isfile(install.config))
    check("aces.exe is 64-bit build", os.path.isfile(install.aces64))
    check("version regex sane", bool(install.version()), str(install.version()))
    for label, path in (
        ("replays folder", install.replays),
        ("screenshots folder", install.screenshots),
        ("user skins", install.user_skins),
        ("sound folder", install.sound),
    ):
        check(f"{label} resolved", bool(path))
    fake = gamepath.GameInstall(root=os.path.join(SANDBOX, "nope"))
    check("bogus path fails validation", not fake.validate().ok)
else:
    note(f"real install not found at {REAL_ROOT}; skipping")

# --------------------------------------------------------------------------- #
section("map names")
check("poland -> 波兰", mapnames.display_name("poland") == "波兰")
check("abandoned_factory -> 废弃工厂", mapnames.display_name("abandoned_factory") == "废弃工厂")
check("unknown code prettified", mapnames.display_name("some_new_map") == "Some New Map")
check("map_code_from_level avg_", mapnames.map_code_from_level("levels/avg_poland.bin") == "poland")
check("map_code_from_level air_", mapnames.map_code_from_level("levels/air_denmark.bin") == "denmark")
check("mode dom", mapnames.mode_label("poland_dom") == ("Domination", "统治"))
check("mode conq1", mapnames.mode_label("abandoned_town_conq1")[0] == "Conquest")
check("mode unknown blank", mapnames.mode_label("weirdthing") == ("", ""))

# --------------------------------------------------------------------------- #
section("replays")
if install and os.path.isdir(install.replays):
    started = time.time()
    infos = replays.list_replays(install.replays)
    elapsed = time.time() - started
    check("replays listed", len(infos) > 50, f"{len(infos)} files")
    check("listing is fast", elapsed < 20, f"{elapsed:.1f}s")
    check("all .wrpl", all(i.filename.lower().endswith(".wrpl") for i in infos))
    parsed = [i for i in infos if i.map_code]
    check(
        "map parsed for most replays",
        len(parsed) >= len(infos) * 0.9,
        f"{len(parsed)}/{len(infos)}",
    )
    verified = [i for i in infos if i.header_verified]
    check("header offsets verified", len(verified) >= len(infos) * 0.9, f"{len(verified)}")
    check("map display names non-empty", all(i.map_name for i in infos))
    check("sizes positive", all(i.size > 0 for i in infos))
    sample = parsed[0] if parsed else None
    if sample:
        note(
            f"example: {sample.filename} -> {sample.map_name} / {sample.mode_display} "
            f"({sample.level_path}, {sample.mission_path})"
        )
        note("mode detection hits: " + str(sum(1 for i in infos if i.mode_en)))
    check("safe_replay_name strips junk", replays.safe_replay_name('a/b:c?.wrpl') == "a_b_c_.wrpl")
else:
    note("no replay folder; skipping")

# --------------------------------------------------------------------------- #
section("logs")
if install:
    groups = gamelog.list_logs(install)
    check("launcher logs found", len(groups[gamelog.LogKind.LAUNCHER]) > 0)
    check("startapp logs found", len(groups[gamelog.LogKind.STARTAPP]) > 0)
    check("game clog found", len(groups[gamelog.LogKind.GAME]) > 0)
    launcher_log = groups[gamelog.LogKind.LAUNCHER][0]
    text = gamelog.read_tail(launcher_log.path, max_bytes=64 * 1024)
    check("launcher log is readable text", "BUILD TIMESTAMP" in text or "[D]" in text, text[:80])
    clog = groups[gamelog.LogKind.GAME][0]
    preview = gamelog.decode_preview(clog)
    check("clog preview explains the format", "私有压缩" in preview)

    # exercise the incremental tailer against a synthetic growing file
    growing = os.path.join(SANDBOX, "grow.log")
    with open(growing, "w", encoding="utf-8") as fh:
        fh.write("line1\n")
    tailer = gamelog.TextTailer(growing, from_end_bytes=1024)
    first = tailer.read_new()
    check("tailer reads initial content", "line1" in first, repr(first))
    with open(growing, "a", encoding="utf-8") as fh:
        fh.write("line2\n")
    second = tailer.read_new()
    check("tailer reads only the new part", second.strip() == "line2", repr(second))
    with open(growing, "a", encoding="utf-8") as fh:
        fh.write("第二行中文\n")
    third = tailer.read_new()
    check("tailer handles multibyte", third.strip() == "第二行中文", repr(third))
    check("level scanning", gamelog.scan_levels("  0.1 [D] a\n 0.2 [E] b\n")["E"] == 1)
else:
    note("no install; skipping log checks")

# --------------------------------------------------------------------------- #
section("cleaner (read-only measurement)")
if install:
    targets = cleaner.build_targets(install, log_keep_days=14)
    check("targets built", len(targets) >= 8)
    check("userdata targets off by default", all(
        not t.enabled_by_default for t in targets if t.category == "userdata"
    ))
    measured = cleaner.measure_all(targets)
    by_key = {t.key: t for t in measured}
    check("game logs measured", by_key["game_logs"].size > 0, by_key["game_logs"].size_text)
    check("shader cache measured", by_key["shaders"].size > 0, by_key["shaders"].size_text)
    note(
        "measurements: "
        + ", ".join(f"{t.label.split(' ')[0]}={t.size_text}" for t in measured if t.size)
    )
    # Age filter must actually exclude recent files.
    fresh = cleaner.CleanTarget(
        key="x", label="x", description="", folder=os.path.join(SANDBOX, "fresh"),
        pattern=".log", keep_within_days=7,
    )
    os.makedirs(fresh.folder, exist_ok=True)
    with open(os.path.join(fresh.folder, "new.log"), "w") as fh:
        fh.write("x" * 100)
    old = os.path.join(fresh.folder, "old.log")
    with open(old, "w") as fh:
        fh.write("y" * 500)
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))
    cleaner.measure_target(fresh)
    check("age filter excludes new files", fresh.file_count == 1, str(fresh.file_count))
    check("age filter size matches old file", fresh.size == 500, str(fresh.size))

    inv = cleaner.inventory(install)
    check("inventory returns every folder", len(inv) == len(cleaner.INVENTORY_FOLDERS))
    check("inventory has sizes", any(row["size"] > 0 for row in inv))
else:
    note("no install; skipping cleaner checks")

# --------------------------------------------------------------------------- #
section("library: zip install / trash / restore")
dest = os.path.join(SANDBOX, "UserSkins")
os.makedirs(dest, exist_ok=True)
archive_path = os.path.join(SANDBOX, "MySkin.zip")
make_zip(
    archive_path,
    {
        "MySkin/ussr_t34.blk": b"blk data",
        "MySkin/body.dds": b"D" * 4096,
        "MySkin/turret.dds": b"T" * 2048,
        "__MACOSX/._junk": b"junk",
        "MySkin/nested/detail.png": b"P" * 512,
    },
)
preview = library.inspect_archive(archive_path)
check("archive inspected", preview.ok and preview.entries >= 4, str(preview))
check("suggested name from top folder", preview.suggested_name == "MySkin", preview.suggested_name)

outcome = library.install_from_path(archive_path, dest, trash_root=appdirs.trash_dir())
check("zip installed", outcome.ok, outcome.error)
check("installed count excludes __MACOSX", outcome.installed == 4, str(outcome.installed))
target = os.path.join(dest, "MySkin")
check("target folder created", os.path.isdir(target))
check("blk present", os.path.isfile(os.path.join(target, "ussr_t34.blk")))
check("nested file present", os.path.isfile(os.path.join(target, "nested", "detail.png")))
check("junk not installed", not os.path.exists(os.path.join(target, "._junk")))

items = library.list_content(dest, kind="skin")
check("library lists the skin", len(items) == 1 and items[0].is_dir, str(items))
check("library detected blk", items[0].has_blk)
check("library counted files", items[0].file_count == 4, str(items[0].file_count))
check("library detail text", "文件" in items[0].detail, items[0].detail)

conflict = library.install_from_path(archive_path, dest, trash_root=appdirs.trash_dir())
check("existing target refused without overwrite", not conflict.ok, conflict.error)
over = library.install_from_path(archive_path, dest, overwrite=True, trash_root=appdirs.trash_dir())
check("overwrite succeeds", over.ok, over.error)
check("overwrite reported", over.replaced)

deleted, freed, errors = library.delete_items(items, use_trash=True, trash_root=appdirs.trash_dir())
check("delete moved to trash", deleted == 1 and not errors, str(errors))
check("freed bytes reported", freed > 0)
check("source folder gone", not os.path.exists(target))

entries = trash.list_entries()
matching = [e for e in entries if e.name == "MySkin"]
check("trash lists the entry", bool(matching), f"{len(entries)} entries")
if matching:
    entry = matching[0]
    check("trash recorded origin", entry.origin == dest, f"{entry.origin} vs {dest}")
    ok, message = trash.restore_entry(entry)
    check("restore succeeded", ok, message)
    check("restored folder back", os.path.isdir(target))
    check("restored contents intact", os.path.isfile(os.path.join(target, "nested", "detail.png")))

# folder (non-zip) install
plain_src = os.path.join(SANDBOX, "PlainSight")
os.makedirs(plain_src, exist_ok=True)
with open(os.path.join(plain_src, "sight.blk"), "w", encoding="utf-8") as fh:
    fh.write("sight")
outcome2 = library.install_from_path(plain_src, dest, trash_root=appdirs.trash_dir())
check("folder install works", outcome2.ok, outcome2.error)
check("folder install named after source", os.path.basename(outcome2.target) == "PlainSight")

# zip-slip guard
evil = os.path.join(SANDBOX, "evil.zip")
make_zip(evil, {"Evil/../../escaped.txt": b"nope", "Evil/ok.txt": b"fine"})
outcome3 = library.install_from_path(evil, dest, trash_root=appdirs.trash_dir())
check("zip-slip entry rejected", not os.path.exists(os.path.join(SANDBOX, "escaped.txt")))
check("safe sibling still installed", os.path.isfile(os.path.join(dest, "Evil", "ok.txt")))

# --------------------------------------------------------------------------- #
section("sound mods: install + full rollback (sandbox)")
sandbox_sound = os.path.join(SANDBOX, "sound")
os.makedirs(os.path.join(sandbox_sound, "mods"), exist_ok=True)
original_banks = {
    "masterbank.bank": b"M" * 10000,
    "tanks_weapons.bank": b"W" * 5000,
    "mods/extra.bank": b"E" * 1000,
}
for rel, data in original_banks.items():
    with open(os.path.join(sandbox_sound, rel), "wb") as fh:
        fh.write(data)

before = soundmods.snapshot(sandbox_sound)
check("snapshot captured", len(before) == 3, str(len(before)))

inventory = soundmods.sound_inventory(sandbox_sound)
check("sound inventory lists entries", len(inventory) == 3, str([r.name for r in inventory]))
check("inventory picks a bank", any(r.kind == "bank" for r in inventory))

mod_zip = os.path.join(SANDBOX, "SoundMod.zip")
make_zip(
    mod_zip,
    {
        "sound/masterbank.bank": b"REPLACED" * 100,
        "sound/mods/extra.bank": b"CHANGED" * 50,
        "sound/mods/brand_new.bank": b"NEW" * 30,
    },
)
result = soundmods.install_sound_set(mod_zip, sandbox_sound, label="TestPack")
check("sound mod installed", result.ok, result.error)
check("wrapper 'sound/' was stripped", result.target_root == os.path.abspath(sandbox_sound), result.target_root)
check("one replacement overwrote bank", result.replaced == 2, f"replaced={result.replaced}")
check("one file added", result.added == 1, f"added={result.added}")
with open(os.path.join(sandbox_sound, "masterbank.bank"), "rb") as fh:
    check("bank content replaced", fh.read() == b"REPLACED" * 100)
check("new bank present", os.path.isfile(os.path.join(sandbox_sound, "mods", "brand_new.bank")))

after = soundmods.snapshot(sandbox_sound)
diff = soundmods.diff_snapshots(before, after)
check("diff sees the addition", diff["added"] == ["mods/brand_new.bank"], str(diff))
check("diff sees changed files", "masterbank.bank" in diff["changed"], str(diff))

sets = soundmods.list_sets()
mine = [s for s in sets if s.label == "TestPack"]
check("rollback set recorded", bool(mine))
if mine:
    ok, message = soundmods.restore_set(mine[0])
    check("rollback succeeded", ok, message)
    restored = soundmods.snapshot(sandbox_sound)
    check("added file removed by rollback", "mods/brand_new.bank" not in restored)
    with open(os.path.join(sandbox_sound, "masterbank.bank"), "rb") as fh:
        data = fh.read()
    check("original bank bytes restored", data == b"M" * 10000, f"{len(data)} bytes")
    with open(os.path.join(sandbox_sound, "mods", "extra.bank"), "rb") as fh:
        check("second original restored", fh.read() == b"E" * 1000)
    final_diff = soundmods.diff_snapshots(before, restored)
    check(
        "sound folder byte-identical after rollback",
        not final_diff["added"] and not final_diff["removed"] and not final_diff["changed"],
        str(final_diff),
    )
    soundmods.delete_set(mine[0])
    check("backup set deleted", not os.path.exists(mine[0].folder))

# install into a subfolder must not escape
escape = soundmods.install_sound_set(mod_zip, sandbox_sound, dest_subdir="../../outside")
check("escaping dest_subdir rejected", not escape.ok, escape.error)

# --------------------------------------------------------------------------- #
section("news (live)")
result = news.fetch_news("zh", force=True, timeout=25)
check("news fetched from warthunder.com", result.ok, result.error)
if result.ok:
    check("at least 10 items", len(result.items) >= 10, str(len(result.items)))
    check("titles non-empty", all(i.title.strip() for i in result.items))
    check("urls absolute", all(i.url.startswith("http") for i in result.items))
    check("comments parsed", sum(1 for i in result.items if i.comment) >= 5)
    check("images parsed", sum(1 for i in result.items if i.image) >= 5)
    note(f"首条资讯：{result.items[0].title}")
    note(f"链接示例：{result.items[0].url}")
    cached = news.fetch_news("zh", max_age_seconds=3600)
    check("cache is used on the second call", cached.from_cache and cached.ok)

# --------------------------------------------------------------------------- #
section("launch command construction")
if install:
    launcher_cmd = gamelaunch.build_launcher_command(install)
    game_cmd = gamelaunch.build_game_command(install)
    replay_cmd = gamelaunch.build_replay_command(install, os.path.join(SANDBOX, "x.wrpl"))
    check("launcher command points at launcher.exe", launcher_cmd[0].endswith("launcher.exe"))
    check("game command points at aces.exe", game_cmd[0].endswith("aces.exe"))
    check("replay command passes the file", replay_cmd[1].endswith("x.wrpl"))
    check("both executables exist", os.path.isfile(launcher_cmd[0]) and os.path.isfile(game_cmd[0]))
    missing = gamelaunch.build_launcher_command(gamepath.GameInstall(root=os.path.join(SANDBOX, "no")))
    bad = gamelaunch._spawn(missing, SANDBOX)
    check("spawn refuses a missing exe", not bad.ok and "找不到文件" in bad.error, bad.error)

# --------------------------------------------------------------------------- #
shutil.rmtree(SANDBOX, ignore_errors=True)

print(f"\n{'-' * 64}")
if _notes:
    print("observations:")
    for text in _notes:
        print("   ·", text)
print(f"{'-' * 64}")
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for item in _failures:
        print("   -", item)
    sys.exit(1)
print(f"All {_passed} checks passed.")
