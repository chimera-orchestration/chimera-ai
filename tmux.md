  4. Why tmux? And How Each System Interacts With Running Sessions

  Why tmux?
  - Claude Code is a TUI CLI app — there's no programmatic API for interactive sessions
  - tmux provides: human observability (operators can attach), persistence (sessions survive the orchestrating process), and process isolation

  Gastown's tmux interaction surface (vast — 2185-line tmux.go):
  - NudgeSession(): send-keys with vim Escape handling, 500ms debounce, 3 retries, per-session channel semaphore locks to prevent interleaving, WakePaneIfDetached() call
  - WakePane(): resize pane -1 row, sleep 50ms, resize +1 row → triggers SIGWINCH to wake Claude's TUI event loop in detached sessions
  - WaitForIdle(): polls capture-pane for ❯  (U+276F) prompt prefix
  - AcceptBypassPermissionsWarning(): reads pane content, navigates the bypass permissions dialog automatically
  - RespawnPane(): hot-reload agent in-place via respawn-pane -k
  - SetAutoRespawnHook(): tmux pane-died hook for Deacon crash auto-recovery
  - SetDynamicStatus(): status bar runs gt status-line every 5s
  - ConfigureGasTownSession(): theme, keybindings for mail/feed/agent cycling, mouse mode

  Overstory's tmux interaction surface (minimal — ~150 lines):
  - createSession(): create detached session with env exports via shell string prefix
  - sendKeys(): flatten newlines→spaces, tmux send-keys ... Enter
  - killSession(): process tree SIGTERM→2s grace→SIGKILL, then tmux kill-session
  - isSessionAlive(), getPanePid(), getCurrentSessionName()
  - No SIGWINCH wake, no vim mode handling, no retry logic, no nudge serialization, no status bar

  The key difference: gastown treats the tmux session as an ongoing interactive surface requiring sophisticated manipulation; overstory treats it as a launch-and-monitor container.
