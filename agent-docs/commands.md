# Command structure

Layout under `src/chimera/` (ordinary packages, `__init__.py` present):
- `commands/<name>.py` — flat command (`ch <name>`)
- `commands/<group>/<sub>.py` — grouped (`ch <group> <sub>`)
- tests mirror the tree, `test_` prefix: `tests/commands/<group>/test_<sub>.py`
- `__main__.py` assembles the Typer app and enables `python -m chimera`

## Each command = a pure function + a thin CLI wrapper

The function in `commands/**` is plain importable Python:
- takes its domain args + any context (e.g. `Workspace`) explicitly
- `return`s values and `raise`s ordinary exceptions — never `typer.echo` / `typer.Exit`
- built from other pure functions where possible

`__main__.py` is the only module that imports typer: it parses args, injects
context, renders the return value, and lets exceptions bubble to a non-zero exit.

Typer metadata always rides on `Annotated`, never a default value — this keeps
the function callable from Python:
- `goal: Annotated[str, typer.Argument()]`  ✓  default stays a real value
- `goal: str = typer.Argument()`             ✗  default becomes a Typer sentinel

## Synonyms

A group may accept synonyms for a command (e.g. `goal new` → `goal start`,
`goal cleanup` → `goal finish`, and `list` → `ls` on every group that has an `ls`).
Two rules, no exceptions:
- **`--help` shows only the canonical name** — synonyms never appear.
- **only the canonical name is logged** — a synonym must dispatch to the canonical
  command, never run as a command of its own.

Synonyms *do* tab-complete, though, so a half-typed one (`goal clea<TAB>` →
`cleanup`) can be finished — completion is the one place they surface.

`alias_group({...})` (in `__main__.py`) gives all three: pass it as the group's `cls=`.
It resolves the synonym in `get_command` (so the real command runs) and leaves
`list_commands` canonical (so `--help`/logging never see synonyms), while
`shell_complete` adds the synonyms back as completion candidates. Add one by
extending the dict. A synonym must not collide with a real command name (a test
enforces this; the canonical command would win anyway).

## Shell completion

Value-taking params complete via callbacks in `chimera/completions.py`, attached with
`autocompletion=` on the shared `Annotated` types (`ProjectOpt`, `GoalOpt`, `ActorOpt`,
`ExistingGoalArg`) — a new param that names a project/goal/actor should reuse those.
Rules:
- a completer must never raise or print — swallow everything, return `[]`
- scope like the listers (narrow by flags/cwd, widen otherwise); read a typed `-p` by
  walking `ctx`/`ctx.parent` params — group callbacks (so `ctx.obj`) don't run during
  completion
- args naming something *new* (e.g. `goal start`) get no completer

## Self-documentation

Every command — group *and* leaf — sets its summary via explicit `help=` (groups use
`typer.Typer(help=…)`, leaves `@app.command(help=…)`); never a wrapper docstring (groups
can't carry one, so docstrings would split the convention). One `help=` string is the
single source: `--help`, `ch X help`, and `ch help` all derive from it.

`ch help` is the whole tree in one chunk — flat, plain text, terse, agent-optimised. It's
**derived** by walking the live command objects (`chimera/help.py`), never a hand-kept
list, so it can't drift. Default lists canonical leaf commands + summaries; `-v` adds each
command's options and synonyms; `--json` emits the structured index. A leaf with no `help=`
shows blank — a test (`test_every_command_has_a_summary`) fails on it.

## Testing

- Put logic depth in pure-function tests — assert on return values / raised exceptions.
- Still cover the CLI: a smoke test per command plus the wrapper's job (arg parsing,
  exit codes, output), so the CLI is proven to work.
