The workspace is the project working space for Chimera (default name: `lycia`).

## Layout

```
~/lycia/                        # git repo — tracks everything except gitignored dirs
  .gitignore                    # ignores: */repo/ */worktrees/
  config.yaml                   # `kind: workspace` — marks the workspace root
  routes.jsonl                  # beads prefix routing table (workspace-level)
  .beads/                       # workspace-level beads DB (goals, cross-project tasks; prefix: ws-)
  processes/                    # workspace-wide process definitions
  principles/                   # workspace-wide principles
  knowledge/                    # workspace-wide extracted knowledge (plain markdown)
  {project}/
    config.yaml                 # `kind: project` + metadata: repo path/url, beads prefix, etc.
    .beads/                     # project beads DB (project tasks, agent beads; prefix: {project}-)
    knowledge/                  # project-specific extracted knowledge (tracked)
    prompts/                    # pre-computed agent context for this project (tracked)
    principles/                 # project-specific principles (tracked)
    processes/                  # project-specific processes (tracked)
    repo/                       # gitignored — clone managed by Chimera (ch project add only)
    worktrees/                  # gitignored — one worktree per goal (agent only)
      {goal}-agent/             # git worktree on branch {goal}/agent
        .beads/redirect         # → ../../.beads (routes to project beads DB)
                                # branch {goal}/human exists but has no worktree
```

## Project types

Three types, all with the same layout above — difference is where the repo lives:

| Type | Description | repo/ |
|---|---|---|
| **working** | Actively developed; agent worktree per goal (+ a bare {goal}/human branch) | `{project}/repo/` (ch project add <url>) or external path (ch project add <path>) |
| **knowledge** | Source repo checked out for knowledge extraction | same as working |
| **reference** | No live checkout; only extracted knowledge tracked in lycia | absent |

## Locating the workspace and project

Commands resolve where they are by walking up from cwd, reading each `config.yaml`'s
`kind` (see `chimera.config`):
- workspace commands (`ch project …`) find the nearest `kind: workspace` — they refuse outside one
- project commands (`ch goal`, `ch worktree`, `ch agent`) find the nearest `kind: project`, unless `-p/--project <name>` names one under the workspace

`config.yaml` is the only marker; depth/naming is never assumed.

## Keeping a workspace healthy

`ch doctor [path]` (default cwd) walks up to the workspace root — skipping project dirs, which it
spots by the `repo:` key in their `config.yaml` — then reports drift from the current schema/layout;
`--fix` applies the repairs. It's a registry of independent checks (`chimera.commands.doctor`,
add/retire via the `CHECKS` tuple). Current checks:
- **workspace-config / project-config** — add/upgrade `config.yaml` `kind:` markers (migrates
  pre-marker workspaces and legacy `repo:`-only project configs)
- **human-worktrees** — remove leftover `{goal}-human` worktrees from the old per-actor layout when
  clean (no uncommitted changes, no unmerged commits); the bare `{goal}/human` branch survives
- **orphaned-worktrees** — prune stale git worktree registrations; flag untracked dirs under
  `worktrees/`

Reports findings and exits non-zero while any remain unresolved.

## Adding and removing projects

`ch project add <url|path>` (run anywhere in the workspace) dispatches on its argument:
- a git URL — clones into `{project}/repo/`, registers in `routes.jsonl`
- a local path — registers an existing checkout by path; repo stays in place

Both paths:
1. Create the project directory structure in lycia
2. Assign a beads prefix and append to `routes.jsonl`
3. Initialise `{project}/.beads/` as a new Dolt database

`ch project rm <name>` removes a project directory. It refuses while the project
still has goals — run `ch goal finish` on each first, or pass `--force` to finish
every goal (discarding unmerged/uncommitted work) and remove the project in one
shot. A live agent in any worktree always aborts, even with `--force`. A tracked
repo living outside the workspace is left untouched. `ch project ls` lists tracked
projects.

## Worktrees and beads isolation

Naming pattern (see core concepts): each actor gets branch `{goal}/{actor}`; agents additionally get worktree `{goal}-{actor}`.

`ch worktree add <goal> [actor…]` (the primitive; repo read from `config.yaml`) creates a branch `{goal}/{actor}` for each actor (default `human`, `agent`), but only a worktree for non-human actors:
1. `git branch --no-track {goal}/human <base>` — a bare branch, no worktree (the human checks it out where they like)
2. `git worktree add --no-track worktrees/{goal}-agent -b {goal}/agent <base>` from the project repo
3. Write `worktrees/{goal}-agent/.beads/redirect` → `../../.beads`
4. Append `.beads/` to the worktree's `.git/info/exclude` — keeps Chimera's beads invisible to the upstream project's git, even if the project also uses beads

`<base>` is the start point for all branches: `--from <ref>` if given, else the most recently committed of local `main` and `origin/main` (NOT whatever the repo currently has checked out), falling back to `HEAD` if neither exists. Branches are created with no upstream tracking.

`ch goal start <goal>` is the high-level orchestrator: it runs `worktree add` then launches the goal's agent (foreground, or background with `--prompt`). `ch goal finish <goal>` is the lifecycle name for `worktree rm` — it removes the goal's worktrees and branches.

Refuses if the repo has no commits (nothing to branch from) — including bare repos. All agents on the same goal share the project's beads DB via the redirect; no beads state leaks into upstream commits.

> Built so far: steps 1–2 + the no-commits guard. The beads redirect/exclude (steps 3–4) is planned, not yet wired.

## Beads routing

`routes.jsonl` at the workspace root maps prefixes to DB paths:

```json
{"prefix": "ws-", "path": ".beads"}
{"prefix": "chimera-", "path": "chimera/.beads"}
{"prefix": "testfixtures-", "path": "testfixtures/.beads"}
```

- Workspace-level issues: `ws-` prefix
- Per-project issues: `{project}-` prefix, isolated Dolt DB
- Cross-project references resolved via `routes.jsonl`

## What lycia's git tracks vs ignores

**Tracked:** `config.yaml`, `knowledge/`, `prompts/`, `principles/`, `processes/`, `.beads/` metadata, `routes.jsonl`

**Gitignored:** `*/repo/` (live clones), `*/worktrees/` (git worktrees with nested `.git` files)

Worktrees and clones stay inside the workspace directory for locality, but are excluded from lycia's git to avoid submodule detection.
