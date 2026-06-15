- `uv run pytest tests/test_{}.py` to run tests (`tests/` at project root)
- collect tests for a component in `test_{component}.py`
- commands: test the pure function for logic, the CLI for wiring — see @agent-docs/commands.md

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
- `d.write('file.txt', b'data')` returns `Path`; `d.makedir('subdir')` returns `Path`
- `TempDirectory` is the old deprecated API with str/bytes interface — do NOT use it
- dep: `testfixtures @ git+https://github.com/simplistix/testfixtures` (main branch)

**giterator.testing.Repo** (`from giterator.testing import Repo`)
- `Repo.make(path)` — creates an initialized git repo at path
- `repo.commit_content('prefix', datetime(...))` — writes file and commits, returns short hash
- `repo('log', ...)` — run raw git commands (instance is callable)
- dep: `giterator @ git+https://github.com/simplistix/giterator` (main branch)
- conftest fixture pattern: `with TempDir() as d: yield Repo.make(d.path / 'repo')`
