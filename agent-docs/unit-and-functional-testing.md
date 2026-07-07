- `uv run pytest tests/test_{}.py` to run tests (`tests/` at project root)
- collect tests for a component in `test_{component}.py`
- commands: test the pure function for logic, the CLI for wiring — see @agent-docs/commands.md

## Grouping

Group related tests into a class-based suite (`class TestResolveScope: ...`), never with
banner comments like `# ---- scope ----`. The class name carries the grouping; method names
drop the redundant prefix (`TestResolveScope.test_pins_the_goal`, not `test_resolve_scope_*`).
Module-level helpers stay at module scope, above the class that uses them.

## Assertions

Two tools, split by what the check *is*:

- **A value → `compare`** (from testfixtures): `compare(actual, expected=<>)` — name the
  `expected=` side so failures read correctly. Equality of data always goes through `compare`,
  never `assert ==` — its failure output diffs the two values.
  - **Compare whole objects, once.** One `compare` of the full object beats several checks on
    its attributes — compare the Finding, not `finding.fixable` then `finding.message`.
  - Only when an exact whole-object compare is genuinely impossible, narrow with
    `like(Cls, attr=…)` (a typed partial `Comparison`) — never fall back to comparing pieces.
- **A boolean fact → plain `assert`**: membership, existence, identity, inequality and
  predicates — `assert 'x' in text`, `assert not path.exists()`, `assert result is None`,
  `assert a != b`, `assert is_merged(git, ref, base)`.
  - **Never launder a boolean through compare** — `compare('x' in text, expected=True)` fails
    as an unreadable `False != True`. If you're typing `expected=True`, `expected=False` or
    `expected=None`, you wanted `assert x` / `assert not x` / `assert x is None`.
  - Nor pad the assert — `assert ('x' in text) is True` is the same truth test with noise on;
    reserve `is` for identity itself (`is None`, `is sentinel`).

The line: `compare` when a failure should show a *diff of values*; `assert` when the check is
inherently True/False and a failure needs no diff to read.

## Exceptions

Assert raised exceptions with `ShouldRaise` from testfixtures — never `pytest.raises`:
- `ShouldRaise(SomeError(...))` — the **exact** exception instance (type *and* message). The
  default; mirrors the whole-object rule above. Build the message from the test's own inputs.
- `ShouldRaise(SomeError, match='…')` — only when the message embeds something you can't
  reconstruct (a subprocess's output, a timestamp); `match` is a substring/regex of `str(exc)`.
- `ShouldRaise(SomeError)` — type only, when the message is genuinely uninteresting (e.g. a
  pydantic `ValidationError`, whose text isn't ours to pin).

```python
with ShouldRaise(NotInProjectError(ws / 'ghost')):   # exact: type + message
    resolve_project(ws, 'ghost')
with ShouldRaise(RuntimeError, match='no commits'):  # message embeds `git status` output
    add(repo.path, worktrees, 'g')
with ShouldRaise(ValidationError):                   # type alone is the contract
    load_services_config(path)
```
`ShouldRaise` is a context manager like `pytest.raises`; the value check lives in its argument,
not an `assert` afterwards.

## Mocking

Use `testfixtures.Replacer` — never `monkeypatch.setattr`, never a dotted-path string target.
Reach for the typed helper that fits:
- `replace.in_module(thing, replacement)` — module-level functions/objects
- `replace.on_class(Cls.method, replacement)` — methods/attributes on a class
- `replace.in_environ(name, value)` — environment variables
If (and only if) none of those can express it, fall back to the explicit form:
`replace(target=thing, container=parent, name='<attr>', replacement=<>)`.

## Fixture return types

yield-based fixtures return `Iterator[T]` — not `Generator[T, None, None]` (verbose) or `Iterable[T]` (too broad):
```python
from collections.abc import Iterator
def tmpdir() -> Iterator[TempDir]: ...
```

## Testing fixtures

**testfixtures.TempDir** (`from testfixtures import TempDir`)
- Use as context manager: `with TempDir() as d: ...`
- `d.path` is already a `Path` object — use directly, no wrapping needed
- `d / 'sub'` works (≡ `d.path / 'sub'`) — prefer it; reserve `d.path` for passing the dir itself
- path args accept an **absolute** `Path` inside the tempdir (auto-relativised) as well as a
  relative str — so `d.dump(project / 'config.yaml', …)`, never
  `d.dump(str(project.relative_to(d.path) / 'config.yaml'), …)`
- `d.write('file.txt', b'data')` returns `Path`; `d.makedir('subdir')` returns `Path`
- `TempDirectory` is the old deprecated API with str/bytes interface — do NOT use it
- dep: `testfixtures>=12.2.0` (PyPI)

*Writing structured files* — `d.dump(relpath, obj)` serialises by extension (`.yaml`/`.json`/
`.toml`), creating parent dirs; never hand-format YAML/JSON. `d.parse(relpath)` reads it back.
Plain text stays `d.write(relpath, text)`.
```python
d.dump('proj/config.yaml', {'kind': 'project', 'repo': str(repo.path)})  # → kind: project\nrepo: …
```

*Checking directory structure* — assert on-disk layout with `d.compare(...)`, which checks the
**whole** listing (so it also proves nothing extra was written) and scopes with `path=`:
```python
d.compare(['feature-x@agent'], path='worktrees', recursive=False)  # exactly one worktree, no human
d.compare(path='worktrees', expected=())                           # …emptied after goal finish
```
Drop to a single `path.exists()`/`path.is_dir() is True/False` only when it's significantly
shorter than the equivalent `compare` (e.g. one path among an otherwise-noisy tree like a repo).

**giterator.testing.Repo** (`from giterator.testing import Repo`)
- `Repo.make(path)` — creates an initialized git repo at path
- `repo.commit_content('prefix', datetime(...))` — writes file and commits, returns short hash
- `repo('log', ...)` — run raw git commands (instance is callable)
- dep: `giterator>=1.0.0` (PyPI)
- conftest fixture pattern: `with TempDir() as d: yield Repo.make(d.path / 'repo')`
