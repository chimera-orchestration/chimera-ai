# Sessions: what harnesses actually do

**Read this before touching** session identity, liveness, the archive, hooks, occupancy,
`agent start`/`resume`/`stop`, or adding a harness adapter. It records behaviour that is
expensive to re-derive and easy to guess wrong — every claim below cost a live experiment.

Findings are **version-stamped**. A defence built on one is removed only when the mechanism
needing it is gone — never because a later version stopped exhibiting the symptom. Absence
of a bug in one run is not evidence of a fix.

## Noticing when the harness changes under us

Most of what follows is *observed*, not promised (see *What is actually documented*), so it
will drift. Detection must be free and automatic — no one will remember to re-run probes:

- **Record the harness version on every session** (`AI_AGENT` carries it:
  `claude-code_2-1-220_agent`). "Which versions have we seen?" then becomes a query, and a
  session recorded under a version this doc has never validated is itself the alarm.
- **Assert the invariants passively.** A doctor check re-validates recorded sessions against
  the claims here — transcript stem matches `native_id`, payload id matches env id, a `fork`
  event has a plausible parent. Costs a SQL read, no model turn, and fires the moment any
  session behaves differently.
- **Surface unmodeled payload keys** (already live — `capture.unmodeled`): if claude starts
  sending a parent id, a bridge id, or anything else new, it lands in the log without a code
  change.
- **Cheap capability probes**: `claude --help` still offering `--session-id` is a free grep;
  behaviours needing a real session (does `--bg` still refuse it?) are re-run by hand and the
  result stamped here with its version.

## Claude Code (observed 2.1.212–2.1.220)

### The five ways a session starts

| mode | how | `source` |
|---|---|---|
| foreground | `claude` | `startup` |
| born-background | `claude --bg "prompt"` (a prompt is what triggers `--bg`) | `startup` |
| bridge | backgrounding a running foreground session | `fork` |
| resume | `claude --resume <id>` | `resume` |
| non-conversation | the `claude agents` browser, or one-shot `claude -p` | `startup` |

### …and two ways it fires without one

SessionStart is **not** only "a session started". It fires again for a session already
running, when its conversation is emptied or condensed — same process, same id, same
archive row:

| event | how | `source` | seen |
|---|---|---|---|
| compact | `/compact`, or claude compacting on its own | `compact` | 5 firings, 2026-07-16 → 2026-09-01, across three sessions |
| clear | `/clear` | `clear` | never in this archive; named by chimera's own pre-2.1 docstring, so treat as claimed, not observed |

**This is the trap.** An adapter that maps only `fork` and passes everything else through
hands `compact` to the cold-start path, where it claims a `PendingLaunch` — one meant for a
session genuinely starting in that directory, which then comes up unaddressed. Claude
compacts on its own schedule during long runs, so nothing unusual has to happen to hit it.

So the rule for `Agent.lifecycle` is that these answer **`resume`** — the session is
continuing, and a continuation claims nothing. And since the list will grow, an
*unrecognised* source must be treated as a cold start rather than passed through: an
unclaimed launch costs an address, while a claim taken by the wrong session costs someone
else's mail.

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
  2.1.220 — treat as latent, not fixed. Always cross-check payload id, transcript stem and env
  id; anchor on the **transcript stem** and log loudly on disagreement (see *What is actually
  documented* for why the stem, not the env id).
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

**The pooled worker's env is the *daemon's*, which makes this a containment problem, not just
an injection one.** A `claude bg-spare` worker was started from the user's own shell, so it
carries that shell's `$CHIMERA_WORKSPACE` — whatever the launching process had set, or cleared.
Observed 2.1.220: sessions accidentally spawned by the *test suite* (cwds in pytest tempdirs,
`CHIMERA_WORKSPACE` deliberately unset) had hooks that resolved the user's **live** workspace and
wrote five junk rows into its `archive.db`. Clearing an env var in the launching process is
therefore not a defence against a stray session — nothing downstream of the spawn is. Only
refusing the spawn is (`tests/conftest.py`'s `_no_real_harness`).

**`$CLAUDE_ENV_FILE` works, and must still not be built on.** A SessionStart hook is handed
`~/.claude/session-env/<session-id>/sessionstart-hook-1.sh`; a line of `export FOO=bar`
written there **does** reach the session's shell environment. Verified 2.1.220 on both a
foreground session and its **fork**, each receiving its own fresh injection (the fork's hook
wrote its own file — timestamps `inj-180419` vs `inj-180437` matched the two SessionStart
firings exactly). It is set for **SessionStart only** — absent at SessionEnd, and absent
inside the session itself.

So it is the one known channel that can stamp env into bg and forked sessions, where a
launcher's overlay cannot reach. Nevertheless: it is **undocumented**, and the official hooks
page states the opposite — SessionStart hooks "cannot directly set environment variables for
the session", their only outputs being `additionalContext`, `initialUserMessage`,
`watchPaths`, `sessionTitle`, `reloadSkills`. Something undocumented *and* contradicted by the
docs can disappear without a deprecation. Use the archive row (documented surfaces only) as
the source of truth; this may be an optimisation on top, never the foundation under it.

**Its path is also a fourth witness to identity**, and that use survives the objection above.
The session id is the file's *parent directory*, so reading it corroborates the transcript
stem for free — and a witness that vanishes simply stops being consulted, where an injection
that vanishes breaks whatever depended on it. `Claude.identity` therefore folds it into the
same disagreement warning as the payload and env ids (`_env_file_id`), and only for the
observed `session-env/<id>/` layout: `Path('/tmp/x.sh').parent.name` is `tmp`, a
plausible-looking id that is nothing of the sort, and a false alarm would blunt the one
signal that means *the harness changed under us*.

### What is actually documented

The hooks page guarantees only these env vars to hook commands: `CLAUDE_PROJECT_DIR`,
`CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_EFFORT`, `CLAUDE_CODE_REMOTE`,
`CLAUDE_CODE_BRIDGE_SESSION_ID` (Remote Control, 2.1.199+), `CLAUDE_PLUGIN_OPTION_<KEY>`.

**`CLAUDE_CODE_SESSION_ID` is not among them** — it is observed-reliable but unpromised, while
the *documented* identity channel (the payload's `session_id`) is the one that misbehaved in
#41. So: anchor on the payload's **`transcript_path` stem** — documented, and definitionally
the resumable id since claude locates a session by its transcript file — then cross-check
payload id, transcript stem and env id, and log loudly on any disagreement. Never silently
pick a winner among them.

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
  registry-`busy` until its **TUI wrapper** exits. Husk windows observed: 96 s, 3 min, 36 min,
  ~35 h. (The 96 s run corroborates the model from two independent clocks: the shell reported
  `claude` running 1m55s, and the parent's SessionEnd landed 96 s after its fork — i.e. at
  wrapper exit, not at the bridge.)
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

`chimera.commands.hook.capture.addressed()` already filters both — the verdict chimera
stores as **`addressable`**. It fails open (both signals
absent ⇒ treat as a conversation), because a real chat losing its mail is worse than a draft
gaining one.

### What a hook can and cannot stop

`SessionStart` **cannot block** (documented): exit code 2 renders stderr to the user as a hook
error, Claude never sees it, and the session proceeds. It offers context injection only —
`additionalContext`, `initialUserMessage`, `watchPaths`, `sessionTitle`, `reloadSkills` — with
no `decision`, `permissionDecision` or `continue`. So a session that shouldn't have started
cannot be turned away at the hook; only whoever *launches* it can refuse, which is why chimera's
launcher-side guard carries the weight and the hook can only warn.

`UserPromptSubmit` **can** block ("blocks prompt processing and erases the prompt"), as can
`PreToolUse`, `Stop`, `PostToolBatch`, `PreCompact` and `ConfigChange` — recorded so the
asymmetry is on file, not as a suggestion.

### Lifecycle detail

- SessionEnd `reason` carries signal beyond `other`: a clean quit gives `prompt_input_exit`.
- Every launch is accompanied by more SessionStart firings than there are conversations —
  budget for it when reasoning about "how many sessions are in this directory".

## What this means for chimera

**Identity comes from the launcher, but not from the environment.** A launcher records a
`PendingLaunch` (cwd, address, model) *before* it spawns, and the session's own start hook
claims it. This is uniform across both modes because neither lets a launcher write a complete
row afterwards: a foreground launch blocks until the session exits, and `--bg` is refused the
chance to choose an id at all. `--session-id` is still passed foreground — it makes the id
chimera chose the one claude uses everywhere — but nothing depends on it.

A claim is **consumed**, so two sessions starting in one directory can't both take an address;
the second stays unaddressed, which is the safe way to be wrong. An unclaimed record **expires**
(`LAUNCH_WINDOW`, 2 min): a launch that never produced a session would otherwise lie in wait to
hand its address to whatever started there next.

**Location is not identity.** cwd survives every transition, which makes it the right source
for *axes* (workspace/project/goal/actor), but never for granting an address. The axes and the
address are separate columns for exactly this reason. A raw `claude` in a goal worktree gets
the axes and no address: no mail, no board slot, no resume handle — and **no fence**, because
role and scope are read off the address and nothing else.

That is deliberate: a fence granted by location would be entitlement from geography wearing
a different hat. What *does* still come from cwd is occupancy — a hand-launched session counts
as an occupant, because who is writing in a directory really is a fact about the directory.

So an unaddressed session is unfenced, and the wall is the harness permission layer, not us.
The fence's value was never containment of a determined session — it is not advertising
footguns to a session that would otherwise reach for them.

**The address is the only thing that crosses a bridge.** No id survives one, and the fork
payload doesn't name its parent, so a `branched` session inherits from the newest session still
open in the same cwd — *presumed*, and logged as such. Without it, backgrounding a chat would
silently orphan its mail.

**The role and its fence come from the address**, never from the environment: `@@captain`
unfenced, `<project>@@manager` and `<project>@<goal>@<actor>` fenced to their project. An
environment stamp cannot do this job — it reaches a foreground session and nothing else (see
*Environment inheritance*), and passing a prompt is exactly what makes a launch background, so
the unattended agents would be the unfenced ones.

**`$CLAUDE_ENV_FILE` injection was deliberately not built.** It works, but the archive row
answers the same question over documented surfaces, and an optimisation is not worth a
dependency the docs contradict. Reading it as a **cross-check** is a different bargain and
was built: an absent witness costs nothing, so an undocumented channel can corroborate
identity without anything resting on it.

**Liveness needs the `(pid, create_time)` pair**, captured at SessionStart and matched later —
never an inequality. `Agent.stop` re-verifies immediately before signalling, since that is the
one place acting on a stale answer is unrecoverable.

**Occupancy is not liveness.** `occupants()` excludes non-conversations (the harness's own
`addressable` verdict, as recorded) and discounts husks via the fork-event marker. The launchers
refuse a second writer; a harness-native start never reaches them and can only be warned at
SessionStart, which cannot refuse.

**Sessions that die unheard are closed by the listers** (`reconcile`), because an open row
outranks the closed ones a resume chooses between. **Drift is caught by a doctor check**
(`harness-contract`) re-asserting the claims above against recorded rows.

## Adding a harness

The adapter answers **chimera's questions**; it is never asked to hold an opinion about
another harness's mechanics. So the contract is behaviour, not declared trivia — every claude
peculiarity above (`--session-id` working foreground but refused by `--bg`, the
`backgrounded · <short>` line, `agent_type`, `sdk-cli`, `source == 'fork'`, `claude stop`)
lives *inside* `Claude` and appears nowhere in the interface:

| chimera asks | meaning | how `Claude` answers it |
|---|---|---|
| `start(…) -> native id \| None` | launch a session and tell me its id, if you can | foreground: mints a uuid and passes `--session-id`; `--bg` refuses that flag, so it answers `None` and the hook binds the id |
| `session_id_from_env()` | am I running inside one of your sessions — which? | `CLAUDE_CODE_SESSION_ID` |
| `identity(payload, env)` | which session does this start event name? | the transcript stem, cross-checked against the payload id, `$CLAUDE_CODE_SESSION_ID` and `$CLAUDE_ENV_FILE`'s directory |
| `addressable(payload, env)` | may this session hold an address? | no `agent_type`, entrypoint ≠ `sdk-cli` |
| `lifecycle(payload)` | started, resumed, or branched from another? | `source`, mapping `fork` → branched |
| `stop(session)` | end it | `claude stop <id>` for background, SIGTERM otherwise |

Note what is *not* asked: whether a launch is allowed. An adapter reports what its registry
claims is live; which of those count as occupants is chimera's policy, applied by the launchers
(`chimera.commands.agent.occupants`). Keeping that inside the adapter meant the lower layer
importing the higher one, which is how the layering announced itself.

The line: **declare data chimera must compare against** (`platform`, `restricted`'s bypass
spellings — chimera matches user input against these), **encapsulate every decision the
harness itself makes**. A capability flag chimera would branch on is a decision wearing a
fact's clothing; push it behind a method.

A harness that can't answer `start`'s id or `session_id_from_env` degrades to payload-only
identity — still enough to record sessions, just not to guarantee them.
