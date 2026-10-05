"""WTToolbox entry point.

Responsibilities:

* redirect ``stdout``/``stderr`` when running as a windowed executable (where
  Python hands us ``None`` and a stray ``traceback.print_exc`` would crash)
* enforce a single instance and raise the existing window instead of opening a
  second copy
* apply the saved theme and build :class:`MainWindow`
* kick off game detection on startup without blocking the first paint
* catch unhandled exceptions everywhere and log them instead of vanishing
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

# Make ``src`` importable when running straight from the checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PySide6.QtCore import QCoreApplication, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wttoolbox import APP_NAME, __version__  # noqa: E402
from wttoolbox.core import appdirs, gamepath  # noqa: E402
from wttoolbox.core.applog import log  # noqa: E402
from wttoolbox.core.settings import Settings  # noqa: E402
from wttoolbox.ui import theme  # noqa: E402
from wttoolbox.ui.context import PAGE_ORDER, AppContext  # noqa: E402

SINGLE_INSTANCE_KEY = "WTToolbox-SingleInstance-v1"


# --------------------------------------------------------------------------- #
#  Streams / errors
# --------------------------------------------------------------------------- #
def _redirect_streams() -> None:
    """Give the process real stdout/stderr when frozen in windowed mode."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        path = os.path.join(appdirs.logs_dir(), "stdout.log")
        stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        import io

        stream = io.StringIO()
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _install_excepthook() -> None:
    def write_crash(text: str) -> None:
        """Last-resort crash log that does not depend on logging working."""
        try:
            path = os.path.join(appdirs.logs_dir(), "crash.log")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n===== {__import__('time').strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                handle.write(text)
        except Exception:
            pass

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        write_crash(text)
        try:
            log.error(f"未捕获的异常：{text}", "崩溃")
        except Exception:
            pass
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                box = QMessageBox()
                box.setIcon(QMessageBox.Critical)
                box.setWindowTitle(f"{APP_NAME} 遇到问题")
                box.setText("程序遇到了一个未预期的错误，但已尽量保持运行。")
                box.setDetailedText(text)
                box.exec()
        except Exception:
            pass

    sys.excepthook = hook


def selftest_report() -> tuple[int, str]:
    """Import everything the app needs and describe the environment.

    Written for the frozen build: a windowed executable has no console, so the
    report is saved to ``%APPDATA%\\WTToolbox\\logs\\selftest.txt``.
    """
    import importlib
    import platform
    import traceback as tb

    lines: list[str] = []
    failures = 0

    def add(text: str) -> None:
        lines.append(text)

    add(f"{APP_NAME} 自检报告")
    add(f"时间       : {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"版本       : {__version__}")
    add(f"打包运行   : {getattr(sys, 'frozen', False)}")
    add(f"可执行文件 : {sys.executable}")
    add(f"_MEIPASS   : {getattr(sys, '_MEIPASS', '(无)')}")
    add(f"Python     : {platform.python_version()} ({platform.architecture()[0]})")
    add(f"系统       : {platform.platform()}")
    add(f"工作目录   : {os.getcwd()}")
    add(f"数据目录   : {appdirs.appdata_dir()}")
    add("")

    try:
        from PySide6.QtCore import qVersion

        add(f"Qt         : {qVersion()}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        add(f"Qt         : 导入失败 {exc}")
    add("")

    add("--- 资源文件 ---")
    for relative in ("assets/icon.ico", "assets/banner.png"):
        path = appdirs.resource_path(*relative.split("/"))
        exists = os.path.isfile(path)
        if not exists:
            failures += 1
        add(f"  {'OK ' if exists else 'MISSING'} {relative}  ({path})")
    add("")

    add("--- 模块导入 ---")
    modules = [
        "wttoolbox",
        "wttoolbox.core.appdirs",
        "wttoolbox.core.applog",
        "wttoolbox.core.blk",
        "wttoolbox.core.blk_schema",
        "wttoolbox.core.cleaner",
        "wttoolbox.core.config_backup",
        "wttoolbox.core.gamelaunch",
        "wttoolbox.core.gamelog",
        "wttoolbox.core.gamepath",
        "wttoolbox.core.healthcheck",
        "wttoolbox.core.library",
        "wttoolbox.core.mapnames",
        "wttoolbox.core.news",
        "wttoolbox.core.replays",
        "wttoolbox.core.settings",
        "wttoolbox.core.soundmods",
        "wttoolbox.core.trash",
        "wttoolbox.core.winutil",
        "wttoolbox.core.wtdata",
        "wttoolbox.ui.theme",
        "wttoolbox.ui.icons",
        "wttoolbox.ui.widgets",
        "wttoolbox.ui.context",
        "wttoolbox.ui.titlebar",
        "wttoolbox.ui.mainwindow",
        "wttoolbox.ui.pages.home",
        "wttoolbox.ui.pages.sound",
        "wttoolbox.ui.pages.tools",
        "wttoolbox.ui.pages.library",
        "wttoolbox.ui.pages.settings",
        "wttoolbox.ui.pages.vehicles",
        "wttoolbox.ui.pages.stats",
        "wttoolbox.ui.dialogs.config_editor",
        "wttoolbox.ui.dialogs.vehicle_picker",
    ]
    for name in modules:
        try:
            importlib.import_module(name)
            add(f"  OK      {name}")
        except Exception:  # noqa: BLE001
            failures += 1
            add(f"  FAILED  {name}")
            add("          " + tb.format_exc().replace("\n", "\n          ").rstrip())
    add("")

    add("--- 功能冒烟 ---")
    checks = [
        ("blk 解析器",
         lambda: importlib.import_module("wttoolbox.core.blk")
         .BlkDocument('a:t="1"\nblk{\n  x:i=2\n}\n').value("blk", "x") == 2),
        ("图标渲染",
         lambda: not importlib.import_module("wttoolbox.ui.icons")
         .icon_pixmap("home", "#000000", 16).isNull()),
        ("设置读写",
         lambda: importlib.import_module("wttoolbox.core.settings").Settings().get("theme") in ("light", "dark")),
        ("进程枚举",
         lambda: len(importlib.import_module("wttoolbox.core.winutil").find_processes()) > 0),
        ("游戏目录检测",
         lambda: isinstance(
             importlib.import_module("wttoolbox.core.gamepath").detect_registry(), list)),
    ]
    for label, probe in checks:
        try:
            ok = bool(probe())
        except Exception:  # noqa: BLE001
            ok = False
            add("          " + tb.format_exc().replace("\n", "\n          ").rstrip())
        if not ok:
            failures += 1
        add(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    add("")

    add(f"结论: {'全部通过' if failures == 0 else f'{failures} 项失败'}")
    return failures, "\n".join(lines)


def _selftest() -> int:
    # Some checks touch QPixmap/QSvgRenderer, which need a live QGuiApplication.
    if QApplication.instance() is None:
        try:
            QApplication(sys.argv[:1])
        except Exception:
            pass
    failures, report = selftest_report()
    path = os.path.join(appdirs.logs_dir(), "selftest.txt")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(report)
    except OSError:
        pass
    try:
        print(report)
    except Exception:
        pass
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
#  Single instance
# --------------------------------------------------------------------------- #
class SingleInstance:
    """A local-socket guard: the second launch activates the first window."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.server: QLocalServer | None = None
        self._handler = None

    def try_activate_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if socket.waitForConnected(400):
            socket.write(b"show\n")
            socket.flush()
            socket.waitForBytesWritten(400)
            socket.disconnectFromServer()
            return True
        return False

    def listen(self, on_activate) -> bool:
        QLocalServer.removeServer(self.key)
        server = QLocalServer()
        if not server.listen(self.key):
            return False

        def handle() -> None:
            connection = server.nextPendingConnection()
            if connection is None:
                return
            connection.readyRead.connect(lambda: connection.readAll())
            connection.disconnected.connect(connection.deleteLater)
            on_activate()

        self._handler = handle
        server.newConnection.connect(handle)
        self.server = server
        return True


# --------------------------------------------------------------------------- #
#  Startup
# --------------------------------------------------------------------------- #
def _auto_detect(ctx: AppContext, window) -> None:
    """Fill in both game paths on first run without blocking the UI.

    The live client and the DEV-server client are detected separately, so one
    being missing never affects the other.
    """
    found_any = False
    for channel in gamepath.CHANNELS:
        if ctx.has_install_for(channel):
            continue
        remembered = ctx.settings.known_game_paths(channel)
        candidates = gamepath.detect_all(
            channel=channel, include_scan=False, extra_candidates=remembered
        )
        valid = [c for c in candidates if c.validate().ok]
        pool = valid or candidates
        label = gamepath.CHANNEL_LABELS[channel]
        if not pool:
            log.info(
                f"启动时未检测到{label}客户端"
                + ("（未安装测试版）" if channel == gamepath.CHANNEL_DEV else ""),
                "路径",
            )
            continue
        best = pool[0]
        ctx.set_install(best, channel=channel)
        log.ok(f"启动时检测到{label}目录：{best.root}（{best.source}）", "路径")
        found_any = True
        if len(pool) > 1:
            ctx.installsFound.emit(pool)
    if not found_any and not ctx.has_install:
        log.warn("启动时未检测到游戏目录，将交由用户手动选择", "路径")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="War Thunder 桌面辅助工具箱")
    parser.add_argument("--minimized", action="store_true", help="启动后最小化到系统托盘")
    parser.add_argument(
        "--page", default="",
        help="启动时打开的页面：" + "/".join(PAGE_ORDER),
    )
    parser.add_argument("--game-path", default="", help="直接指定游戏目录")
    parser.add_argument("--detect", action="store_true", help="启动时强制重新搜索游戏目录")
    parser.add_argument("--selftest", action="store_true", help="运行自检并写出诊断报告后退出")
    parser.add_argument("--version", action="store_true", help="打印版本后退出")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    appdirs.ensure_all()
    if args.selftest:
        return _selftest()

    _redirect_streams()
    _install_excepthook()

    QCoreApplication.setAttribute(Qt.AA_DontUseNativeMenuBar, False)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(f"{APP_NAME} 战雷工具箱")
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstance(SINGLE_INSTANCE_KEY)
    if guard.try_activate_existing():
        print("已有实例在运行，已请求其显示窗口。")
        return 0

    settings = Settings()
    ctx = AppContext(settings)
    palette = theme.palette_for(settings.get("theme", "light"))
    theme.apply_theme(app, palette, font_scale=float(settings.get("font_scale", 1.0) or 1.0))
    ctx.palette = palette

    icon_file = appdirs.resource_path("assets", "icon.ico")
    if os.path.isfile(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    log.info(f"{APP_NAME} v{__version__} 启动 · 主题 {palette.name} · Python {sys.version.split()[0]}", "启动")

    if args.game_path and os.path.isdir(args.game_path):
        # The folder itself decides which channel it belongs to.
        manual = gamepath.GameInstall(root=args.game_path, source="manual")
        ctx.set_install(manual, channel=gamepath.channel_of(args.game_path))

    from wttoolbox.ui.mainwindow import MainWindow

    window = MainWindow(ctx)
    guard.listen(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))

    start_page = args.page or settings.get("start_page") or "home"
    window._apply_page(start_page if start_page in PAGE_ORDER else "home")

    wants_tray_start = args.minimized or settings.get("start_minimized", False)
    if wants_tray_start and window.tray is not None:
        window.hide()
        log.info("已按设置最小化到系统托盘启动", "启动")
    else:
        window.show()

    if args.detect or (settings.get("auto_detect_on_start", True) and not ctx.has_install):
        QTimer.singleShot(400, lambda: _auto_detect(ctx, window))

    code = app.exec()

    # Worker threads may still be inside a blocking network call (the news
    # fetch, a folder size walk).  Qt's teardown would otherwise wait on them
    # while they try to re-acquire the GIL - a classic PySide6 shutdown
    # deadlock.  Cancel, give them a moment, then leave immediately.
    try:
        from wttoolbox.ui import widgets as ui_widgets

        ui_widgets.cancel_tasks(window)
        for task in list(getattr(window, "_tk_tasks", ())):
            task.cleanup()
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
