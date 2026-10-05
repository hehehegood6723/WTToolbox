"""Windows-specific helpers built on ctypes / winreg / shell32.

Everything here is deliberately defensive: a failure returns a sentinel instead
of raising, because none of these are worth crashing a GUI over.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import stat
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable

__all__ = [
    "human_size",
    "human_count",
    "walk_size",
    "delete_tree",
    "move_to_trash",
    "reveal_in_explorer",
    "open_path",
    "open_url",
    "is_admin",
    "run_as_admin",
    "set_startup_enabled",
    "get_startup_command",
    "find_processes",
    "is_process_running",
    "file_version",
    "is_windows",
]

is_windows = sys.platform.startswith("win")

# --------------------------------------------------------------------------- #
#  Formatting
# --------------------------------------------------------------------------- #
def human_size(num: float, *, suffix: str = "B") -> str:
    """1536 -> ``1.50 KB`` (binary units, two decimals)."""
    try:
        value = float(num)
    except (TypeError, ValueError):
        return f"0 {suffix}"
    sign = "-" if value < 0 else ""
    value = abs(value)
    for unit in ("", "K", "M", "G", "T", "P"):
        if value < 1024 or unit == "P":
            if unit == "":
                return f"{sign}{int(value)} {suffix}"
            return f"{sign}{value:.2f} {unit}{suffix}"
        value /= 1024.0
    return f"{sign}{value:.2f} P{suffix}"


def human_count(n: int) -> str:
    return f"{n:,}"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


# --------------------------------------------------------------------------- #
#  Filesystem
# --------------------------------------------------------------------------- #
ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], bool]


def walk_size(
    path: str,
    *,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
    _state: dict | None = None,
) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for a file or directory.

    ``on_progress(bytes_so_far, current_path)`` is called periodically so a GUI
    can show live progress on multi-gigabyte folders.
    """
    state = _state if _state is not None else {"bytes": 0, "files": 0, "tick": 0.0}
    if not path or not os.path.exists(path):
        return state["bytes"], state["files"]

    if os.path.isfile(path):
        try:
            state["bytes"] += os.path.getsize(path)
            state["files"] += 1
        except OSError:
            pass
        return state["bytes"], state["files"]

    stack = [path]
    while stack:
        if should_cancel and should_cancel():
            break
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if should_cancel and should_cancel():
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    state["bytes"] += entry.stat(follow_symlinks=False).st_size
                    state["files"] += 1
            except OSError:
                continue
        if on_progress:
            now = time.monotonic()
            if now - state["tick"] > 0.08:
                state["tick"] = now
                on_progress(state["bytes"], current)
    if on_progress:
        on_progress(state["bytes"], path)
    return state["bytes"], state["files"]


def _on_rm_error(func, path, exc_info):
    """Clear the read-only bit and retry - shader caches are often R/O."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def delete_tree(path: str, *, should_cancel: CancelFn | None = None) -> tuple[bool, str]:
    """Permanently delete a file or directory tree.  Returns ``(ok, error)``."""
    if should_cancel and should_cancel():
        return False, "已取消"
    if not os.path.exists(path):
        return True, ""
    if os.path.isfile(path):
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True, ""
        except OSError as exc:
            return False, str(exc)

    errors: list[str] = []
    for root, dirs, files in os.walk(path, topdown=False):
        if should_cancel and should_cancel():
            return False, "已取消"
        for name in files:
            target = os.path.join(root, name)
            try:
                os.chmod(target, stat.S_IWRITE)
                os.remove(target)
            except OSError as exc:
                errors.append(f"{name}: {exc}")
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        shutil.rmtree(path, onerror=_on_rm_error)
    except OSError as exc:
        errors.append(str(exc))
    if os.path.exists(path):
        return False, (errors[0] if errors else "部分文件正被占用，无法删除")
    return True, ""


def move_to_trash(path: str, trash_root: str) -> tuple[bool, str]:
    """Move a file/folder into WTToolbox's own recoverable trash folder.

    A ``.tk-trash.json`` sidecar records the original location so
    :mod:`wttoolbox.core.trash` can offer a one-click restore.
    """
    if not os.path.exists(path):
        return False, "路径不存在"
    path = os.path.normpath(os.path.abspath(path))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(path.rstrip("\\/")) or "item"
    dest_dir = os.path.join(trash_root, stamp)
    # Keep same-second deletions in separate buckets so nothing collides.
    suffix = 1
    while os.path.exists(os.path.join(dest_dir, ".tk-trash.json")):
        dest_dir = os.path.join(trash_root, f"{stamp}-{suffix}")
        suffix += 1
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, base)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{base} ({counter})")
        counter += 1

    origin = os.path.dirname(path)
    try:
        with open(os.path.join(dest_dir, ".tk-trash.json"), "w", encoding="utf-8") as fh:
            import json

            json.dump(
                {"origin": origin, "created_at": time.time(), "source": path},
                fh,
                ensure_ascii=False,
                indent=1,
            )
    except OSError:
        pass

    try:
        shutil.move(path, dest)
        return True, dest
    except OSError as exc:
        return False, str(exc)


def reveal_in_explorer(path: str) -> bool:
    """Open Explorer with the item selected (or the folder opened)."""
    if not is_windows or not path:
        return False
    path = os.path.normpath(path)
    try:
        if os.path.isdir(path):
            os.startfile(path)  # noqa: S606 - intended shell open
        elif os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", path])
        else:
            parent = os.path.dirname(path)
            if parent and os.path.isdir(parent):
                os.startfile(parent)
            else:
                return False
        return True
    except Exception:
        return False


def open_path(path: str) -> bool:
    """Open a file with its default handler."""
    if not path or not os.path.exists(path):
        return False
    try:
        os.startfile(path)  # noqa: S606 - intended shell open
        return True
    except Exception:
        return False


def open_url(url: str) -> bool:
    """Open a URL with the system default handler (Qt-free fallback chain)."""
    if not url:
        return False

    # 1. ShellExecute through os.startfile
    try:
        os.startfile(url)  # noqa: S606 - intended shell open
        return True
    except Exception:
        pass

    # 2. The shell's protocol handler directly
    if is_windows:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv
                ["rundll32", "url.dll,FileProtocolHandler", url],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            if completed.returncode == 0:
                return True
        except Exception:
            pass

    # 3. Python's webbrowser module
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:
        return False


def open_url_verbose(url: str) -> tuple[bool, str]:
    """Like :func:`open_url` but reports which mechanism failed."""
    if not url:
        return False, "链接为空"
    try:
        os.startfile(url)  # noqa: S606 - intended shell open
        return True, "os.startfile"
    except Exception as exc:
        first = f"{type(exc).__name__}: {exc}"
    if is_windows:
        try:
            completed = subprocess.run(
                ["rundll32", "url.dll,FileProtocolHandler", url],
                capture_output=True, text=True, timeout=20,
                creationflags=0x08000000,
            )
            if completed.returncode == 0:
                return True, "rundll32"
            first += f" / rundll32 rc={completed.returncode}"
        except Exception as exc:
            first += f" / rundll32 {type(exc).__name__}"
    try:
        import webbrowser

        if webbrowser.open(url):
            return True, "webbrowser"
    except Exception as exc:
        first += f" / webbrowser {type(exc).__name__}"
    return False, first


# --------------------------------------------------------------------------- #
#  Elevation
# --------------------------------------------------------------------------- #
def is_admin() -> bool:
    if not is_windows:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin(exe: str, params: str = "", cwd: str | None = None) -> tuple[bool, str]:
    """ShellExecute with the ``runas`` verb.  Returns ``(started, error)``."""
    if not is_windows:
        return False, "仅支持 Windows"
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params or None, cwd or None, 1
        )
    except Exception as exc:
        return False, str(exc)
    # ShellExecuteW returns >32 on success.
    if int(result) > 32:
        return True, ""
    return False, f"ShellExecute 返回 {int(result)}（用户可能取消了 UAC 授权）"


# --------------------------------------------------------------------------- #
#  Autostart
# --------------------------------------------------------------------------- #
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "WTToolbox"


def set_startup_enabled(enabled: bool, command: str | None = None) -> tuple[bool, str]:
    """Add/remove the per-user autostart entry.  Returns ``(ok, message)``."""
    if not is_windows:
        return False, "仅支持 Windows"
    try:
        import winreg
    except ImportError:
        return False, "无法访问注册表"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            if enabled:
                if not command:
                    return False, "缺少启动命令"
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, command)
                return True, "已设置为开机启动"
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
                return True, "已取消开机启动"
            except FileNotFoundError:
                return True, "开机启动本来就是关闭的"
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def get_startup_command() -> str | None:
    if not is_windows:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_VALUE)
            return str(value)
    except OSError:
        return None


def startup_command_for_exe(exe: str, extra_args: str = "--minimized") -> str:
    return f'"{exe}" {extra_args}'.strip()


# --------------------------------------------------------------------------- #
#  Processes
# --------------------------------------------------------------------------- #
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH_LONG = 4096


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    path: str

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0].lower()


def find_processes(names: Iterable[str] | None = None) -> list[ProcessInfo]:
    """Enumerate running processes, optionally filtered by exe name."""
    if not is_windows:
        return []
    wanted = {n.lower() for n in names} if names else None
    results: list[ProcessInfo] = []

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1 or snapshot == 0:
        return results

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return results
        while True:
            name = entry.szExeFile
            stem = os.path.splitext(name)[0].lower()
            if wanted is None or stem in wanted or name.lower() in wanted:
                results.append(
                    ProcessInfo(pid=int(entry.th32ProcessID), name=name, path=_process_path(int(entry.th32ProcessID)))
                )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return results


def _process_path(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(MAX_PATH_LONG)
        buf = ctypes.create_unicode_buffer(MAX_PATH_LONG)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def is_process_running(names: Iterable[str]) -> bool:
    return bool(find_processes(names))


# --------------------------------------------------------------------------- #
#  Version resources
# --------------------------------------------------------------------------- #
def file_version(path: str) -> str | None:
    """Read an executable's FILEVERSION, e.g. ``2.59.0.44``."""
    if not is_windows or not path or not os.path.isfile(path):
        return None
    try:
        version_dll = ctypes.windll.version
        size = version_dll.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(path, 0, size, buf):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_dll.VerQueryValueW(buf, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        if not pointer:
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        if info.dwSignature != 0xFEEF04BD:
            return None
        parts = (
            info.dwFileVersionMS >> 16,
            info.dwFileVersionMS & 0xFFFF,
            info.dwFileVersionLS >> 16,
            info.dwFileVersionLS & 0xFFFF,
        )
        return ".".join(str(p) for p in parts)
    except Exception:
        return None


def app_executable() -> str:
    """The exe to re-launch (bundle) or the python entry script (source)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.executable
