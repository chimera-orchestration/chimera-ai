# Writing Effective AGENTS.md / CLAUDE.md Files

Research summary from: Anthropic official docs, Boris Cherny (Claude Code author), Garry Tan, and community workflow patterns.

---

## The Fundamental Constraint

**Context window is the resource.** LLM performance degrades as context fills. Every line in AGENTS.md costs tokens on every session. The longer the file, the more likely rules get ignored. This is the lens through which everything else should be evaluated.

---

## What to Include vs Exclude

| ✅ Include | ❌ Exclude |
|---|---|
| Bash commands Claude can't guess | Anything Claude can infer from reading code |
| Code style rules that differ from defaults | Standard language conventions |
| Testing instructions and preferred test runners | Detailed API docs (link instead) |
| Repo etiquette (branch naming, PR conventions) | Information that changes frequently |
| Architectural decisions specific to the project | Long explanations or tutorials |
| Developer environment quirks (required env vars) | File-by-file descriptions of the codebase |
| Common gotchas and non-obvious behaviours | Self-evident practices like "write clean code" |
| Domain terminology agents must use consistently | |

**Test each line:** "Would removing this cause Claude to make mistakes?" If not, cut it.

---

## Size and Structure

- **Target: under 200 lines** per file. Adherence drops sharply beyond this.
- Use markdown headers and bullets. Claude scans structure the same way humans do.
- Write **specific, verifiable instructions**: "Use 2-space indentation" not "format code properly."
- **Check for contradictions** across files. Claude may pick one arbitrarily when rules conflict.
- Review and prune regularly. Treat it like code — stale instructions are worse than missing ones.
- For critical rules: add `IMPORTANT:` or `YOU MUST` — measurably improves adherence.

---

## File Locations and Scope

Claude Code loads CLAUDE.md files from multiple locations, more specific takes precedence:

| Scope | Location | Shared via |
|---|---|---|
| Managed policy (org-wide) | `/Library/Application Support/ClaudeCode/CLAUDE.md` | IT/MDM — cannot be excluded |
| Project (team-shared) | `./CLAUDE.md` or `./.claude/CLAUDE.md` | git |
| User (all projects) | `~/.claude/CLAUDE.md` | just you |
| Local (private, project-specific) | `./CLAUDE.local.md` | not committed |

CLAUDE.md files in directories **above** the working directory are loaded in full at launch. Files in **subdirectories** load on demand when Claude reads files there.

---

## The Self-Improvement Loop (Boris Cherny)

The CLAUDE.md file compounds in value over time:

1. Claude makes a mistake.
2. You correct it.
3. End with: *"Update your CLAUDE.md so you don't make that mistake again."*
4. Claude writes a rule for itself.

Over time the file accumulates real project-specific knowledge that no generic template provides. The `.agents/lessons.md` pattern is a variant: agents self-maintain a lessons file separate from AGENTS.md, reviewing it at session start.

---

## The Trigger Pattern (Garry Tan)

When a context file grows past ~200 lines, **don't keep adding** — instead add triggers:

```
If you are working on evals (files matching *_generator.*), read docs/EVALS.md before proceeding.
```

This lazy-loads domain context only when relevant. Benefits:
- Fewer tokens per session
- Higher adherence to the rules that remain
- Domain docs can be detailed without bloating the main file

Can be tested: paste the trigger instruction into a separate Claude window to verify it fires correctly.

The **built-in equivalent** is `.claude/rules/` with `paths:` frontmatter — path-scoped rules that load only when Claude works with matching files:

```markdown
---
paths:
  - "src/api/**/*.ts"
---
# API Rules
- All endpoints must include input validation
```

Rules files go in `.claude/rules/<topic>.md`. They support glob patterns, brace expansion, and symlinks for sharing across projects.

---

## Importing Additional Files

CLAUDE.md can pull in other files with `@path/to/file` syntax:

```markdown
See @README.md for project overview.

# Additional Instructions
- Git workflow: @docs/git-instructions.md
- Personal overrides: @~/.claude/my-project-instructions.md
```

Imported files are loaded at launch alongside the CLAUDE.md that references them. Max depth: 5 hops. First encounter shows an approval dialog.

---

## Auto Memory

Claude's second memory system — it writes notes for itself without you doing anything.

- **Enabled by default.** Toggle via `/memory` or `autoMemoryEnabled` in settings.
- **Storage:** `~/.claude/projects/<project>/memory/` — shared across all worktrees in a git repo.
- **`MEMORY.md`** is the index: first 200 lines loaded every session, details go in topic files.
- Claude decides what's worth saving: build commands, debugging insights, architecture notes, preferences it discovers.
- All files are plain markdown — readable, editable, deletable at any time.
- Run `/memory` to browse loaded files and toggle auto memory.

To explicitly ask Claude to remember something: "remember that the API tests require local Redis." To add to CLAUDE.md instead of auto memory: "add this to CLAUDE.md."

---

## Principles vs Imperatives (Dario Amodei)

**Principles-based beats imperative** for agent instructions. Instead of:
> "Always run tests before committing"

prefer:
> "Never mark work complete without proving it works."

Principles generalise better across novel situations. The agent reasons about *why* rather than pattern-matching a rule.

Exception: for **critical, irreversible actions** (git push, deploys), imperatives are appropriate.

---

## Skills — For Task-Specific Knowledge

**Skills** are different from CLAUDE.md: they load *on demand*, not every session. Use them for domain knowledge or workflows that are only sometimes relevant.

```
.claude/skills/
  api-conventions/
    SKILL.md      # Loaded when invoked or when Claude judges it relevant
  fix-issue/
    SKILL.md      # Workflow skill, invoked with /fix-issue 1234
```

Skills can be invoked explicitly (`/skill-name`) or Claude applies them automatically when relevant. Use `disable-model-invocation: true` for side-effectful workflows you want to trigger manually.

**Rule of thumb:** CLAUDE.md for things that always apply, skills for things that sometimes apply.

---

## Giving Claude a Way to Verify Its Work

The single highest-leverage thing: **provide verification criteria** so Claude can check itself.

- Run tests after implementation
- Compare screenshots for UI changes
- Validate outputs with scripts
- Ask Claude to diff behaviour between main and its changes

Without this, Claude produces things that *look* right but don't work — and you become the only feedback loop.

---

## The Explore-Plan-Implement Workflow

Recommended 4-step workflow to avoid solving the wrong problem:

1. **Explore** (plan mode) — Claude reads files, no changes
2. **Plan** (plan mode) — Claude drafts implementation plan; press `Ctrl+G` to edit it before proceeding
3. **Implement** (normal mode) — Claude codes, verifies against plan
4. **Commit** — descriptive commit + PR

Skip planning for single-sentence diffs. Use it when scope is uncertain, multiple files change, or you're unfamiliar with the code.

---

## Parallel Sessions and Fan-Out

- **Writer/Reviewer pattern:** Session A implements, Session B reviews from a fresh context (less bias toward code it wrote)
- **Fan-out:** Generate task list, then loop `claude -p "migrate $file"` — test on 2-3 files first, then scale
- **Non-interactive mode:** `claude -p "prompt"` for CI, hooks, scripts; `--output-format stream-json` for piping
- Use `--allowedTools` to restrict what Claude can do in unattended runs

---

## A Workflow Section Template

A useful structure for the Workflow section of AGENTS.md:

1. **Plan first** — plan mode for 3+ step or architectural tasks; stop and re-plan if sideways
2. **Subagents aggressively** — offload research/exploration to subagents; one task per subagent; keeps main context clean
3. **Self-improvement loop** — after any correction, update lessons file; review at session start
4. **Verify before done** — never mark complete without proving it works; "would a staff engineer approve this?"
5. **Demand elegance** — non-trivial changes: "is there a more elegant way?"; skip for simple fixes
6. **Fix bugs autonomously** — given a bug report, just fix it; find root causes; no hand-holding

---

## Common Failure Patterns

| Pattern | Symptom | Fix |
|---|---|---|
| Kitchen-sink session | Context full of unrelated tasks | `/clear` between tasks |
| Correcting over and over | Same mistake repeated | After 2 failures: `/clear`, write a better prompt |
| Over-specified CLAUDE.md | Rules get ignored | Ruthlessly prune; convert redundant rules to hooks |
| Trust-then-verify gap | Plausible-but-wrong implementation | Always provide verification (tests, scripts, screenshots) |
| Infinite exploration | Claude reads hundreds of files | Scope investigations narrowly or use subagents |

---

## Context Management Tools

- **`/clear`** — reset context between unrelated tasks
- **`/compact <instructions>`** — summarise with a focus (e.g. `/compact Focus on the API changes`)
- **`Esc+Esc` / `/rewind`** — restore conversation + code state to a checkpoint
- **`--continue` / `--resume`** — resume a previous session
- **`/rename`** — name sessions like branches (`"oauth-migration"`)
- Customise compaction in CLAUDE.md: *"When compacting, always preserve the full list of modified files"*

---

## File Organisation Reference

```
AGENTS.md               # Primary agent instructions
CLAUDE.md               # Symlink to AGENTS.md (read by Claude Code)
CLAUDE.local.md         # Private project preferences, not committed
.agents/
  lessons.md            # Agent-maintained self-correction log
.claude/
  CLAUDE.md             # Alternative location for project instructions
  rules/
    testing.md          # Path-scoped: loads when touching test files
    api-design.md       # Path-scoped: loads when touching API files
  skills/
    fix-issue/
      SKILL.md          # On-demand workflow skill
  agents/
    security-reviewer.md  # Custom subagent definition
~/.claude/
  CLAUDE.md             # User-level: applies to all projects
  rules/
    preferences.md      # Personal preferences, all projects
  projects/<repo>/
    memory/
      MEMORY.md         # Auto memory index (first 200 lines loaded)
      debugging.md      # Auto memory topic file (loaded on demand)
```

---

## Key Sources

- Anthropic memory docs: https://code.claude.com/docs/en/memory
- Anthropic best practices: https://code.claude.com/docs/en/best-practices
- Boris Cherny workflow (via @heygurisingh): https://x.com/heygurisingh/status/2025572300658287030
- Garry Tan trigger tip: https://x.com/garrytan/status/2025939266275254478
- Workflow template principles: community patterns from agent prompt engineering discussions
