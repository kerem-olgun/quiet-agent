import contextlib
import io
import json
from unittest import mock

from quiet_agent import audio, cli, shield

from .fakes import FakeTarget, StateDirTestCase


class CliTests(StateDirTestCase):
    def call(self, *argv):
        """Run the command line with a fake audio layer. Returns (exit code, parsed stdout, target)."""
        made = {}

        def make_target(processes, match):
            made["target"] = FakeTarget({"0:10": {}}, processes=processes, match=match)
            return made["target"]

        out = io.StringIO()
        with mock.patch.object(cli, "_make_target", make_target), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        text = out.getvalue().strip()
        return code, (json.loads(text) if text.startswith("{") else text), made.get("target")

    def test_shield_start_refuses_without_a_named_instance(self):
        with mock.patch.object(shield, "start_detached") as start:
            code, output, target = self.call("shield", "start")
        self.assertEqual(code, shield.EXIT_NO_TARGET)
        self.assertEqual(output["refused"], "no_target")
        start.assert_not_called()
        self.assertIsNone(target)

    def test_shield_run_refuses_without_a_named_instance(self):
        with mock.patch.object(shield, "run") as run:
            code, output, _ = self.call("shield", "run", "--minutes", "1")
        self.assertEqual(code, shield.EXIT_NO_TARGET)
        run.assert_not_called()

    def test_shield_start_passes_the_target_and_duration(self):
        with mock.patch.object(shield, "start_detached", return_value=(0, {"pid": 1, "mode": "breakaway"})) as start:
            code, output, target = self.call("shield", "start", "--match", "AgentProfile", "--minutes", "45")
        self.assertEqual(code, 0)
        self.assertEqual(output, {"pid": 1, "mode": "breakaway"})
        self.assertEqual(target.match, "AgentProfile")
        self.assertEqual(target.processes, audio.DEFAULT_PROCESSES)
        self.assertEqual(start.call_args[0][1], 45.0)

    def test_whole_browser_is_an_explicit_opt_in(self):
        with mock.patch.object(shield, "run", return_value=(0, {})) as run:
            code, _, target = self.call("shield", "run", "--whole-browser", "--process", "chrome.exe")
        self.assertEqual(code, 0)
        self.assertIsNone(target.match)
        self.assertEqual(target.processes, ("chrome.exe",))
        run.assert_called_once()

    def test_listening_refusal_becomes_exit_code_two(self):
        with mock.patch.object(shield, "start_detached", return_value=(2, {"refused": "listening", "peak": 0.2})):
            code, output, _ = self.call("shield", "start", "--match", "AgentProfile")
        self.assertEqual(code, shield.EXIT_LISTENING)
        self.assertEqual(output["refused"], "listening")

    def test_status_lists_sessions_and_changes_nothing(self):
        with mock.patch.object(shield, "sample_peak", return_value=0.0):
            code, output, target = self.call("status", "--match", "AgentProfile")
        self.assertEqual(code, 0)
        self.assertEqual(output["sessions"], [
            {"key": "0:10", "pid": 10, "process": "fake.exe", "state": 1, "muted": False}])
        self.assertEqual(output["shields_running"], [])
        self.assertEqual(target.calls, [])

    def test_shield_stop_writes_the_stop_flag(self):
        code, output, _ = self.call("shield", "stop")
        self.assertEqual(code, 0)
        self.assertEqual(self.state_files("stop"), ["stop"])

    def test_recover_all_is_passed_on(self):
        with mock.patch.object(shield, "recover", return_value={"result": "nothing_to_restore"}) as recover:
            code, _, _ = self.call("recover", "--all", "--match", "AgentProfile")
        self.assertEqual(code, 0)
        self.assertTrue(recover.call_args[1]["everything"])
        self.assertEqual(recover.call_args[1]["match"], "AgentProfile")

    def test_platform_errors_become_exit_code_one(self):
        def unavailable(processes, match):
            raise OSError("quiet-agent needs Windows")

        err = io.StringIO()
        with mock.patch.object(cli, "_make_target", unavailable), contextlib.redirect_stderr(err):
            code = cli.main(["status"])
        self.assertEqual(code, shield.EXIT_ERROR)
        self.assertIn("needs Windows", err.getvalue())

    def test_no_command_prints_help(self):
        code, output, _ = self.call()
        self.assertEqual(code, 0)
        self.assertIn("usage: quiet-agent", output)

    def test_version_flag(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(out):
            cli.main(["--version"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("quiet-agent", out.getvalue())
