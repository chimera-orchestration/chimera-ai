# Sessions: what harnesses actually do

**Read this before touching** session identity, liveness, the archive, hooks, occupancy,
`agent start`/`resume`/`stop`, or adding a harness adapter. It records behaviour that is
expensive to re-derive and easy to guess wrong — every claim below cost a live experiment.

Findings are **version-stamped**. A defence built on one is removed only when the mechanism
needing it is gone — never because a later version stopped exhibiting the symptom. Absence
of a bug in one run is not evidence of a fix.

## Claude Code (observed 2.1.212–2.1.220)

### The five ways a session starts

| mode | how | `source` |
|---|---|---|
| foreground | `claude` | `startup` |
| born-background | `claude --bg "prompt"` (a prompt is what triggers `--bg`) | `startup` |
| bridge | backgrounding a running foreground session | `fork` |
| resume | `claude --resume <id>` | `resume` |
| non-conversation | the `claude agents` browser, or one-shot `claude -p` | `startup` |

### Identity

- `CLAUDE_CODE_SESSION_ID` and `CLAUDE_PID` are present and fresh in the session's own
  environment *and* in its hooks, in all five modes. This is the reliable identity channel.
- The transcript is `~/.claude/projects/<slug>/<session-id>.jsonl` — the filename is the id.
- **`--session-id <uuid>` is honoured for foreground launches** (2.1.220): session env, hook
  payload and transcript name all take the supplied uuid. Re-launching on the same id works.
- **`--bg` refuses it**: `warning: --bg manages the session id; ignoring --session-id`. A
  born-bg id must be read back from stdout: `backgrounded · <short>`, where `<short>` is the
  first 8 chars of the uuid. It also prefixes `$CLAUDE_JOB_DIR`.
- A **bridge mints a brand-new id everywhere** (env, registry, transcript), matching
  `--fork-session`'s documented "create a new session ID". **No id survives a bridge.**
- The SessionStart payload's `session_id` has been seen to diverge from env/registry/job-dir
  on a background job (2.1.212, chimera issue #41). It did *not* diverge in 22 firings on
  2.1.220 — treat as latent, not fixed. Always cross-check payload vs env vs transcript stem
  and prefer the env id.
- `TERM_SESSION_ID` is **not** session identity: Terminal.app mints it per *tab* and zsh
  inherits it, so every session from that tab shares one value, and a daemon started from
  that tab hands the same value to unrelated sessions days later.

### Environment inheritance — exactly one hop

A process inherits its **actual OS parent's** environment, nothing more:

| session | OS parent | inherits launcher's env? |
|---|---|---|
| foreground | the launcher | **yes** |
| born-background | a daemon-pooled worker | **no** |
| bridged fork | a daemon-pooled worker | **no** |
| resume | whatever shell ran it | that shell's |

So an env var injected by a launcher (a role stamp, a tracking token) reaches a foreground
session only. Tracer-verified. `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID` and friends survive
everywhere because *claude* stamps them into every process it spawns, not the launcher.

**Lead, unverified**: `$CLAUDE_ENV_FILE` points a SessionStart hook at
`~/.claude/session-env/<session-id>/sessionstart-hook-1.sh`. If claude sources it, a hook
could inject env into the session it just started — the one known route to stamping bg and
forked sessions. 183 such dirs exist on a working machine, all empty (nothing writes them).
Undocumented in `--help`. Verify before relying on it.

### Processes

- A session's serving process is often a **pooled worker claimed after the session started**
  (one observed created a day into its session's life), and a SIGTERM'd bg job **respawns
  under a fresh pid** mid-life.
- Therefore: pid is not stable within a session, and "create-time is later than session start
  ⇒ stale" is **wrong** and will mark healthy sessions dead. Identify a process by the
  `(pid, create_time)` **pair** matching a pair captured earlier — never by inequality.
- Process ancestry does not reveal lineage: the serving process is a `bg-spare` pool worker;
  the `--fork-session --resume <parent>` argv lives on separate relauncher processes.

### Bridging (foreground → background)

- The parent is left **alive but conversationally frozen** — a *husk*. It stays
  registry-`busy` until its **TUI wrapper** exits. Husk windows observed: 3 min, 36 min, ~35 h.
- SessionEnd fires at **wrapper exit**, the same moment the registry drops the entry — so
  "archive says ended, registry says live" can never detect a husk.
- The only reliable husk marker is a **`fork` event in the same cwd within seconds of the
  parent going quiet** (44 s and 105 s observed).
- The fork payload **does not name its parent** — five keys only. It also omits `model`.
- A fork **copies the parent's transcript**; the parent's file is left a pre-bridge stub.
  Exact lineage is recoverable from transcript-prefix overlap (agentsview's job, not ours).

### Non-conversation sessions

These register real SessionStart/SessionEnd hooks in ordinary directories and must never
hold an address, nor count as occupants:

- **`claude agents` browser** — running it spawns a session in whatever cwd it was started
  from (verified from `~`, which isn't even in the workspace). It self-identifies four ways:
  payload `agent_type: "claude"`, `AI_AGENT` ending `_harness` (a real session ends `_agent`),
  `CLAUDE_CODE_AGENT=claude`, and `BROWSER=true` (plus `COLUMNS`/`LINES` for its TUI). It
  always runs the *default* model, never the launched session's. It ends when you exit the
  browser.
  **Backgrounding drops you into this browser**, which is why these cluster around bridges —
  they are not caused by launching a session. On one occasion the browser's session registered
  one second *before* the fork it accompanied.
- **One-shot `claude -p`** — `CLAUDE_CODE_ENTRYPOINT=sdk-cli` (an interactive session gets
  `cli`). Chimera's own commit-message and PR-description writers and `ch errand` are these,
  so they fire inside project and worktree cwds routinely.

`chimera.commands.hook.capture.addressed()` already filters both. It fails open (both signals
absent ⇒ treat as a conversation), because a real chat losing its mail is worse than a draft
gaining one.

### Lifecycle detail

- SessionEnd `reason` carries signal beyond `other`: a clean quit gives `prompt_input_exit`.
- Every launch is accompanied by more SessionStart firings than there are conversations —
  budget for it when reasoning about "how many sessions are in this directory".

## What this means for chimera

- **Identity comes from the launcher**, not the environment: mint a uuid and pass
  `--session-id` (foreground), or read `backgrounded · <short>` back from stdout (`--bg`).
- **Location is not identity.** cwd survives every transition, which makes it the right source
  for *axes* (workspace/project/goal/actor) and a legitimate basis for *restricting* a session
  — but never for granting it an address. See `chimera.addresses`.
- **Liveness needs the `(pid, create_time)` pair**, captured at SessionStart and matched later.
- **Occupancy must exclude non-conversations** and discount husks via the fork-event marker.

## Adding a harness

Everything claude-specific lives behind the `Agent` adapter, so a new harness fills in a
table rather than editing capture logic:

| contract | claude |
|---|---|
| `session_env` — env var holding the native id | `CLAUDE_CODE_SESSION_ID` |
| `supplied_id` — may the launcher choose the id? | foreground only |
| `launch_id_from(stdout)` — when the harness assigns it | parse `backgrounded · <short>` |
| `is_conversation(payload, env)` | no `agent_type`, entrypoint ≠ `sdk-cli` |
| fork signal | `source == 'fork'` |
| stop | `claude stop <id>` for background, SIGTERM for interactive |

A harness declaring none of these degrades to payload-only identity, which is still enough to
record sessions — just not to guarantee them.
