import json
import os

from quiet_agent import audio, shield

from .fakes import FakeClock, FakePopen, FakeTarget, StateDirTestCase

BREAKAWAY = 0x01000000


def read_log():
    path = os.path.join(shield.home(), "shield.jsonl")
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


class RunTests(StateDirTestCase):
    def run_shield(self, target, clock, minutes=1):
        return shield.run(target, minutes, pid=4242, clock=clock.time, sleep=clock.sleep)

    def test_mutes_the_target_then_restores_it(self):
        target = FakeTarget({"0:10": {}})
        code, result = self.run_shield(target, FakeClock())
        self.assertEqual(code, shield.EXIT_OK)
        self.assertEqual(target.calls, [("0:10", True), ("0:10", False)])
        self.assertFalse(target.world["0:10"]["muted"])
        self.assertEqual(result["restored"], ["0:10"])
        self.assertEqual(result["finished"], "time is up")

    def test_leaves_a_session_the_user_muted_alone(self):
        target = FakeTarget({"0:10": {}, "0:11": {"muted": True}})
        self.run_shield(target, FakeClock())
        self.assertTrue(target.world["0:11"]["muted"])
        self.assertNotIn(("0:11", False), target.calls)
        self.assertFalse(target.world["0:10"]["muted"])

    def test_refuses_while_the_target_is_audible(self):
        target = FakeTarget({"0:10": {"peak": 0.3}})
        code, result = self.run_shield(target, FakeClock())
        self.assertEqual(code, shield.EXIT_LISTENING)
        self.assertEqual(result["refused"], "listening")
        self.assertEqual(target.calls, [])
        self.assertEqual(self.state_files("lease-"), [])

    def test_mutes_a_session_that_appears_mid_run(self):
        target = FakeTarget({"0:10": {}})
        clock = FakeClock()
        clock.at(10, lambda: target.world.update({"0:12": {"muted": False, "peak": 0.0}}))
        self.run_shield(target, clock)
        self.assertIn(("0:12", True), target.calls)
        self.assertFalse(target.world["0:12"]["muted"])

    def test_mutes_again_when_the_page_unmutes_itself(self):
        target = FakeTarget({"0:10": {}})
        clock = FakeClock()
        clock.at(10, lambda: target.world["0:10"].update(muted=False))
        self.run_shield(target, clock)
        self.assertEqual(target.calls.count(("0:10", True)), 2)
        self.assertFalse(target.world["0:10"]["muted"])

    def test_stop_request_ends_the_shield_early(self):
        target = FakeTarget({"0:10": {}})
        clock = FakeClock()
        started = clock.now
        clock.at(5, shield.request_stop)
        code, result = self.run_shield(target, clock, minutes=60)
        self.assertEqual(code, shield.EXIT_OK)
        self.assertEqual(result["finished"], "stop requested")
        self.assertLess(clock.now - started, 60)
        self.assertFalse(target.world["0:10"]["muted"])
        self.assertFalse(os.path.exists(shield.stop_path()))

    def test_interruption_still_restores_the_sound(self):
        target = FakeTarget({"0:10": {}})
        clock = FakeClock()

        def interrupt():
            raise KeyboardInterrupt

        clock.at(5, interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_shield(target, clock)
        self.assertFalse(target.world["0:10"]["muted"])
        finished = [event for event in read_log() if event["event"] == "finished"]
        self.assertEqual(finished[-1]["reason"], "interrupted")

    def test_lease_names_what_was_muted_and_is_removed_at_the_end(self):
        target = FakeTarget({"0:10": {}})
        clock = FakeClock()
        seen = {}

        def look():
            with open(shield.lease_path(4242), encoding="utf-8") as handle:
                seen.update(json.load(handle))

        clock.at(5, look)
        self.run_shield(target, clock)
        self.assertEqual(seen["pid"], 4242)
        self.assertEqual(seen["keys"], ["0:10"])
        self.assertEqual(seen["match"], "agent-profile")
        self.assertEqual(seen["processes"], ["fake.exe"])
        self.assertEqual(self.state_files("lease-"), [])

    def test_every_session_handle_is_released(self):
        target = FakeTarget({"0:10": {}, "0:11": {}})
        self.run_shield(target, FakeClock())
        self.assertEqual(target.released, target.handed_out)


class RecoverTests(StateDirTestCase):
    def test_restores_exactly_what_a_dead_shield_muted(self):
        target = FakeTarget({"0:10": {"muted": True}, "0:11": {"muted": True}})
        shield.write_lease(999, ["0:10"], 0, target.processes, target.match)
        result = shield.recover(lambda processes, match: target, alive=lambda pid: False)
        self.assertEqual(result["result"], "restored")
        self.assertEqual(result["restored"], ["0:10"])
        self.assertFalse(target.world["0:10"]["muted"])
        self.assertTrue(target.world["0:11"]["muted"])
        self.assertEqual(self.state_files("lease-"), [])

    def test_does_nothing_while_a_shield_is_running(self):
        target = FakeTarget({"0:10": {"muted": True}})
        shield.write_lease(999, ["0:10"], 0, target.processes, target.match)
        result = shield.recover(lambda processes, match: target, alive=lambda pid: pid == 999)
        self.assertEqual(result["result"], "shield_running")
        self.assertTrue(target.world["0:10"]["muted"])
        self.assertEqual(self.state_files("lease-"), ["lease-999.json"])

    def test_everything_also_unmutes_sessions_without_a_lease(self):
        target = FakeTarget({"0:10": {"muted": True}, "0:11": {"muted": True}})
        result = shield.recover(lambda processes, match: target, everything=True, alive=lambda pid: False)
        self.assertEqual(sorted(result["restored"]), ["0:10", "0:11"])

    def test_without_leases_there_is_nothing_to_restore(self):
        target = FakeTarget({"0:10": {"muted": True}})
        result = shield.recover(lambda processes, match: target, alive=lambda pid: False)
        self.assertEqual(result["result"], "nothing_to_restore")
        self.assertTrue(target.world["0:10"]["muted"])

    def test_survives_a_corrupt_lease_file(self):
        with open(shield.lease_path(1), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        target = FakeTarget({"0:10": {"muted": True}})
        result = shield.recover(lambda processes, match: target, alive=shield.shield_alive)
        self.assertEqual(result["result"], "nothing_to_restore")
        self.assertEqual(self.state_files("lease-"), [])

    def test_uses_the_target_recorded_in_the_lease(self):
        asked = []
        target = FakeTarget({"0:10": {"muted": True}})

        def make_target(processes, match):
            asked.append((tuple(processes), match))
            return target

        shield.write_lease(999, ["0:10"], 0, ["brave.exe"], "AgentProfile")
        shield.recover(make_target, alive=lambda pid: False)
        self.assertEqual(asked, [(("brave.exe",), "AgentProfile")])


class StartDetachedTests(StateDirTestCase):
    def start(self, target, popen, clock=None, alive=lambda pid: False):
        clock = clock or FakeClock()
        return shield.start_detached(target, 30, alive=alive, popen=popen, clock=clock.time, sleep=clock.sleep)

    def test_starts_a_detached_shield_for_the_same_target(self):
        popen = FakePopen()
        code, result = self.start(FakeTarget({"0:10": {}}), popen)
        self.assertEqual(code, shield.EXIT_OK)
        self.assertEqual(result, {"pid": 777, "mode": "breakaway"})
        command, flags = popen.calls[0]
        self.assertEqual(command[1:7], ["-m", "quiet_agent", "shield", "run", "--minutes", "30"])
        self.assertEqual(command[-2:], ["--match", "agent-profile"])
        self.assertIn("--process", command)
        self.assertTrue(flags & BREAKAWAY)

    def test_default_process_list_is_not_repeated_on_the_command_line(self):
        popen = FakePopen()
        self.start(FakeTarget({"0:10": {}}, processes=audio.DEFAULT_PROCESSES), popen)
        self.assertNotIn("--process", popen.calls[0][0])

    def test_falls_back_when_leaving_the_job_object_is_not_allowed(self):
        popen = FakePopen(fail_first=True)
        code, result = self.start(FakeTarget({"0:10": {}}), popen)
        self.assertEqual(result["mode"], "detached")
        self.assertEqual(len(popen.calls), 2)
        self.assertFalse(popen.calls[1][1] & BREAKAWAY)

    def test_does_not_start_a_second_shield(self):
        clock = FakeClock()
        target = FakeTarget({"0:10": {}})
        shield.write_lease(555, [], clock.now + 600, target.processes, target.match)
        popen = FakePopen()
        code, result = self.start(target, popen, clock=clock, alive=lambda pid: pid == 555)
        self.assertEqual(code, shield.EXIT_OK)
        self.assertEqual(result, {"shield_running": 555, "minutes_left": 10.0})
        self.assertEqual(popen.calls, [])

    def test_refuses_while_the_target_is_audible(self):
        popen = FakePopen()
        code, result = self.start(FakeTarget({"0:10": {"peak": 0.2}}), popen)
        self.assertEqual(code, shield.EXIT_LISTENING)
        self.assertEqual(popen.calls, [])

    def test_whole_browser_is_passed_on_explicitly(self):
        popen = FakePopen()
        self.start(FakeTarget({"0:10": {}}, match=None), popen)
        self.assertEqual(popen.calls[0][0][-1], "--whole-browser")
        self.assertNotIn("--match", popen.calls[0][0])


class LeaseTests(StateDirTestCase):
    def test_shield_alive_rejects_values_that_are_not_a_pid(self):
        for value in (None, -1, 0, "12", 3.5):
            self.assertFalse(shield.shield_alive(value))

    def test_shield_alive_rejects_a_pid_that_does_not_exist(self):
        self.assertFalse(shield.shield_alive(2 ** 22 + 12345))
