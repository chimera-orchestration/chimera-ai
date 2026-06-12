# Claude session registration

How Claude Code's session registry (`claude agents --json`) behaves, probed live on
2026-06-12 with claude 2.1.175. This is what chimera's `live_sessions()` /
`refuse_if_agent_running()` are built on.

## The registry validates pids at read time — no stale ghosts

A SIGKILL'd interactive session (no chance to deregister itself) vanished from
`claude agents --json` on the first query, ~0s after the kill. So an entry in the
output means a process really is alive right now.

A reported session can still be very hard to *find*, though:

- The `claude agents` TUI lists only **background** agents; `kind: interactive`
  sessions appear in `--json` but not in the TUI.
- A session registers its **launch** identity (`name`, `cwd`) and keeps it. An
  interactive session that has since attached to a background session elsewhere
  shows that other session's name in its terminal-tab title — so scanning tab
  titles for the registered name finds nothing, yet the worktree is still pinned.
  (Observed: a `ch goal start` session for sybil whose tab read
  `chimera@ghost-agent@agent`.)

## Sessions at the trust prompt are not yet registered

An interactive claude sitting at the "do you trust this folder?" prompt occupies
the directory as a process but does **not** appear in `claude agents --json` — a
false-negative window for liveness checks.

## A claude spawned from inside another claude doesn't register itself

A child `claude` inherits the parent session's environment and takes on its
identity: its UI title flipped to the parent session's name and it never appeared
in `claude agents --json` under its own name/cwd. Relevant if chimera ever
launches agents from within an agent — the child must get a scrubbed environment.

Claude-related vars observed in a session's environment:

```
AI_AGENT
CLAUDECODE
CLAUDE_CODE_CHILD_SESSION
CLAUDE_CODE_ENTRYPOINT
CLAUDE_CODE_EXECPATH
CLAUDE_CODE_SESSION_ID
CLAUDE_EFFORT
CLAUDE_ENABLE_STREAM_WATCHDOG
CLAUDE_JOB_DIR
CLAUDE_AGENTS_AUTO_RELAUNCHED_AT
```

Unsetting all of these before spawning made the child register normally
(seen within ~2s of accepting the trust prompt).

## Useful queries

- `claude agents --json --cwd <path>` — sessions started under `<path>`
- `claude agents --json --all` — include completed sessions (background only;
  exited interactive sessions are not retained)
