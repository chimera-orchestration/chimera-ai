# macOS Terminal Emulators: Scripting & Programmatic Control

Research date: 2026-03-11

Focus: window/tab creation, title setting & locking, Python APIs, scripting capabilities.

---

## Terminal.app (macOS built-in)

### Overview
The default macOS terminal, shipped with every Mac. AppleScript support is first-class since Terminal.app predates most alternatives.

### AppleScript Support

Terminal.app has a full AppleScript dictionary. Key verbs and objects:

**Creating windows and tabs:**
```applescript
-- New window running default shell
tell application "Terminal"
    do script ""
end tell

-- New tab in front window (using SystemEvents keyboard shortcut)
tell application "System Events"
    tell process "Terminal"
        keystroke "t" using command down
    end tell
end tell

-- New window running a command
tell application "Terminal"
    do script "cd ~/projects && vim ."
end tell
```

**Setting custom titles:**
```applescript
tell application "Terminal"
    -- set custom title on a tab
    set custom title of front tab of front window to "My Tab"
    -- run a command that also sets title via escape sequence
    do script "echo hello" in front tab of front window
end tell
```

Tab properties include `custom title`, `title displays custom title`, `title displays shell path`, `title displays current file name`, `title displays process name`, `title displays device name`, `title displays window size`.

**Executing commands in existing tabs:**
```applescript
tell application "Terminal"
    do script "ls -la" in tab 1 of front window
end tell
```

### Title Locking Against ANSI Escape Override

Terminal.app has **no mechanism to lock a custom title** against ANSI escape sequences (OSC `\033]0;...\007` or `\033]1;...\007`). Once a `custom title` is set via AppleScript, any shell or program running in that tab can still override the displayed title using escape sequences.

The Profile settings (Profiles > Tab > Title) allow choosing what components appear in the tab title (active process name, working directory, etc.), but there is no checkbox to block programs from changing the title.

Workaround: set the shell's `PROMPT_COMMAND` / `precmd` to never emit title escape sequences, i.e., prevent the shell from emitting them rather than blocking at the terminal level.

### CLI/Shell Control
No CLI remote control protocol. Only way to script Terminal.app programmatically is via AppleScript/osascript.

### Python API
No dedicated Python API. Use `subprocess` to invoke `osascript`:
```python
import subprocess
subprocess.run([
    "osascript", "-e",
    'tell application "Terminal" to do script "echo hello"'
])
```

### Pane Splitting
Terminal.app does not support pane splitting. Tabs only.

---

## iTerm2

**Age:** First release 2010 (fork of the original iTerm from 2002). iTerm2 v2.0 stable: July 2014. Current: v3.6.x (Feb 2026).
**GitHub stars:** ~17,000 (gnachman/iTerm2)
**Perception:** The long-reigning "power user" macOS terminal. Deeply loved for Shell Integration, imgcat, triggers, and scripting. Some perceive it as bloated compared to newer GPU-accelerated alternatives, but it remains the most fully-featured macOS terminal for scripting purposes.

### AppleScript Support

iTerm2 exposes a full AppleScript dictionary. Bundle ID: `com.googlecode.iterm2`.

```applescript
-- Create new window with default profile
tell application id "com.googlecode.iterm2"
    activate
    create window with default profile
    tell current session of current window
        write text "echo hello"
    end tell
end tell

-- Create new tab in current window
tell application id "com.googlecode.iterm2"
    tell current window
        create tab with default profile
    end tell
end tell

-- Create new window with named profile
tell application id "com.googlecode.iterm2"
    create window with profile "Hotkey Window"
end tell

-- Split pane horizontally
tell application id "com.googlecode.iterm2"
    tell current session of current window
        split horizontally with default profile
    end tell
end tell
```

### Python API

iTerm2 ships a full Python API (`pip install iterm2`). Scripts run as daemons or one-shots connected via a WebSocket. "AutoLaunch" scripts run on iTerm2 startup.

**Key classes:**
- `iterm2.App` — application-level access
- `iterm2.Window` — OS window
- `iterm2.Tab` — tab within a window
- `iterm2.Session` — individual pane/session

**Window operations:**
```python
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)

    # Create new window
    window = await iterm2.Window.async_create(connection)

    # Create new window with profile
    window = await iterm2.Window.async_create(connection, profile="Default")

    # Create tab in existing window
    tab = await window.async_create_tab(profile="Default", command="htop")

    # List all windows and tabs
    for window in app.windows:
        for tab in window.tabs:
            print(tab.tab_id)

iterm2.run_until_complete(main)
```

**Tab title:**
```python
# Set tab title (interpolated string)
await tab.async_set_title("My Custom Tab")
```

**Pane splitting:**
```python
session = tab.current_session
right_session = await session.async_split_pane(vertical=True)
below_session = await session.async_split_pane(vertical=False)
```

### Title Locking Against ANSI Override

iTerm2 **can lock titles** via profile property `set_allow_title_setting(False)`. When set to False, ANSI escape sequences from programs running in the session cannot change the title.

```python
import iterm2
import AppKit

AppKit.NSWorkspace.sharedWorkspace().launchApplication_("iTerm2")

async def main(connection):
    app = await iterm2.async_get_app(connection)
    await app.async_activate()

    myterm = app.current_terminal_window
    if not myterm:
        myterm = await iterm2.Window.async_create(connection)

    session = myterm.current_tab.current_session
    update = iterm2.LocalWriteOnlyProfile()
    update.set_allow_title_setting(False)          # LOCK: blocks ANSI overrides
    update.set_name("This is my locked title")
    await session.async_set_profile_properties(update)

iterm2.run_until_complete(main, True)
```

Equivalent via Preferences: Profiles > Terminal > uncheck "Allow terminal applications to change the window title" (or similar wording — the checkbox is in Profiles > Terminal tab).

### Session Title Provider
For fully dynamic programmatic titles, register a "Session Title Provider" via the Python API — a daemon that computes titles on the fly and pushes them, overriding any shell-set title.

---

## Kitty

**Age:** First public release 2017 by Kovid Goyal (creator of Calibre). Mature, actively maintained.
**GitHub stars:** ~32,000 (kovidgoyal/kitty)
**Perception:** Highly respected among power users and Linux users. Known for GPU acceleration, extensible "kittens" plugin system, image rendering protocol (Kitty Graphics Protocol adopted industry-wide), and powerful remote control. Config is text-based. Perceived as performance-focused and hackable. Some find the config less approachable than YAML-based alternatives. macOS support is present but feels more Linux-native.

### Remote Control Protocol

Kitty provides a comprehensive CLI remote control system via the `kitten @` subcommand (also callable as `kitty @`). Requires `allow_remote_control yes` (or a password) in `kitty.conf`.

```bash
# Enable remote control in kitty.conf
allow_remote_control yes
# or with password auth:
remote_control_password "secret" *
```

**Creating windows and tabs:**
```bash
# New tab
kitten @ launch --type=tab

# New tab with title and command
kitten @ launch --type=tab --tab-title "My Tab" bash

# New OS window
kitten @ launch --type=os-window

# New window (split) in current tab
kitten @ launch --type=window
```

**Setting titles:**
```bash
# Set tab title (PERMANENT - blocks future ANSI override by default)
kitten @ set-tab-title "My Tab Title"

# Set tab title for matched tab
kitten @ set-tab-title --match "title:old title" "New Title"

# Set window title (PERMANENT by default)
kitten @ set-window-title "My Window Title"

# Set window title TEMPORARILY (allows programs to override afterward)
kitten @ set-window-title --temporary "Temporary Title"
```

### Title Locking - Key Behavior

`kitten @ set-window-title` **permanently locks the title by default** — once set, child processes cannot change it via ANSI escape sequences. The `--temporary` flag inverts this: it sets the title but allows programs to change it afterward.

This is the opposite of most terminals: opt-in to allow override rather than opt-out.

`kitten @ set-tab-title` works similarly.

**Querying state (JSON output):**
```bash
kitten @ ls  # returns full window/tab tree as JSON
```

### Remote Control from Python
```python
import subprocess, json

# Launch new tab
subprocess.run(["kitten", "@", "launch", "--type=tab", "--tab-title", "My Tab"])

# Set title (locked by default)
subprocess.run(["kitten", "@", "set-tab-title", "Locked Title"])

# Get window list
result = subprocess.run(["kitten", "@", "ls"], capture_output=True, text=True)
state = json.loads(result.stdout)
```

### AppleScript Support
None. Kitty does not expose an AppleScript dictionary.

### Config: Dynamic Titles
To allow or disallow programs changing titles globally (not per-window), set `dynamic_title` in `kitty.conf`:
```
# kitty.conf
dynamic_title yes   # default: allow programs to set title
```
When `dynamic_title no`, ANSI title escape sequences are ignored globally.

---

## Ghostty

**Age:** Public beta/closed testing through 2023-2024; v1.0 released December 26, 2024, by Mitchell Hashimoto (HashiCorp founder). Crossed 1,000 GitHub stars within a week of release.
**GitHub stars:** ~46,000 (ghostty-org/ghostty) — explosive growth on release
**Perception:** Enormous initial hype, validated by actual quality. Reviews describe it as fast, native-feeling, zero-configuration-needed on macOS, with native platform UI (no custom chrome). Used natively by a large portion of the developer community within months of release. In December 2025 it moved under Hack Club's 501(c)(3) umbrella for long-term governance. Ghostty 1.3 (early 2025) added AppleScript support. Main critiques: younger ecosystem, scripting API still labeled "preview".

### AppleScript Support (added in v1.3, early 2025)

Ghostty 1.3 added a native AppleScript dictionary wrapping App Intents. The API is labeled "preview" while it stabilizes, but is enabled by default.

```applescript
-- New window
osascript -e 'tell application "Ghostty" to new terminal'

-- New window running a command
osascript -e 'tell application "Ghostty" to new terminal command "htop"'

-- New window with working directory
osascript -e 'tell application "Ghostty" to new terminal directory "~/projects"'

-- New window with command and directory
osascript -e 'tell application "Ghostty" to new terminal command "vim ." directory "~/projects"'

-- New tab (in current window)
osascript -e 'tell application "Ghostty" to new terminal location tab'

-- New tab with command
osascript -e 'tell application "Ghostty" to new terminal location tab command "htop"'

-- Split pane right
osascript -e 'tell application "Ghostty" to split direction right in terminal 1'

-- Send text to terminal
osascript -e 'tell application "Ghostty" to send text "ls -la" to terminal 1'

-- Get terminal contents
osascript -e 'tell application "Ghostty" to get contents of terminal 1'

-- Open quick terminal dropdown
osascript -e 'tell application "Ghostty" to open quick terminal'
```

Terminal objects have properties: `index`, `UUID`, `title`, `working directory`, `contents`.

### CLI Control
No native CLI remote control protocol (as of early 2026). Feature is actively discussed in GitHub Discussions #2353. Workarounds:

```bash
# macOS: open new window at path
open -na Ghostty --args --working-directory="$(pwd)"

# macOS: open new window running command
open -na Ghostty --args -e sh -c 'mycommand; exec $SHELL'

# macOS: open new tab (if Ghostty already running)
open -a Ghostty "$(pwd)"
```

### Title Locking Against ANSI Override
No documented title locking mechanism as of early 2026. The AppleScript `title` property is readable but the API for locking is not yet described.

### Python API
No dedicated Python API. Use subprocess + AppleScript:
```python
import subprocess
subprocess.run([
    "osascript", "-e",
    'tell application "Ghostty" to new terminal location tab command "python3 myapp.py"'
])
```

---

## Warp

**Age:** Public beta launched April 5, 2022 on macOS. Version 2.0 ("Agentic Development Environment") launched June 2025. Warp for Windows launched 2025.
**GitHub stars:** ~26,000 (warpdotdev/Warp — note: repo is mostly issues tracker, not full source)
**Perception:** Divisive. Strong enthusiasts value its AI assistance (AI command suggestions, natural language to shell, error explanations), block-based output model, and modern UX. Critics object to the mandatory login requirement for the free tier, closed-source core, privacy concerns about data leaving the machine (though Warp claims zero-data-retention options for paid tiers), and the "AI-first" direction feeling at odds with traditional terminal workflows. SOC 2 Type 2 compliant. The product has evolved rapidly — now marketed as an "Agentic Development Environment" integrating Claude, GPT-4o, and other LLMs.

### AppleScript Support
**None.** Warp explicitly has no AppleScript dictionary. This is a frequently-requested feature (GitHub issue #3364, open since 2023).

### URI Scheme
Warp supports a `warp://` URI scheme for programmatic control from scripts and launchers:

```bash
# Open new window at path
open "warp://action/new_window?path=/Users/me/projects"

# Open new tab at path
open "warp://action/new_tab?path=/Users/me/projects"

# Launch a saved Launch Configuration
open "warp://launch/path/to/config.yaml"
```

For Warp Preview builds: `warppreview://` instead of `warp://`.

### Launch Configurations (YAML)
Pre-defined workspace layouts saved as YAML files. Supports multi-window, multi-tab, split panes, custom tab titles, and startup commands:

```yaml
---
name: My Project
windows:
  - tabs:
      - title: Backend
        color: blue
        layout:
          cwd: /Users/me/projects/backend
          commands:
            - exec: docker-compose up
      - title: Frontend
        color: green
        layout:
          cwd: /Users/me/projects/frontend
          split_direction: vertical
          panes:
            - cwd: /Users/me/projects/frontend
              is_focused: true
            - cwd: /Users/me/projects/frontend
```

YAML files stored at `~/.warp/launch_configurations/`. Can be combined with URI scheme:
```bash
open "warp://launch/$HOME/.warp/launch_configurations/myproject.yaml"
```

### Title Locking Against ANSI Override
No documented mechanism. Warp's unique "block" model (each command is a discrete block) partially sidesteps the title problem — Warp doesn't rely on traditional shell title-setting patterns as heavily, but there is no API to lock a tab title against ANSI escape sequences.

### Python API
No dedicated Python API. From Python, use subprocess with the URI scheme:
```python
import subprocess
subprocess.run(["open", "warp://action/new_tab?path=/home/user/project"])
```

Or generate and launch a YAML config file:
```python
import subprocess, yaml, tempfile, os

config = {
    "name": "My Session",
    "windows": [{"tabs": [{"title": "Work", "layout": {"cwd": "/home/user/project"}}]}]
}
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    yaml.dump(config, f)
    path = f.name
subprocess.run(["open", f"warp://launch/{path}"])
```

### System Events Fallback
Before URI scheme, community used System Events accessibility scripting:
```applescript
tell application id "dev.warp.Warp-Stable" to activate
tell application "System Events"
    tell (first process whose bundle identifier is "dev.warp.Warp-Stable")
        keystroke "t" using command down  -- new tab
        delay 0.5
        keystroke "v" using command down  -- paste clipboard content
        keystroke return
    end tell
end tell
```
Requires Accessibility permission.

---

## Alacritty

**Age:** Announced January 6, 2017 by Joe Wilm. First public release 2017. Current: v0.15.x. Long-time GPU-accelerated pioneer, but development is methodical and scope is intentionally narrow.
**GitHub stars:** ~63,000 (alacritty/alacritty) — the highest star count of any terminal listed here, a legacy of being a GPU-accelerated pioneer.
**Perception:** Respected for simplicity, speed, and cross-platform consistency (macOS, Linux, Windows, BSD). Intentionally minimal — no tabs, no splits, no ligatures. Philosophy: "do one thing well; use tmux for the rest." Critics note it feels stagnant compared to Ghostty and Kitty, the no-tabs stance frustrates users who want a standalone terminal, and the config migration from YAML to TOML was a breaking change that annoyed users. However, it's stable, predictable, and extremely fast. Often used as the backend renderer with tmux on top.

### AppleScript Support
**None.** Alacritty has no AppleScript dictionary.

### IPC via Unix Socket (`alacritty msg`)

Alacritty runs as a single process per user and exposes a Unix domain socket for IPC. The socket path is `$TMPDIR/alacritty-{pid}.sock` on macOS, or overridden via `--socket` / `$ALACRITTY_SOCKET`.

**Available IPC commands:**

```bash
# Create a new window (within same Alacritty process)
alacritty msg create-window

# Create new window with title
alacritty msg create-window -T "My Window Title"

# Create new window at specific directory
alacritty msg create-window --working-directory /home/user/project

# Create new window running a command
alacritty msg create-window -e vim myfile.txt

# Create new window with config override
alacritty msg create-window -o 'cursor.style.shape="Beam"'

# Modify config of running window(s)
alacritty msg config 'window.opacity=0.9'

# Modify config of all windows
alacritty msg config --window-id=-1 'font.size=14'

# Get current config as JSON
alacritty msg get-config
```

**Tabs and Splits:** Not supported. Alacritty explicitly excludes tabs and splits. Use tmux, zellij, or a window manager.

### Title Control

**Static title:** Set via `--title` / `-T` at launch, or via `alacritty msg create-window -T "name"`.

**Dynamic title:** Controlled by `dynamic_title` in `alacritty.toml`:
```toml
[window]
title = "Alacritty"
dynamic_title = false   # set to false to LOCK title, block ANSI escape overrides
```

When `dynamic_title = false`, ANSI escape sequences (OSC `\033]0;...\007`) are ignored — the title stays whatever `title` is set to. This is a static config option, not a per-window runtime toggle.

### Python API
No dedicated Python API. Use subprocess:
```python
import subprocess

# New window
subprocess.run(["alacritty", "msg", "create-window", "-T", "My Title"])

# New window in specific directory
subprocess.run(["alacritty", "msg", "create-window",
                "--working-directory", "/home/user/project"])
```

If you need to target a specific socket:
```python
import os, subprocess
socket = os.environ.get("ALACRITTY_SOCKET")
subprocess.run(["alacritty", "msg", "--socket", socket, "create-window"])
```

---

## Summary Comparison Table

### Third-Party Apps: Age, Popularity, Perception

| App | First Release | GitHub Stars | Community Perception |
|-----|---------------|--------------|----------------------|
| **iTerm2** | 2010 | ~17,000 | Long-standing gold standard for macOS. Full-featured, deeply scriptable. Some see it as legacy/bloated vs newer GPU terminals. Still the best for scripting. |
| **Kitty** | 2017 | ~32,000 | Power-user favourite. GPU-fast, extensible, great remote control. Linux-first feel. Known for Kitty Graphics Protocol. |
| **Ghostty** | Dec 2024 | ~46,000 | Explosive debut. Native macOS UI, fast, zero-config default, young ecosystem. AppleScript added in v1.3. Huge hype mostly validated. |
| **Warp** | Apr 2022 | ~26,000 | AI-first, modern UX. Divisive: login requirement and closed-source core alienate traditionalists. Strong for AI-assisted workflows. No AppleScript. |
| **Alacritty** | Jan 2017 | ~63,000 | Pioneer GPU terminal. Minimal, fast, stable. No tabs/splits by design. Intentionally small scope. Stars reflect early-mover advantage. |

### Scripting Capability Matrix

| Feature | Terminal.app | iTerm2 | Kitty | Ghostty | Warp | Alacritty |
|---------|-------------|--------|-------|---------|------|-----------|
| AppleScript | Full | Full | None | Partial (v1.3+, preview) | None | None |
| CLI remote control | None | None | Full (`kitten @`) | None (planned) | URI scheme | `alacritty msg` |
| Python API | None (osascript) | Full (`iterm2` package) | None (subprocess) | None (subprocess) | None | None |
| New window | AppleScript | AppleScript / Python | `kitten @ launch --type=os-window` | AppleScript / `open` | URI / YAML | `alacritty msg create-window` |
| New tab | AppleScript (via SystemEvents) | AppleScript / Python | `kitten @ launch --type=tab` | AppleScript | URI / YAML | Not supported |
| Split panes | Not supported | AppleScript / Python | `kitten @ launch --type=window` | AppleScript | YAML layout | Not supported |
| Set tab title | AppleScript `custom title` | AppleScript / Python `async_set_title` | `kitten @ set-tab-title` | AppleScript (read `title` property) | YAML `title:` field | Not supported |
| Lock title vs ANSI | None | `set_allow_title_setting(False)` | Default! (`--temporary` to unlock) | Not documented | Not documented | `dynamic_title = false` (global) |

### Title Locking Detail

| App | Title Lock Mechanism | Granularity |
|-----|---------------------|-------------|
| Terminal.app | None | — |
| iTerm2 | `LocalWriteOnlyProfile.set_allow_title_setting(False)` via Python API; or Preferences checkbox | Per-session |
| Kitty | `kitten @ set-window-title` locks by default; use `--temporary` to allow override | Per-window |
| Ghostty | Not yet implemented / documented | — |
| Warp | Not documented | — |
| Alacritty | `dynamic_title = false` in config | Global (all windows) |

---

## Recommended Approach for Programmatic Title Locking

If title locking against ANSI escape sequences is a hard requirement:

1. **iTerm2** — only terminal with a proper per-session programmatic API (`set_allow_title_setting(False)`)
2. **Kitty** — per-window locking via `kitten @ set-window-title` (locked by default, `--temporary` to unlock)
3. **Alacritty** — global config only (`dynamic_title = false`), not per-window
4. **Ghostty / Warp / Terminal.app** — no locking mechanism

## Links

- iTerm2 Python API: https://iterm2.com/python-api/
- iTerm2 set_title_forever example: https://iterm2.com/python-api/examples/set_title_forever.html
- Kitty remote control: https://sw.kovidgoyal.net/kitty/remote-control/
- Kitty set-window-title man page: https://man.archlinux.org/man/extra/kitty/kitten-@-set-window-title.1.en
- Ghostty AppleScript discussion: https://github.com/ghostty-org/ghostty/discussions/10201
- Ghostty scripting API discussion: https://github.com/ghostty-org/ghostty/discussions/2353
- Warp launch configurations: https://docs.warp.dev/terminal/sessions/launch-configurations
- Warp URI scheme: https://docs.warp.dev/terminal/more-features/uri-scheme
- Alacritty msg docs: https://alacritty.org/cmd-alacritty-msg.html
