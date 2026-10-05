"""Every Python file in the repository must byte-compile.

Run:  python tests/test_syntax.py

This is deliberately more than an ``ast.parse`` pass.  ``ast.parse`` only
builds a syntax tree and skips the compiler's semantic checks, so a real error
such as a ``return`` that ended up outside its function, a ``break`` outside a
loop, or a duplicated argument name sails through ``ast.parse`` and only fails
when the module is imported.  That is exactly how a bad edit to
``tools/shot_all.py`` once reached a commit.  ``compile()`` catches both
classes, so every file goes through it here.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

SKIP_DIRS = {"dist", "__pycache__", ".git", ".venv", "venv", "node_modules",
             "build", "shots", ".wiki-cache"}

_failures: list[str] = []
_passed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"  [ok]   {label}")
    else:
        _failures.append(f"{label} :: {detail}")
        print(f"  [FAIL] {label}  {detail}")


def section(title: str) -> None:
    print(f"\n== {title}")


def collect() -> list[str]:
    paths: list[str] = []
    for current, dirs, names in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            if name.endswith(".py"):
                paths.append(os.path.join(current, name))
    return sorted(paths)


section("byte-compile every Python file")
files = collect()
check(f"found the source tree ({len(files)} files)", len(files) > 40, str(len(files)))

broken: list[str] = []
for path in files:
    relative = os.path.relpath(path, _ROOT)
    try:
        with open(path, "rb") as fh:
            source = fh.read()
    except OSError as exc:
        broken.append(f"{relative}: cannot read ({exc})")
        continue
    try:
        compile(source, path, "exec")
    except SyntaxError as exc:
        broken.append(f"{relative}:{exc.lineno}: {exc.msg}")
    except ValueError as exc:  # e.g. source containing null bytes
        broken.append(f"{relative}: {exc}")

check("every file compiles", not broken, "; ".join(broken[:5]))
for item in broken:
    print(f"         {item}")

section("the compiler is actually stricter than the parser")
# Guard the guard: if ast.parse ever starts rejecting these too, this test
# should be revisited rather than silently kept as ceremony.
sneaky = "def f():\n    pass\nreturn 1\n"
import ast  # noqa: E402

parsed = True
try:
    ast.parse(sneaky)
except SyntaxError:
    parsed = False
check("ast.parse accepts a stray return (so we must use compile)", parsed)

compiled = True
try:
    compile(sneaky, "<sneaky>", "exec")
except SyntaxError:
    compiled = False
check("compile() rejects it", not compiled)

section("no module imports Qt into the pure-Python core")
# core/ is documented as Qt-free so the backend can be exercised headlessly.
core_dir = os.path.join(_ROOT, "src", "wttoolbox", "core")
offenders = []
for name in sorted(os.listdir(core_dir)):
    if not name.endswith(".py"):
        continue
    with open(os.path.join(core_dir, name), encoding="utf-8") as fh:
        text = fh.read()
    if "PySide6" in text or "from PySide" in text:
        offenders.append(name)
check("src/wttoolbox/core stays Qt-free", not offenders, str(offenders))

section("no file writes to a developer-machine path")
# Absolute paths from one machine must never reach the repository.
markers = ("C:\\Users\\", "D:\\ThunderKit-build", "D:\\Projects\\")
hits: list[str] = []
for path in files:
    if os.path.basename(path) == "test_syntax.py":
        continue  # this file names the markers on purpose
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh, 1):
            for marker in markers:
                if marker in line:
                    relative = os.path.relpath(path, _ROOT)
                    hits.append(f"{relative}:{number}")
check("no hard-coded developer paths", not hits, ", ".join(hits[:6]))

print(f"\n{'-' * 60}")
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for item in _failures:
        print("   -", item)
    sys.stdout.flush()
    os._exit(1)
print(f"All {_passed} checks passed.")
sys.stdout.flush()
os._exit(0)
