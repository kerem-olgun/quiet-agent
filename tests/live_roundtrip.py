"""Live check of the real Core Audio path. It plays digital silence, so nothing is audible.

What it does:

1. starts a helper process that loops a silent WAV file, which gives that process an audio session
2. runs a short shield against that helper only
3. checks that the session is found, is muted while the shield runs, and is unmuted afterwards

It never touches a browser. Run it on Windows from the repository root:

    python tests/live_roundtrip.py

The unit tests (python -m unittest) do not run this file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from quiet_agent import audio, shield  # noqa: E402

HELPER = (
    "import sys, time, winsound\n"
    "winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)\n"
    "time.sleep(float(sys.argv[3]))\n"
)
NO_WINDOW = 0x08000000


def write_silence(path: str, seconds: float = 1.0, rate: int = 44100) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(2 * int(rate * seconds)))


def snapshot(target) -> dict:
    """Session key -> muted, for every session of the target right now."""
    rows = {}
    for session in target.sessions():
        try:
            rows[session.key] = session.muted
        finally:
            session.release()
    return rows


def wait_for(predicate, seconds: float, step: float = 0.2):
    end = time.time() + seconds
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def main() -> int:
    if sys.platform != "win32":
        print("SKIP  this check needs Windows")
        return 0

    marker = f"quiet-agent-live-{os.getpid()}"
    work = tempfile.mkdtemp(prefix="quiet-agent-live-")
    wav = os.path.join(work, "silence.wav")
    write_silence(wav)
    state = os.path.join(work, "state")
    os.environ["QUIET_AGENT_HOME"] = state
    child_env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    process_name = os.path.basename(sys.executable)

    results = []

    def check(label: str, passed: bool, detail: str = "") -> None:
        results.append(passed)
        print(("PASS  " if passed else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))

    helper = subprocess.Popen([sys.executable, "-c", HELPER, wav, marker, "60"], creationflags=NO_WINDOW)
    try:
        api = audio.CoreAudio()
        target = shield.Target(api, (process_name,), marker)

        found = wait_for(lambda: snapshot(target), 10)
        check("the helper's audio session is found", bool(found), str(found))
        if not found:
            return 1
        check("it starts unmuted", not any(found.values()))
        browsers = shield.Target(api, audio.DEFAULT_PROCESSES, marker)
        check("no browser session matches the test marker", snapshot(browsers) == {})

        run = subprocess.Popen(
            [sys.executable, "-m", "quiet_agent", "shield", "run", "--process", process_name,
             "--match", marker, "--minutes", "0.1"],
            env=child_env, stdout=subprocess.PIPE, text=True, creationflags=NO_WINDOW)

        def all_muted():
            rows = snapshot(target)
            return bool(rows) and all(rows.values())

        check("the shield mutes it", bool(wait_for(all_muted, 12)))

        output, _ = run.communicate(timeout=40)
        check("the shield exits cleanly", run.returncode == 0, f"exit code {run.returncode}")
        report = json.loads(output.strip().splitlines()[-1]) if output.strip() else {}
        check("the shield reports what it restored", sorted(report.get("restored", [])) == sorted(found),
              json.dumps(report))

        after = snapshot(target)
        check("the session is unmuted again", bool(after) and not any(after.values()), str(after))
        check("no lease is left behind", shield.read_leases() == [])
    finally:
        helper.kill()
        helper.wait(timeout=10)
        shutil.rmtree(work, ignore_errors=True)

    print(f"{sum(results)} of {len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
