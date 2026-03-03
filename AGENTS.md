# Agent Instructions

## Self-Improvement

Read `.agents/lessons.md` at session start. After any correction from the user, add a rule preventing the same mistake. Write rules for yourself — not descriptions of what went wrong. Ruthlessly iterate until mistake rate drops.

## What Chimera Is

Chimera orchestrates AI agents working on goals across projects. It manages the `~/lycia/` directory tree and provides the `ch` CLI.

**Core concepts** (use this terminology consistently):
- **Goal** — a thing that needs doing (e.g. "implement feature X")
- **Task** — a tracked unit of work, discovered while executing a goal
- **Process** — an ordered sequence of steps to complete a task
- **Step** — a single atomic thing that must be done
- **Principle** — context an agent must load before beginning a process
- **Knowledge** — named, versioned context loaded on demand (e.g. "load knowledge for testfixtures")

**Layout:**
```
~/lycia/
  {project}/
    worktrees/
  processes/      # shared process definitions
  .beads/         # issue tracking
```

## Workflow

**Plan first.** Enter plan mode for any non-trivial task (3+ steps or architectural decisions). Stop and re-plan immediately if things go sideways — don't push through.

**Use subagents aggressively.** Offload research, exploration, and parallel analysis to subagents to keep the main context clean. One task per subagent.

**Verify before done.** Never mark work complete without proving it works. Ask: "Would a staff engineer approve this?" Run tests, check logs, demonstrate correctness.

**Demand elegance.** For non-trivial changes, ask "is there a more elegant way?" Skip for simple obvious fixes.

**Fix bugs autonomously.** When given a bug report, just fix it. Find root causes — no temporary patches, no hand-holding required.

## Context File Hygiene

When any context file (this file, CLAUDE.md, or any topic doc) grows past 200 lines, or where splitting by topic improves navigation: extract into a dedicated file and replace with a trigger:

> If you are working on X, read `path/to/X.md` before proceeding.

Keep this file terse. Triggers over bulk.
