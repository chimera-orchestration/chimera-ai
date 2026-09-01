- `uv run pytest tests/test_{}.py` to run tests (`tests/` at project root)
- Smoke-testing `ch` by hand needs `CHIMERA_WORKSPACE=<scratch>` pinned on the command —
  see Testing in @agent-docs/commands.md (pytest itself is safe; only manual runs bite)
- collect tests for a component in `test_{component}.py`
- commands: test the pure function for logic, the CLI for wiring — see @agent-docs/commands.md
- a test that plants a git hook must point the repo's *local* `core.hooksPath` at the hook's
  dir — a user-global `core.hooksPath` (set on this machine) silently shadows per-repo
  `hooks/`, so the hook never fires and the test quietly asserts nothing
- `happy.sh` runs pytest with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null` — a repo
  built or cloned in a test must never depend on the machine's own git identity/config (a dev
  machine has one, CI doesn't); use `Repo.make`/`Repo.clone` below, which always configure one.
  The first instance of *The machine never decides a test* (next section), which is the rule

## The machine never decides a test

A test's outcome must come from what the test set up. Whatever the *host* contributes — what
is installed, what is configured, who is logged in, what the interpreter seeded — is not an
input the test chose. And the box we develop on is the least representative machine there is:
the suite runs *inside* claude, with the user's git identity, their `~/.claude/settings.json`
and their PATH. CI is what an empty machine looks like, which makes it the oracle. Green
locally proves nothing until it is green there.

This is the failure this project keeps repeating. Three of the four CI breaks on `main` were
this and nothing else, plus a standing set held off by hand — each fixed where it was found,
each lesson written into a different corner, the class never named, which is why the latest
one looked new:

| when | what the host decided | fix |
|---|---|---|
| 2026-07-16 | the machine's **git identity** — a dev box has one, CI has none, so `git commit` exited 128 | `happy.sh` pins `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` to `/dev/null`; `Repo.make`/`Repo.clone` always configure a user |
| 2026-07-31 | **`PYTHONHASHSEED`** — a set-vs-frozenset `compare` fell back to the order-sensitive comparer, so it passed or failed on the seed | coerce both sides to one container type (see *Assertions*) |
| 2026-09-01 | **`claude` on the PATH** — `Agent.available()` really consults it, and `reconcile` warns and closes nothing when a harness can't be asked, so 21 lister tests passed locally and failed on push | `_stub_harness_binaries`, `_no_host_lookups` |
| ongoing | **`fblog`, this repo's own git state and network, `~/.claude/settings.json`** | a hand-kept `_no_chimera_checkout` fixture in `tests/commands/doctor/test_doctor.py` |
| 2026-09-01 | the host's **bash version** — typer probes it before emitting a completion script and branches on it (this box ships 3.2, a runner 5.x). Found by `_no_host_lookups` on the run that introduced it, not by CI | the test neutralises typer's probe |

The table is the point, and it is append-only: a new instance goes in a **new row here**,
never in a fresh paragraph elsewhere. Four write-ups in four corners is what let the class
stay invisible while every individual instance was understood perfectly well.

The test to apply while writing one: **would this answer differently on a machine with
nothing installed and nobody logged in?** If yes, the test must supply the answer itself.

**A new host dependency earns a positional guard in `tests/conftest.py`, never a stub in the
one test file that noticed.** That last row is how the third instance got through: the
awareness existed — as a comment, in one file, neutralising four host facts by hand — and
everything outside that file stayed exposed. `Agent.available()` was outside it. A guard in
conftest cannot be walked past; a local stub protects only its own file.

**Guarded today** (all autouse in `tests/conftest.py` unless noted):

- `_no_host_lookups` — `shutil.which` refuses any name the suite has not decided about, so
  "is X installed" can never be answered by the machine. A test that means to decide it
  stubs `shutil.which` itself, as the `fblog`/`brew` tests do.
- `_stub_harness_binaries` — prepends a dir holding an inert stub per registered harness, so
  `available()` answers *available* everywhere, through the real `shutil.which` rather than
  around it. A test wanting the other answer says so: `replace.in_environ('PATH', '')`. The
  stubs are only ever found, never run — `_no_real_harness` refuses the spawn, and they exit
  non-zero if anything ever gets past it.
- `_no_real_harness`, `_no_live_archive`, `_clear_workspace_env` — see *Mocking* below.
- git config — pinned by `happy.sh`, not a fixture, because it is the environment pytest
  inherits rather than anything a fixture can reach.

**Not guarded, and so still live hazards**: `Path.home()` and anything read out of it, and
the network. Both are neutralised by hand in the doctor tests and nowhere else — which is
precisely the shape the row above describes, recorded here rather than presumed safe.

## Bugs and regressions: the test comes first

A fix without a test that was *seen to fail* is a claim, not a repair. The order is fixed:

1. **Reproduce it as a test**, before touching `src/`. Run it. Watch it fail — and check the
   failure is the *reported* one, not an unrelated error (a typo in the fixture also goes red).
2. **Then** fix.
3. Re-run: green.

**A test written after the fix is not finished until you have seen it red.** If the fix is
already in, take it back out and confirm the test fails, then restore it. Copy the file aside
(`cp x.py $SCRATCH/x.keep`) — **never `git stash`**: the stash stack is shared with every other
worktree and session on this machine, and a dropped entry is a lost afternoon.

**Assert the mechanism, not the surface.** The test should fail if the *cause* returns, not
merely if today's symptom does. A migration that dropped every index needed a test comparing
the rebuilt index set against a freshly-built one — counting rows would have passed throughout.

**A hand-check is not a test.** Anything proved by running something at the console — a
rehearsal against a copy of real data, a one-off script, a REPL poke — protects nothing after
today. Either it becomes a test or it did not happen. Every claim in `TestUpgradeRehearsal`
was a manual check first, and every one of them was true when checked and untrue a week later.

**Cover the guard, not just the guarded.** When a defence is added (a refusal, a fallback, a
fence), test that it *fires*, and test what happens when the thing it depends on is missing —
that is where the destructive cases live (a harness that cannot be consulted answering "empty").

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
  - **Both sides must be the same container type.** `compare(a_set, expected=A_FROZENSET)`
    doesn't compare as sets: testfixtures picks its comparer from the shared MRO, `set` and
    `frozenset` share none, so it falls back to the *order-sensitive* generator comparer — and
    since pydantic is installed (it registers `ignore_eq`, which blocks the `x == y`
    shortcut for containers), that fallback is always reached. Two sets with identical members
    then pass or fail on `PYTHONHASHSEED` alone — the machine deciding a test (above). Coerce
    one side: `expected=set(FROZEN_CONST)`.
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
- `ShouldRaise(SomeError, match='…')` — when the message embeds something you can't reconstruct
  (a subprocess's output, a timestamp) *or* comes from a third party in a form not ours to pin
  (a pydantic `ValidationError`'s field trace, versioned doc-link footer). `match` is a
  substring/regex of `str(exc)` — pin the stable bit that proves *which* thing failed (a field
  name, an error type), not the whole message.
- `ShouldRaise(SomeError)` — type only, and only when genuinely nothing in the message is
  stable enough to pin. Rare: a bare type check passes for *any* instance of that type, so it
  can't distinguish "the field we broke failed validation" from "some other field failed" or
  "validation didn't run at all" — a real gap, not a shortcut for a message that's merely
  verbose or third-party-formatted (that's what `match=` is for).

<!-- invisible-code-block: python
from pydantic import ValidationError
from testfixtures import ShouldRaise, TempDir
from giterator.testing import Repo

from chimera.commands.worktree.add import add
from chimera.config import NotInProjectError, find_project
from chimera.service_config import load_services_config

d = TempDir().create()
ws = d / 'ws'
repo = Repo.make(d / 'repo')  # deliberately no commits
worktrees = d / 'worktrees'
path = d.dump('services.yaml', {'services': [{'name': 'x', 'type': 'docker'}]})  # docker needs image
-->

```python
with ShouldRaise(NotInProjectError(ws / 'ghost')):   # exact: type + message
    find_project(ws / 'ghost')
with ShouldRaise(RuntimeError, match='no commits'):  # message embeds `git status` output
    add(repo.path, worktrees, goal='g')
with ShouldRaise(ValidationError, match=r'docker\.image\s+Field required'):  # the right field, not just "a" ValidationError
    load_services_config(path)
```
`ShouldRaise` is a context manager like `pytest.raises`; the value check lives in its argument,
not an `assert` afterwards.

`str_like`/`repr_like` (testfixtures 12.3+) also slot into `ShouldRaise` directly
(`ShouldRaise(str_like(SomeError, '…'))`), but they check an *exact* `str()`/`repr()` (or a
regex via their own `match=`) — reach for them only when checking an exception *outside* a
raise (nested inside a larger `compare()`, or a constructed-not-raised instance). For "this
raise's message contains X", `ShouldRaise(SomeError, match='X')` already does the job and is
simpler.

## Mocking

Use `testfixtures.Replacer` — never `monkeypatch.setattr`, never a dotted-path string target.
Reach for the typed helper that fits:
- `replace.in_module(thing, replacement)` — module-level functions/objects
- `replace.on_class(Cls.method, replacement)` — methods/attributes on a class
- `replace.in_environ(name, value)` — environment variables
If (and only if) none of those can express it, fall back to the explicit form:
`replace(target=thing, container=parent, name='<attr>', replacement=<>)`.

**Stub the chokepoint, not the wrapper it happens to use.** `subprocess.run` is a thin wrapper
over `subprocess.Popen`, so a stub on `Popen` covers `run`/`check_output`/`call` and any future
spelling, while a stub on `run` covers exactly one. For anything irreversible — spawning an
agent, pushing, deleting — stub `Popen`; stub `run` only to shape the return value of something
harmless if it *did* execute (a `git log`, a `gh` read). Every launch test once stubbed `run`
and was correct when written; production moving down one layer silently un-guarded all of them
and started real sessions.

**A stand-in must answer to the name it replaces.** `Replacer.in_module` addresses its target by
the `__module__`/`__name__` of the object handed to it, so if something already replaced that
attribute, a later `in_module` resolves to the *stand-in's* defining module and fails with
`Original '<stand-in>' not found` (testfixtures#259). A replacement that outlives one test —
a conftest guard — sets `stand_in.__module__, stand_in.__name__ = real.__module__, real.__name__`.

**Never reach the user's live workspace.** Two autouse guards in `tests/conftest.py`, and
both are positional rather than trusting configuration (the host-state guards above are the
same bargain, for a different question):

- `_no_real_harness` refuses any `Popen` naming a registered harness (below).
- `_no_live_archive` refuses any `Archive.open` outside *this test's own* `tmpdir`. It depends
  on the `tmpdir` fixture deliberately: that chdirs every test into a fresh directory, so
  workspace resolution — which **walks up from cwd**, and the suite runs inside a chimera
  worktree — can't reach the real workspace at all, and the check then has something concrete
  to compare against. `tempfile.gettempdir()` would not do: "somewhere temporary" is satisfied
  by any stray path, where "under this test's directory" is the actual rule.

Clearing `$CHIMERA_WORKSPACE` does not cover this: identity resolution reads the archive on
every `ch` invocation, and the walk-up finds the live workspace with the variable unset.

**Loguru's default `extra` is process-global and nothing else resets it.**
`chimera.logging.configure` binds `caller`/`seat` with `logger.configure(extra=…)`, which
`LogCapture` does *not* restore — its loguru source saves and restores the handlers and the
minimum level, a different thing (testfixtures#265). Without the `_clear_bound_identity`
fixture, a test that never configures logging inherits whoever ran last, and log assertions
start depending on test order.

**Never launch a real agent session.** An autouse `_no_real_harness` fixture (`tests/conftest.py`)
refuses any `Popen` whose argv names a registered harness. Observe a launch with
`tests.cli.capture_launches`, a registry query with `MockPopen` — never by stubbing a layer of the
launcher's choosing. This is a guard, not a convention: the env clearing beside it *cannot* help,
because a spawned session is served by a pooled worker holding the daemon's environment (the user's
real `$CHIMERA_WORKSPACE`), so its hooks write to the live archive. The launch is the only place
this can be stopped.

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
- dep: `testfixtures>=12.3.0` (PyPI)

*Writing structured files* — `d.dump(relpath, obj)` serialises by extension (`.yaml`/`.json`;
`.toml` would need tomlkit, which this project doesn't install), creating parent dirs; never
hand-format YAML/JSON. `d.parse(relpath)` reads it back.
Plain text stays `d.write(relpath, text)`.
```python
d.dump('proj/config.yaml', {'kind': 'project', 'repo': str(repo.path)})  # → kind: project\nrepo: …
```

*Checking directory structure* — assert on-disk layout with `d.compare(...)`, which checks the
**whole** listing (so it also proves nothing extra was written) and scopes with `path=`:
<!-- skip: next "the two lines are before/after snapshots of the same dir — they can't both hold at once" -->
```python
d.compare(['feature-x@agent'], path='worktrees', recursive=False)  # exactly one worktree, no human
d.compare(path='worktrees', expected=())                           # …emptied after goal finish
```
Drop to a single `path.exists()`/`path.is_dir() is True/False` only when it's significantly
shorter than the equivalent `compare` (e.g. one path among an otherwise-noisy tree like a repo).

**giterator.testing.Repo** (`from giterator.testing import Repo`)
- `Repo.make(path)` — creates an initialized git repo at path
- `Repo.clone(source, path)` — clones and always configures a user in the clone (inherited from
  a `Git` `source`, else the same default as `Repo.make`) — use this, never `chimera.git.Git.clone`
  + `Repo(path)`, which leaves the clone with no local identity so a commit in it silently falls
  back to the machine's global git config (works on a dev box, fails on CI — see the top note)
- `repo.commit_content('prefix', datetime(...))` — writes file and commits, returns short hash
- `repo('log', ...)` — run raw git commands (instance is callable)
- dep: `giterator>=1.1.0` (PyPI)
- conftest fixture pattern: `with TempDir() as d: yield Repo.make(d.path / 'repo')`

<!-- invisible-code-block: python
d.cleanup()
-->

