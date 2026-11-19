"""The shield loop, its lease file, and recovery.

Each rule below exists because the opposite went wrong once:

* A shield never mutes a whole browser family unless told to. It guards one named instance.
* A shield does not start while that instance is audibly playing. Someone may be listening.
* A shield unmutes only the sessions it muted itself.
* A shield outlives the agent session that started it, and it leaves a lease on disk, so a
  shield that was killed can still be cleaned up by `recover`.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from typing import Callable, Iterable, List, Optional, Tuple

from . import audio

LISTENING_PEAK = 0.01   # above this the guarded instance is audibly playing
POLL_SECONDS = 0.1      # a session that appears mid-run is muted within one poll

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LISTENING = 2      # refused: the guarded instance is playing sound right now
EXIT_NO_TARGET = 3      # refused: no instance was named

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


# --- state on disk ------------------------------------------------------------------------------
def home() -> str:
    """Folder for the log, the stop flag and lease files. QUIET_AGENT_HOME overrides it."""
    base = os.environ.get("QUIET_AGENT_HOME")
    if not base:
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "quiet-agent")
    os.makedirs(base, exist_ok=True)
    return base


def _path(name: str) -> str:
    return os.path.join(home(), name)


def log(event: dict) -> dict:
    record = dict(event)
    record["t"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with open(_path("shield.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def stop_path() -> str:
    return _path("stop")


def lease_path(pid: int) -> str:
    return _path(f"lease-{pid}.json")


def write_lease(pid: int, keys: Iterable[str], until: float, processes: Iterable[str],
                match: Optional[str]) -> None:
    data = {"pid": pid, "keys": sorted(keys), "until": until, "processes": list(processes), "match": match}
    with open(lease_path(pid), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def read_leases() -> List[dict]:
    leases = []
    for name in sorted(os.listdir(home())):
        if not (name.startswith("lease-") and name.endswith(".json")):
            continue
        path = _path(name)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["path"] = path
        leases.append(data)
    return leases


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def shield_alive(pid) -> bool:
    """True when pid is a running quiet-agent process, not some other program that reused the pid."""
    if not isinstance(pid, int) or pid <= 0 or audio.psutil is None:
        return False
    try:
        process = audio.psutil.Process(pid)
        if not process.is_running():
            return False
        return any("quiet_agent" in part or "quiet-agent" in part for part in process.cmdline())
    except Exception:
        return False


# --- what a shield guards -----------------------------------------------------------------------
class Target:
    """The sessions a shield guards: chosen processes, narrowed to one browser instance."""

    def __init__(self, api, processes: Iterable[str], match: Optional[str]):
        self.api = api
        self.processes = tuple(processes)
        self.match = match

    def sessions(self):
        return self.api.sessions(self.processes, self.match)


def sample_peak(target, seconds: float, *, clock: Callable[[], float] = time.time,
                sleep: Callable[[float], None] = time.sleep) -> float:
    """Loudest peak seen on the target's sessions over the next `seconds`."""
    highest = 0.0
    end = clock() + seconds
    while clock() < end:
        for session in target.sessions():
            try:
                highest = max(highest, session.peak())
            finally:
                session.release()
        sleep(POLL_SECONDS)
    return highest


def _unmute(target, keys) -> List[str]:
    """Unmute the target's muted sessions whose key is in `keys` (None means every key)."""
    restored = []
    for session in target.sessions():
        try:
            if (keys is None or session.key in keys) and session.muted:
                session.set_mute(False)
                restored.append(session.key)
        finally:
            session.release()
    return restored


def _still_muted(target, keys) -> List[str]:
    muted = []
    for session in target.sessions():
        try:
            if session.key in keys and session.muted:
                muted.append(session.key)
        finally:
            session.release()
    return muted


# --- commands -----------------------------------------------------------------------------------
def run(target, minutes: float, *, pid: Optional[int] = None, clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep) -> Tuple[int, dict]:
    """Keep the target muted until the time is up or a stop is requested, then restore it."""
    pid = pid or os.getpid()
    _remove(stop_path())

    peak = sample_peak(target, 2.0, clock=clock, sleep=sleep)
    if peak > LISTENING_PEAK:
        log({"event": "refused", "reason": "the target is audibly playing; someone may be listening",
             "peak": round(peak, 4)})
        return EXIT_LISTENING, {"refused": "listening", "peak": round(peak, 4)}

    ours = set()
    until = clock() + minutes * 60
    write_lease(pid, ours, until, target.processes, target.match)
    log({"event": "started", "pid": pid, "minutes": minutes, "processes": list(target.processes),
         "match": target.match})

    reason = "interrupted"
    try:
        while clock() < until and not os.path.exists(stop_path()):
            for session in target.sessions():
                try:
                    if not session.muted:
                        session.set_mute(True)
                        if session.key not in ours:
                            ours.add(session.key)
                            write_lease(pid, ours, until, target.processes, target.match)
                            log({"event": "muted", "key": session.key, "state": session.state})
                finally:
                    session.release()
            sleep(POLL_SECONDS)
        reason = "stop requested" if os.path.exists(stop_path()) else "time is up"
    finally:
        restored = _unmute(target, ours)
        left_muted = _still_muted(target, ours)
        log({"event": "finished", "reason": reason, "restored": restored, "still_muted": left_muted})
        if left_muted:
            log({"event": "warning", "text": "sessions this shield muted are still muted; run `quiet-agent recover`"})
        else:
            _remove(lease_path(pid))
        _remove(stop_path())
    return EXIT_OK, {"finished": reason, "restored": restored, "still_muted": left_muted}


def _windowless_python() -> str:
    """pythonw.exe next to the running interpreter, so the detached shield has no console window."""
    executable = sys.executable
    if executable.lower().endswith("python.exe"):
        candidate = executable[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(candidate):
            return candidate
    return executable


def start_detached(target, minutes: float, *, alive: Callable[[object], bool] = shield_alive,
                   popen=subprocess.Popen, clock: Callable[[], float] = time.time,
                   sleep: Callable[[float], None] = time.sleep) -> Tuple[int, dict]:
    """Start a shield that keeps running, and still restores the sound, after the caller exits."""
    for lease in read_leases():
        if alive(lease.get("pid")):
            left = max(0.0, (lease.get("until", 0) - clock()) / 60)
            log({"event": "start_skipped", "reason": "a shield is already running", "pid": lease.get("pid"),
                 "minutes_left": round(left, 1)})
            return EXIT_OK, {"shield_running": lease.get("pid"), "minutes_left": round(left, 1)}

    peak = sample_peak(target, 2.0, clock=clock, sleep=sleep)
    if peak > LISTENING_PEAK:
        log({"event": "refused", "reason": "the target is audibly playing; someone may be listening",
             "peak": round(peak, 4)})
        return EXIT_LISTENING, {"refused": "listening", "peak": round(peak, 4)}

    command = [_windowless_python(), "-m", "quiet_agent", "shield", "run", "--minutes", str(minutes)]
    if tuple(target.processes) != tuple(audio.DEFAULT_PROCESSES):
        for name in target.processes:
            command += ["--process", name]
    command += ["--match", target.match] if target.match else ["--whole-browser"]

    flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    try:
        # Leave the job object of the calling terminal or agent, which is killed with its session.
        child = popen(command, creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
        mode = "breakaway"
    except OSError:
        child = popen(command, creationflags=flags, close_fds=True)
        mode = "detached"
    log({"event": "start", "pid": child.pid, "minutes": minutes, "mode": mode})
    return EXIT_OK, {"pid": child.pid, "mode": mode}


def request_stop() -> dict:
    with open(stop_path(), "w", encoding="utf-8") as handle:
        handle.write("stop")
    return log({"event": "stop_requested"})


def recover(make_target: Callable[[Iterable[str], Optional[str]], object], *, everything: bool = False,
            processes: Iterable[str] = audio.DEFAULT_PROCESSES, match: Optional[str] = None,
            alive: Callable[[object], bool] = shield_alive) -> dict:
    """Restore sound after a shield died without cleaning up.

    Unmutes exactly the sessions recorded in the leases of dead shields. With `everything`, also
    unmutes every muted session of the given target, for the case where a lease is missing.
    """
    leases = read_leases()
    running = [lease.get("pid") for lease in leases if alive(lease.get("pid"))]
    if running:
        return log({"event": "recover", "result": "shield_running", "pids": running})

    restored: List[str] = []
    for lease in leases:
        keys = set(lease.get("keys") or [])
        if keys:
            restored += _unmute(make_target(lease.get("processes") or processes, lease.get("match")), keys)
    if everything:
        restored += _unmute(make_target(processes, match), None)
    for lease in leases:
        _remove(lease["path"])
    return log({"event": "recover", "result": "restored" if restored else "nothing_to_restore",
                "restored": restored, "dead_leases": [lease.get("pid") for lease in leases]})
