"""Locate a War Thunder installation for the tests.

The suites run in two modes:

* **with a game install** - the integration checks (byte-exact ``config.blk``
  round-trip, ``aces.exe`` version reading, live path detection, reading the
  ``sound`` folder) really run;
* **without one** - those checks are reported as skipped and everything else
  still runs, so a contributor can clone the repository and run the whole suite
  on a machine that has never had War Thunder installed.

Point ``WTTOOLBOX_GAME`` at your install to be explicit.  When it is set it is
used verbatim (no fallback), which also makes it possible to simulate a machine
without the game:

    WTTOOLBOX_GAME=C:\\nope python tests/test_core.py
"""

from __future__ import annotations

import os

#: Conventional locations, checked in order when WTTOOLBOX_GAME is not set.
CANDIDATES = (
    r"C:\WarThunder",
    r"D:\WarThunder",
    r"E:\WarThunder",
    r"C:\Program Files\WarThunder",
    r"C:\Program Files (x86)\Steam\steamapps\common\War Thunder",
    r"D:\SteamLibrary\steamapps\common\War Thunder",
    r"E:\SteamLibrary\steamapps\common\War Thunder",
    r"C:\Games\WarThunder",
)

#: A path that is guaranteed not to be a game install, so ``os.path.join``
#: calls in the suites stay well-formed when nothing was found.
ABSENT = r"C:\__wttoolbox_no_game_installed__"


def _looks_like_install(root: str) -> bool:
    if not root or not os.path.isdir(root):
        return False
    # config.blk is the one file every install has, live or DEV-server.
    return os.path.isfile(os.path.join(root, "config.blk"))


def find_game() -> str:
    """Return the install directory, or ``""`` when there is none."""
    override = os.environ.get("WTTOOLBOX_GAME", "").strip()
    if override:
        # Explicit wins, even when it is wrong: the caller asked for this path.
        return os.path.normpath(override) if _looks_like_install(override) else override
    for candidate in CANDIDATES:
        if _looks_like_install(candidate):
            return candidate
    return ""


#: The detected install (may not exist), and the safe placeholder to join onto.
GAME = find_game()
HAVE_GAME = _looks_like_install(GAME)
ROOT = GAME if HAVE_GAME else ABSENT

_skipped: list[str] = []


def section(title: str) -> None:
    print(f"\n== {title}")


def note(text: str) -> None:
    print(f"  [note] {text}")


def skip(what: str) -> None:
    """Report a game-dependent check as skipped rather than failing it."""
    _skipped.append(what)
    print(f"  [skip] {what}")


def skip_summary() -> None:
    if _skipped:
        print(f"\n  {len(_skipped)} game-dependent check(s) skipped: no War Thunder install found.")
        print("  Set WTTOOLBOX_GAME=<your install> to run them.")


if __name__ == "__main__":
    print(f"  WTTOOLBOX_GAME = {os.environ.get('WTTOOLBOX_GAME', '(unset)')!r}")
    print(f"  detected       = {GAME!r}")
    print(f"  HAVE_GAME      = {HAVE_GAME}")
    print(f"  ROOT           = {ROOT!r}")
