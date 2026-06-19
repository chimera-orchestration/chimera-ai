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
    repo/                       # gitignored — clone managed by Chimera (ch project add only)
    worktrees/                  # gitignored — one worktree per goal (agent only)
      {goal}@agent/             # git worktree on branch {goal}/agent
                                # branch {goal}/human exists but has no worktree
```

## Project types

Three types, all with the same layout above — difference is where the repo lives:

| Type | Description | repo/ |
|---|---|---|
| **working** | Actively developed; agent worktree per goal (+ a bare {goal}/human branch) | `{project}/repo/` (ch project add <url>) or external path (ch project add <path>) |
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
It's a registry of independent checks (`chimera.commands.doctor`,
add/retire via the `CHECKS` tuple). Current checks:
- **workspace-config / project-config** — add/upgrade `config.yaml` `kind:` markers (migrates
  pre-marker workspaces and legacy `repo:`-only project configs)
- **gitignore** — the workspace `.gitignore` carries every entry the current template ships
  (`logs/`, `*/repo/`, …); `--fix` appends any missing ones, preserving existing/custom lines.
  Reconciles workspaces created before a template entry was added
- **human-worktrees** — remove leftover `{goal}-human` worktrees from the old per-actor layout when
  clean (no uncommitted changes, no unmerged commits); the bare `{goal}/human` branch survives
- **worktree-separator** — rename legacy dash-joined `{goal}-{actor}` worktree dirs to `{goal}@{actor}`
  via `git worktree move` (keyed off each worktree's `{goal}/{actor}` branch, so the boundary is never
  guessed; preserves uncommitted work; humans are left to the human-worktrees check)
- **orphaned-worktrees** — prune stale git worktree registrations; flag untracked dirs under
  `worktrees/`
- **workspace-env** — `$CHIMERA_WORKSPACE` is set and points at this workspace; not auto-fixable
  (never touches your shell profile) — the finding prints the `export …` line to add to
  `~/.zshrc`/`~/.bashrc`/`~/.profile`
- **shell-completion** — tab completion for `ch` is installed for `$SHELL` (zsh/bash; other or
  unset shells are skipped): either the `ch --install-completion` artifact (`~/.zfunc/_ch` /
  `~/.bash_completions/ch.sh`) or a `_CH_COMPLETE` eval line in a shell startup file; not
  auto-fixable (same no-profile-edits rule) — the finding prints both fixes

Reports findings and exits non-zero while any remain unresolved.

## Adding and removing projects

`ch project add <url|path>` (run anywhere in the workspace) dispatches on its argument:
- a git URL — clones into `{project}/repo/`
- a local path — registers an existing checkout by path; repo stays in place

Both paths:
1. Create the project directory structure in the workspace
   (`knowledge/`, `prompts/`, `principles/`, `processes/`)
2. Write `{project}/config.yaml` (`kind: project` + the repo location)

`ch project rm <name>` removes a project directory. It refuses while the project
still has goals — run `ch goal finish` on each first, or pass `--force` to finish
every goal (discarding unmerged/uncommitted work) and remove the project in one
shot. A live agent in any worktree always aborts, even with `--force`. A tracked
repo living outside the workspace is left untouched. `ch project ls` lists tracked
projects.

## Worktrees

Naming pattern (see core concepts): each actor gets branch `{goal}/{actor}`; agents additionally get worktree `{goal}@{actor}`. The dir uses `@` (not the branch's `/`, which would nest, nor a dash, which blurs the boundary against kebab-case goals); `@` can't appear in a goal or actor, so the pair always splits cleanly.

`ch worktree add <goal> [actor…]` (the primitive; repo read from `config.yaml`) creates a branch `{goal}/{actor}` for each actor (default `human`, `agent`), but only a worktree for non-human actors:
1. `git branch --no-track {goal}/human <base>` — a bare branch, no worktree (the human checks it out where they like)
2. `git worktree add --no-track worktrees/{goal}@agent -b {goal}/agent <base>` from the project repo

`<base>` is the start point for all branches: `--from <ref>` if given, else the most recently committed of local `main` and `origin/main` (NOT whatever the repo currently has checked out), falling back to `HEAD` if neither exists. Branches are created with no upstream tracking.

`ch goal start <goal>` is the high-level orchestrator: it runs `worktree add` then launches the goal's agent (foreground, or background when a `[prompt]` positional is given). `ch goal adopt <branch>` is the same orchestrator for *existing* work: it takes a branch already carrying commits and restructures it into the `{branch}/{human,agent}` pair (renaming `{branch}` to `{branch}/human` — git can't hold `refs/heads/{branch}` beside `refs/heads/{branch}/*`, and the rename carries any checkout's HEAD along — then splitting `{branch}/agent` off that tip), creates the agent worktree, and launches the agent. Unlike `start`, the base is the adopted branch's own tip, not main. It is idempotent: the restructure is skipped once both actor branches exist, and the worktree is reused if already checked out, so a re-run only (re)launches the agent. `ch goal finish <goal>` is the lifecycle name for `worktree rm` — it removes the goal's worktrees and branches. It refuses while a claude session is live in the agent worktree, reporting each session's pid/kind/status/start/name (sessions can be invisible — see `research/claude-session-registration.md`); `--force` bypasses the liveness check as well as discarding unsaved work. `ch project rm --force` never bypasses it.

`ch agent start` launches `claude` in an existing worktree (`--name <project>@<goal>@<actor>`); `ch agent resume` reattaches to that same session label (`claude --resume <name>`). Resume exists because `claude` has no `--cwd`: Chimera knows the worktree and sets it, so a dead session is revived in the right place from anywhere. Both run foreground, or background (`--bg`) when a `[prompt]` is given, and both refuse if a session is already live in the worktree.

Everything after a `--` on `ch agent start`, `ch agent resume`, `ch goal start` or `ch goal adopt` is forwarded verbatim to `claude` (e.g. `ch agent resume -- --dangerously-skip-permissions`, `ch goal start x -- --model opus`). The split is done before arg parsing (like git/cargo), so a flag is never mistaken for the `[prompt]` positional even when no prompt is given. Forgetting the `--` makes `claude`'s flags unknown options and errors, rather than silently misparsing.

Every launch adds `--allow-dangerously-skip-permissions` automatically so bypass-permissions mode stays reachable with shift-tab even when auto mode is unavailable — it only *enables* the mode, never activates it (the autonomous run keeps its resolved mode). A `--bg` session is an attachable fork, not headless, and the mode's availability is fixed at *its* launch, so the flag rides on background launches too — you cycle after attaching (`claude agents attach` / `ch agent resume`). Not duplicated when a `--dangerously-skip-permissions`/`--allow-dangerously-skip-permissions` is already passed after `--`. Note: claude rejects a `--bg` launch carrying any dangerous-skip flag unless the bypass disclaimer has been accepted (`skipDangerousModePermissionPrompt` in claude settings, or accepting it once interactively).

Refuses if the repo has no commits (nothing to branch from) — including bare repos.

## What the workspace's git tracks vs ignores

**Tracked:** `config.yaml`, `knowledge/`, `prompts/`, `principles/`, `processes/`

**Gitignored:** `*/repo/` (live clones), `*/worktrees/` (git worktrees with nested `.git` files)

Worktrees and clones stay inside the workspace directory for locality, but are excluded from the workspace's git to avoid submodule detection.
