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

## Testing

- Put logic depth in pure-function tests — assert on return values / raised exceptions.
- Still cover the CLI: a smoke test per command plus the wrapper's job (arg parsing,
  exit codes, output), so the CLI is proven to work.
