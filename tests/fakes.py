"""Stand-ins for the audio layer, the clock and process creation."""
import os
import shutil
import tempfile
import types
import unittest


class FakeSession:
    state = 1
    name = "fake.exe"

    def __init__(self, target, key):
        self._target = target
        self.key = key
        self.pid = int(key.split(":")[1])

    @property
    def muted(self):
        return self._target.world[self.key]["muted"]

    def set_mute(self, value):
        self._target.world[self.key]["muted"] = bool(value)
        self._target.calls.append((self.key, bool(value)))

    def peak(self):
        return self._target.world[self.key]["peak"]

    def release(self):
        self._target.released += 1


class FakeTarget:
    """Behaves like shield.Target. `world` maps a session key to its mute flag and peak level."""

    def __init__(self, world, processes=("fake.exe",), match="agent-profile"):
        self.world = {key: {"muted": False, "peak": 0.0, **state} for key, state in world.items()}
        self.processes = tuple(processes)
        self.match = match
        self.calls = []      # every set_mute call, in order
        self.handed_out = 0
        self.released = 0

    def sessions(self):
        found = [FakeSession(self, key) for key in list(self.world)]
        self.handed_out += len(found)
        return found


class FakeClock:
    """Time moves only when sleep() is called. Actions can be scheduled to run at a later moment."""

    def __init__(self, start=1000.0):
        self.now = start
        self._scheduled = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        due = sorted((item for item in self._scheduled if item[0] <= self.now), key=lambda item: item[0])
        self._scheduled = [item for item in self._scheduled if item[0] > self.now]
        for _, action in due:
            action()

    def at(self, offset, action):
        self._scheduled.append((self.now + offset, action))


class FakePopen:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, command, creationflags=0, close_fds=True):
        self.calls.append((list(command), creationflags))
        if self.fail_first and len(self.calls) == 1:
            raise OSError("breakaway from the job object is not allowed")
        return types.SimpleNamespace(pid=777)


class StateDirTestCase(unittest.TestCase):
    """Gives every test its own empty state folder."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="quiet-agent-test-")
        self._previous = os.environ.get("QUIET_AGENT_HOME")
        os.environ["QUIET_AGENT_HOME"] = self._home

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("QUIET_AGENT_HOME", None)
        else:
            os.environ["QUIET_AGENT_HOME"] = self._previous
        shutil.rmtree(self._home, ignore_errors=True)

    def state_files(self, prefix=""):
        return sorted(name for name in os.listdir(self._home) if name.startswith(prefix))
