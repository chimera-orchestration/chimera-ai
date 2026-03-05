- `uv run pytest tests/test_{}.py` to run tests (`tests/` at project root)
- collect tests for a component in `test_{component}.py`

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
