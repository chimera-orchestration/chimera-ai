The workspace is the project working space for Chimera (default name: `lycia`).

## Layout

```
~/lycia/                        # git repo — tracks everything except gitignored dirs
  .gitignore                    # ignores: */repo/ */worktrees/
  config.yaml                   # `kind: workspace` — marks the workspace root
  processes/                    # workspace-wide process definitions
  principles/                   # workspace-wide principles
  knowledge/                    # workspace-wide extracted knowledge (plain markdown)
  {project}/
    config.yaml                 # `kind: project` + metadata: repo path/url, etc.
    knowledge/                  # project-specific extracted knowledge (tracked)
    prompts/                    # pre-computed agent context for this project (tracked)
    principles/                 # project-specific principles (tracked)
    processes/                  # project-specific processes (tracked)
    repo/                       # gitignored — bare clone managed by Chimera (ch project add only)
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
- **actor** — `-a/--actor`, else inferred from the same dir/branch token; defaults to `agent`.

`config.yaml` `kind` is the only on-disk marker; depth/naming is never assumed. The branch is
trusted for goal/actor only when it matches the `<goal>/<actor>` shape — never for a review or
feature branch.

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
- **gitignore** — the workspace `.gitignore` carries every entry the current template ships
  (`logs/`, `*/repo/`, …); `--fix` appends any missing ones, preserving existing/custom lines.
  Reconciles workspaces created before a template entry was added
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
- **workspace-clean** — the workspace's own git repo has no uncommitted or untracked content
  (skipped when the workspace isn't a git repo; `*/repo/` and `*/worktrees/` are gitignored, so
  only tracked workspace files count). Runs last, so it sweeps up the config/gitignore edits the
  earlier `--fix` checks just made. `--fix` stages everything (`git add -A`) and commits it with a
  one-line message written by a lightweight model (`claude -p --model haiku` fed the staged diff);
  if claude can't be reached it falls back to a generic subject so the commit still happens. The
  committed branch's before/after shas are logged for recovery

Reports findings and exits non-zero while any remain unresolved.

## Adding and removing projects

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
   (`knowledge/`, `prompts/`, `principles/`, `processes/`)
2. Write `{project}/config.yaml` (`kind: project` + the repo location)

`ch project rm <name>` removes a project directory. It refuses while the project
still has goals — run `ch goal finish` on each first, or pass `--force` to finish
every goal (discarding unmerged/uncommitted work) and remove the project in one
shot. A live agent in any worktree always aborts, even with `--force`. A tracked
repo living outside the workspace is left untouched. `--dry` previews the whole
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

`<base>` is the start point for all branches: `--from <ref>` if given, else the most recently committed of local `main` and `origin/main` (NOT whatever the repo currently has checked out), falling back to `HEAD` if neither exists. Branches are created with no upstream tracking.

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

`ch goal start <goal>` is the high-level orchestrator: it runs `worktree add` then launches the goal's agent (foreground, or background when a `[prompt]` positional is given). `ch goal adopt <branch>` is the same orchestrator for *existing* work: it takes a branch already carrying commits and restructures it into the `{branch}/{human,agent}` pair (renaming `{branch}` to `{branch}/human` — git can't hold `refs/heads/{branch}` beside `refs/heads/{branch}/*`, and the rename carries any checkout's HEAD along — then splitting `{branch}/agent` off that tip), creates the agent worktree, and launches the agent. Unlike `start`, the base is the adopted branch's own tip, not main. It is idempotent: the restructure is skipped once both actor branches exist, and the worktree is reused if already checked out, so a re-run only (re)launches the agent. `ch goal finish <goal>` is the lifecycle name for `worktree rm` — it removes the goal's worktrees and branches. It sweeps **every** actor in the goal's namespace, not just the default human/agent pair: any `{goal}/{actor}` branch and any `{goal}@{actor}` worktree is discovered (see `goal_actors`) and, if the same cleanup rules hold (clean, merged), removed. It refuses while a claude session is live in *any* of the goal's worktrees, reporting each session's pid/kind/status/start/name (sessions can be invisible — see `research/claude-session-registration.md`); `--force` bypasses the liveness check as well as discarding unsaved work. `ch project rm --force` never bypasses it. `--dry` (on `worktree rm`/`goal finish`, and `project rm`) runs every discovery and safety check but deletes nothing, reporting what *would* go — pair with `--force` to preview a forced teardown. It shares the real code path (`chimera.dry.Dry` guards each mutation), so the preview can't drift from the actual run.

`ch goal sync [<goal>] --move <actor> --to <actor>` brings one actor branch up to another's work, materialising it if absent (default: `human`←`agent`). It's the logged, idempotent replacement for repointing/appending to the human branch by hand. `--move` is the branch that moves (default `human`), `--to` the branch it catches up to (default `agent`); the goal is a positional or inferred from cwd/`-g`. Over the mover↔target relationship: **creates** the mover at the target when it doesn't exist; **fast-forwards** it when strictly behind (moving its work tree too when checked out, refusing on uncommitted changes); a **no-op** when already there; leaves it when it **leads** the target; and, when the two have **diverged** (the usual state once you squash the agent's commits on the human branch), **appends** just the target commits made since the last sync. Refuses when the target is missing or `--move`/`--to` name the same branch. The before/after mover sha is logged (`goal sync: refs`) on every actual move. Once the mover branch is settled (never after a conflict), if the cwd is a clean plain checkout of the project repo (not the workspace, never an agent worktree) the mover is checked out there in place — so a human lands *on* it in one command instead of a manual `git checkout`; a dirty checkout is left with a note, and the HEAD move is logged. This is the reusable `checkout_here` primitive that `ch review` also uses.

**Append (the squash case).** A **watermark** ref `refs/chimera/synced/<goal>/<mover>` records the target sha last integrated; the new commits are `<watermark>..<target>`, cherry-picked onto the mover, so a squashed history gains the new work without re-applying what was folded in. The watermark is set on every integrating outcome. A legacy branch with no watermark is auto-seeded by matching the mover tip's *tree* to a target commit (a faithful squash preserves the tree); a squash that also carries the human's own edits matches nothing and is refused (do that first integration by hand — there's no seed flag). The append needs the mover **checked out** (bare → refused) and clean; a faithful squash applies conflict-free, but a conflict against the human's own edits is **left in the checkout** to resolve and `git cherry-pick --continue` (exit 1). A transient marker (`<git-common-dir>/chimera/appending/<goal>@<mover>`) lets a re-run tell a finished append (advance the watermark) from an aborted one (retry), and blocks a re-run while a cherry-pick is still in progress. The mover and watermark shas ride the `goal sync: refs` log line; `goal finish` sweeps a goal's watermark refs and markers.

`ch review <PR|url>` stands a goal up from a pull request and launches a pre-human review agent. It resolves the PR through `gh` (authoritative `headRefOid`); the *project* is still resolved from cwd/`-p`, so a URL naming a repo other than the resolved project's github origin is refused up front (both must carry a github identity — a local-path origin skips the check). It then fetches `refs/pull/<N>/head` into the `origin/pr/<N>` remote-tracking ref (targeted, so a missing PR ref fails cleanly), persists the refspec only after that fetch succeeds (so `git status` compares against the PR without a failed run leaving a dead refspec that bricks future fetches), verifies the fetch matches `headRefOid`, then branches `pr-<N>/{human,agent}` off that verified head with the PR ref as upstream — reusing `worktree add`, so a re-run only relaunches. The agent's prompt is the project's `prompts/review.md` (rendered with `string.Template`; `$PR $PR_URL $PR_TITLE $BASE $GOAL $PROJECT`) if present, else a packaged default, always behind a hardcoded guardrail forbidding any post to the PR — publishing stays the human's call. Like `goal sync`, a clean project-repo cwd is landed on `pr-<N>/human`.

`ch agent start` launches `claude` in an existing worktree (`--name <project>@<goal>@<actor>`); `ch agent resume` reattaches to that same session label (`claude --resume <name>`). Resume exists because `claude` has no `--cwd`: Chimera knows the worktree and sets it, so a dead session is revived in the right place from anywhere. Both run foreground, or background (`--bg`) when a `[prompt]` is given, and both refuse if a session is already live in the worktree.

Everything after a `--` on `ch agent start`, `ch agent resume`, `ch goal start` or `ch goal adopt` is forwarded verbatim to `claude` (e.g. `ch agent resume -- --dangerously-skip-permissions`, `ch goal start x -- --model opus`). The split is done before arg parsing (like git/cargo), so a flag is never mistaken for the `[prompt]` positional even when no prompt is given. Forgetting the `--` makes `claude`'s flags unknown options and errors, rather than silently misparsing.

The `--dangerous` flag (on `ch agent start`, `ch agent resume`, `ch goal start`, `ch goal adopt`) adds `--allow-dangerously-skip-permissions` so bypass-permissions mode is reachable with shift-tab — it only *enables* the mode, never activates it (the autonomous run keeps its resolved mode). It is **opt-in and off by default**: passing the flag *displaces* auto-accept from claude's shift-tab cycle (`normal → plan → bypass` instead of `normal → auto-accept → plan`), so the everyday default keeps auto-accept and only an explicit `--dangerous` pays that cost. **Agents must never pass `--dangerous` on their own — only when the user explicitly asks.** A `--bg` session is an attachable fork, not headless, and the mode's availability is fixed at *its* launch, so the flag rides on background launches too — you cycle after attaching (`claude agents attach` / `ch agent resume`). Not duplicated when a `--dangerously-skip-permissions`/`--allow-dangerously-skip-permissions` is already passed after `--`. Note: claude rejects a `--bg` launch carrying any dangerous-skip flag unless the bypass disclaimer has been accepted (`skipDangerousModePermissionPrompt` in claude settings, or accepting it once interactively).

Refuses if the repo has no commits (nothing to branch from) — including bare repos.

## What the workspace's git tracks vs ignores

**Tracked:** `config.yaml`, `knowledge/`, `prompts/`, `principles/`, `processes/`

**Gitignored:** `*/repo/` (live clones), `*/worktrees/` (git worktrees with nested `.git` files)

Worktrees and clones stay inside the workspace directory for locality, but are excluded from the workspace's git to avoid submodule detection.
