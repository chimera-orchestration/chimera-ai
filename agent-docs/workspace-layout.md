The workspace is the project working space for Chimera (default name: `lycia`).

## Layout

```
~/lycia/                        # git repo — tracks everything except gitignored dirs
  .gitignore                    # ignores: */repo/ */worktrees/
  config.yaml                   # `kind: workspace` — marks the workspace root
  processes/                    # workspace-wide processes (agent runbooks — see AGENTS.md core concepts)
  roles/                        # workspace-level role directives: roles/{role}/*.md (e.g. roles/captain/)
  principles/                   # workspace-wide principles
  knowledge/                    # workspace-wide extracted knowledge (plain markdown)
  {project}/
    config.yaml                 # `kind: project` + metadata: repo path/url, etc.
    knowledge/                  # project-specific extracted knowledge (tracked)
    prompts/                    # pre-computed agent context for this project (tracked)
    principles/                 # project-specific principles (tracked)
    processes/                  # project-specific processes (tracked)
    roles/                      # project-level role directives, layered after the workspace's (tracked)
    repo/                       # gitignored — bare repo managed by Chimera (ch project add / new)
    worktrees/                  # gitignored — one worktree per goal (agent only)
      {goal}@agent/             # git worktree on branch {goal}/agent
                                # {goal}/human (and any reviewer/pr) is materialised on demand
                                # by `ch goal sync`, never up front — no worktree
```

## Project types

Three types, all with the same layout above — difference is where the repo lives:

| Type | Description | repo/ |
|---|---|---|
| **working** | Actively developed; agent worktree per goal ({goal}/human materialised on demand) | `{project}/repo/` (ch project add <url>) or external path (ch project add <path>) |
| **knowledge** | Source repo checked out for knowledge extraction | same as working |
| **reference** | No live checkout; only extracted knowledge tracked in the workspace | absent |

## Locating the workspace, project, goal and actor

Commands resolve four axes (see `chimera.context`), each with an explicit override:

- **workspace** — `$CHIMERA_WORKSPACE` if set (the norm; it's in the user's shell profile),
  else walk up from cwd to the nearest `kind: workspace`.
- **project** — `-p/--project <name>` (under the workspace), else walk up to the nearest
  `kind: project`, else (from a checkout outside the workspace) match the git repo's identity
  against each project's `repo:`.
- **goal** — `-g/--goal`, else inferred from a managed worktree dir (`<goal>@<actor>`) or, in a
  checkout, from the branch *only* when it is exactly `<goal>/<actor>` for an existing goal; else required.
- **actor** — `-a/--actor`; defaults to `agent`.

`config.yaml` `kind` is the only on-disk marker; depth/naming is never assumed. The branch is
trusted for goal/actor only when it matches the `<goal>/<actor>` shape — never for a review or
feature branch.

## Addresses: naming a session

Those axes render into one string — the **address** — which is a session's `--name`, its
Maildir under `state/mail/`, and its `caller` on every log line. One grammar, three shapes,
always exactly three `@`-joined segments with an empty segment where a role has none
(`chimera.addresses`):

| Role | Address | Segments |
|---|---|---|
| captain | `@@captain` | project and goal both empty |
| manager | `<project>@@manager` | goal empty |
| goal actor | `<project>@<goal>@<actor>` | all three |

The uniform segment count is what makes `Address.parse` total and unambiguous — which
segments are empty picks the type, so there is exactly one string-parsing site in the
codebase and every other caller builds or holds a typed `Captain`/`Manager`/`Actor`. An
incomplete address (bare `manager`, missing its project) matches no shape and is refused
where it enters — `ch msg send` parses `to` before writing, so a malformed address can
never silently mint a mailbox nobody reads.

`manager` and `captain` are reserved: never valid as a goal actor's own name, enforced both
in `Actor`'s construction and in `require_valid_actor` off one shared `RESERVED_ACTORS`.
The captain's persona (`captain:` in the workspace config) is *not* its address — see *Chat*
below.

## Choosing the harness and model

Every launching command (`agent start`/`resume`, `goal start`/`adopt`, `review`, `chat`, `errand`) — the
one list, referred to below as *the launchers*; a test pins it to the live command tree — resolves an
`AgentSpec` (`chimera.agents.registry.resolve_spec`): which registered harness runs the session
(claude today) and which model it uses. Each field resolves independently, nearest wins:
`--harness`/`-m/--model` flags → the project `config.yaml`'s `agent:` block → the workspace's →
harness `claude`, model the harness's own default. A project standing alone (no workspace) just
loses the workspace level. An explicit `-- --model X` passthrough beats the resolved model; an
unknown harness name errors, listing what's registered.

```yaml
# config.yaml (workspace or project)
agent:
  harness: claude   # optional; must be registered
  model: opus       # optional; harness-native name
```

Every launcher also puts the session's **address** on record before spawning
(`chimera.commands.agent.record_launch`), and the session's own start hook claims it — see
`agent-docs/sessions.md`. That address is the whole of its identity: role and fence both, since
`@@captain` names no project (unfenced), while `<project>@@manager` and
`<project>@<goal>@<actor>` name theirs. It drives the per-role command strip and the arg-level
project fence (both under `agent-docs/commands.md`, *Role-scoped commands*); `--dry` previews it
as an `address:` line.

This replaced a `CHIMERA_ROLE` environment stamp, which reached a foreground session and
nothing else — a background launch runs in a pooled worker that never saw the launcher's
environment, and passing a prompt is exactly what makes a launch background. The unattended
agents were the unfenced ones. Honesty, still: the fence is a fence, not a wall — a session that
holds no address is simply unfenced, and the wall is the harness permission layer. Its value is
in not advertising footguns.

## Chat: the captain and scoped conversations

`ch chat` launches a conversation at the current scope, resolved like the listers: in a project
as `<project>@@manager` in the project dir, and at the bare workspace as the **captain** — the
workspace-level agent that directs all work (its address `@@captain`; see *Addresses* above).
The captain has no goal, branch or worktree: it
works on the workspace as a whole. Its *persona* name comes from `config.yaml` (`captain: pegasus`,
or the full form `captain: {name: …, harness: …, model: …}` to also override the agent cascade;
`ch init --captain pegasus` sets it at creation) and is cosmetic — it colours the captain's own
prime, never its address, so renaming a persona can't orphan a session or a mailbox. The captain's
context indexes workspace-wide knowledge (every project, qualified); a manager's is the
project render (see *Launch context* below for the role section both lead with).

A chat deliberately sits *alongside* whatever agent is working in the same cwd, so the
one-session-per-worktree guard is off; instead the scope's chat itself being live refuses with
an attach hint (under `--dry` a `note:` on the preview instead — a preview mutates nothing, so
a live chat never blocks it). `--resume`/`-r` revives the scope's previous (dead) chat session. `-p` overrides
the scope as usual; prompt/`--`-passthrough/`--dangerous`/`--harness`/`-m` behave as on the other
launchers. There is no goal scope: a goal already has its agent, so a pinned or explicitly
requested goal (even a `-g` no project could be resolved for) refuses, pointing at
`ch agent resume -g <goal>` to talk to the agent and `ch chat` *from the project dir* for a
side conversation — inside the goal worktree, cwd re-pins the goal, so `-p` can't escape it.

## Errands: one-shot research in a foreign project

`ch errand <target-project> "<prompt>"` dispatches a one-shot, headless, **read-only** agent
into another project and delivers its report. It is deliberately not the **Task** concept:
a Task is tracked and subordinate to a goal, and spending that noun here would leave the
real feature homeless — an errand is untracked and self-sweeping. The target is a required positional (it
tab-completes like `-p` but is deliberately not `-p`): it names the project dispatched *into*,
never who the session acts as, so it resolves through a dedicated single-caller helper
(`_foreign`, its one-caller status pinned by a test) the scope fence never guards — an
inherited `-p` is refused, not reinterpreted. A reference project (no repo checkout) refuses
up front. The lifecycle is `ch review`'s pattern compressed to one synchronous command
(`chimera.commands.errand`): an ephemeral goal `errand-<6hex>` (fresh against the existing
worktrees; branch + worktree via the goal machinery, refs on the `errand: refs` log line),
the target's rendered context and agent role stamp, then a headless blocking run
(`Agent.run`) on a guardrailed prompt. The guardrail is affirmative — identity plus "your
final message is the report, captured verbatim" — never a prohibition list: the harness's
read-only tool wall (`readonly=True`; claude maps it to `--allowedTools` — Read/Grep/Glob
plus curated git Bash) blocks Write/Edit and general Bash. Not watertight: claude's
allowlist prefix-matches, so the git commands admit git's own writing flags (e.g.
`--output`) — an accepted, bounded residual; the ephemeral worktree, the sweep and the
caller's own audit of the report are the containment. No daemon, no
polling: background the `ch errand` invocation itself for concurrency, or bound it with
`--timeout <seconds>`.

The report is delivered by *chimera*, not the errand: printed to stdout, or written to
`--out <path>` (resolved against the caller's cwd) with an `errand: result` log line binding
path/bytes/sha256 — the audit twin of `context: rendered`; the harness's `errand: run` line
binds the session id, the pointer back to the transcript. The goal is then swept through
`worktree rm`'s safety checks: a clean, trivially merged errand vanishes; one that somehow
left work behind is reported and left standing — never an errand failure, the report was
already delivered — and `--keep` opts out of the sweep deliberately. Either way the leftover
is an ordinary goal: `ch goal finish <goal> -p <target>` cleans it up. A failed run still
attempts the sweep, then exits non-zero — a sweep failure is logged (WARNING), never
displacing the run's own error. `--dry` resolves everything for real — target,
generated goal id, rendered context — and runs/writes/removes nothing. Alone among the
launchers, `errand` carries no `--dangerous` (nothing interactive to make bypass reachable
in); its `--` passthrough is still fenced by `refuse_restricted`.

## Launch context: principles inline, knowledge indexes

The same launching commands inject a rendered launch context (`chimera.agents.context`,
assembled by `assemble`). A `# Role:` section leads *every* chimera-launched session's
context: an **affirmative** identity block stating what the session is, never what it must
not do — the role's whole prime (`chimera/prime.py`), identity plus the golden-path loop,
so a session starts already knowing `ch` and its tools instead of having to guess to pull
`ch prime` itself (chat pushes the captain's/manager's, the goal launchers the agent's).
`ch errand` alone keeps a single identity sentence (`You are the agent for goal <goal> on
<project>; …`): the agent prime's commit-as-you-go would contradict its read-only wall.
The block is followed by the role's `roles/<role>/*.md` directives, inlined whole and
layered like principles: the workspace's first (the generic layer, every project's
managers/agents), then the pinned project's (its specific persona). A scope with no
project — the captain — has only the workspace layer; an absent dir on either level still
introduces. The rest follows the Principle/Knowledge split: workspace + project
`principles/*.md` inline whole (always-on, small), while `knowledge/*.md` lands as an *index*
of trigger lines (`- topic: <abs path>`) the agent reads on demand with its own tools — a
pinned project indexes only its own knowledge, an unpinned scope indexes every project's,
qualified by name. `prompts/` is *not* injected — those are hand-curated prompt templates
(e.g. `review.md`; see *Prompt templates* below).

Every inlined file sits behind a source-attribution line — `<!-- <abs path> (workspace) -->`
or `(project)` — so the session can resolve a tension between directives by layer order
(project builds on workspace), cite a directive back to its file, and propose an edit to the
right place. Source dirs are read **non-recursively**: only `*.md` files at a dir's top level
are live context — a subdir (`drafts/`, an archive) is structure, not payload. A directive
big enough to want loading on demand is knowledge misfiled: move it to `knowledge/` and let
the index carry it.

The render is a build product, never committed: it's written content-addressed to
`<workspace>/state/context/<session>-<sha8>.md` (gitignored; identical re-renders land on the
same file) and handed to the harness by path — claude gets `--append-system-prompt-file` — so
the repo and worktree stay untouched. The `context: rendered` log line binds the path, the
full sha256 and a sources map (each glob searched → the files it matched): the audit record
of exactly what a session was launched with, and of why a directive did or didn't make it in.
No workspace (a lone
project) → nothing rendered, nothing injected, no log line; with one, the role section always
renders, so every workspace launch injects context.

`ch prime` is the *pull* counterpart of this pushed context: run anywhere, it prints the
scope's role-shaped golden path (the role from the session's own address when chimera
launched it, else inferred from cwd) — the same text `ch chat` pushes — see
`agent-docs/commands.md`, *Self-documentation*.

## Prompt templates

The prompts chimera *renders for you* — `review` (the review agent's brief) and `pr` (what
the model writing a PR description is asked for). Not context: these are the text of a
launch, one file each, and they are the only things `prompts/` is read for.

Each ships with chimera (`chimera/prompts/*.md`) and is overridden **whole** by a file of
the same name in `<project>/prompts/` — nothing is merged, so the packaged text is a
starting point, not a base to extend. `chimera.commands.prompt.resolve` is the single place
that knows the cascade; `ch review`, `ch goal pr` and `ch prompt show` all read through it,
so what `show` prints is provably what a launch renders. The names come from the packaged
dir itself, so adding a template there lists, completes and copies with no second list to
update.

Holes are `string.Template` `$VAR`s (`safe_substitute` — an unknown `$` is left intact, so a
template predating a new hole still renders). What each one fills with is declared beside the
cascade (`prompt.HOLES`), which is where a *default* like `REVIEW_STEP` lives so `ch prompt
show` can print it instead of leaving it buried in the renderer; a test pins every hole a
packaged template uses to a declaration (the reverse doesn't hold — `$SOURCE` is offered to
`pr.md` and unused by it).

- `ch prompt ls` — every template and the file it currently resolves to, `(packaged)` marking
  the ones the project hasn't taken over.
- `ch prompt show <name>` — the source file, the text, and each `$hole` with the value it
  renders as (`<angle brackets>` for one only a launch can fill) plus the flag that overrides it.
- `ch prompt init <name>` — copy the packaged text into `<project>/prompts/`. Idempotent and
  it **never** clobbers: an existing override is exactly the work a re-run would destroy.
- `ch prompt edit <name>` — `init` then `$VISUAL`/`$EDITOR` (or `--editor`). Human-only
  (`RESTRICTED_COMMANDS`): blocking on an editor is a dead end for an agent, which runs
  `prompt init` and writes the file with its own tools. Neither variable set is a refusal, not
  a guess — an editor that isn't yours can be unquittable.

A manager's tree carries `ls`/`show`/`init` (its project's templates are its to tune); the
scope fence applies as to any project-scoped action.

The `-p/-g/-a` flags may appear at any level of a project-scoped command — before the group,
between group and subcommand, or after it — so `ch -p chimera goal ls`, `ch goal -p chimera ls`
and `ch goal ls -p chimera` are equivalent. A shared `_context` callback (in `chimera.__main__`)
collects them into `Overrides` on Click's `ctx.obj`; the more specific (later) position wins, and
a leaf's own flag beats any earlier one.

## Listing: scope model

The `ls` family (`chimera.context.resolve_scope` → `Scope(workspace, project|None, goal|None)`)
separates **inference** from **enumeration**: cwd/flags *infer* the axis values (reusing the
action resolvers), but enumeration always reads the workspace's managed dirs and is bounded by
the workspace. Two rules:

- **Listing widens; actions stay exact.** A read-only lister that can't pin a single project
  broadens to all of them (`CannotIdentifyProjectError` → `project=None`). The goal is pinned for
  listing only by an explicit `-g` or by *physically standing in* a managed worktree
  (`resolve_scope` uses `goal_from_worktree`, not the branch): a human checkout that merely shares
  a goal's `<goal>/<actor>` branch widens to the project, so its other agents stay visible. (The
  *actions* still infer the goal from the branch via `resolve_goal` — you're working that goal.)
  A bad explicit `--project` still raises (naming a ghost is an error). Requires a resolvable
  workspace — `$CHIMERA_WORKSPACE` from an external checkout.
- **The bare dashboard is global; the `X ls` commands are local.** `ch ls` is the overview — its
  job is to show what you *can't* already see from where you stand, so it never narrows by cwd
  (`resolve_scope(..., infer=False)`); only an explicit `-p/-g` focuses it. The scoped listers
  infer from cwd and widen when they can't pin. Each enumerates one axis, scoped by the one above
  (agents are scoped by session `cwd`, the only reliable axis — names aren't always the
  `<project>@<goal>@<actor>` triple):
  - `ch project ls` — always the workspace.
  - `ch goal ls` — the pinned project's goals (bare names), else every project's (qualified).
  - `ch agent ls` — agents under the pinned goal's worktrees (only when cwd is *in* one, or `-g`
    is given), else the project, else **every** agent on the machine (the flat global list; the
    workspace tree is `ch ls`'s job); `-p/-g` filter explicitly. A `scope:` banner heads the
    output naming what it's bounded to (`<project>@<goal>`, `<project>`, or `all agents`).
    Stale-marked sessions (registry corpses — `Session.stale`) surface only under `-v`, status
    `stale` with the reason as the detail; the default withholds them, ending with a one-line
    `-v` hint when any were in scope.
  - `ch ls` — the workspace-wide dashboard (project → goal → agent tree), the same wherever you
    run it; `-p` focuses on one project, `-g` on one goal (by name, across projects). Agents not
    under any goal/project surface as `loose` so a running agent is never hidden.

## Keeping a workspace healthy

`ch doctor [path]` (default cwd) walks up to the workspace root — skipping project dirs, which it
spots by the `repo:` key in their `config.yaml` — then reports drift from the current schema/layout;
`--fix` applies the repairs; `--verbose`/`-v` also prints the checks that pass (`[name] (ok)`).
`-c/--check <name>` (repeatable, tab-completes) limits the run — and so `--fix` — to the named
checks, always in registry order (workspace-clean still sweeps last); use it to fix one problem
while leaving the rest alone. `-x/--exclude <text>` (repeatable) is the complement: skip findings
whose check name equals or message contains `<text>` — so `-x <worktree-dir-name>` mutes one
known in-flight worktree while everything else reports and fixes. An excluded finding is never
fixed (checks consult the exclusions with the message the plain report shows, before mutating),
doesn't fail the exit code, and each drop is logged; the output ends with `(N findings excluded
by -x)` and a token that matched nothing gets a warning line.
It's a registry of independent checks (`chimera.commands.doctor`,
add/retire via the `CHECKS` tuple). Current checks:
- **workspace-config / project-config** — add/upgrade `config.yaml` `kind:` markers (migrates
  pre-marker workspaces and legacy `repo:`-only project configs)
- **workspace-dirs** — every directory the current workspace template ships (`processes/`,
  `roles/`, …) exists; derived from the template itself so it can't drift. `--fix` creates the
  dir with a `.gitkeep` (matching `ch init`), which workspace-clean then commits
- **captain** — the workspace names its captain persona (`captain:` in the workspace
  `config.yaml`) and `roles/captain/` holds at least one top-level `*.md` directive for it
  (nested files don't count — the launch render reads roles dirs non-recursively). `--fix` writes
  the literal default (`captain: captain`) onto a config that predates the feature — it never
  invents a unique persona name, that stays a human's call. Missing directives are only ever
  reported, and only once a captain is actually named, so an unnamed captain isn't flagged twice
- **occupancy-warning** — no workspace `config.yaml` still carries a `hooks.occupancy_warning`
  key. That key briefly gated a SessionStart double-occupancy warning that fired on a harmless
  harness-native attach (resuming or watching a running background job from `claude agents`) as
  readily as a genuine second writer — the check itself was removed rather than left gated, so
  the key is now dead config; `--fix` strips it (and the `hooks:` block with it, if it was the
  only entry)
- **gitignore** — the workspace `.gitignore` carries every entry the current template ships
  (`state/`, `*/repo/`, …); `--fix` appends any missing ones, preserving existing/custom lines.
  Reconciles workspaces created before a template entry was added
- **state-dir** — runtime state (the action log, session archive, rendered contexts, mailboxes)
  lives under one gitignored `state/`; `--fix` migrates the legacy layout, renaming `logs/` →
  `state/` (its `chimera.jsonl` → `state/log.jsonl`) and `comms/` → `state/mail/`. Clean-only: a
  collision (the target already exists) is reported for a human to merge, never clobbered
- **archive-schema** — `state/archive.db` is on the current session schema. The archive once
  carried searchable history, cost and summaries beside identity (agentsview does those better,
  and the conflation is what let identity go quietly wrong); `name` became `address`, and
  `addressable`/`harness_version` arrived. `--fix` rebuilds the database in place — a rebuild,
  not `ALTER … DROP COLUMN`, because the old FTS triggers reference the very columns being
  dropped — keeping every session and every event. It also applies the address rule
  retroactively: a claim survives only where the dying `manager` column proves a launcher
  stamped the session, or the axes name a goal worktree. Claims inferred from geography alone
  are dropped, since geography never entitled a session to an address
- **harness-contract** — the sessions already recorded still behave the way
  `agent-docs/sessions.md` says harnesses do. Almost everything chimera knows about a harness
  is *observed* rather than promised, so it will drift, and drift nobody notices is the
  expensive kind. Re-asserts the load-bearing claims for a SQL read and no model turn: a
  session's transcript is named after it (identity anchors on that stem precisely because it
  is documented *and* definitionally resumable), and a branched session has a plausible parent
  (a fork inherits its address from whatever else was recorded in its directory). Never
  fixable — each finding says a harness changed under us, which is a human's to read. Each run
  logs how many sessions it checked and every harness version seen, so a build this doc has
  never validated is visible too
- **human-worktrees** — remove leftover `{goal}-human` worktrees from the old per-actor layout when
  clean (no uncommitted changes, no unmerged commits); the bare `{goal}/human` branch survives
- **inert-branches** — delete a known goal's non-agent actor branch (`{goal}/human`, `reviewer`, `pr`,
  …) that's dead weight: its tip is already recoverable elsewhere — pushed (contained in a
  remote-tracking ref) or an ancestor of the local default branch — so nothing unique is lost. Most
  often the branch point an old eager `goal start` created. `--fix` deletes it (the human can
  re-materialise it any time with `ch goal sync`), logging the sha first for recovery. A branch that's
  checked out anywhere is left alone (git won't force-delete it, and it may be where a human stands),
  as is a branch with unique unpushed commits; a goal is "known" only when it has a `{goal}@agent`
  worktree, so a stray feature branch is never mistaken for a goal actor branch
- **worktree-separator** — rename legacy dash-joined `{goal}-{actor}` worktree dirs to `{goal}@{actor}`
  via `git worktree move` (keyed off each worktree's `{goal}/{actor}` branch, so the boundary is never
  guessed; a branch that isn't exactly `<goal>/<actor>` — e.g. a nested `parked/…` prefix — is left
  alone; preserves uncommitted work; humans are left to the human-worktrees check)
- **worktree-branch** — an agent worktree `{goal}@{actor}` is checked out on the branch its dir name
  implies (`{goal}/{actor}`); catches a git GUI flipping it onto the wrong branch or detaching HEAD (the
  inverse of worktree-separator: it trusts the dir name and fixes the branch). `--fix` checks the right
  branch back out, but only when the worktree is clean — a dirty switch could lose uncommitted work, so
  it's reported and left. The before/after HEAD shas are logged for recovery. When the implied branch
  is *gone* (goal finished after the work moved elsewhere, e.g. parked under a prefix), the worktree is
  a leftover: `--fix` removes it, but only when clean and on a real branch so every commit stays
  reachable; the branch and sha it held are logged so it can be recreated. A dirty or detached leftover
  is reported for a human
- **orphaned-worktrees** — prune stale git worktree registrations; flag untracked dirs under
  `worktrees/`
- **chimera-up-to-date** — chimera's own dev checkout (found by walking up from the running
  `chimera` package's own `__file__` to the nearest `.git`; for an editable install that resolves
  to the source checkout, so it works for a real globally-installed `ch` — a non-editable/wheel
  install has no `.git` nearby, so the check goes quiet) is fetched from `origin` on every run,
  check or `--fix` alike. The repo it picked is logged each run and, under `-v`, printed as a
  `note:` line. If its default branch is behind `origin/<default>`, `--fix` fast-forwards it (an
  ancestry check first proves it's a true fast-forward, never a merge); a divergent history needs
  a human, so it's reported and left; a local branch already ahead is fine and silent. A branch
  `--fix` can't move because it's checked out somewhere is reported, not forced. Only once the
  default branch is confirmed current does a local `deploy` branch, if one exists, get checked
  against it — `--fix` repoints `deploy` to match. A `deploy` that's checked out somewhere (the
  normal state of a dedicated deploy clone, where `git branch -f` can't move it) is instead
  fast-forwarded in place via `merge --ff-only`, provided that checkout is clean and the move is a
  true fast-forward; a dirty or diverged deploy checkout is reported for a human
- **workspace-env** — `$CHIMERA_WORKSPACE` is set and points at this workspace; not auto-fixable
  (never touches your shell profile) — the finding prints the `export …` line to add to
  `~/.zshrc`/`~/.bashrc`/`~/.profile`
- **shell-completion** — tab completion for `ch` is installed for `$SHELL` (zsh/bash; other or
  unset shells are skipped): either the `ch --install-completion` artifact (`~/.zfunc/_ch` /
  `~/.bash_completions/ch.sh`) or a `_CH_COMPLETE` eval line in a shell startup file; not
  auto-fixable (same no-profile-edits rule) — the finding prints both fixes
- **fblog** — `ch logtail`'s renderer (the `fblog` binary) is on the PATH; `--fix` installs it
  with brew. Without brew there's nothing to install with, so the finding just points at
  fblog's repo — not auto-fixable
- **claude-hooks** — chimera's session-capture + mail-delivery hooks are installed in the
  user's global `~/.claude/settings.json` (SessionStart/End → the archive, UserPromptSubmit →
  `ch hook deliver`). `--fix` merges them in idempotently, preserving any existing hooks while
  sweeping superseded chimera spellings (the old `ch msg drain --inject`, which surfaced only
  the mail it claimed itself — left in place it would double-inject beside the new hook).
  Machine config, not the workspace's, so doctor *is* the installer — there's no `ch hook
  install` to remember
- **bg-isolation** — the user's global `~/.claude/settings.json` sets `worktree.bgIsolation:
  "none"`, turning off Claude Code's own background-session isolation guard (added in Claude
  Code 2.1.143; `"worktree"` is its default) — the one that makes a `--bg` session call
  `EnterWorktree` before its first edit. A chimera-launched agent never needs that guard: it
  always starts inside its own `{goal}@{actor}` worktree already, never the shared checkout
  (a chimera-managed project's `repo/` is often a bare clone with no working tree to guard in
  the first place). Left at the default, the guard is pure friction on top of chimera's own
  isolation — a wasted `EnterWorktree` call at best, an agent second-guessing itself out of a
  worktree it's already isolated in at worst. `--fix` merges the setting into the same
  machine-wide settings file `claude-hooks` above installs into, for the same reason: there's
  no `claude config set` to shell out to, so a direct JSON merge is the only way in
- **workspace-clean** — the workspace's own git repo has no uncommitted or untracked content
  (skipped when the workspace isn't a git repo; `*/repo/` and `*/worktrees/` are gitignored, so
  only tracked workspace files count). Runs last, so it sweeps up the config/gitignore edits the
  earlier `--fix` checks just made. `--fix` stages everything (`git add -A`) and commits it with a
  one-line message written by a lightweight model (`claude -p --model haiku` fed the staged diff);
  if claude can't be reached it falls back to a generic subject so the commit still happens. The
  committed branch's before/after shas are logged for recovery

Reports findings and exits non-zero while any remain unresolved.

`ch doctor` is also the one command that still runs in a workspace too broken to identify a
session in — an archive awaiting migration, say. Every other command says what is wrong and
stops before parsing; doctor proceeds unidentified, and so unfenced, because it is how the
workspace gets repaired.

## Adding and removing projects

`ch project new <name>` creates a **workspace-only** project: a fresh bare repo at
`{project}/repo/` — no URL, no remote. `git init -b main` (forced — `default_branch` only
knows main/master), seeded with an empty-tree commit via plumbing (the user's own git
identity, no README) so the first `goal start` has a commit to branch from; then the same
`register()` as `project add`. Everything downstream is byte-identical to a URL-added
project; the repo's remote list is the single source of truth for whether it has
graduated — no config marker. `--checkout <path>` stands up a plain worktree of `main`
there, as `project add --checkout` does. Refuses if the project already exists.

`ch project push <url>` graduates a workspace-only project into an ordinary remote-backed
one: pushes the default branch (only — `{goal}/{actor}` branches stay local scratch)
straight to the URL *before* writing any config, so a failed push leaves zero config
behind; then `remote add origin`, `fetch --prune`, `remote set-head` to the pushed branch
(explicit, not `-a` — an empty remote's unborn `HEAD` may name a branch that doesn't exist
yet, which `-a` can't resolve), and upstream wired for the default branch only. Takes
`--dry`. Refuses when an origin already exists. `--checkout <path>` also stands up a plain
worktree of the pushed branch there once the wiring is done — graduate and get a checkout
in one command, as `project new`/`project add` offer (skipped under `--dry`).
After it, nothing distinguishes the project from a URL-added one.

`ch project checkout <path> [--branch <name>]` stands up a plain worktree of `--branch`
(default: the default branch) at `<path>` — the discoverable name for `ch worktree add
<branch> <path>`'s ad-hoc mode (below), for when the `--checkout` moment at
`new`/`add`/`push` has passed. Everything (existing vs new branch, upstream wiring,
refusing a path under `worktrees/`) is that mode's behaviour, unchanged.

`ch project add <url|path>` (run anywhere in the workspace) dispatches on its argument:
- a git URL — bare-clones into `{project}/repo/` (no working tree there; all work happens in
  goal worktrees). The fetch refspec, an initial fetch and `origin/HEAD` are set up by hand
  since `git clone --bare` skips them, so `origin/<default>` exists for `base_ref`/`default_branch`
  just as a normal clone would provide.
- a local path — registers an existing checkout by path; repo stays in place

`--checkout <path>` (URL sources only — refuses for a local path, which is already a checkout)
also stands up a plain worktree of the default branch at `<path>` in the same step, via
`ch worktree add`'s ad-hoc mode (below) — the one-command version of adding a project and then
checking out `main` somewhere to work in directly.

Both paths:
1. Create the project directory structure in the workspace
   (`knowledge/`, `prompts/`, `principles/`, `processes/`, `roles/`)
2. Write `{project}/config.yaml` (`kind: project` + the repo location)

`ch project rm <name>` removes a project directory. It refuses while the project
still has goals — run `ch goal finish` on each first, or pass `--force` to finish
every goal (discarding unmerged/uncommitted work) and remove the project in one
shot. A live agent in any worktree always aborts, even with `--force`. A tracked
repo living outside the workspace is left untouched. A workspace-only repo (under
the project dir, no remote) holding real work — history beyond `project new`'s
empty seed commit — is the sole copy of that work, so it too refuses without
`--force`: unrecoverable loss is the one failure the log can't undo. Publish it
first with `ch project push`. `--dry` previews the whole
teardown (running the same checks) without deleting anything. `ch project ls` lists
tracked projects.

## Worktrees

Naming pattern (see core concepts): each actor gets branch `{goal}/{actor}`; agents additionally get worktree `{goal}@{actor}`. The dir uses `@` (not the branch's `/`, which would nest, nor a dash, which blurs the boundary against kebab-case goals); `@` can't appear in a goal or actor, so the pair always splits cleanly.

`ch worktree add` is dual-mode — goal actors, or one ad-hoc branch at an explicit path — with the
mode chosen by which arguments are given (mutually exclusive; mixing them refuses).

**Goal mode**: `ch worktree add --goal <goal> [--actor <actor>]…` (repo read from `config.yaml`)
creates a branch `{goal}/{actor}` for each actor (default: just `agent`), but only a worktree for
non-human actors:
1. `git worktree add --no-track worktrees/{goal}@agent -b {goal}/agent <base>` from the project repo

Only the agent is created up front. The `human` branch (and any ad-hoc `reviewer`/`pr`) is **lazy** — materialised on demand by `ch goal sync`, so a short-lived spike never accrues a dead branch. Naming actors explicitly (`ch worktree add --goal <goal> --actor human --actor agent`) still creates them: `human` gets a bare branch (`git branch --no-track {goal}/human <base>`, checked out where the human likes), every other named actor gets a worktree.

`<base>` is the start point for all branches: `--from <ref>` if given, else the most recently committed of local `main` and `origin/main` (NOT whatever the repo currently has checked out); with neither present the add refuses — pass `--from <ref>`. Branches are created with no upstream tracking.

**Ad-hoc mode**: `ch worktree add <branch> <path>` checks `<branch>` out as a plain worktree at
`<path>`, which must sit outside the project's `worktrees/` — that tree is reserved for the
`{goal}@{actor}` shape doctor's checks assume; naming a path inside it refuses (use `--goal`
instead). Concretely: `ch worktree add main ~/vcs/git/chimera` stands up a normal, pushable
checkout of `main` next to (not managed by) chimera's goal worktrees.

- **Existing branch** (e.g. `main`, mirrored into a bare `repo/` by `ch project add`): checked out
  as-is, no new branch created. A bare clone's mirrored branches carry none of the
  `branch.<name>.remote`/`.merge` tracking config a normal clone sets up for free, so plain
  `git push`/`pull` would silently need `-u` forever — when `origin/<branch>` exists and no
  upstream is set yet, this wires it up once, repo-wide, so every future worktree of that branch
  inherits it too.
- **New branch**: created from `<base>` (same resolution as goal mode) with git's normal
  auto-tracking behaviour — unlike a goal actor branch, it isn't forced `--no-track`, since it
  isn't meant to be managed by `ch goal sync`.

`ch goal start <goal>` is the high-level orchestrator: it runs `worktree add` then launches the goal's agent (foreground, or background when a `[prompt]` positional is given). `ch goal adopt <branch>` is the same orchestrator for *existing* work: it takes a branch already carrying commits and restructures it into the `{branch}/{human,agent}` pair (renaming `{branch}` to `{branch}/human` — git can't hold `refs/heads/{branch}` beside `refs/heads/{branch}/*`, and the rename carries any checkout's HEAD along — then splitting `{branch}/agent` off that tip), creates the agent worktree, and launches the agent. Unlike `start`, the base is the adopted branch's own tip, not main. It is idempotent: the restructure is skipped once both actor branches exist, and the worktree is reused if already checked out, so a re-run only (re)launches the agent. `ch goal finish <goal>` is the lifecycle name for `worktree rm` — it removes the goal's worktrees and branches. It sweeps **every** actor in the goal's namespace, not just the default human/agent pair: any `{goal}/{actor}` branch and any `{goal}@{actor}` worktree is discovered (see `goal_actors`) and, if the same cleanup rules hold (clean, merged), removed. Every problem is gathered before refusing — agent sessions live in the goal's worktrees (from *any* registered harness: `chimera.commands.agent.live`, pid-verified via `Agent.live`; each reported with its pid/kind/status/start/name, since sessions can be invisible to the registry), dirty worktrees, unmerged branches — so a single refusal names them all; `--force` stops any live sessions first (`ch agent stop`'s machinery — a session that can't be stopped still refuses) as well as discarding unsaved work. `ch project rm --force` never stops or bypasses live sessions. `--dry` (on `worktree rm`/`goal finish`, and `project rm`) runs every discovery and safety check but deletes nothing, reporting what *would* go — pair with `--force` to preview a forced teardown. It shares the real code path (`chimera.dry.Dry` guards each mutation), so the preview can't drift from the actual run.

`ch goal rename <old> <new>` (synonym: `goal mv`) renames a goal across everything local: every `{old}/{actor}` branch (`git branch -m`, which carries any checkout's HEAD *and* the branch's `branch.<name>.*` config section along), every registered `{old}@{actor}` worktree (`git worktree move`), and the goal's sync state — watermark refs and append markers. It refuses while an agent is live in any of the goal's worktrees, on a collision (an actor branch/worktree already under `<new>`, or a bare branch `<new>` blocking the `new/*` namespace — `ch goal adopt` that first), and on a name git or the `@` separator can't hold. Remote branches are **never touched**: a `<remote>/{old}/{actor}` ref, and an upstream still tracking the old name on the remote, are each warned about — renaming the remote side is the human's call. Idempotent: each actor's branch/worktree moves only while still under the old name, so a rename that died half-way completes on re-run. The renamed refs are logged before/after (`goal rename: refs`), the worktree moves on `goal rename: worktrees`; an unregistered dir under `worktrees/` is left in place with a warning (doctor's problem). When your cwd was inside a moved worktree, the new path is printed to `cd` back into.

`ch goal sync [<goal>] --move <actor> --to <actor>` brings one actor branch up to another's work, materialising it if absent (default: `human`←`agent`). It's the logged, idempotent replacement for repointing/appending to the human branch by hand. `--move` is the branch that moves (default `human`), `--to` the branch it catches up to (default `agent`); the goal is a positional or inferred from cwd/`-g`. Passing only one of `--move`/`--to` infers the other from the goal's existing actor branches (any actor, not just `human`/`agent`): the one branch that isn't the actor you gave, when there's exactly one such candidate. With more than one — a goal that already has a third actor (`reviewer`, a second agent, …) — inference is refused, listing the candidates, so you pick with the other flag instead of being silently pointed at the wrong one. Over the mover↔target relationship: **creates** the mover at the target when it doesn't exist; **fast-forwards** it when strictly behind (moving its work tree too when checked out, refusing on uncommitted changes); a **no-op** when already there; leaves it when it **leads** the target; and, when the two have **diverged** (the usual state once you squash the agent's commits on the human branch), **appends** just the target commits made since the last sync. `--force` resolves a divergence the blunt way instead: the mover is repointed onto the target's tip, its own commits discarded (count and shas on the `goal sync: refs` log line, so they're restorable) — for when there's no integration record to append from, or the target was rebased and a replay would only conflict; it never touches a mover that merely leads, and still refuses on uncommitted changes. It also cleans up a broken append on the way through: a conflicted append of sync's own is aborted, and a stranded cherry-pick sequence in the mover's clean checkout is quit, before the repoint. Refuses when the target is missing or `--move`/`--to` name the same branch. The before/after mover sha is logged (`goal sync: refs`) on every actual move. Once the mover branch is settled (never after a conflict), if the cwd is a clean plain checkout of the project repo (not the workspace, never an agent worktree) the mover is checked out there in place — so a human lands *on* it in one command instead of a manual `git checkout`; a dirty checkout is left with a note, and the HEAD move is logged. An append that finds the mover checked out *nowhere* claims that same cwd up front — the mover is materialised there before the replay, instead of refusing with a manual-checkout errand (a dirty cwd still refuses; an unusable one falls back to the refusal). This is the reusable `checkout_here` primitive that `ch review` also uses.

**Append (the squash case).** A **watermark** ref `refs/chimera/synced/<goal>/<mover>` records the target sha last integrated; the new commits are `<watermark>..<target>`, cherry-picked onto the mover, so a squashed history gains the new work without re-applying what was folded in. A range carrying merge commits is refused — once the target has rebased onto or merged in other work, the range sweeps in history that isn't the target's own (and cherry-pick can't replay a merge); `--force` is the way through. The watermark is set on every integrating outcome. A legacy branch with no watermark is auto-seeded by matching the mover tip's *tree* to a target commit (a faithful squash preserves the tree); a squash that also carries the human's own edits matches nothing and is refused (do that first integration by hand — there's no seed flag). The append needs the mover **checked out** (bare → refused) and clean; a faithful squash applies conflict-free, but a conflict against the human's own edits is **left in the checkout** to resolve and `git cherry-pick --continue` (exit 1). A transient marker (`<git-common-dir>/chimera/appending/<goal>@<mover>`) lets a re-run tell a finished append (advance the watermark) from an aborted one (retry), and blocks a re-run while a cherry-pick is still in progress. Any cherry-pick already live in the mover's checkout — even one sync didn't start — blocks an append, and a replay that dies without leaving a conflict to resolve is rolled back, never left half-applied. The mover and watermark shas ride the `goal sync: refs` log line; `goal finish` sweeps a goal's watermark refs and markers.

`ch goal merge <goal> [--into <branch>]` is the manager's finish-up: land a finished goal on
`--into` (default: the repo's default branch) and clean everything away. It picks the goal's
**source** branch — the actor branch containing every other actor's work (`is_merged`, so a
squashed human branch still counts; equivalent tips prefer `human`, the curated history) —
fast-forwards the base to its tip, moves any plain checkout sitting on a goal branch onto the
landed base (the sweep couldn't delete a checked-out branch, and that's where its human wants
to be anyway), stops live agent sessions in the goal's worktrees, then sweeps branches and
worktrees via `goal finish`'s machinery. Everything refuses *before* anything moves: actors
that have diverged (no branch contains the others — `ch goal sync` first, or `--force` to
land the newest-committed and discard the rest; the `--force` hint is dropped for AI
sessions, whose trees don't carry the flag), a dirty worktree or plain checkout, a live
session with no pid to signal, a base that isn't a local branch (`origin/main` would DWIM
through every check and then mint a junk `refs/heads/origin/main`), and a base with commits
of its own — integrating those is rebase work for the goal's worktree
(`git rebase main` there, or however you rebase), deliberately never automated here and never
forced: `--force` covers discarding *goal-side* work (recoverable from the ref log), but a
non-fast-forward base move would discard `main`'s own commits. The dirty check runs *again*
once the agents are stopped — work written between the first check and the SIGTERM landing
refuses rather than being force-swept. Idempotent: a re-run after a
half-done landing finds the work contained and carries on with the cleanup. `--dry` previews
the whole landing — merge, checkout moves, agent stops, sweep — changing nothing. The source
choice lands `goal merge: source`; the base move `goal merge: refs`.

`ch goal pr <goal> [--into <branch>] [--draft] [--to <remote>]` is `goal merge`'s
remote-review sibling: the same source selection, but the source's tip is pushed to `--to`
(default: the config cascade's `pr: {remote: …}` — project, then workspace — then `origin`;
must name an existing remote or it refuses) as branch `<goal>` (the actor suffix is local
plumbing; the goal name is the publication) and a PR opened via `gh` against `--into`
(default: the repo's default branch). The base must already be on origin whatever `--to`
names — the PR always targets origin's branch, and the goal's commits are counted against
origin's view of it — so a local-only base refuses (push it first) rather than failing
server-side after the push. A non-origin `--to` is the fork workflow (origin readable but
not writable): the PR opened is *cross-repo*, its head `<fork-owner>:<goal>` with the owner
read from the remote's URL (a remote whose URL names no owner — a local path — refuses), and
the already-open check filters by that owner so origin's own branch of the same name never
shadows the fork's; `gh` itself is pinned to origin's repo (`--repo`, host-qualified) whenever
origin's URL carries one, since with a second github remote around it refuses to infer a base
repo. A stale `<goal>/<actor>` branch on the push remote (left from an earlier by-hand PR)
would block creating `<goal>` — git can't hold `refs/heads/<goal>` beside
`refs/heads/<goal>/*` — so one contained in the PR's base is deleted (sha logged for
recovery) and one carrying unique work refuses, naming the exact ref. A push origin denies
for lack of write access lands as a clean error hinting at `--to` and the repo's other
remotes; the push always runs from the project repo, never an agent worktree, so
HEAD-sensitive pre-push hooks see the repo itself. Nothing local is deleted or stopped —
the goal keeps working until the PR lands, after which `goal merge` (or `goal finish`) cleans
up as usual. Title and body: a single-commit branch reuses its subject and body verbatim —
the same content GitHub itself prefills from a lone commit, computed locally so `--dry` can
preview it. A multi-commit branch's description is *written* by a model
(`claude -p --model haiku`, doctor's workspace-clean pattern) from the project's own
`prompts/pr.md` when present, else the packaged default — the customisation point: a
project's template encodes its own PR dance (required sections, ticket-linking conventions),
the default asks for a succinct *why* with any referenced tickets/issues/threads linked and
forbids restating the diff or commit list. Template holes: `$PROJECT $GOAL $BASE $SOURCE
$COMMITS` (the full messages, oldest first); first output line is the title, the rest the
body. A model failure or empty answer refuses with the `gh pr create` line to run by hand —
never a placeholder description. The written description is cached in the repo's shared git
dir keyed by the exact prompt and reused while that's unchanged — the title/body a `--dry`
previewed are byte-for-byte what the later run ships, never a fresh model run's different
words (`goal pr: description` logs the path, key and whether it was reused; `goal finish`
sweeps the cache with the goal's other transient markers). Idempotent: a re-run re-pushes
(git refuses a non-fast-forward, e.g. after a rebase — resolve by hand) and reports an
already-open PR instead of duplicating it — found *before* any composing, so no model run is
spent on a description the open PR already carries; both respect `--to`. The pushed
remote-tracking ref rides `goal pr: refs`; the PR lands `goal pr: opened`/`goal pr:
existing`. `--dry` resolves everything — source, commits, remote and head spec, title,
body — and pushes and opens nothing.

`ch agent stop [-g <goal>] [-a <actor>]` stops the live agent session in a goal's worktree:
SIGTERM to its pid, waiting (10s) for it to exit — never SIGKILL; a session that won't die,
or reports no pid to signal, is refused for a human to inspect, as is a goal/actor with no
worktree at all (a typo must never read as "nothing running"). `goal merge` — and a forced
`goal finish`/`worktree rm` — call the same machinery before their sweeps. `--dry` previews
which sessions would be stopped.

`ch review <PR|url>` stands a goal up from a pull request and launches a pre-human review agent. A project with no `origin` at all (workspace-only, not yet pushed) is refused up front, pointing at `ch project push`. It resolves the PR through `gh` (authoritative `headRefOid`); the *project* is still resolved from cwd/`-p`, so a URL naming a repo other than the resolved project's github origin is refused up front (both must carry a github identity — a local-path origin skips the check). The URL needn't be github's: any review tool's URL that embeds `owner/repo` and the PR number in its path (reviewable.io, graphite, …) works generically — the origin's slug is located in the path and the first numeric segment after it is the number, no per-tool table; a URL not naming the origin's repo (or a local-path origin, with no slug to match) is refused with a pointer to pass the number. It then fetches `refs/pull/<N>/head` into the `origin/pr/<N>` remote-tracking ref (targeted, so a missing PR ref fails cleanly), persists the refspec only after that fetch succeeds (so `git status` compares against the PR without a failed run leaving a dead refspec that bricks future fetches), verifies the fetch matches `headRefOid`, then branches `pr-<N>/{human,agent}` off that verified head with the PR ref as upstream — reusing `worktree add`, so a re-run only relaunches. The agent's prompt is the project's `prompts/review.md` (rendered with `string.Template`; `$PR $PR_URL $PR_TITLE $BASE $GOAL $PROJECT $REVIEW`) if present, else a packaged default, always behind a hardcoded guardrail forbidding any post to the PR — publishing stays the human's call. `--review <instruction>` fills `$REVIEW` — the one step whose *how* is a per-PR call (which review command, or none), leaving the template's orientation and write-up instructions alone; unpassed it renders `prompt.REVIEW_STEP` (`Run /review …`). A template carrying no `$REVIEW` refuses the flag rather than dropping it — a flag that silently does nothing is the dead end `--dry` and this refusal both exist to prevent — and renders unchanged without it. See *Prompt templates* below for `ch prompt`, which prints and copies them. Like `goal sync`, a clean project-repo cwd is landed on `pr-<N>/human`. `--no-agent` stops after that checkout — branches, worktree and upstream all stand, but no agent launches (the output hints both follow-ups: `ch agent start -g <goal>` for an agent, re-running `ch review <N>` for the standard review); the agent-only knobs (`--dangerous`, `-- …` passthrough) are refused with it.

`ch agent start` launches `claude` in an existing worktree (`--name <project>@<goal>@<actor>`); `ch agent resume` revives that session — never attaching to one still running (that's refused up front, below), always continuing a dead one from its transcript. Identity comes from the **archive**, not the name: the address `(project, goal, actor)` resolves to its newest archived session — live or dead — and resume revives by that immutable native id, re-asserting the canonical `--name` (registry names are mutable; a rename in claude's own UI must neither orphan the session nor survive the resume). Only when the archive has never seen the address (or there's no workspace to hold one) does it fall back to `claude --resume <name>`. Liveness and pids stay with the registry (`agent stop` is keyed by worktree cwd, never name). Resume exists because `claude` has no `--cwd`: Chimera knows the worktree and sets it, so a dead session is revived in the right place from anywhere. Both run foreground, or background (`--bg`) when a `[prompt]` is given, and both refuse if a session is already live in the worktree — the exclusive-launch guard every goal-worktree launcher (`agent start`/`resume`, `goal start`/`adopt`, `review`) takes; only `ch chat` opts out, and it refuses goal scopes anyway. A harness-native attach or revive — the `claude agents` browser, a raw `claude --resume` run in the worktree — never reaches those launchers, so it can't be refused there; a SessionStart-level warning for this case was tried and removed **twice** (`ch doctor`'s `occupancy-warning` check sweeps its config remnant) — it fired on a harmless attach into a worktree its own background job already occupied as readily as a genuine second writer, and the second attempt added a cost the first didn't have: answering "is anyone else here" means asking the harness's own registry, i.e. spawning `claude agents` from inside claude's start hook, on every session start. The guard those launchers already give is what stands; a third attempt needs a way to tell an attach from a second writer, which no payload currently offers.

Everything after a `--` on any of the launchers (the list under "Choosing the harness and model") is forwarded verbatim to `claude` (e.g. `ch agent resume -- --dangerously-skip-permissions`, `ch goal start x -- --model opus`). The split is done before arg parsing (like git/cargo), so a flag is never mistaken for the `[prompt]` positional even when no prompt is given. Forgetting the `--` makes `claude`'s flags unknown options and errors, rather than silently misparsing.

The `--dangerous` flag (on every launcher except `errand`, whose headless print mode has no interactive permission cycle to make the mode reachable in) adds `--allow-dangerously-skip-permissions` so bypass-permissions mode is reachable with shift-tab — it only *enables* the mode, never activates it (the autonomous run keeps its resolved mode). It is **opt-in and off by default**: passing the flag *displaces* auto-accept from claude's shift-tab cycle (`normal → plan → bypass` instead of `normal → auto-accept → plan`), so the everyday default keeps auto-accept and only an explicit `--dangerous` pays that cost. **Agents must never pass `--dangerous` on their own — only when the user explicitly asks.** Under
Claude Code specifically this is enforced at the CLI level, not just convention: `--dangerous` (and
`--force`) are stripped from the command tree entirely, so passing them fails with "no such option"
— see `agent-docs/commands.md`'s "Agent-restricted options". A `--bg` session is an attachable fork, not headless, and the mode's availability is fixed at *its* launch, so the flag rides on background launches too — you cycle after attaching (via the harness's agent view, `claude agents`; `ch agent resume` revives *dead* sessions and refuses while one is live). Not duplicated when a `--dangerously-skip-permissions`/`--allow-dangerously-skip-permissions` is already passed after `--`. Note: claude rejects a `--bg` launch carrying any dangerous-skip flag unless the bypass disclaimer has been accepted (`skipDangerousModePermissionPrompt` in claude settings, or accepting it once interactively). The `--` passthrough is fenced separately: it is split off before Click parses, so the Click-level strip can't see it — instead each harness declares its own bypass spellings (`Agent.restricted`, e.g. claude's `--dangerously-skip-permissions`) and every launcher refuses them at launch (`refuse_restricted`) when chimera is driven by an AI agent.

Refuses if the repo has no commits (nothing to branch from) — including bare repos.

## What the workspace's git tracks vs ignores

**Tracked:** `config.yaml`, `knowledge/`, `prompts/`, `principles/`, `processes/`, `roles/`

**Gitignored:** `*/repo/` (live clones), `*/worktrees/` (git worktrees with nested `.git` files)

Worktrees and clones stay inside the workspace directory for locality, but are excluded from the workspace's git to avoid submodule detection.
