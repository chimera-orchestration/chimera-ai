# Logging

One JSONL sink: `<workspace>/logs/chimera.jsonl` (gitignored), written by loguru through a
custom one-line-JSON format (see `chimera/logging.py`). Every CLI action lands a start/end
pair through `LoggingCommand` → `log_start`/`log_finish`.

## Observability: the log alone must be enough to debug a run

The start/end frame proves a command ran; it says nothing about what it *did*. Between the
pair, log what the command examined, decided and changed — someone holding only the log must
be able to reconstruct the run and undo its mutations. Two forces, in tension:

- **Log decisions and outcomes, not motion.** A line earns its place by answering "what did
  the command see / decide / change": a step's conclusion, a mutation (see ref safety below),
  a fallback taken, a fix refused. Never log loop iterations, function entry/exit, or a
  restatement of what the frame already carries (params, duration) — spew buries the signal
  it pretends to add.
- **Levels are triage, not decoration.** INFO = normal operation (a step ran and concluded, a
  deliberate mutation); WARNING = degraded but continuing (a fallback taken); ERROR = needs
  attention (a problem found and left unresolved, an action that failed).

**Uniform steps log at their driver.** When a family of steps shares a loop (doctor's checks),
the loop logs for all of them so a new step can't forget to. `ch doctor` is the model: every
check lands `<name>: checked` with its findings count bound, and every finding lands its own
line (`fixable`/`resolved` bound) — ERROR while unresolved, INFO once fixed.

## Conventions

- Bind structured data with `logger.bind(key=value)` — never f-string it into the message.
  Bound keys serialise as fields on the JSON line (like `params`).
- The message is the canonical command path (`'worktree rm'`), not a sentence.

## Ref safety (mandatory)

Any time you **add, repoint, or delete** a git ref (branch, tag, any named ref), record the
affected refs and the **full** sha each points at — both before and after the change — so the
log alone is enough to restore a ref (`git branch <name> <sha>`). Pioneered by `goal adopt`;
standard for every site that touches a ref.

Shape — **one line per action**, a `git` key holding `before`/`after` maps of `{ref: full-sha}`
(existing refs only; a ref absent from a map didn't exist at that point). The message is the
command path with a `: refs` suffix, so it reads apart from the `LoggingCommand` action line:

```python
logger.bind(git={'before': before, 'after': after}).info('worktree rm: refs')
```

The before→after pair encodes which operation happened:

| Operation | `before`            | `after`             |
|-----------|---------------------|---------------------|
| create    | `{}`                | `{ref: sha}`        |
| delete    | `{ref: sha}`        | `{}`                |
| repoint   | `{ref: old-sha}`    | `{ref: new-sha}`    |

Full shas only — capture with `rev_parse(ref, short=False)`; short shas aren't safe to recover
from. Snapshot both sides with the `ref_shas(git, *refs)` helper (existing refs → full sha):
take `before` *before* the first mutating call (the refs may be gone afterwards), `after` once
done, and log the line so any completed change can be undone from the record. Skip the line
when nothing changed (`before == after`).

**Moving HEAD (a checkout)** — switching a worktree onto a different branch is not a ref-value
mutation (neither branch's sha changes), so the skip-when-unchanged rule above doesn't apply: log
it anyway, because the recovery datum is *where HEAD pointed*, not a repointed ref. Use the same
shape but key each side by the **branch HEAD was on**, mapped to that HEAD's full sha — `before`
the branch left behind, `after` the branch switched to (`'HEAD'` as the key when detached). To undo,
`git -C <worktree> checkout <before-key>`. Pioneered by doctor's `worktree-branch` check.
