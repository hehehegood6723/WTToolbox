"""Self-contained tests for the blk parser / writer.

Run:  python tests/test_blk.py
No third-party test runner required.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from wttoolbox.core.blk import (  # noqa: E402
    BlkDocument,
    format_float,
    format_value,
    quote,
    unquote,
)

from _game import HAVE_GAME, ROOT, skip_summary  # noqa: E402

#: The real config.blk when this machine has War Thunder installed;
#: empty otherwise, which makes the guard below skip the integration part.
REAL_CONFIG = os.path.join(ROOT, "config.blk") if HAVE_GAME else ""

_failures: list[str] = []
_passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [ok]   {label}")
    else:
        _failures.append(f"{label} {detail}".strip())
        print(f"  [FAIL] {label} {detail}")


def section(title: str) -> None:
    print(f"\n== {title}")


# --------------------------------------------------------------------------- #
section("value helpers")
check("format_float(1.0)", format_float(1.0) == "1")
check("format_float(0.5)", format_float(0.5) == "0.5")
check("format_float(0.1)", format_float(0.1) == "0.1")
check("format_value bool true", format_value("b", True) == "yes")
check("format_value bool false", format_value("b", False) == "no")
check("format_value int", format_value("i", 3.7) == "4")
check("format_value string", format_value("t", "abc") == '"abc"')
check("quote escapes", quote('a"b') == '"a\\"b"')
check("unquote roundtrip", unquote(quote('a"b\\c')) == 'a"b\\c')

# --------------------------------------------------------------------------- #
section("synthetic document")
doc = BlkDocument('a:t="1"\nblk{\n  x:i=2\n}\n')
check("root param", doc.value([], "a") == "1")
check("block param", doc.value("blk", "x") == 2)
check("clean rerender", doc.rerender() == doc.original_text)
check(
    "set existing",
    doc.set_raw("blk", "x", "9") and doc.rerender() == 'a:t="1"\nblk{\n  x:i=9\n}\n',
    repr(doc.rerender()),
)
check("ensure existing block new key", doc.ensure("blk", "y", "b", True))
check("ensure result has y", '  y:b=yes\n' in doc.rerender(), repr(doc.rerender()))
check(
    "ensure produced valid output",
    doc.rerender() == 'a:t="1"\nblk{\n  x:i=9\n  y:b=yes\n}\n',
    repr(doc.rerender()),
)

section("synthetic document - new block")
doc2 = BlkDocument('root:i=1\nblk{\n  x:i=2\n}\n')
doc2.ensure(["brandnew"], "k", "t", "v")
out2 = doc2.rerender()
check("new block appended", "brandnew{\n" in out2, repr(out2))
check("new key inside new block", "  k:t=\"v\"\n" in out2, repr(out2))
check("block braces balanced", out2.count("{") == out2.count("}"), repr(out2))
too = BlkDocument(out2)
check("reparsed new block", too.value("brandnew", "k") == "v")
check("reparsed original intact", too.value([], "root") == 1 and too.value("blk", "x") == 2)

section("nested new block")
doc3 = BlkDocument("a:i=1\n")
doc3.ensure(["outer", "inner"], "deep", "r", 0.25)
out3 = doc3.rerender()
check("nested braces balanced", out3.count("{") == out3.count("}"), repr(out3))
three = BlkDocument(out3)
check("nested value readable", three.value(["outer", "inner"], "deep") == 0.25, repr(out3))
check("nested indent 4", "    deep:r=0.25\n" in out3, repr(out3))

section("multiple inserts into one real block keep order")
doc4 = BlkDocument("b{\n  first:i=1\n}\n")
doc4.ensure("b", "s1", "i", 1)
doc4.ensure("b", "s2", "i", 2)
doc4.ensure("b", "s3", "i", 3)
out4 = doc4.rerender()
check(
    "insertion order preserved",
    out4 == "b{\n  first:i=1\n  s1:i=1\n  s2:i=2\n  s3:i=3\n}\n",
    repr(out4),
)

# --------------------------------------------------------------------------- #
if os.path.exists(REAL_CONFIG):
    section(f"REAL config.blk round-trip ({REAL_CONFIG})")
    with open(REAL_CONFIG, "rb") as fh:
        raw = fh.read()
    real = BlkDocument.load(REAL_CONFIG)

    check("byte-exact rerender", real.rerender().encode("utf-8") == raw)
    check("no spurious edits", not real.dirty)
    check("language", real.value([], "language") == "Chinese")
    check("graphicsQuality", real.value([], "graphicsQuality") == "ultralow")
    check("use_eac bool", real.value([], "use_eac") is True)
    check("forcedLauncher int", real.value([], "forcedLauncher") == 0)
    check("video.mode", real.value("video", "mode") == "fullscreen")
    check("video.vsync bool", real.value("video", "vsync") is False)
    check("graphics.ssaa float", real.value("graphics", "ssaa") == 1.0)
    check("graphics.grassRadiusMul", real.value("graphics", "grassRadiusMul") == 0.1)
    check("download.dnl_speed_rate", real.value("download", "dnl_speed_rate") == 5000)
    check("launcher.startup_with_windows", real.value("launcher", "startup_with_windows") is True)

    n_params = sum(1 for _ in real.walk_params())
    check("param count > 60", n_params > 60, f"got {n_params}")

    # edit an existing value and confirm only that byte range moves
    real.set_value("video", "vsync", True)
    edited = real.rerender()
    check("edit kept length delta", len(edited) == len(raw) + 1, f"{len(raw)} -> {len(edited)}")
    check("edit applied", 'vsync:b=yes' in edited)
    check("edit did not touch neighbours", 'antialiasing_mode:t="off"' in edited)

    # round-trip an edited document through the parser again
    reparsed = BlkDocument(edited)
    check("edited doc reparses", reparsed.value("video", "vsync") is True)
    check("edited doc params stable", sum(1 for _ in reparsed.walk_params()) == n_params)

    # save path (to a temp copy) - must be atomic and parseable
    tmpdir = tempfile.mkdtemp(prefix="tk-blk-")
    try:
        target = os.path.join(tmpdir, "config.blk")
        shutil.copy2(REAL_CONFIG, target)
        d = BlkDocument.load(target)
        d.set_value("graphics", "grassRadiusMul", 0.75)
        bak = d.save()
        check("save created backup", bool(bak) and os.path.exists(bak or ""))
        check("save wrote file", os.path.exists(target))
        with open(bak, "rb") as fh:
            check("backup is original", fh.read() == raw)
        reloaded = BlkDocument.load(target)
        check("saved value persisted", reloaded.value("graphics", "grassRadiusMul") == 0.75)
        check("saved file still parses fully", sum(1 for _ in reloaded.walk_params()) == n_params)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ensure() on the real file must not disturb existing content
    probe = BlkDocument(raw.decode("utf-8"))
    probe.ensure(["graphics"], "tkProbeKey", "b", True)
    probe.ensure(["tkProbeBlock"], "hello", "t", "world")
    final = probe.rerender()
    check("probe output brace-balanced", final.count("{") == final.count("}"))
    final_doc = BlkDocument(final)
    check("probe key readable", final_doc.value("graphics", "tkProbeKey") is True)
    check("probe block readable", final_doc.value("tkProbeBlock", "hello") == "world")
    check(
        "probe preserved original params",
        sum(1 for _ in final_doc.walk_params()) == n_params + 2,
        f"{sum(1 for _ in final_doc.walk_params())} vs {n_params + 2}",
    )
    # the untouched part of the file must be identical
    check(
        "probe kept original prefix bytes",
        final.startswith(raw.decode("utf-8").split("graphics{")[0]),
    )
else:
    print(f"\n!! real config not found at {REAL_CONFIG}; skipping integration checks")

# --------------------------------------------------------------------------- #
print(f"\n{'-' * 60}")
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for f in _failures:
        print("   -", f)
    sys.exit(1)
print(f"All {_passed} checks passed.")
