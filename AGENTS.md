# Agent Instructions

## What is Chimera?

Chimera orchestrates AI agents working on goals across projects. It manages a `lycia` directory tree, usually `~/lycia` and provides the `ch` CLI.

**Core concepts** (use this terminology consistently):
- **Goal** — a thing that needs doing (e.g. "implement feature X")
- **Task** — a tracked unit of work, discovered while executing a goal
- **Process** — an ordered sequence of steps to complete a task
- **Step** — a single atomic thing that must be done
- **Principle** — context an agent must load before beginning a process
- **Knowledge** — named, versioned context loaded on demand (e.g. "load knowledge for testfixtures")

## Principles

When implementing chimera, the following principles must be adhered to:

- **Everything must be a CLI** – Every action must per performance by a simple command line interface.
- **Every CLI action must be logged** – Every action must be logged to the log file.
- **Every CLI action must self documenting** – `--help` and `ch {action} help` must both work & be identical and provide terse, optimised for agents. More in @agent-docs/{documentation}.md.
- **Document everything** - Read @agent-docs/documentation.md when you need to.
- **Independence** - Every part of the system must work independently and on its own to aid debugging and flexible usage.
- **Indempotence** - Where at all possible, actions should have the same outcome when re-run multiple times to aid agents and humans doing things that clash
- **@ means project root** - `@` before a file or path means relative to the project root.
- **uv run all python** - Instead of `pytest`: `uv run pyest`. Intead of `python`, `uv run python`. 

## Self-Improvement

Read `agent-docs/lessons.md` at session start (gitignored — machine-local). After any correction from the user, add a rule preventing the same mistake. Write rules for yourself — not descriptions of what went wrong. Ruthlessly iterate until mistake rate drops.

**Plan first.** Enter plan mode for any non-trivial task (3+ steps or architectural decisions). Stop and re-plan immediately if things go sideways — don't push through.

**Use subagents aggressively.** Offload research, exploration, and parallel analysis to subagents to keep the main context clean. One task per subagent. Subagents don't inherit CLAUDE.md — pass key constraints explicitly (e.g. `uv run`, `git grep`).

**Verify before done.** Never mark work complete without proving it works. Ask: "Would a staff engineer approve this?" Run tests, check logs, demonstrate correctness.

**Demand elegance.** For non-trivial changes, ask "is there a more elegant way?" Skip for simple obvious fixes.

**Fix bugs autonomously.** When given a bug report, just fix it. Find root causes — no temporary patches, no hand-holding required.

## Context File Hygiene

When any context file (this file, CLAUDE.md, or any topic doc) grows past 200 lines, or where splitting by topic improves navigation: extract into a dedicated file and replace with a doc reference below.

## Topic documentation

Topics docs live in agent-docs/{topic}.md, if you are working on/with {topic}, read `agent-docs/{topic}.md` before proceeding.

- @agents.md/coding-standards.md
- @agents.md/unit-and-functional-testing.md
- @agents.md/documentation.md
- @agents.md/git-commits.md
- @agents.md/lycia-layout.md

If you are working on a topic and learn something new, add to the topic.

If the topic needs editing or rewriting, suggest to the user but get confirmation before changes.

Keep this file terse. Triggers over bulk. When editing, match existing style — no padding, no prose, no reminders needed.
