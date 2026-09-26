# automation-notice — see what Claude Code is doing on your machine

**English** | [繁體中文](README.zh-TW.md)

When Claude Code drives your logged-in Chrome (Claude in Chrome) or runs a script that
takes over the real keyboard and mouse, this puts it on screen — instead of asking you in
chat to please not touch the keyboard.

A thin frame around the screen edge, plus a panel:

```
● Claude is using your computer                  Ctrl+Alt+Q to take back control
● NewGame            Clicking in Chrome at (640, 360)             01:24
● automation-notice  Opening example.com/jobs/1234                00:18
```

One row per Claude Code session, each with its own colour, project name (the session's
working directory) and elapsed time. One Claude running means one row.

The action column says **what this step actually touches**, not the tool name: which
coordinate or element was clicked, the exact text typed, the full URL opened, the value
put into a form, how many steps a batch has and what they are. For a tool it has never
seen, it flattens the arguments (`tool: key=value, key=value`), so a new tool never turns
into an unreadable line. The dot on a row flashes when that step changes, so you can tell
it just moved.

The line is a direct translation of the command the agent is actually about to run, not the
agent's own description of it, and it costs no tokens. See **How it works** below.

## Four things worth knowing

- **It stays out of your way.** Click-through, never takes focus, not in the taskbar or
  Alt+Tab. Clicking goes to whatever is underneath.
- **Stopping is a keystroke, not a button.** `Ctrl+Alt+Q` takes back control. There is
  deliberately nothing to click: a window that swallows clicks would eat the one that lands
  on it mid-automation. Stopping leaves a **sticky latch** — until you say something
  yourself, the PreToolUse hook **denies** every tool call that would borrow your keyboard,
  mouse or browser (returning `deny` with the time you stopped it and what it was doing).
  The agent can only stop and report; it cannot decide to try again. Your next message
  clears the latch automatically.
- **Push it off-screen if you don't want to see it.** Drag it past any edge and a small
  handle stays (`config --park right` does it in one line). `config --border off` turns off
  the screen-edge frame; `config --reset-pos` brings the panel back.
- **Screenshots, recordings and streams can't see it** (default). Only your eyes can. The
  agent's own screenshots aren't blocked by it, and a stream won't leak what the agent is
  typing. `config --capture show` makes it recordable.

The panel passes clicks through. To move it, **rest the cursor on it for a moment**: the
outline turns white and the cursor becomes a hand, and you can drag it. Move away and it
goes back to passing clicks through. Holding Ctrl+Alt makes it grabbable right away.
Claude in Chrome drives the page without moving your real cursor, so the agent's own clicks
never land on the panel. There are no buttons on it, by design.

## How it works

It is not part of Claude. It is a small program (`notice.py`) that sits next to Claude Code and is
wired in through Claude Code **hooks**: a hook tells Claude Code "when this happens, run this program".
`install.py` registers these moments in `~/.claude/settings.json`:

| Moment | What `notice.py` does |
| --- | --- |
| Before the agent uses a Chrome tool | Works out the line of text and lights up the panel |
| Before the agent runs a shell command | Checks the command text for keyboard/mouse keywords; if there are none it skips, without even starting Python |
| When you send a new message | Clears this conversation's row and lifts the Ctrl+Alt+Q lock |
| When the agent's turn ends or the session closes | Clears this conversation's row |

One round trip:

```
The agent decides to click (640, 360) in Chrome
  │  Before running it, Claude Code hands the tool name and arguments to notice.py
  ▼
notice.py receives {"tool_name": "mcp__claude-in-chrome__computer",
                    "tool_input": {"action": "left_click", "coordinate": [640, 360]}}
  │  and turns it into "Clicking in Chrome at (640, 360)", written to this conversation's state file
  ▼
The notice window (a small resident process) reads the state files every 0.25 s and draws the
border and panel, one row per conversation
  │
  ▼
Turn ends, or you speak → that row is cleared; when no rows are left the window closes itself
```

To see how any action will be described, feed it in directly:
`echo '{"tool_name":"mcp__claude-in-chrome__computer","tool_input":{"action":"left_click","coordinate":[640,360]}}' | python notice.py describe`

**Why you can trust it, and why it costs no tokens.** The line is translated from the command the
agent actually issued; the agent is never asked to describe itself, so it cannot make an action
sound nicer than it is. Everything runs as an ordinary program on your machine, outside the model,
so it takes no tokens. The hook prints nothing, so nothing is added to the conversation. The one
exception: if you pressed Ctrl+Alt+Q and the agent still tries to act, the hook blocks it with one
sentence saying when you took back control and what it was doing, and only that sentence reaches
the conversation.

## Install and use

### 1. What you need

- Windows 10 2004 or later
- Claude Code (desktop app or CLI)
- Python 3.9+ with tkinter. The python.org installer includes tkinter by default; tick "Add python.exe to PATH".
  To check, run `python -c "import tkinter"` in a command prompt; no error means you're set.
  If the Microsoft Store opens instead, the `python` on your PATH is the Store stub, so install real Python first.

### 2. Install (pick one method, not both, or every hook runs twice)

**Method A: as a Claude Code plugin (recommended).** In Claude Code, type:

```
/plugin marketplace add barryclown/automation-notice
/plugin install automation-notice@barryclown
```

Then restart Claude Code. The plugin uses the `python` on your PATH.

**Method B: wire it into your own settings**

```bash
git clone https://github.com/barryclown/automation-notice.git   # or Code → Download ZIP on GitHub
cd automation-notice
python install.py              # backs up ~/.claude/settings.json to ~/.claude/backups/ first
```

Then restart Claude Code. `install.py` uses the Python you run it with, so there are no paths to edit;
if you move the folder, just run it again. `python install.py --dry-run` shows what it would write.

### 3. Check it works

- Method B: in the folder, run `python notice.py start --detail test --ttl 8`. An orange border and a
  panel appear and disappear on their own after 8 seconds.
- Method A: ask Claude to open any web page with Claude in Chrome; the panel should light up with
  "Opening …".

### 4. Everyday use

Nothing to do. Whenever Claude touches your Chrome or runs a command that drives the keyboard or
mouse, it lights up by itself, and it goes away when the turn ends or you send a new message.

- **Make the agent stop**: press `Ctrl+Alt+Q`. Until you send your next message, the agent cannot
  touch your keyboard or mouse.
- **Panel in the way**: rest the cursor on it for a moment and drag it, or `config --park right` to
  push it to the edge with only a small handle showing.
- **Opacity, letting recordings capture it, turning off the border**: see Settings below.
- **Your own keyboard/mouse scripts**: put their names in `triggers.local.txt` (see When it lights up).

With Method A, `notice.py` lives in `~/.claude/plugins/cache/barryclown/automation-notice/<version>/`;
use that path for `config` commands.

### 5. Update and remove

- Method A: update with `/plugin marketplace update barryclown`; remove with `/plugin uninstall automation-notice@barryclown`
- Method B: update with `git pull` and run `python install.py` again; remove with `python install.py --uninstall` (other hooks are left alone)

## When it lights up

| Event | Condition | Effect |
| --- | --- | --- |
| PreToolUse | `mcp__claude-in-chrome__*` (except read-only `tabs_context` / `list_connected_browsers`) | that session's row appears or extends |
| PreToolUse | a `Bash` / `PowerShell` command whose text contains a trigger keyword | same |
| Stop / SessionEnd | that session's turn ends | its row goes away (others stay) |
| UserPromptSubmit | you send a message | its row goes away and the stop latch clears |

The in-app browser (`mcp__Claude_Browser__*`) does not trigger it: it runs in a side panel
and never borrows your input.

**Trigger keywords** live in two files: `triggers.txt` (shared defaults — `SendKeys`,
`SetForegroundWindow`, `pyautogui`, …) and `triggers.local.txt` (your own script names, not
committed). One literal, case-insensitive keyword per line; `#` starts a comment. No
reinstall needed — the list is rebuilt at the end of the next turn. Every Bash/PowerShell
command fires a hook, so the shell greps for a keyword first and only then starts Python;
ordinary commands are not slowed down.

**How long it stays up**: every automated action pushes the deadline out by `hook_ttl`
seconds (180 by default). So it stays up for as long as the agent keeps working; it only
goes dark if more than 180 seconds pass *between* two actions, and the timer continues
rather than resetting when the next action brings it back. The turn ending is what restarts
the clock. The deadline is a safety net: if the agent crashes or the turn is interrupted,
the overlay disappears on its own within 180 seconds.

## Settings

```bash
python notice.py config --opacity 0.5    # 0.2-1.0 (default 0.72)
python notice.py config --lang en        # overlay language: auto (default, follows Windows) / zh / en
python notice.py config --capture show   # let screen capture see it (default: hide)
python notice.py config --border off     # turn off the screen-edge frame
python notice.py config --park right     # push it off-screen: left / right / top / bottom
python notice.py config --reset-pos      # bring it back to bottom centre
python notice.py config --hook-ttl 300   # seconds allowed between two actions (default 180, min 30)
```

On screen: rest the cursor on the panel (or hold Ctrl+Alt) to drag it; Ctrl+Alt+- / = or the
scroll wheel while holding Ctrl+Alt adjusts opacity. Settings persist
and apply live.

## Manual use

```bash
python notice.py start --detail "Filling forms" --ttl 600
python notice.py set   --detail "9 of 15"     # change the text without restarting
python notice.py status                       # which sessions are up, and in what colour
python notice.py check-abort                  # exit 3 if the user stopped it
python notice.py clear-abort                  # clear the stop latch (hooks do this for you)
python notice.py stop                         # turn everything off
python selftest.py                         # headless self-test (descriptions / layout / latch / colours)

# want to know how a tool call will read on screen? feed it the payload
echo '{"tool_name":"mcp__claude-in-chrome__computer","tool_input":{"action":"type","text":"hi"}}' | python notice.py describe
```

## Limitations

- Windows only.
- **It does not mark the control being clicked.** Claude in Chrome reports coordinates
  inside the browser viewport; turning those into screen coordinates means guessing the
  Chrome window and viewport offset, and a wrong guess marks an unrelated spot. The
  coordinates go in the text instead.
- Bash/PowerShell matching looks at the command **text**, so it over-triggers: a command
  that merely mentions `SetForegroundWindow` (while writing docs, say) lights it up too.
  A false positive only shows an extra row; nothing else changes.
- It only makes things visible. It does not judge whether an action is dangerous — that
  belongs in Claude Code's own permission settings.
- If official computer use doesn't go through PreToolUse hooks, it won't light up for those
  actions (not yet verified).
- UI strings are English and Traditional Chinese; **code comments are Traditional Chinese only.**

## Runtime files

Under `%LOCALAPPDATA%\ClaudeAutomationNotice\`:

| File | Written by | Purpose |
| --- | --- | --- |
| `sessions/<session_id>.json` | CLI / hooks | what each session is doing (`manual.json` for `notice.py start`) |
| `aborts/<session_id>.json` | overlay | per-session sticky latch (used by the CLI) |
| `abort.json` | overlay | the global Ctrl+Alt+Q latch |
| `heartbeat.json` | overlay | pid + timestamp, used to tell whether it is alive |
| `config.json` | CLI | your settings |
| `triggers.grep` | hooks | the two keyword files merged for grep |

Drop an empty file named `DEBUG` in that folder and the overlay writes its startup steps to
`boot.log` (pythonw has no console, so this is the only way to see them).

## Notes from the bugs (read before changing things)

- **Don't show the window with `deiconify()`**: it goes through `ShowWindow(SW_NORMAL)`,
  which activates, and takes focus even with `WS_EX_NOACTIVATE` set. Use
  `ShowWindow(hwnd, SW_SHOWNOACTIVATE)`.
- **Tk calls `SetActiveWindow` when it creates the process's first window** (the
  `firstWindow` branch of `UpdateWrapper` in tkWinWm.c), and a freshly started process is
  allowed to take the foreground — so the overlay stole focus on the first
  `update_idletasks`, before it was even visible. It only reproduces when the user has been
  idle for a while (watching a video), not while they are gaming, so testing once looks
  fine. The fix is `LockSetForegroundWindow(LSFW_LOCK)` before creating Tk and `LSFW_UNLOCK`
  after showing. Dropping `-topmost` does not help. Find it by logging
  `GetForegroundWindow()` after every single step.
- **Tk can still raise the window after `mainloop()` starts**, so there is also a foreground
  guard (every 25ms for the first 3 seconds, then every 250ms; if the foreground is us, hand
  it back). That is the backstop, not the fix.
- **Verify the window you hand focus back to**: `SetForegroundWindow` reports success on
  cloaked/invisible windows while the foreground actually stays on the overlay. Prefer the
  foreground recorded by `hook-pre` before the overlay started (`restore_fg` in the session
  file); when picking a fallback, skip topmost windows or you land on system popups. If the
  original window is gone, `next_activatable_window()` picks another.
- **Lay out before showing**: `ShowWindow(SW_SHOWNOACTIVATE)` comes after `draw_panel()`, and
  the restore loop only acts while the overlay itself holds the foreground — if the user
  switched windows, leave them alone.
- **Tk decides mouse events from the real cursor position**, not the coordinates in the
  message. So `PostMessage` with made-up coordinates cannot test a button; an automated test
  has to move the window under the cursor, or accept that this last step needs a human click.
- **The stop latch must live outside the state file**: `ensure_overlay()` rewrites a
  session's state every time it lights up, so a flag kept in there is wiped by the next
  action — and the overlay comes back after the user deliberately stopped it.
- **Claude Code does not send a Stop event when the user interrupts a turn from the UI**, so
  turning off is also wired to UserPromptSubmit.
- **One state file per session**: several hook processes used to race on a single
  `state.json`, and on Windows `os.replace` fails while the target is being read, leaving
  orphaned temp files. One file per session almost removes the contention; the retry and
  `sweep_stale_tmp()` stay (it needs `rglob` to reach subdirectories).
- **Only one overlay at a time.** Between a hook deciding "nothing is running" and the new overlay
  writing its first heartbeat there is a 0.3-0.8 s gap. Parallel tool calls or other sessions in that
  window each started their own copy, stacking identical panels (4 simultaneous triggers gave 4 overlays).
  The overlay now takes a named mutex at startup, waits up to 1.5 s (taking over if the old one is
  shutting down), and exits if it still can't get it.
- **Use `CREATE_BREAKAWAY_FROM_JOB` for the long-lived process**, not WMI. The agent's shell
  lives in a KILL_ON_JOB_CLOSE job, so an ordinary child dies with that one tool call; WMI
  created processes die silently on some machines without reaching the first line of notice.py
  (observed — `boot.log` completely empty).

## Licence

MIT. See [LICENSE](LICENSE).
