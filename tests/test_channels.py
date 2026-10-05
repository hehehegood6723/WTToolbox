"""Tests for the two independent game-path channels (live vs DEV-server).

Run:  python tests/test_channels.py

The DEV client is recognised from the markers the official wiki documents
(https://wiki.warthunder.com/mechanics/dev_server): a ``matchingdevmode`` file,
``wt_dev_launcher.exe``, or ``yunetwork { curCircuit:t="dev" }`` in config.blk.
The real dev client is not installed on this machine, so the dev branch is
exercised with folders built exactly that way.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from wttoolbox.core import gamepath  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import theme  # noqa: E402
from wttoolbox.ui.context import AppContext  # noqa: E402

HUGE = Qt.WidgetAttribute.WA_DontShowOnScreen
SANDBOX = os.path.join(tempfile.gettempdir(), "tk-channels-test")
from _game import HAVE_GAME, ROOT, skip, skip_summary  # noqa: E402

REAL_ROOT = ROOT

CONFIG_PROD = 'graphics{\n  renderer:t="auto"\n}\nyunetwork{\n  curCircuit:t="production"\n}\n'
CONFIG_DEV = 'graphics{\n  renderer:t="auto"\n}\nyunetwork{\n  curCircuit:t="dev"\n}\n'
CONFIG_PLAIN = 'graphics{\n  renderer:t="auto"\n}\n'

_failures: list[str] = []
_passed = 0

app = QApplication([])
theme.apply_theme(app, theme.LIGHT)


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [ok]   {label}")
    else:
        _failures.append(f"{label} :: {detail}")
        print(f"  [FAIL] {label}  {detail}")


def section(title: str) -> None:
    print(f"\n== {title}")


def pump(rounds: int = 10) -> None:
    for _ in range(rounds):
        app.processEvents()


def make_install(name: str, config: str, *, marker: bool = False,
                 dev_launcher: bool = False) -> str:
    root = os.path.join(SANDBOX, name)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "win64"), exist_ok=True)
    with open(os.path.join(root, "config.blk"), "w", encoding="utf-8") as fh:
        fh.write(config)
    for rel in (os.path.join("win64", "aces.exe"), "launcher.exe"):
        with open(os.path.join(root, rel), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 64)
    if marker:
        open(os.path.join(root, "matchingdevmode"), "wb").close()
    if dev_launcher:
        with open(os.path.join(root, "wt_dev_launcher.exe"), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 64)
    return root


shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX, exist_ok=True)

stable = make_install("WarThunder", CONFIG_PROD)
dev_both = make_install("WarThunderDev", CONFIG_DEV, marker=True)
dev_circuit = make_install("WarThunderDevCircuit", CONFIG_DEV)
dev_launcher = make_install("WarThunderDevLauncher", CONFIG_PROD, dev_launcher=True)
plain = make_install("WarThunderPlain", CONFIG_PLAIN)

# --------------------------------------------------------------------------- #
section("channel classification (official dev markers)")
check("production circuit -> stable", gamepath.channel_of(stable) == gamepath.CHANNEL_STABLE,
      gamepath.channel_of(stable))
check("circuit=dev -> dev", gamepath.channel_of(dev_circuit) == gamepath.CHANNEL_DEV,
      gamepath.channel_of(dev_circuit))
check("matchingdevmode -> dev", gamepath.channel_of(dev_both) == gamepath.CHANNEL_DEV,
      gamepath.channel_of(dev_both))
check("wt_dev_launcher.exe -> dev", gamepath.channel_of(dev_launcher) == gamepath.CHANNEL_DEV,
      gamepath.channel_of(dev_launcher))
check("a client without a yunetwork block is still a client",
      gamepath.channel_of(plain) == gamepath.CHANNEL_STABLE, gamepath.channel_of(plain))
check("circuit() reads 'dev'", gamepath.circuit(dev_circuit) == "dev", gamepath.circuit(dev_circuit))
check("circuit() reads 'production'", gamepath.circuit(stable) == "production",
      gamepath.circuit(stable))
check("circuit() is empty without the block", gamepath.circuit(plain) == "", gamepath.circuit(plain))
check("a dev folder reports its evidence", len(gamepath.dev_markers(dev_both)) >= 2,
      str(gamepath.dev_markers(dev_both)))
check("a stable folder reports no dev evidence", gamepath.dev_markers(stable) == [],
      str(gamepath.dev_markers(stable)))
check("a missing folder is unknown",
      gamepath.channel_of(os.path.join(SANDBOX, "nope")) == gamepath.CHANNEL_UNKNOWN)

# --------------------------------------------------------------------------- #
section("the two channels never see each other's folders")
pool = [stable, dev_both, dev_circuit, dev_launcher, plain]
dev_found = gamepath.detect_all(channel=gamepath.CHANNEL_DEV, include_scan=False,
                                extra_candidates=pool)
stable_found = gamepath.detect_all(channel=gamepath.CHANNEL_STABLE, include_scan=False,
                                   extra_candidates=pool)
dev_roots = {os.path.normcase(i.root) for i in dev_found}
stable_roots = {os.path.normcase(i.root) for i in stable_found}
check("dev finds exactly the three dev folders", len(dev_roots) == 3, str(sorted(dev_roots)))
check("dev excludes the live client", os.path.normcase(stable) not in dev_roots)
check("dev excludes a client without dev markers", os.path.normcase(plain) not in dev_roots)
check("stable excludes every dev folder",
      not any(os.path.normcase(p) in stable_roots
              for p in (dev_both, dev_circuit, dev_launcher)))
check("the two result sets are disjoint", not (dev_roots & stable_roots))
check("dev results are labelled 测试版",
      all(i.is_dev and i.channel_label == "测试版" for i in dev_found))
check("stable results are labelled 正式版",
      all((not i.is_dev) and i.channel_label == "正式版" for i in stable_found))

# --------------------------------------------------------------------------- #
section("this machine")
real_stable = gamepath.detect_all(channel=gamepath.CHANNEL_STABLE, include_scan=False)
real_dev = gamepath.detect_all(channel=gamepath.CHANNEL_DEV, include_scan=False)
if HAVE_GAME:
    check("the live client is found as 正式版",
          any(os.path.normcase(i.root) == os.path.normcase(REAL_ROOT) for i in real_stable),
          str([i.root for i in real_stable]))
    check("no dev client is claimed", real_dev == [], str([i.root for i in real_dev]))
    check("the live client is never offered as 测试版",
          all(os.path.normcase(i.root) != os.path.normcase(REAL_ROOT) for i in real_dev))
else:
    check("detection runs without crashing on a machine with no install",
          isinstance(real_stable, list) and isinstance(real_dev, list))
    check("nothing is claimed as a dev client", real_dev == [], str([i.root for i in real_dev]))
    skip("live client detection against a real install")

# --------------------------------------------------------------------------- #
section("settings keep the two paths apart")
settings_path = os.path.join(SANDBOX, "settings.json")
if os.path.isfile(settings_path):
    os.remove(settings_path)
settings = Settings(settings_path)
settings.remember_game_path(r"D:\WarThunder", "stable")
settings.remember_game_path(r"D:\WarThunderDev", "dev")
check("stable path stored", settings.game_path_for("stable") == r"D:\WarThunder",
      settings.game_path_for("stable"))
check("dev path stored", settings.game_path_for("dev") == r"D:\WarThunderDev",
      settings.game_path_for("dev"))
check("stable path untouched by the dev write",
      (settings.get("game_path") or "") == r"D:\WarThunder", str(settings.get("game_path")))
check("known lists stay separate",
      settings.known_game_paths("dev") == [r"D:\WarThunderDev"]
      and settings.known_game_paths("stable") == [r"D:\WarThunder"],
      f"{settings.known_game_paths('dev')} / {settings.known_game_paths('stable')}")
settings.forget_game_path("dev")
check("forgetting one channel keeps the other",
      settings.game_path_for("dev") == "" and settings.game_path_for("stable") == r"D:\WarThunder")

# --------------------------------------------------------------------------- #
section("AppContext keeps one install per channel")
ctx = AppContext(Settings(os.path.join(SANDBOX, "ctx.json")))
check("starts with no installs", ctx.install is None and not ctx.has_install)
check("default active channel is stable", ctx.active_channel == gamepath.CHANNEL_STABLE)

ctx.set_install(gamepath.GameInstall(root=stable, source="registry"))
check("stable install stored", ctx.stable_install is not None and ctx.stable_install.root == stable)
check("active install is the stable one", ctx.install.root == stable)
check("dev install still empty", ctx.dev_install is None and not ctx.has_dev_install)

# a live folder must not be filed as the dev client
ctx.set_install(gamepath.GameInstall(root=stable), channel=gamepath.CHANNEL_DEV)
check("a live folder is refused as 测试版", ctx.dev_install is None, str(ctx.dev_install))
check("and the stable entry survives", ctx.stable_install.root == stable)

# a dev folder must not be filed as the live client
ctx.set_install(gamepath.GameInstall(root=dev_both), channel=gamepath.CHANNEL_STABLE)
check("a dev folder is refused as 正式版", ctx.stable_install.root == stable,
      ctx.stable_install.root)

ctx.set_install(gamepath.GameInstall(root=dev_both), channel=gamepath.CHANNEL_DEV)
check("dev install stored", ctx.dev_install is not None and ctx.dev_install.root == dev_both)
check("stable entry unchanged", ctx.stable_install.root == stable)
check("has_dev_install is true", ctx.has_dev_install)

switches: list[str] = []
ctx.channelChanged.connect(switches.append)
ctx.set_active_channel(gamepath.CHANNEL_DEV)
check("channel switch emitted", switches == [gamepath.CHANNEL_DEV], str(switches))
check("active install follows the channel", ctx.install.root == dev_both, str(ctx.install))
check("channel persisted", ctx.settings.get("active_channel") == gamepath.CHANNEL_DEV)
ctx.set_active_channel(gamepath.CHANNEL_STABLE)
check("switching back restores the live client", ctx.install.root == stable, str(ctx.install))
check("a real switch emits", switches == [gamepath.CHANNEL_DEV, gamepath.CHANNEL_STABLE],
      str(switches))
before = len(switches)
ctx.set_active_channel(gamepath.CHANNEL_STABLE)   # same channel: must be a no-op
check("re-selecting the same channel is silent", len(switches) == before, str(switches))

# --------------------------------------------------------------------------- #
section("home page shows two independent client blocks")
from wttoolbox.ui.pages.home import HomePage  # noqa: E402

page_ctx = AppContext(Settings(os.path.join(SANDBOX, "home.json")))
page_ctx.set_install(gamepath.GameInstall(root=stable, source="registry"))
page = HomePage(page_ctx)
page.setAttribute(HUGE, True)
page.resize(1180, 800)
page.show()
pump(14)

check("a block exists per channel",
      set(page.channel_blocks) == set(gamepath.CHANNELS), str(sorted(page.channel_blocks)))
block_stable = page.channel_blocks[gamepath.CHANNEL_STABLE]
block_dev = page.channel_blocks[gamepath.CHANNEL_DEV]
check("stable block shows its path", block_stable.path_edit.text() == stable,
      block_stable.path_edit.text())
check("stable block reports 校验通过", block_stable.state_badge.text() == "校验通过",
      block_stable.state_badge.text())
check("stable block shows the circuit", "production" in block_stable.circuit_badge.text(),
      block_stable.circuit_badge.text())
check("dev block says 未安装测试版", block_dev.state_badge.text() == "未安装测试版",
      block_dev.state_badge.text())
check("dev block is empty", block_dev.path_edit.text() == "", block_dev.path_edit.text())
check("dev block has its own detect button", block_dev.detect_button.isEnabled())
check("dev block still offers 浏览", block_dev.browse_button.isVisible())
check("the dev hint names the official markers",
      "matchingdevmode" in block_dev.hint.text() and "wt_dev_launcher" in block_dev.hint.text(),
      block_dev.hint.text()[:80])
check("the card shows which client is active", "正式版" in page.active_badge.text(),
      page.active_badge.text())

# point the dev block at a dev folder: it must stick there, not on the live one
page._set_install_for(gamepath.CHANNEL_DEV, dev_both, source="manual", quiet=True)
pump(10)
check("dev block picked the dev folder", page_ctx.dev_install is not None
      and page_ctx.dev_install.root == dev_both, str(page_ctx.dev_install))
check("stable block is untouched", page_ctx.stable_install.root == stable)
check("dev block now reports the dev circuit", "dev" in block_dev.circuit_badge.text(),
      block_dev.circuit_badge.text())
check("dev badge no longer says 未安装", block_dev.state_badge.text() != "未安装测试版",
      block_dev.state_badge.text())
check("dev evidence is listed", "matchingdevmode" in block_dev.hint.text(),
      block_dev.hint.text()[:80])

# feeding a live folder to the dev block must re-file it, not corrupt anything
page._set_install_for(gamepath.CHANNEL_DEV, plain, source="manual", quiet=True)
pump(10)
check("a live folder given to the dev block is re-filed to 正式版",
      page_ctx.stable_install.root == plain and page_ctx.dev_install is not None
      and page_ctx.dev_install.root == dev_both,
      f"stable={page_ctx.stable_install.root} dev={page_ctx.dev_install.root}")

# switching the active client
page._set_active_channel(gamepath.CHANNEL_DEV)
pump(10)
check("active channel switched", page_ctx.active_channel == gamepath.CHANNEL_DEV)
check("active badge updated", "测试版" in page.active_badge.text(), page.active_badge.text())
check("active install is the dev one", page_ctx.install.root == dev_both)
page._set_active_channel(gamepath.CHANNEL_STABLE)
pump(10)
check("switched back", page_ctx.active_channel == gamepath.CHANNEL_STABLE)

# --------------------------------------------------------------------------- #
section("main window status strip names the client")
from wttoolbox.ui.mainwindow import MainWindow  # noqa: E402

win_ctx = AppContext(Settings(os.path.join(SANDBOX, "win.json")))
win_ctx.set_install(gamepath.GameInstall(root=stable, source="registry"))
win_ctx.set_install(gamepath.GameInstall(root=dev_both), channel=gamepath.CHANNEL_DEV)
window = MainWindow(win_ctx)
window.setAttribute(HUGE, True)
window.resize(1180, 780)
window.show()
pump(14)
check("status strip names 正式版", "正式版" in window.status_left.text(), window.status_left.text())
check("status strip shows the path", stable.lower() in window.status_left.text().lower(),
      window.status_left.text())
win_ctx.set_active_channel(gamepath.CHANNEL_DEV)
pump(14)
check("status strip follows the switch to 测试版", "测试版" in window.status_left.text(),
      window.status_left.text())
check("status strip shows the dev path", dev_both.lower() in window.status_left.text().lower(),
      window.status_left.text())
check("pages were rebuilt for the new client", window.stack.count() == 7
      and set(window._built) <= {window.current_page_key}, str(sorted(window._built)))

window._force_quit = True
window.close()
pump(6)

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{'-' * 64}")
if _failures:
    print(f"FAILED {len(_failures)} / {_passed + len(_failures)} checks")
    for item in _failures:
        print("   -", item)
    sys.stdout.flush()
    os._exit(1)
print(f"All {_passed} checks passed.")
sys.stdout.flush()
os._exit(0)
