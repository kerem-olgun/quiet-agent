# Rules for an agent that shares a computer with a person

Paste this into your agent's instruction file (`CLAUDE.md`, `AGENTS.md`, a system prompt). Replace `AgentBrowser` with the folder name of the agent's own browser profile.

---

## Work without disturbing the person at this computer

You may use the browser and run commands. The work does not stop. What stops is anything the person can hear or see while they are working.

Five things on this computer belong to the person, not to you: the speakers, the screen, window focus, the clipboard, and the keyboard and mouse.

| Resource | Do not | Do instead |
|---|---|---|
| Speakers | Open a site that can play video or audio without protection. Mute the person's whole browser. | Work in your own browser instance. Before opening such a site run `quiet-agent shield start --match AgentBrowser --minutes N`. Exit code 2 means the person is listening to something in that instance: do not continue with media sites, ask first. |
| Screen | Start anything that shows a console or terminal window. Open a new browser window. Click a button that opens a native dialog (download, save, upload, file picker). | Run commands without a window (`pythonw`, no-window process flags). Use a background tab. Download with `fetch` inside the page. Upload by setting the file input directly. |
| Focus | Bring a window to the foreground. | Enter text through the page (DOM insertion or the browser's automation protocol). If a step only works with focus, stop and leave it for a time when the person is away. |
| Clipboard | Write to the system clipboard in any way, including simulated copy and paste. | Write a file, upload directly, or enter the text directly. |
| Keyboard and mouse | Send system-level key presses or mouse moves while the person is at the computer. | Use page-level events inside your own browser instance. |

When you are not sure whether a command opens a window, start `pythonw -m quiet_agent watch-windows --minutes N` first, run the command, and read the log. If a `window` event appears, stop using that way of starting the program or find a hidden way to start it.

If you break a rule: stop the work, undo the disturbance (`quiet-agent shield stop`, close the window), and then remove the cause for the whole class of cases, not just for the one that happened.

At the start of a session run `quiet-agent recover`. It restores sound that an earlier, killed shield left muted, and does nothing otherwise.
