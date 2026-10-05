"""Launching the game, the launcher and replays.

Verified facts used here
------------------------
* ``launcher.exe`` is the official entry point: it checks for updates and then
  starts ``win64/aces.exe`` with the working directory set to the game root.
* ``win64/aces.exe`` can also be started directly, but it skips the update and
  anti-cheat bootstrap, so the UI labels it as an advanced option.
* ``.wrpl`` has **no** shell file association on this machine, so "play replay"
  must hand the file to ``aces.exe`` as an argument.

Every spawn is detached from WTToolbox's process group, so closing the toolkit
never closes the game.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Sequence

from .applog import log
from .winutil import is_admin, run_as_admin

__all__ = [
    "LaunchResult",
    "build_launcher_command",
    "build_game_command",
    "build_replay_command",
    "launch_launcher",
    "launch_game",
    "open_replay",
    "terminate_game",
    "quit_launcher",
]

# Windows process creation flags
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass
class LaunchResult:
    ok: bool
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    pid: int | None = None
    elevated: bool = False
    error: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def command_line(self) -> str:
        def quote(part: str) -> str:
            return f'"{part}"' if " " in part else part

        return " ".join(quote(p) for p in self.command)


# --------------------------------------------------------------------------- #
#  Command construction (pure, so it is trivially testable)
# --------------------------------------------------------------------------- #
def build_launcher_command(install, extra_args: Sequence[str] = ()) -> list[str]:
    command = [install.launcher]
    command.extend(a for a in extra_args if a)
    return command


def build_game_command(
    install,
    *,
    extra_args: Sequence[str] = (),
    use_min_cpu: bool = False,
) -> list[str]:
    exe = install.aces
    if use_min_cpu:
        candidate = os.path.join(install.root, "win64", "aces-min-cpu.exe")
        if os.path.isfile(candidate):
            exe = candidate
    command = [exe]
    command.extend(a for a in extra_args if a)
    return command


def build_replay_command(install, replay_path: str) -> list[str]:
    return [install.aces, os.path.abspath(replay_path)]


# --------------------------------------------------------------------------- #
#  Spawning
# --------------------------------------------------------------------------- #
def _spawn(command: Sequence[str], cwd: str) -> LaunchResult:
    if not command or not command[0]:
        return LaunchResult(ok=False, command=list(command), cwd=cwd, error="可执行文件路径为空")
    if not os.path.isfile(command[0]):
        return LaunchResult(
            ok=False, command=list(command), cwd=cwd,
            error=f"找不到文件：{command[0]}",
        )
    if not os.path.isdir(cwd):
        cwd = os.path.dirname(command[0])

    try:
        proc = subprocess.Popen(  # noqa: S603 - deliberate, path validated above
            list(command),
            cwd=cwd,
            close_fds=True,
            creationflags=_DETACHED | _NEW_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.error(f"启动失败：{exc}", "启动")
        return LaunchResult(ok=False, command=list(command), cwd=cwd, error=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        log.error(f"启动异常：{exc}", "启动")
        return LaunchResult(ok=False, command=list(command), cwd=cwd, error=str(exc))

    return LaunchResult(ok=True, command=list(command), cwd=cwd, pid=proc.pid)


def launch_launcher(
    install,
    *,
    extra_args: Sequence[str] = (),
    elevated: bool = False,
) -> LaunchResult:
    """Start ``launcher.exe`` (the recommended way to play)."""
    command = build_launcher_command(install, extra_args)
    if elevated and not is_admin():
        ok, error = run_as_admin(command[0], "", install.root)
        result = LaunchResult(
            ok=ok, command=command, cwd=install.root, elevated=True, error=error
        )
        log.info(f"以管理员身份启动启动器（{install.root}）" if ok else f"提权启动失败：{error}", "启动")
        return result

    result = _spawn(command, install.root)
    if result.ok:
        log.ok(f"已启动官方启动器 · {install.root}", "启动")
    return result


def launch_game(
    install,
    *,
    extra_args: Sequence[str] = (),
    use_min_cpu: bool = False,
    elevated: bool = False,
) -> LaunchResult:
    """Start ``aces.exe`` directly, bypassing the launcher."""
    command = build_game_command(install, extra_args=extra_args, use_min_cpu=use_min_cpu)
    if elevated and not is_admin():
        params = " ".join(extra_args)
        ok, error = run_as_admin(command[0], params, install.root)
        result = LaunchResult(
            ok=ok, command=command, cwd=install.root, elevated=True, error=error
        )
        log.info("以管理员身份直接启动游戏" if ok else f"提权启动失败：{error}", "启动")
        return result

    result = _spawn(command, install.root)
    if result.ok:
        log.ok(f"已直接启动游戏客户端 · {os.path.basename(command[0])}", "启动")
    return result


def open_replay(install, replay_path: str, *, elevated: bool = False) -> LaunchResult:
    """Hand a ``.wrpl`` file to the game client for playback."""
    if not replay_path or not os.path.isfile(replay_path):
        return LaunchResult(ok=False, error="回放文件不存在")
    command = build_replay_command(install, replay_path)
    if elevated and not is_admin():
        ok, error = run_as_admin(command[0], f'"{os.path.abspath(replay_path)}"', install.root)
        return LaunchResult(ok=ok, command=command, cwd=install.root, elevated=True, error=error)

    result = _spawn(command, install.root)
    if result.ok:
        log.ok(f"已请求游戏打开回放 · {os.path.basename(replay_path)}", "启动")
    else:
        log.error(f"打开回放失败：{result.error}", "启动")
    return result


# --------------------------------------------------------------------------- #
#  Process control
# --------------------------------------------------------------------------- #
def terminate_game(install, *, force: bool = True) -> tuple[bool, str]:
    """Close the running game client.  Returns ``(ok, message)``."""
    procs = install.running_processes()
    if not procs:
        return False, "没有检测到正在运行的游戏进程"

    killed = 0
    errors: list[str] = []
    for proc in procs:
        args = ["taskkill", "/PID", str(proc.pid), "/T"]
        if force:
            args.append("/F")
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                args,
                capture_output=True,
                text=True,
                creationflags=_NO_WINDOW,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue
        if completed.returncode == 0:
            killed += 1
        else:
            errors.append((completed.stderr or completed.stdout or "").strip())

    if killed:
        log.warn(f"已结束 {killed} 个游戏进程", "启动")
        return True, f"已结束 {killed} 个游戏进程"
    return False, errors[0] if errors else "结束进程失败"


def quit_launcher(install) -> tuple[bool, str]:
    """Close a lingering official launcher."""
    from .winutil import find_processes

    procs = [p for p in find_processes({"launcher"}) if p.path and
             os.path.normcase(p.path).startswith(os.path.normcase(install.root))]
    if not procs:
        return False, "没有检测到正在运行的启动器"
    ok_count = 0
    for proc in procs:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=20,
            )
            if completed.returncode == 0:
                ok_count += 1
        except (OSError, subprocess.SubprocessError):
            continue
    if ok_count:
        return True, f"已关闭 {ok_count} 个启动器进程"
    return False, "关闭启动器失败"
