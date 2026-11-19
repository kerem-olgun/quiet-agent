# quiet-agent

Keep your speakers and your screen yours while an AI agent drives a browser on Windows.

[Türkçe](README.tr.md)

An agent that browses for you will sooner or later open a page that plays sound, and something it starts will flash a console window over your work. `quiet-agent` is two small tools and a rule sheet for that situation:

- `quiet-agent shield` mutes the agent's browser, and only the agent's browser, for a limited time. Then it gives the sound back.
- `quiet-agent watch-windows` records which process showed that window.
- [`docs/agent-rules.md`](docs/agent-rules.md) is a short rule sheet to paste into your agent's instructions.

It runs on Windows and talks to the Core Audio session API through `ctypes`, so there is nothing to compile.

## Why not just mute the browser?

Because you are using the browser too. I ran into three problems, in this order.

**A browser instance is one audio session.** Chromium plays every tab through a single audio service process. From the outside, Windows sees one session for the whole browser, so one tab or one window cannot be muted on its own. My first version muted "the browser" and took my own video with it. The fix is to give the agent its own browser instance with its own `--user-data-dir`. That instance has its own audio process, and that one can be muted alone.

**Muting inside the page does not hold.** A script that mutes `<video>` elements is gone after the next navigation, and autoplay previews start before it runs. A mute on the Windows audio session does not depend on what the page does.

**The mute has to be given back.** The process that muted the browser was killed together with the agent session, so it never ran its cleanup. The browser stayed silent for about six hours before I noticed. Now the shield runs detached from the agent, writes down what it muted, and has a `recover` command for the case where it is killed anyway.

## Install

```
pip install git+https://github.com/kerem-olgun/quiet-agent
```

Windows 10 or 11, Python 3.10 or newer. The only dependency is `psutil`.

## Give the agent its own browser instance (once)

Make a shortcut that starts your browser with a separate profile folder:

```
"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --user-data-dir="C:\AgentBrowser"
```

Install the agent's browser extension in that window and sign in to the sites the agent needs. From then on the agent works there and your own browser is left alone.

## Use

```
quiet-agent status --match AgentBrowser
quiet-agent shield start --match AgentBrowser --minutes 30
   ... the agent works ...
quiet-agent shield stop
```

`--match` is any text that appears in the command line of the agent's instance. The profile folder name is the obvious choice.

| Command | What it does |
|---|---|
| `status` | Lists the matching audio sessions and their current peak level. Changes nothing. |
| `shield start` | Starts a shield that keeps running after the calling terminal or agent session ends. This is the one to use. |
| `shield run` | Runs the same shield in the foreground. |
| `shield stop` | Ends the running shield early and restores the sound. |
| `recover` | Restores sessions left muted by a shield that was killed. Add `--all` to unmute every muted session of the target when a lease file is missing. |
| `watch-windows` | Records for a while which processes showed console windows or dialogs. |

Every command prints one JSON object, so an agent can read the result. This is `status` on my machine while a shield was running on the agent's instance:

```json
{"sessions": [{"key": "0:35540", "pid": 35540, "process": "brave.exe", "state": 0, "muted": true}], "peak": 0.0, "shields_running": []}
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done. |
| 1 | Cannot run here (not Windows, or `psutil` is missing). |
| 2 | Refused: the instance is audibly playing right now. |
| 3 | Refused: no instance was named. |

## What the shield promises

Each line is there because the opposite went wrong once.

- **It does not mute everything.** Without `--match` it exits with code 3. `--whole-browser` exists, and you have to type it.
- **It does not start while you are listening.** It samples the target for two seconds first. If sound is coming out, it exits with code 2 and touches nothing.
- **It unmutes only what it muted.** A session you muted yourself stays muted.
- **It keeps the target muted.** It checks about ten times a second, so a new session is muted almost at once, and a page that unmutes itself is muted again.
- **It outlives the agent.** `shield start` launches the shield outside the job object of the calling terminal when Windows allows it, so closing the agent session does not kill it. It still ends on its own when the time is up.
- **It can be cleaned up after.** The shield keeps a lease file listing the sessions it muted. If the process is killed, `quiet-agent recover` unmutes exactly those.

## Finding the window that flashes

Console windows that appear for a fraction of a second are hard to trace by eye. Start the watcher, work as usual, then read the log:

```
pythonw -m quiet_agent watch-windows --minutes 60
```

`pythonw` matters here, because the watcher should not open a window of its own. The log is one JSON object per line. A `window` event carries the window class, title, position and the chain of parent processes, which tells you who started the thing that flashed. A `process` event is written for every newly started console-type program. The last line counts what was seen:

```json
{"event": "finished", "top_level_windows_shown": 1, "windows_recorded": 0, "processes_recorded": 107, "t": "11:57:59.737"}
```

The log stays on your machine. It contains window titles and command lines, so read it before you share it.

## Rules for the agent

Tools only help if the agent knows when to use them. [`docs/agent-rules.md`](docs/agent-rules.md) names five things on the computer that belong to the person and not to the agent (speakers, screen, focus, clipboard, keyboard and mouse) and says what to do instead for each. Paste it into `CLAUDE.md`, `AGENTS.md` or wherever your agent reads its instructions.

## Limits

- Windows only.
- It grew out of a script I have been running with Brave on Windows 11 since early November 2026. Chrome, Edge, Vivaldi and Opera are built on the same audio architecture and are in the default process list, but I have not tested them. If you try one, please open an issue with the result.
- Firefox is not supported.
- One shield at a time.
- The shield mutes. It does not pause playback.
- Sessions are remembered as `device:pid`. If an audio device is added or removed while a shield runs, a session can end up under a new key. `quiet-agent recover --all --match <text>` covers that case.
- The unit tests replace the audio layer with a fake, so they change no sound and run anywhere. The real Core Audio calls have their own check, `tests/live_roundtrip.py`, which needs a Windows machine with an audio device. CI runs only the unit tests.

## How it works

`IMMDeviceEnumerator` lists the active output devices. For each device, `IAudioSessionManager2` gives the session list. For each session the tool reads the owning process id, keeps the session if the process is one of the chosen browsers and the command line of that process or of one of its parents contains the `--match` text, and then uses `ISimpleAudioVolume` to mute and `IAudioMeterInformation` to read the peak level. The code is in [`src/quiet_agent/audio.py`](src/quiet_agent/audio.py), a little over two hundred lines.

## Related tools

[SoundVolumeView](https://www.nirsoft.net/utils/sound_volume_view.html) and [pycaw](https://github.com/AndreMiras/pycaw) can mute the audio of a process, and `quiet-agent` uses the same Windows API underneath. What it adds is the handling around the mute for the agent case: picking one browser instance, refusing while you listen, and getting the sound back after the agent is gone.

## Development

```
git clone https://github.com/kerem-olgun/quiet-agent
cd quiet-agent
python -m unittest -v
python tests/live_roundtrip.py
```

The second command is the live check. It starts a helper process that plays digital silence, runs a real shield against that helper only, and verifies that the session is muted and then unmuted. Nothing is audible and no browser is touched.

## License

MIT
