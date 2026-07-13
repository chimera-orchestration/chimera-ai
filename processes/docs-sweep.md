# Docs sweep — the pattern

Keeps both documentation surfaces true: **agent docs first** (AGENTS.md + agent-docs/),
**then human docs** (docs/, Sphinx). That order is load-bearing — human docs are written
*from* the agent docs, so auditing afterwards would launder drift into polished prose.

## Trigger

On demand: after landed work changed behaviour the docs describe, when a doc claim smells
wrong, or on a bare "are the docs still true?". The continuous protections (Sybil-executed
examples, registry-pinning tests, the derived `ch help`) catch example and list rot on
every CI run — this process is for the prose between them.

## Dispatch

Needs a checkout → a goal agent, reused across sweeps:

    ch goal start docs-sweep "run the docs sweep per @processes/docs-sweep.md"
    ch agent resume -g docs-sweep "run another sweep per @processes/docs-sweep.md"

Each phase below runs as subagents inside the goal's worktree — one task per subagent,
key constraints passed explicitly (subagents inherit no CLAUDE.md: give them `uv run`,
`git grep` never bare grep, and the scratch-workspace rule verbatim).

## Phase 1 — audit the agent docs

This phase is executable: under Claude Code, run the `docs-sweep` workflow
(@.claude/workflows/docs-sweep.js) — it scouts the docs, fans out the auditors below,
adversarially verifies every finding, and returns the drift report; optional
`{docs: [...]}` args limit scope. Elsewhere (or by hand), the spec it implements:

Fan out one **read-only** auditor per doc, in parallel; slice any doc over ~200 lines at
section boundaries so each auditor holds its whole slice. Every auditor gets the same
method:

- Extract every independently checkable claim, by category: **names** (commands, flags,
  module/attr paths, file paths, env vars, log-line messages, ref shapes, marker paths),
  **behaviour** ("X refuses when Y", defaults, idempotence claims), **tests** ("a test
  pins X" — find that exact test), **cross-refs**.
- Verify against code, never plausibility. Any CLI smoke-run MUST pin a scratch
  workspace on the command itself: `CHIMERA_WORKSPACE=<scratch> uv run python -m chimera …`
  — a bare run can mutate the live workspace.
- Verdict per claim: CONFIRMED (count only, don't itemise) / DRIFTED (quote the doc,
  cite code file:line, state actual behaviour) / MISSING / UNVERIFIABLE (say why).
- Calibration: paraphrase and undocumented extras are NOT drift; a named artifact absent
  under its exact name IS a finding even when something similar exists — names are
  load-bearing for agents.

AGENTS.md's auditor additionally runs the **reverse sweep**: first-class concepts live in
the code but absent from the vocabulary list.

Before acting on anything: spot-verify the headline findings by hand — auditors can read
stale context, and one refuted finding poisons trust in the rest.

## Phase 2 — fix

- Factual corrections (prose says X, code does Y) are applied without ceremony.
- Source-of-truth conflicts (a stated principle vs the implementation) go to the human —
  never resolved silently in either direction.
- Vocabulary rule: the front page defines nouns the CLI can't self-describe; commands
  stay discoverable via `ch help` and are never enumerated in prose. Design intent enters
  the vocabulary only when it's confirmed roadmap.
- Any hand-kept list mirroring a code registry gets a pinning test **in the same commit**
  (patterns: `test_workspace_layout_doc_lists_every_check`, `tests/test_prime.py`).
- One logical change per commit, @happy.sh green before each.

## Phase 3 — write the human docs

One writer subagent wearing three personas at once — name them in its prompt, each with a
concrete job, and require all three:

- **Georg Brandl** — reST/Sphinx craft: toctree architecture, semantic markup,
  cross-references that resolve, zero warnings under `sphinx-build -W` (conf is nitpicky).
- **Jacob Kaplan-Moss** — layering and voice: tutorial (one golden path, every command
  *run* before it's documented) / topic guides (one concept each) / reference (points at
  the authoritative source, duplicates nothing); respect the reader's time.
- **Eric Holscher** — information architecture: the index organised by reader intent
  (new user / working user / operator), never by code layout; ten seconds to "what is
  this and where do I go".

Sources of truth in order: the live CLI (scratch workspace; launchers previewed with
`--dry`, never real sessions), the freshly audited agent docs, `ch help -v`. Hard rules:
no hand-enumerated command inventories (the reference chapter frames `ch help` instead);
unbuilt vocabulary is never documented as usable; the writer reports anything
undocumentable or surprising — fresh reader-seat eyes reliably catch drift the audit
missed.

## Phase 4 — pin the examples

Every example must execute in the suite (the rule lives in @agent-docs/documentation.md).
Console blocks run through the Sybil wiring (@conftest.py + @tests/sybil_console.py):
scratch home per document, path and sha normalisation (repeated shas provably name one
commit), doctest `...` ellipsis for elided tails. A genuinely unrunnable step
(interactive session, network install) gets `.. skip: next` with its reason as an
adjacent comment; state it would have created is built by an
`.. invisible-code-block: python` so every later block runs real. Machine- or
network-dependent output (doctor's report) is never pinned.

## Guardrails

- Never run `ch`/`python -m chimera` against the live workspace — scratch pin, always,
  in every subagent prompt.
- AGENTS.md rewrites and source-of-truth flips need the human's word; factual
  corrections don't.
- The branch tells the story: each phase's outcome lands as its own commits, every one
  behind a green @happy.sh (which also executes the doc examples).

The artifact: agent docs that map to code, human docs a reader can type from, and the
pins that keep both true until the next sweep.
