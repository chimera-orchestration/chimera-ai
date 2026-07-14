# Command structure

Layout under `src/chimera/` (ordinary packages, `__init__.py` present):
- `commands/<name>.py` — flat command (`ch <name>`)
- `commands/<group>/<sub>.py` — grouped (`ch <group> <sub>`)
- tests mirror the tree, `test_` prefix: `tests/commands/<group>/test_<sub>.py`
- `__main__.py` assembles the Typer app and enables `python -m chimera`

## Each command = a pure function + a thin CLI wrapper

The function in `commands/**` is plain importable Python:
- takes its domain args + any context (e.g. `Workspace`) explicitly
- `return`s values and `raise`s ordinary exceptions — never `typer.echo` / `typer.Exit`
- built from other pure functions where possible

`__main__.py` is the only module that assembles the CLI from typer: it parses args,
injects context, renders the return value, and lets exceptions bubble to a non-zero
exit. (`completions.py` and `help.py` also import from typer, but only its click
layer — completion callbacks and the derived help tree; no command logic.)

Typer metadata always rides on `Annotated`, never a default value — this keeps
the function callable from Python:
- `goal: Annotated[str, typer.Argument()]`  ✓  default stays a real value
- `goal: str = typer.Argument()`             ✗  default becomes a Typer sentinel

## Synonyms

A group may accept synonyms for a command (e.g. `goal new` → `goal start`,
`goal cleanup` → `goal finish`, and `list` → `ls` on every group that has an `ls`).
Two rules, no exceptions:
- **`--help` shows only the canonical name** — synonyms never appear.
- **only the canonical name is logged** — a synonym must dispatch to the canonical
  command, never run as a command of its own.

Synonyms *do* tab-complete, though, so a half-typed one (`goal clea<TAB>` →
`cleanup`) can be finished — completion is the one place they surface.

`alias_group({...})` (in `__main__.py`) gives all three: pass it as the group's `cls=`.
It resolves the synonym in `get_command` (so the real command runs) and leaves
`list_commands` canonical (so `--help`/logging never see synonyms), while
`shell_complete` adds the synonyms back as completion candidates. Add one by
extending the dict. A synonym must not collide with a real command name (a test
enforces this; the canonical command would win anyway).

## One command, mutually exclusive modes

A command can cover two shapes of the same job (`ch worktree add`: goal actors via `--goal`, or
one ad-hoc branch+path) without becoming two commands. Put the dispatch — validating which
combination of arguments was given, raising `UserError` on a conflicting or incomplete one — in
the **pure function**, not the CLI wrapper. The CLI wrapper just passes through whatever it parsed.

This matters because `@logs(fn)` (see `agent-docs/logging.md`) tags a command with a single,
fixed dotted path at decoration time — if the wrapper itself branched between two different pure
functions, the logged delegate would be wrong for whichever branch didn't match the tag. Routing
the dispatch through one pure function keeps a single, always-correct delegate, and puts the mode
logic where `commands.md`'s own rule already says it belongs — tested at the function layer, not
the CLI layer.

## Shell completion

Value-taking params complete via callbacks in `chimera/completions.py`, attached with
`autocompletion=` on the shared `Annotated` types (`ProjectOpt`, `GoalOpt`, `ActorOpt`,
`ExistingGoalArg`) — a new param that names a project/goal/actor should reuse those.
Rules:
- a completer must never raise or print — swallow everything, return `[]`
- scope like the listers (narrow by flags/cwd, widen otherwise); read a typed `-p` by
  walking `ctx`/`ctx.parent` params — group callbacks (so `ctx.obj`) don't run during
  completion
- args naming something *new* (e.g. `goal start`) get no completer

## Self-documentation

Every command — group *and* leaf — sets its summary via explicit `help=` (groups use
`typer.Typer(help=…)`, leaves `@app.command(help=…)`); never a wrapper docstring (groups
can't carry one, so docstrings would split the convention). One `help=` string is the
single source: `--help` and `ch help` both derive from it.

`ch help` is the whole tree in one chunk — flat, plain text, terse, agent-optimised. It's
**derived** by walking the live command objects (`chimera/help.py`), never a hand-kept
list, so it can't drift. Default lists canonical leaf commands + summaries; `-v` adds each
command's options and synonyms; `--json` emits the structured index. A leaf with no `help=`
shows blank — a test (`test_every_command_has_a_summary`) fails on it.

`ch prime` is `ch help`'s editorial counterpart: help is the *reference* (what exists —
derived, exhaustive), prime the *orientation* (how to work here, right now — the golden path
for the scope you stand in). Its per-role templates (`chimera/prime.py`) are editorial prose,
so they *could* drift — a test (`tests/test_prime.py`) pins every backtick-cited `ch …`
command to a live leaf of the tree that role's sessions actually see: the role allowlist
prune for the listed roles (the captain skips it), then minus the human-only
`RESTRICTED_COMMANDS` every AI session loses — so prime provably never mentions fenced
capability. The role is the session's
`CHIMERA_ROLE` stamp when set, else inferred from cwd (goal worktree → agent, project dir →
manager, bare workspace → captain) — the pull path for sessions chimera didn't launch, and
for humans. The launchers also *push* the role's prime as the identity block of the launch
context — chat the captain's/manager's, the goal launchers the agent's; `ch errand` alone
keeps a bare identity sentence (see `agent-docs/workspace-layout.md`, *Launch context*) —
so sessions never have to pull it. Every template ends by signposting `ch help`.

**Terse-default `-v` hint** (the *Terse defaults signpost their depth* principle). A view that
hides detail behind `-v` (`ch help`, `ch doctor`, `ch agent ls`) must end with a one-line hint
naming the `-v` command — but only when it actually withheld something *and* `-v` wasn't given.
Never under `-v` (nothing left to reveal) and never in machine output (`--json`). So the hint
reveals what's hidden exactly when an agent would otherwise have no way to discover it: `ch help`
trails `ch help -v also lists…`; `ch doctor` reveals the count of passing checks it suppressed;
`ch agent ls` the count of stale sessions it withheld.

## Agent-restricted options

An option too risky to trust to an AI agent's own judgement (`--force`, `--dangerous`) is named
in `chimera.agent_env.RESTRICTED_OPTIONS`. When `chimera.agent_env.ai_session()` is true —
either signal: a harness marker (`running_under_ai_agent()`, currently `CLAUDECODE`) *or* a
chimera role stamp (`CHIMERA_ROLE`; launchers only ever stamp roles into AI sessions, so a
future non-claude harness with no marker of its own still can't hand the options back) —
`__main__.main()` builds the Click command tree via
`typer.main.get_command(app)` and strips any parameter matching `RESTRICTED_OPTIONS` from every
command's `.params` before invoking it (`_strip_restricted_options`) — not hidden, physically
absent, so Click's own parser, `--help`, and `ch help`/`ch help -v`/`--json` (which all read
`command.get_params(ctx)`) stop knowing the option exists. No pure function needs a check: Click
never parses the flag, so it never reaches one. A future high-risk option joins the same
frozenset rather than inventing a new mechanism. This only works because console-script entry
points point at `main()`, not `app` directly — `Typer.__call__` rebuilds an unstripped tree from
scratch on every call, so stripping has to happen on a tree we build and invoke ourselves.

`RESTRICTED_COMMANDS` (same module) is the identical strip one level up: a whole command
that only makes sense at a human's terminal (`logtail`, which blocks following the live log
until Ctrl-C) is deleted from **every** AI session's tree — captain included, unlike the
per-role allowlists, which narrow further but never grant these back (a test keeps the two
sets disjoint). Same trigger, same absence-not-admonition semantics; an agent wanting log
content reads the JSONL directly.

The `--` passthrough tail is the one place this strip can't reach — `PassthroughCommand`
splits it off before Click parses. Its fence is per-harness: each `Agent` subclass declares
its own bypass spellings (`Agent.restricted`, e.g. claude's `--dangerously-skip-permissions`),
and `chimera.commands.agent.refuse_restricted` — called by every launcher once the spec is
resolved — refuses them (never silently drops: a session launched *without* the bypass its
caller asked for would just be confusing). It triggers on the same `ai_session()` pair.

## Role-scoped commands

The same machinery one level up: where `RESTRICTED_OPTIONS` strips options, per-role command
allowlists (`chimera.agent_env.ROLE_COMMANDS`, canonical leaf paths keyed by role) strip whole
commands. The launchers themselves set the variable — every launch stamps
`role_env(role, scope)` into the session's environment (see `agent-docs/workspace-layout.md`,
*Choosing the harness and model*). When `CHIMERA_ROLE` names a listed role (`session_role()`;
empty counts as unset), `main()` prunes the tree it built (`_strip_to_role`) before invoking: a leaf not in the role's
set is deleted from its group's `commands` dict, a group emptied by that is deleted too —
absent from parsing, `--help`, `ch help` and completion alike, and a synonym dies with its
canonical target (`alias_group` resolves through the pruned dict). *Strip, don't admonish*:
anything needing a "must not" in prose is instead absent from the session's world — written
prohibitions advertise targets. `errand` rides in **both** the manager's and the agent's
allowlists — cross-project *reading* is knowledge, not capability (the same rule that leaves
listers unfenced), and its target axis carries its own containment (below). The captain has
no `ROLE_COMMANDS` entry — full tree (the
option strip still applies: any role stamp marks an AI session); an **unknown role fails hard
and early** — `ch`
refuses to run at all, before any command parses — never a silent full tree, never a silently
narrowed one. One carve-out: Click's completion dispatch (`chimera.git.completing`, the same
detection that mutes the git DEBUG trace) instead completes *nothing*, silently, exit 0 — a
completer must never raise or print, and a stale role stamp in a shell would otherwise break
every TAB; fail-closed keeps both rules standing (loud for invocations, silent for completers).
Honesty: env-based identity is a fence, not a wall (unset-able, like
`CLAUDECODE`) — the wall is the harness permission layer; the fence's real value is not
advertising footguns.

**Arg-level scope fencing** — policy the strip can't express: a command fine in-scope whose
`-p` could reach another project. The fence arms when `session_role()` is `manager` and
`role_scope()` names a project (`chimera.agent_env.fenced_project`; the agent role needs no
fence — its tree carries no `-p` anywhere — but is fenced identically anyway, its scope's
first `@` segment naming the project, since the symmetry costs one membership test and a
split). The chokepoint is `_project()` in `__main__.py` — the single funnel every
project-scoped **action** resolves through: after `resolve_project` returns,
`refuse_cross_scope` compares the **resolved** project against the fence, so an explicit
cross-scope `-p` and a cwd standing in another project refuse identically. Listers are
**never** fenced: `ls`/`goal ls`/`agent ls` resolve through `_scope()`, untouched —
cross-project listing is knowledge, not capability. There is deliberately no `-g` rule: a
goal resolves inside the already-fenced project — guaranteed because every seam a goal or
actor name enters through (an explicit `-g`/`-a`, the new-goal positionals) validates it
first (`require_valid_goal`/`require_valid_actor` in `chimera.worktrees`: one path segment,
no `@`, git's ref rules), so a traversal like `-g ../../other/worktrees/x` refuses before
any path or ref is built — and `chat -g` is stripped for managers
anyway — a decision, not an accident. The refusal is `scoped to <project>; ask the captain`
(a `UserError`, exit 1): *signpost depth, never privilege* — it states identity and
escalates, never narrating the prevented operation or a flag that would permit it.

`ch errand`'s *target* positional is the one deliberate exemption — an axis, not a hole: it
names the project dispatched *into*, never who the session acts as, so it resolves through a
dedicated helper (`__main__._foreign`) the fence never guards, whose single-caller status a
test pins (`test_foreign_has_exactly_one_caller`). `-p` keeps its fenced meaning everywhere:
an inherited `-p` reaching errand is refused, not reinterpreted. The verb's own narrow
semantics — one-shot, the `Agent.run(readonly=True)` tool wall (a bounded git-flag leak
accepted; see workspace-layout.md's Errands), the ephemeral worktree and sweep — are the
containment.

## Destructive commands preview with --dry

A command that deletes or discards (worktree/branch removal, project removal, …) must offer
`--dry`: run every discovery and safety check but mutate nothing, reporting what *would* go.
Thread `chimera.dry.Dry` through the pure function and route each mutation through it
(`dry(git, 'branch', '-D', ref)`, `dry(shutil.rmtree, path)`), so the preview shares the real
code path and can't drift from it. `--dry` previews *under whatever other flags are given*, so
it still reports a refusal on unsafe state and `--dry --force` previews a forced teardown.
Report with `dry.verb('Removed', 'Would remove')`. Read-only commands never take `--dry`.

**Launching commands preview with `--dry` too** (`agent start`/`resume`, `goal start`/`adopt`,
`review`, `chat`): everything resolves for real — scope, spec cascade, rendered context (the
file is written and logged; it's the same content-addressed artifact a real launch would use) —
but every mutation (worktree/branch setup, the harness launch) routes through the same `Dry`,
so nothing is created and nothing runs. The report names the target, then what would be
injected: harness/model, prompt, passthrough, the context sources (each glob searched with
its match count — a `(0)` names the dir a missing directive should have been in), and the
full context text. Scope guards (e.g.
chat's a-goal-never-chats refusal) still fire under `--dry` — the command would be wrong at any
time — but liveness never blocks a preview: the harness's in-launch check rides the skipped
launch, and chat's already-live-by-name guard degrades to a `note:` line on the preview (a
real launch would refuse; a preview mutates nothing, and the scope's chat being live is its
normal state).

## Testing

- Put logic depth in pure-function tests — assert on return values / raised exceptions.
- Still cover the CLI: a smoke test per command plus the wrapper's job (arg parsing,
  exit codes, output), so the CLI is proven to work.
- Manual smoke runs: `$CHIMERA_WORKSPACE` in your environment points at the user's *live*
  workspace, so a bare `uv run python -m chimera …` mutates real state (and merely unsetting
  it isn't enough — cwd walk-up can still land there). Pin a scratch workspace on the
  command itself: `CHIMERA_WORKSPACE=<scratch> uv run python -m chimera …`, scratch made
  with `ch init`. pytest is already safe (TempDir + the autouse fixture clearing the var).
