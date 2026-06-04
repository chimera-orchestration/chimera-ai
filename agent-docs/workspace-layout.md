The workspace is the project working space for Chimera (default name: `lycia`).

## Layout

```
~/lycia/                        # git repo — tracks everything except gitignored dirs
  .gitignore                    # ignores: */repo/ */worktrees/
  routes.jsonl                  # beads prefix routing table (workspace-level)
  .beads/                       # workspace-level beads DB (goals, cross-project tasks; prefix: ws-)
  processes/                    # workspace-wide process definitions
  principles/                   # workspace-wide principles
  knowledge/                    # workspace-wide extracted knowledge (plain markdown)
  {project}/
    config.yaml                 # project metadata: type, repo path/url, beads prefix, etc.
    .beads/                     # project beads DB (project tasks, agent beads; prefix: {project}-)
    knowledge/                  # project-specific extracted knowledge (tracked)
    prompts/                    # pre-computed agent context for this project (tracked)
    principles/                 # project-specific principles (tracked)
    processes/                  # project-specific processes (tracked)
    repo/                       # gitignored — clone managed by Chimera (ch add only)
    worktrees/                  # gitignored — one worktree per goal × role
      {goal}-human/             # git worktree on branch {goal}/human
        .beads/redirect         # → ../../.beads (routes to project beads DB)
      {goal}-agent/             # git worktree on branch {goal}/agent
        .beads/redirect         # → ../../.beads
```

## Project types

Three types, all with the same layout above — difference is where the repo lives:

| Type | Description | repo/ |
|---|---|---|
| **working** | Actively developed; human + agent worktrees per goal | `{project}/repo/` (ch add) or external path (ch track) |
| **knowledge** | Source repo checked out for knowledge extraction | same as working |
| **reference** | No live checkout; only extracted knowledge tracked in lycia | absent |

## Adding projects

- `ch add <git-url>` — clones into `{project}/repo/`, registers in `routes.jsonl`
- `ch track <path>` — registers an existing checkout by path; repo stays in place

Both commands:
1. Create the project directory structure in lycia
2. Assign a beads prefix and append to `routes.jsonl`
3. Initialise `{project}/.beads/` as a new Dolt database

## Worktrees and beads isolation

`ch goal new <goal>` (run in the project dir; repo read from `config.yaml`) creates one worktree per role. For `role` in `human`, `agent`:
1. `git worktree add worktrees/{goal}-{role} -b {goal}/{role} <base>` from the project repo
2. Write `worktrees/{goal}-{role}/.beads/redirect` → `../../.beads`
3. Append `.beads/` to the worktree's `.git/info/exclude` — keeps Chimera's beads invisible to the upstream project's git, even if the project also uses beads

`<base>` is the start point for the new branches: `--branch <ref>` if given, else the most recently committed of local `main` and `origin/main` (NOT whatever the repo currently has checked out), falling back to `HEAD` if neither exists.

Refuses if the repo has no commits (nothing to branch from) — including bare repos. All agents on the same goal share the project's beads DB via the redirect; no beads state leaks into upstream commits.

> Built so far: step 1 + the no-commits guard. The beads redirect/exclude (steps 2–3) is planned, not yet wired.

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
