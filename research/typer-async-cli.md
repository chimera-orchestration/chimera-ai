# Typer: `ch action subaction` commands + async handlers

## Status
- **typer** 0.24.1 — installed
- **asyncer** — NOT installed (add to `pyproject.toml` if needed)

---

## Sub-app pattern (`ch goal create`)

Each action group is a separate module with its own `typer.Typer()` instance.

```python
# chimera/cli/goal.py
from typing import Annotated
import typer

app = typer.Typer()

@app.callback()
def callback():
    """Manage goals."""

@app.command("create")
def goal_create(
    title: Annotated[str, typer.Argument(help="Goal title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name.")] = "",
) -> None:
    """Create a new goal."""
    ...

@app.command("list")
def goal_list(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project.")] = None,
) -> None:
    """List goals."""
    ...
```

```python
# chimera/cli/__init__.py
import typer
from chimera.cli import goal

app = typer.Typer()
app.add_typer(goal.app, name="goal")
```

- `@app.callback()` docstring → help text for `ch goal --help`
- `name=` in `add_typer()` → the CLI verb (`goal`)
- `@app.command("create")` → the subcommand verb

### `invoke_without_command`

Default (omitted): `ch goal` with no subcommand prints help and exits.

```python
# Run callback even when called bare, then show help
app = typer.Typer(invoke_without_command=True)

@app.callback()
def callback(ctx: typer.Context):
    """Manage goals."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
```

### `Annotated` typing rules

```python
# Positional, required
name: Annotated[str, typer.Argument(help="...")]

# Positional, optional (has default)
name: Annotated[str, typer.Argument(help="...")] = "default"

# Option flag, optional
verbose: Annotated[bool, typer.Option("--verbose", "-v", help="...")] = False

# Option flag, optional value (None = not provided)
filter: Annotated[str | None, typer.Option("--filter", help="...")] = None

# Option flag, required (no default)
project: Annotated[str, typer.Option("--project", help="...")]
```

Default always goes after `=`, never inside `typer.Option()`/`typer.Argument()`.

---

## Async handlers

### Option A: `asyncio.run()` (no extra deps)

```python
import asyncio
from typing import Annotated
import typer

app = typer.Typer()

@app.callback()
def callback():
    """Manage goals."""

async def _create(title: str, project: str) -> None:
    # async implementation
    ...

@app.command("create")
def goal_create(
    title: Annotated[str, typer.Argument(help="Goal title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name.")] = "",
) -> None:
    """Create a new goal."""
    asyncio.run(_create(title=title, project=project))
```

Simple, zero deps, sufficient for most cases.

### Option B: `asyncer.runnify()` (type-safe, IDE-friendly)

Requires `asyncer` in `pyproject.toml`. Built on AnyIO (works with asyncio or trio).

```python
import asyncer
from typing import Annotated
import typer

app = typer.Typer()

@app.callback()
def callback():
    """Manage goals."""

async def _create(title: str, project: str) -> None:
    # async implementation
    ...

async def _list(project: str | None) -> list[str]:
    # async implementation
    return []

@app.command("create")
def goal_create(
    title: Annotated[str, typer.Argument(help="Goal title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name.")] = "",
) -> None:
    """Create a new goal."""
    asyncer.runnify(_create)(title=title, project=project)

@app.command("list")
def goal_list(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project.")] = None,
) -> None:
    """List goals."""
    goals = asyncer.runnify(_list)(project=project)
    for g in goals:
        typer.echo(g)
```

`runnify(async_fn)` returns a sync callable with the same signature — full type inference, no lambdas needed.

### When to use which

| Situation | Use |
|---|---|
| Simple fire-and-forget | `asyncio.run()` |
| Need return value with type safety | `asyncer.runnify()` |
| async calls blocking code | `asyncer.asyncify()` (inside async) |

### `asyncer` decision guide

- `runnify(fn)` — sync entry point → async (for Typer handlers)
- `asyncify(fn)` — async context → blocking sync (e.g. blocking DB call inside async)
- `syncify(fn)` — inside a worker thread spawned from async context (not for Typer handlers)

---

## File layout

```
chimera/
  cli/
    __init__.py    # main app + add_typer registrations
    goal.py        # app = typer.Typer(); commands + async impls
    task.py
    process.py
```

Keep async implementation functions (`_create`, `_list`) private (underscore prefix) and co-located with their command module.
