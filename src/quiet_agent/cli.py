"""Command line for quiet-agent. Every command prints one JSON object, so an agent can read it."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__, audio, shield

NO_TARGET_TEXT = (
    "Refusing to mute every window of the browser. Name the agent's own browser instance with "
    "--match (for example the folder name of its --user-data-dir), or pass --whole-browser if "
    "muting everything is really what you want."
)

_api = None


def _make_target(processes, match):
    """Real target on Windows. Tests replace this function."""
    global _api
    if _api is None:
        _api = audio.CoreAudio()
    return shield.Target(_api, processes, match)


def _processes(args) -> tuple:
    return tuple(args.process) if getattr(args, "process", None) else audio.DEFAULT_PROCESSES


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _needs_target(args) -> bool:
    return not args.match and not args.whole_browser


def _status(args) -> int:
    target = _make_target(_processes(args), args.match)
    rows = []
    for session in target.sessions():
        try:
            rows.append({"key": session.key, "pid": session.pid, "process": session.name,
                         "state": session.state, "muted": session.muted})
        finally:
            session.release()
    running = [lease.get("pid") for lease in shield.read_leases() if shield.shield_alive(lease.get("pid"))]
    _emit({"sessions": rows, "peak": round(shield.sample_peak(target, 1.5), 4), "shields_running": running})
    return shield.EXIT_OK


def _shield_start(args) -> int:
    if _needs_target(args):
        shield.log({"event": "refused", "reason": "no browser instance was named"})
        _emit({"refused": "no_target", "help": NO_TARGET_TEXT})
        return shield.EXIT_NO_TARGET
    code, result = shield.start_detached(_make_target(_processes(args), args.match), args.minutes)
    _emit(result)
    return code


def _shield_run(args) -> int:
    if _needs_target(args):
        shield.log({"event": "refused", "reason": "no browser instance was named"})
        _emit({"refused": "no_target", "help": NO_TARGET_TEXT})
        return shield.EXIT_NO_TARGET
    code, result = shield.run(_make_target(_processes(args), args.match), args.minutes)
    _emit(result)
    return code


def _shield_stop(args) -> int:
    shield.request_stop()
    _emit({"stop": "requested"})
    return shield.EXIT_OK


def _recover(args) -> int:
    result = shield.recover(_make_target, everything=args.all, processes=_processes(args), match=args.match)
    _emit(result)
    return shield.EXIT_OK


def _watch_windows(args) -> int:
    from . import windows
    path = windows.watch(args.minutes, args.out, tuple(args.ignore) if args.ignore else windows.DEFAULT_IGNORE)
    _emit({"log": path})
    return shield.EXIT_OK


def _add_target_options(parser, whole_browser: bool) -> None:
    parser.add_argument("--match", metavar="TEXT",
                        help="text found in the command line of the agent's browser instance, "
                             "usually the folder name of its --user-data-dir")
    parser.add_argument("--process", action="append", metavar="NAME",
                        help="executable name to guard; repeat for several "
                             "(default: Chromium-based browsers)")
    if whole_browser:
        parser.add_argument("--whole-browser", action="store_true",
                            help="guard every instance of the browser instead of one")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quiet-agent",
        description="Keep your speakers and your screen yours while an AI agent drives a browser on Windows.")
    parser.add_argument("--version", action="version", version=f"quiet-agent {__version__}")
    commands = parser.add_subparsers(dest="command")

    status = commands.add_parser("status", help="list the audio sessions that would be guarded; changes nothing")
    _add_target_options(status, whole_browser=False)
    status.set_defaults(handler=_status)

    shield_parser = commands.add_parser("shield", help="mute the agent's browser instance for a limited time")
    shield_commands = shield_parser.add_subparsers(dest="shield_command")

    start = shield_commands.add_parser(
        "start", help="start a shield that survives the end of the agent session (use this one)")
    _add_target_options(start, whole_browser=True)
    start.add_argument("--minutes", type=float, default=20, help="how long to guard (default: 20)")
    start.set_defaults(handler=_shield_start)

    run = shield_commands.add_parser("run", help="run the shield in the foreground")
    _add_target_options(run, whole_browser=True)
    run.add_argument("--minutes", type=float, default=20, help="how long to guard (default: 20)")
    run.set_defaults(handler=_shield_run)

    stop = shield_commands.add_parser("stop", help="end the running shield early and restore the sound")
    stop.set_defaults(handler=_shield_stop)

    recover = commands.add_parser("recover", help="restore sound left muted by a shield that was killed")
    _add_target_options(recover, whole_browser=False)
    recover.add_argument("--all", action="store_true",
                         help="also unmute every muted session of the target, for when a lease is missing")
    recover.set_defaults(handler=_recover)

    watch = commands.add_parser("watch-windows", help="record which process flashes console windows")
    watch.add_argument("--minutes", type=float, default=60, help="how long to record (default: 60)")
    watch.add_argument("--out", metavar="FILE", help="log file (default: a new file in the state folder)")
    watch.add_argument("--ignore", action="append", metavar="TEXT",
                       help="skip process chains whose command line contains this text; repeatable")
    watch.set_defaults(handler=_watch_windows)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return shield.EXIT_OK
    try:
        return handler(args)
    except OSError as error:
        print(f"quiet-agent: {error}", file=sys.stderr)
        return shield.EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
