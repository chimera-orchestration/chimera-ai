# Agent Instructions


## What is Chimera?

Chimera orchestrates AI agents working on goals across projects. It manages a workspace directory tree (e.g. `~/lycia`) and provides the `ch` CLI.

**Core concepts** (use this terminology consistently). Some name design intent that
isn't built yet — the vocabulary stands regardless; what's implemented is the CLI you can run today.
- **Workspace** - a directory tree managed by Chimera where all work is done (default name: `lycia`)
- **Project** — a project managed by Chimera within a workspace
- **Goal** — a thing that needs doing (e.g. "implement feature X")
- **Task** — a tracked unit of work, discovered while planning or executing a goal
- **Errand** — a one-shot, read-only, headless agent dispatched into another project to fetch a report (`ch errand`); its goal (`errand-<id>`) is ephemeral — swept as soon as the report is delivered
- **Actor** — a participant in a goal: a **human** or an **agent**. Each works on its own **branch**; an agent additionally always works in a **worktree** (a human checks their branch out wherever they like).
- **Branch** — a git branch named `<goal>/<actor>`
- **Worktree** — a git worktree named `<goal>@<actor>` (agents only)
- **Principle** — context an agent must always load before beginning work (inlined at launch)
- **Knowledge** — named, versioned context loaded on demand (e.g. "load knowledge for testfixtures")
- **Reference** — a project used only for tracking knowledge
- **Service** — a long-running system process managed by Chimera (e.g. a tmux session or docker container); distinct from the workflow concept "Process"
- **Agent** — a service running an AI agent instance (e.g. a Claude Code session) managed by Chimera; works in a **worktree** (only the captain works on the workspace itself)
- **Role** — the function an agent is launched as; a role's directives live in the workspace `roles/{role}/` dir, and each workspace names its own instance of a role (the concept/instance split mirrors workspace/lycia)
- **Captain** — the role of the workspace-level agent chatted with to direct all work across the workspace; no goal, branch or worktree — it works on the workspace as a whole (lycia's captain is named *pegasus*)

## Principles

When implementing chimera, the following principles must be adhered to:

- **Everything must be a CLI** – Every action is a CLI command thinly wrapping a pure, importable function. Compose from pure functions; put logic tests at the function layer and still cover the CLI itself. More in @agent-docs/commands.md.
- **Every CLI action must be logged** – Every action must be logged to the log file, with enough of what it examined, decided and changed that the log alone can debug the run — outcomes only, never spew; mutating a git ref must log its before/after shas first. More in @agent-docs/logging.md.
- **Every CLI action must self documenting** – `--help` and `ch {action} help` must both work & be identical and provide terse, optimised for agents. More in @agent-docs/documentation.md.
- **Terse defaults signpost their depth** – any view that hides detail behind `--verbose` must, when it actually hid something and `-v` wasn't given, end with a one-line hint naming the `-v` command to reveal it. No silent dead ends. More in @agent-docs/commands.md.
- **Document everything** - Read @agent-docs/documentation.md when you need to.
- **Independence** - Every part of the system must work independently and on its own to aid debugging and flexible usage.
- **Idempotence** - Where at all possible, actions should have the same outcome when re-run multiple times to aid agents and humans doing things that clash
- **@ means project root** - `@` before a file or path means relative to the project root.
- **uv run all python** - Instead of `pytest`: `uv run pytest`. Instead of `python`, `uv run python`.

## Self-Improvement

Read `agent-docs/lessons.md` at session start (may not exist yet, gitignored — machine-local). After any correction from the user, add a rule preventing the same mistake. Write rules for yourself — not descriptions of what went wrong. Ruthlessly iterate until mistake rate drops.

**Plan first.** Enter plan mode for any non-trivial task (3+ steps or architectural decisions). Stop and re-plan immediately if things go sideways — don't push through.

**Use subagents aggressively.** Offload research, exploration, and parallel analysis to subagents to keep the main context clean. One task per subagent. Subagents don't inherit CLAUDE.md — pass key constraints explicitly (e.g. `uv run`, `git grep`).

**Verify before done.** Never mark work complete without proving it works. Ask: "Would a staff engineer approve this?" Run tests, check logs, demonstrate correctness.

**Demand elegance.** For non-trivial changes, ask "is there a more elegant way?" Skip for simple obvious fixes.

**Fix bugs autonomously.** When given a bug report, just fix it. Find root causes — no temporary patches, no hand-holding required.

**Git discipline.** Read @agent-docs/git-commits.md — covers commits, branching, and rebasing.

## Context File Hygiene

When any context file (this file, AGENTS.md/CLAUDE.md, or any topic doc) grows past 200 lines, or where splitting by topic improves navigation: extract into a dedicated file and replace with a doc reference below.

Note: CLAUDE.md is a symlink to AGENTS.md — edits to either always show as `AGENTS.md` in git.

## Research files

Research notes live in the chimera project's knowledge dir in the workspace
(`$CHIMERA_WORKSPACE/chimera/knowledge/`), not in this repo. Save ad-hoc research there.

## Topic documentation

Topics docs live in agent-docs/{topic}.md, if you are working on/with {topic}, read `agent-docs/{topic}.md` before proceeding.

- @agent-docs/coding-standards.md
- @agent-docs/commands.md
- @agent-docs/unit-and-functional-testing.md
- @agent-docs/documentation.md
- @agent-docs/git-commits.md
- @agent-docs/logging.md
- @agent-docs/workspace-layout.md

If you are working on a topic and learn something new, add to the topic.

If the topic needs editing or rewriting, suggest to the user but get confirmation before changes.

Keep this file terse. Triggers over bulk. When editing, match existing style — no padding, no prose, no reminders needed.
