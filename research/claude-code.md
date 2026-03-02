  The key insight: --append-system-prompt injects context before turn 1 — the agent knows its role, rig, current task from the very first token, with zero round-trips.

  Current best practice would be:

  claude \
    --append-system-prompt "You are a crew agent on rig 'gastown'.
  Your current task is bd-task-123: ...
  GT_ROOT=/path/to/town" \
    --dangerously-skip-permissions \
    "bd show bd-task-123 && get to work"

  vs. gastown's pattern which: launches → waits for idle → sends the task via nudge.

  Also worth noting:
  - --settings '{"key":"val"}' accepts a JSON string directly — no filesystem write needed for per-session config
  - -p / --print mode eliminates the entire tmux machinery for tasks that are well-scoped (no need for SIGWINCH hacks, no idle polling)

  The question for chimera is: how much of the work is "give an agent a task and wait for completion" (use -p mode, simple) vs. "long-running interactive sessions that need
  human observability and mid-flight nudges" (tmux is worth it, but still use --append-system-prompt to front-load context)?


  What chimera can do:                                                                                                                                                           
                                                                                                                                                                                 
  If orchestrating via the Claude API directly, full control — inject <system-reminder> content anywhere in the message stream, including tool results, user turns, etc.

  If orchestrating via the claude CLI + tmux (like gastown), the only mid-conversation injection point is send-keys. But wrapping nudge content in <system-reminder> tags is a
  reasonable trick:

```
  <system-reminder>
  Your current bead is now bd-task-456. Previous task bd-task-123 was completed.
  Remaining context: ...
  </system-reminder>
```

  Please start on bd-task-456.

  That signals "this is orchestrator-level context" vs. conversational noise. Whether Claude Code does something special with the tag at the transport layer (beyond my seeing it
   as XML in content) — I genuinely don't know, since the binary is compiled and I can't inspect it.

  The more interesting angle for chimera: Claude Code injects CLAUDE.md as a system-reminder dynamically (not in the initial system prompt). That means even with the CLI, you
  could potentially craft a CLAUDE.md that gets updated between sessions with fresh task context — and it'd be re-injected on each session start. Simpler than nudges for initial
   context.


  For local-machine tools with Claude Code's built-in toolset, there are actually two paths beyond CLI+tmux that I should have mentioned:                                        

  1. --input-format stream-json + --output-format stream-json — this is a bidirectional streaming JSON protocol over stdin/stdout. You pipe JSON messages in, get JSON (including
   tool calls, results, text) back. It's a programmatic interface to Claude Code with all its native tools. Overstory uses claude --print -p for single-shot tasks; this is the
  multi-turn version of that.
  2. The Claude Code SDK (@anthropic-ai/claude-code npm package) — wraps the above into a library. You call a function, pass messages, get back a stream of events including tool
   executions. No tmux, no TUI, fully programmatic.

  So chimera could potentially use the SDK or the stream-json protocol instead of tmux, getting:
  - Full control over system-reminder injection
  - No idle polling, no SIGWINCH hacks
  - Structured event stream (easier to log metrics, costs, etc.)

  The tradeoff vs. tmux: no human observability — you can't just tmux attach and watch an agent work. Gastown's tmux approach has real value there.


⏺ There it is — cleanupPeriodDays in settings.json, default 30 days. Sessions inactive longer than that get deleted at startup. That's what's cleaning up your older files.                                                      
                                                                                                                                                                                                                                 
  So the retention picture is:                                                                                                                                                                                                   
  - Default: 30 days after last activity, deleted on next claude startup                                                                                                                                                         
  - Configurable: set cleanupPeriodDays in ~/.claude/settings.json                                                                                                                                                               
  - Disable cleanup: set to 0 deletes everything immediately (probably not what you want), no apparent way to set it to "never" other than a very large number                                                                   
