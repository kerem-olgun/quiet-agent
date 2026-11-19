"""Find out which process keeps flashing console windows on your screen.

For a limited time it records two things:

1. every top-level window that becomes visible (SetWinEventHook, EVENT_OBJECT_SHOW), so a flash
   of a few milliseconds is still caught
2. every newly started console-type process, with its chain of parent processes

Start it with pythonw, or from a shell that has no window of its own, so the watcher does not add
a window itself. The output is one JSON object per line and never leaves your machine. It contains
window titles and command lines, so read it before you share it.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from typing import Iterable, Optional

from . import shield

try:
    import psutil
except ImportError:
    psutil = None

# Programs whose windows or launches are worth recording.
WATCHED_PROCESSES = frozenset({
    "conhost.exe", "openconsole.exe", "windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe",
    "bash.exe", "sh.exe", "mintty.exe", "wsl.exe", "py.exe", "pyw.exe", "python.exe", "pythonw.exe",
    "node.exe", "npx.exe", "npm.exe", "uv.exe", "git.exe", "claude.exe", "msedge.exe", "ffmpeg.exe",
    "ffprobe.exe", "csc.exe",
})
# Window classes worth recording. #32770 is a native dialog, such as a browser's file picker.
WATCHED_CLASSES = frozenset({
    "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow", "mintty", "#32770",
})
# Process chains containing this text are skipped. A status line script that starts every second
# would otherwise bury everything else.
DEFAULT_IGNORE = ("statusline",)

_EVENT_OBJECT_SHOW = 0x8002
_GA_ROOT = 2
_TH32CS_SNAPPROCESS = 0x2
_QS_ALLINPUT = 0x04FF
_PM_REMOVE = 1


def default_log_path() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(shield.home(), f"windows-{stamp}.jsonl")


def watch(minutes: float = 60, out_path: Optional[str] = None,
          ignore: Iterable[str] = DEFAULT_IGNORE) -> str:
    """Record window and process events for `minutes`, then return the path of the log."""
    if sys.platform != "win32":
        raise OSError("quiet-agent needs Windows")
    import ctypes
    from ctypes import wintypes as W

    out_path = out_path or default_log_path()
    ignore = tuple(ignore)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", W.DWORD),
                    ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    WinEventProc = ctypes.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)
    TimerProc = ctypes.WINFUNCTYPE(None, W.HWND, W.UINT, ctypes.c_size_t, W.DWORD)

    # Handles are pointer sized; without these declarations ctypes would truncate them to 32 bits.
    kernel32.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel32.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [W.HANDLE]
    user32.GetAncestor.restype = W.HWND
    user32.GetAncestor.argtypes = [W.HWND, W.UINT]
    user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.IsWindowVisible.argtypes = [W.HWND]
    user32.SetWinEventHook.restype = W.HANDLE
    user32.SetWinEventHook.argtypes = [W.DWORD, W.DWORD, W.HMODULE, WinEventProc, W.DWORD, W.DWORD, W.DWORD]
    user32.UnhookWinEvent.argtypes = [W.HANDLE]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.SetTimer.argtypes = [W.HWND, ctypes.c_size_t, W.UINT, TimerProc]
    user32.KillTimer.argtypes = [W.HWND, ctypes.c_size_t]
    user32.MsgWaitForMultipleObjects.argtypes = [W.DWORD, ctypes.c_void_p, W.BOOL, W.DWORD, W.DWORD]
    user32.PeekMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]

    counts = {"shown": 0, "windows": 0, "processes": 0}

    def write(event: dict) -> None:
        event["t"] = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def snapshot() -> dict:
        """pid -> (executable name, parent pid) for every running process."""
        table = {}
        handle = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
            more = kernel32.Process32NextW(handle, ctypes.byref(entry))
        kernel32.CloseHandle(handle)
        return table

    state = {"table": snapshot()}
    seen = dict(state["table"])  # also remembers processes that have already exited

    def chain(pid: int, depth: int = 6) -> list:
        """The process and its ancestors, nearest first."""
        members = []
        for _ in range(depth):
            info = state["table"].get(pid) or seen.get(pid)
            if not info:
                members.append({"pid": pid, "name": "?"})
                break
            name, parent = info
            member = {"pid": pid, "name": name}
            if psutil is not None:
                try:
                    process = psutil.Process(pid)
                    member["cmd"] = " ".join(process.cmdline())[:300]
                    member["cwd"] = process.cwd()[:200]
                except Exception:
                    pass
            members.append(member)
            if parent in (0, 4) or parent == pid:
                break
            pid = parent
        return members

    def ignored(members: list) -> bool:
        return any(text in (member.get("cmd") or "") for member in members for text in ignore)

    def scan_processes() -> None:
        current = snapshot()
        for pid, (name, parent) in current.items():
            if pid not in state["table"] and name.lower() in WATCHED_PROCESSES:
                seen[pid] = (name, parent)
                members = chain(pid)
                if ignored(members):
                    continue
                counts["processes"] += 1
                write({"event": "process", "name": name, "chain": members})
        seen.update(current)
        state["table"] = current

    def on_show(hook, event, hwnd, object_id, child_id, thread, timestamp) -> None:
        if object_id != 0 or child_id != 0 or not hwnd:
            return
        if user32.GetAncestor(hwnd, _GA_ROOT) != hwnd:
            return  # not a top-level window
        counts["shown"] += 1
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info = state["table"].get(pid.value) or seen.get(pid.value)
        owner = info[0] if info else "?"
        if class_name.value in WATCHED_CLASSES or owner.lower() in WATCHED_PROCESSES:
            members = chain(pid.value)
            if ignored(members):
                return
            rect = W.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            counts["windows"] += 1
            write({"event": "window", "class": class_name.value, "title": title.value[:200],
                   "rect": [rect.left, rect.top, rect.right, rect.bottom],
                   "visible": bool(user32.IsWindowVisible(hwnd)), "chain": members})

    # Keep references to the callbacks for as long as Windows may call them.
    show_callback = WinEventProc(on_show)
    timer_callback = TimerProc(lambda hwnd, message, timer_id, tick: scan_processes())
    hook = user32.SetWinEventHook(_EVENT_OBJECT_SHOW, _EVENT_OBJECT_SHOW, None, show_callback, 0, 0, 0)
    timer = user32.SetTimer(None, 0, 100, timer_callback)
    write({"event": "started", "pid": os.getpid(), "minutes": minutes, "psutil": psutil is not None,
           "hook": bool(hook)})

    end = time.time() + minutes * 60
    message = W.MSG()
    try:
        while time.time() < end:
            user32.MsgWaitForMultipleObjects(0, None, False, 200, _QS_ALLINPUT)
            # The deadline is checked here too: on a busy machine a process scan can take longer
            # than the timer period, and then this inner loop would never run dry.
            while time.time() < end and user32.PeekMessageW(ctypes.byref(message), None, 0, 0, _PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
    finally:
        if timer:
            user32.KillTimer(None, timer)
        if hook:
            user32.UnhookWinEvent(hook)
        write({"event": "finished", "top_level_windows_shown": counts["shown"],
               "windows_recorded": counts["windows"], "processes_recorded": counts["processes"]})
    return out_path
