import unittest

from quiet_agent.audio import is_target

BROWSERS = ("brave.exe", "chrome.exe")
AUDIO_SERVICE = "brave.exe --type=utility --utility-sub-type=audio.mojom.AudioService"
AGENT_MAIN = "brave.exe --user-data-dir=C:/profiles/AgentProfile"
OWN_MAIN = "brave.exe"


class IsTargetTests(unittest.TestCase):
    def test_other_programs_are_never_a_target(self):
        self.assertFalse(is_target("spotify.exe", [], BROWSERS, None))
        self.assertFalse(is_target("spotify.exe", ["spotify.exe AgentProfile"], BROWSERS, "AgentProfile"))

    def test_without_match_every_instance_of_the_browser_is_a_target(self):
        self.assertTrue(is_target("brave.exe", [], BROWSERS, None))

    def test_process_name_is_compared_without_case(self):
        self.assertTrue(is_target("Brave.EXE", [], BROWSERS, None))

    def test_instance_is_found_through_the_parent_command_line(self):
        self.assertTrue(is_target("brave.exe", [AUDIO_SERVICE, AGENT_MAIN], BROWSERS, "AgentProfile"))

    def test_match_is_compared_without_case(self):
        self.assertTrue(is_target("brave.exe", [AUDIO_SERVICE, AGENT_MAIN], BROWSERS, "agentprofile"))

    def test_the_users_own_instance_is_not_a_target(self):
        self.assertFalse(is_target("brave.exe", [AUDIO_SERVICE, OWN_MAIN], BROWSERS, "AgentProfile"))

    def test_unreadable_command_lines_are_not_a_target(self):
        self.assertFalse(is_target("brave.exe", [], BROWSERS, "AgentProfile"))


if __name__ == "__main__":
    unittest.main()
