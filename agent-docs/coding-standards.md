- always use the latest possible python features, we are targeting the bleeding edge, update @.python-version if necessary
- name base classes plainly (`Service`, not `ServiceBase`); give discriminated union type aliases a qualified name (`AnyService`, `AnyServiceConfig`)
- read pyproject.toml to see what libraries are installed, aggressively use them rather than writing code from scratch
- type annotate everything
- inline trivial single-use locals: don't bind a name only to use it once on the next line —
  `replace.in_environ('X', str(tmpdir.makedir('d')))`, not a throwaway `d = tmpdir.makedir('d')`.
  Keep a name only when it's used more than once, or when it documents a genuinely non-obvious
  value (a long/dense expression that reads better named).
- `ruff format` frequently
- code comments are terse, WHY-only, one line — no caller/fix/test references (`# see
  tests/conftest.py's bare_repo`, `# fixes the CI flake`). That density is for agent-docs
  prose, not code; motivation belongs in the commit message, not the comment.
- run @happy.sh any time you want to see if your code is of sufficient quality to commit.
- never `splitlines()` over JSONL or any other line-oriented data: it also splits on U+2028,
  U+2029, `\x1c`–`\x1e`, `\x85`, `\v` and `\f`, which legitimately occur *inside* a record's
  payload — iterate the open file object or `split('\n')`. Learned when `ch ls` died on a live
  transcript whose prompt text carried U+2028 (`session_summary` tore the record and
  `json.loads` raised). Git/porcelain output and chimera's own marker files are fine either way.
