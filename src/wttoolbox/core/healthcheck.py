"""Integrity self-check for a War Thunder installation.

Answers a question the toolkit can actually answer honestly: "are the files this
game needs still present and readable?"  It cannot verify file *contents* the way
the official launcher's repair function does (that needs Gaijin's manifests), so
it never claims to.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from . import blk
from .applog import log
from .winutil import human_size

__all__ = ["CheckResult", "run_checks", "summarise", "LEVELS"]

LEVELS = ("ok", "warn", "error", "info")

# (relative path, label, required, hint)
FILE_CHECKS: tuple[tuple[str, str, bool, str], ...] = (
    ("config.blk", "游戏配置文件 config.blk", True, "缺失会导致游戏以默认设置启动"),
    ("aces.vromfs.bin", "核心资源包 aces.vromfs.bin", True, "缺少主资源包，游戏无法启动"),
    ("char.vromfs.bin", "载具资源包 char.vromfs.bin", True, "缺少载具资源"),
    ("game.vromfs.bin", "游戏逻辑包 game.vromfs.bin", True, "缺少游戏逻辑资源"),
    ("gui.vromfs.bin", "界面资源包 gui.vromfs.bin", True, "缺少界面资源"),
    ("lang.vromfs.bin", "本地化资源包 lang.vromfs.bin", True, "缺少语言资源"),
    ("mis.vromfs.bin", "任务资源包 mis.vromfs.bin", True, "缺少任务资源"),
    ("webUi.vromfs.bin", "网页界面包 webUi.vromfs.bin", True, "缺少内置网页界面"),
    ("launcher.exe", "官方启动器 launcher.exe", False, "缺失时仍可直接启动游戏客户端"),
    ("aces_BE.exe", "BattlEye 启动组件 aces_BE.exe", False, "缺失可能影响反作弊引导"),
    ("win64/aces.exe", "64 位游戏主程序 win64/aces.exe", True, "缺少主程序，无法启动"),
    ("win64/BEClient_x64.dll", "BattlEye 客户端库", False, "缺失可能无法进入联机对局"),
    ("win64/fmod64.dll", "音频引擎 fmod64.dll", False, "缺失会导致游戏无声或崩溃"),
    ("win64/libcef.dll", "内置浏览器内核 libcef.dll", False, "缺失可能导致启动器界面异常"),
    ("win64/d3dcompiler_47.dll", "着色器编译器", False, "缺失可能导致画面异常"),
)

DIR_CHECKS: tuple[tuple[str, str, bool, str], ...] = (
    ("levels", "地图数据目录 levels", True, "缺少地图数据，无法进入战斗"),
    ("sound", "音效目录 sound", True, "缺少音效资源"),
    ("ui", "界面资源目录 ui", True, "缺少界面资源"),
    ("content", "内容目录 content", True, "缺少基础内容"),
    ("UserSkins", "自定义涂装目录", False, "缺失时游戏会在启动后重建"),
    ("UserSights", "自定义瞄具目录", False, "缺失时游戏会在启动后重建"),
    ("UserMissions", "自定义任务目录", False, "缺失时游戏会在启动后重建"),
    ("Replays", "回放目录", False, "缺失时游戏会在启动后重建"),
    ("Screenshots", "截图目录", False, "缺失时游戏会在启动后重建"),
    ("compiledShaders", "着色器缓存目录", False, "缺失时会重新编译，首次进图较慢"),
    ("cache", "缓存目录", False, "缺失时会重新生成"),
)

MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024


@dataclass
class CheckResult:
    name: str
    level: str = "ok"
    detail: str = ""
    hint: str = ""
    category: str = "文件"

    @property
    def ok(self) -> bool:
        return self.level in ("ok", "info")

    @property
    def badge_kind(self) -> str:
        return {
            "ok": "success",
            "info": "info",
            "warn": "warn",
            "error": "error",
        }.get(self.level, "neutral")

    @property
    def badge_text(self) -> str:
        return {
            "ok": "通过",
            "info": "信息",
            "warn": "警告",
            "error": "缺失",
        }.get(self.level, "—")


def _check_file(install, relative: str, label: str, required: bool, hint: str) -> CheckResult:
    path = install.sub(*relative.split("/"))
    if not os.path.isfile(path):
        return CheckResult(
            name=label,
            level="error" if required else "warn",
            detail=f"未找到 {relative}",
            hint=hint,
            category="文件",
        )
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return CheckResult(name=label, level="warn", detail=f"无法读取：{exc}", hint=hint, category="文件")
    if size == 0:
        return CheckResult(
            name=label, level="error", detail=f"{relative} 为空文件（0 字节）",
            hint="建议用官方启动器的“检查文件完整性”修复", category="文件",
        )
    return CheckResult(name=label, level="ok", detail=f"{relative} · {human_size(size)}", category="文件")


def _check_dir(install, relative: str, label: str, required: bool, hint: str) -> CheckResult:
    path = install.sub(relative)
    if not os.path.isdir(path):
        return CheckResult(
            name=label,
            level="error" if required else "warn",
            detail=f"目录不存在：{relative}",
            hint=hint,
            category="目录",
        )
    try:
        count = sum(1 for entry in os.scandir(path) if entry.is_file(follow_symlinks=False))
    except OSError as exc:
        return CheckResult(name=label, level="warn", detail=f"无法读取：{exc}", hint=hint, category="目录")
    if count == 0 and required:
        return CheckResult(
            name=label, level="warn", detail=f"{relative} 中没有文件",
            hint="建议用官方启动器检查文件完整性", category="目录",
        )
    return CheckResult(name=label, level="ok", detail=f"{relative} · {count} 个文件", category="目录")


def run_checks(
    install,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[CheckResult]:
    """Run every structural check.  Never raises."""
    results: list[CheckResult] = []
    total = len(FILE_CHECKS) + len(DIR_CHECKS) + 6
    done = 0

    def tick(label: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, total, label)
        return None

    if not install or not install.exists:
        return [
            CheckResult(
                name="游戏目录", level="error",
                detail="尚未设置或目录不存在", hint="请在主页选择正确的游戏目录",
                category="目录",
            )
        ]

    for relative, label, required, hint in FILE_CHECKS:
        if should_cancel and should_cancel():
            return results
        results.append(_check_file(install, relative, label, required, hint))
        tick(label)

    for relative, label, required, hint in DIR_CHECKS:
        if should_cancel and should_cancel():
            return results
        results.append(_check_dir(install, relative, label, required, hint))
        tick(label)

    # --- config.blk must be parseable and writable ---------------------------
    if should_cancel and should_cancel():
        return results
    try:
        document = blk.BlkDocument.load(install.config)
        count = sum(1 for _ in document.walk_params())
        results.append(
            CheckResult(
                name="config.blk 可解析", level="ok",
                detail=f"成功解析 {count} 个键", category="配置",
            )
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        results.append(
            CheckResult(
                name="config.blk 可解析", level="error",
                detail=f"解析失败：{exc}",
                hint="可以用工具箱的“图形配置”从备份还原，或让游戏重建该文件",
                category="配置",
            )
        )
    tick("config.blk 可解析")

    writable = os.access(install.config, os.W_OK) and os.access(install.root, os.W_OK)
    results.append(
        CheckResult(
            name="配置可写", level="ok" if writable else "warn",
            detail="游戏目录可写" if writable else "游戏目录当前不可写",
            hint="" if writable else "以管理员身份运行本工具，或检查目录权限",
            category="配置",
        )
    )
    tick("配置可写")

    # --- leftovers from an interrupted save ---------------------------------
    leftovers = [
        name
        for name in ("config.blk.tk-tmp", "config.blk.bak")
        if os.path.isfile(install.sub(name))
    ]
    results.append(
        CheckResult(
            name="无残留临时文件",
            level="ok" if not leftovers else "info",
            detail="没有发现临时文件" if not leftovers else "发现：" + "、".join(leftovers),
            hint="" if not leftovers else "这些文件不影响游戏，可在磁盘清理中一并删除",
            category="配置",
        )
    )
    tick("残留检查")

    # --- disk space ---------------------------------------------------------
    try:
        usage = shutil.disk_usage(install.root)
        free = usage.free
        level = "ok" if free >= MIN_FREE_BYTES else ("warn" if free >= 5 * 1024 ** 3 else "error")
        results.append(
            CheckResult(
                name="磁盘剩余空间",
                level=level,
                detail=f"{human_size(free)} 可用 / 共 {human_size(usage.total)}",
                hint="" if level == "ok" else "建议至少保留 20 GB，避免更新失败",
                category="系统",
            )
        )
    except OSError as exc:
        results.append(CheckResult(name="磁盘剩余空间", level="warn", detail=str(exc), category="系统"))
    tick("磁盘剩余空间")

    # --- versions -----------------------------------------------------------
    version = install.version()
    results.append(
        CheckResult(
            name="客户端版本", level="ok" if version else "warn",
            detail=version or "无法读取版本资源",
            hint="" if version else "如果游戏能正常启动可以忽略",
            category="系统",
        )
    )
    tick("客户端版本")

    log.info(
        f"完整性自检完成：{sum(1 for r in results if r.level == 'ok')} 项通过，"
        f"{sum(1 for r in results if r.level == 'warn')} 项警告，"
        f"{sum(1 for r in results if r.level == 'error')} 项缺失",
        "自检",
    )
    return results


def summarise(results: list[CheckResult]) -> dict:
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r.level == "ok"),
        "info": sum(1 for r in results if r.level == "info"),
        "warn": sum(1 for r in results if r.level == "warn"),
        "error": sum(1 for r in results if r.level == "error"),
    }
