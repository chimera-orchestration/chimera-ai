import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from giterator import Git
from loguru import logger
from giterator.testing import DEFAULT_USER, Repo
from testfixtures import LogCapture, Replacer, TempDir, not_there

from chimera.__main__ import app
from chimera.agents.registry import AGENTS
from chimera.archive import Archive
from chimera.commands.init import init
from tests.cli import Command, Run, full_capture


@pytest.fixture()
def replace() -> Iterator[Replacer]:
    with Replacer() as replacer:
        yield replacer


@pytest.fixture(autouse=True)
def _no_real_harness(replace: Replacer) -> None:
    """Make it impossible for a test to launch a real agent session.

    A stray ``claude`` doesn't just waste a session: its serving process is a pooled
    worker holding the *daemon's* environment, so it reads the user's real
    ``$CHIMERA_WORKSPACE`` and its hooks write into the live archive — which the env
    clearing below cannot prevent, because that env never came from this process (see
    ``agent-docs/sessions.md``). Refusing the spawn is the only thing that can.

    Guarding :class:`subprocess.Popen` rather than ``subprocess.run`` is deliberate:
    ``run`` is a wrapper *over* ``Popen``, so this one guard covers every spelling and
    sits below any layer a test — or the launcher — might otherwise stub. A test that
    means to observe a launch opts in with :func:`tests.cli.capture_launches`.
    """
    real = subprocess.Popen

    def guarded(args: Any, *rest: Any, **kw: Any) -> Any:
        argv = list(args) if isinstance(args, list | tuple) else [args]
        if argv and Path(str(argv[0])).name in AGENTS:
            raise AssertionError(
                f'a test tried to run the real harness binary: {argv}\n'
                'stub the launch with tests.cli.capture_launches, or the registry query '
                'with a MockPopen — never at subprocess.run, which the harness may not use'
            )
        return real(args, *rest, **kw)

    # the stand-in must answer to the name it replaces: Replacer.in_module reads
    # __module__/__name__ off whatever it's handed, so a test replacing Popen again
    # would otherwise be sent looking for tests.conftest.guarded
    guarded.__module__, guarded.__name__ = real.__module__, real.__name__
    replace.in_module(subprocess.Popen, guarded)


@pytest.fixture(autouse=True)
def _clear_bound_identity() -> Iterator[None]:
    """Reset loguru's default extra, so one test's identity can't attribute the next's lines.

    ``chimera.logging.configure`` binds ``caller``/``seat`` with ``logger.configure(extra=…)``,
    which sets *process-global* state on loguru's core. ``LogCapture`` doesn't restore it —
    its loguru source saves and restores the handlers and the minimum level, which is a
    different thing — so without this a test that never configures logging inherits whoever
    ran last. Harmless in production (one process, one command); in a suite it makes log
    assertions depend on test order.
    """
    logger.configure(extra={})
    yield
    logger.configure(extra={})


@pytest.fixture(autouse=True)
def _no_live_archive(tmpdir: TempDir, replace: Replacer) -> None:
    """Make it impossible for a test to open an archive outside its own directory.

    Clearing ``$CHIMERA_WORKSPACE`` is not enough: workspace resolution *walks up from
    cwd*, and the suite itself runs inside a chimera worktree, so a test that stayed
    there resolved the user's live workspace. Identity resolution reads the archive on
    every ``ch`` invocation, which made that reachable from almost anywhere.

    Depending on ``tmpdir`` does the work twice over. It chdirs every test into a fresh
    directory, so the walk-up can't reach the real workspace in the first place; and it
    gives the check something concrete to compare against — *this test's* directory, not
    merely "somewhere temporary", which any stray path under the system temp dir would
    satisfy.
    """
    real = Archive.open.__func__  # the underlying function, not the bound classmethod
    # resolved on both sides: macOS hands out /var/folders/… for a TempDir while a path
    # built by walking up from cwd comes back /private/var/folders/…
    own = tmpdir.path.resolve()

    def guarded(cls: type[Archive], path: Path) -> Archive:
        if not path.resolve().is_relative_to(own):
            raise AssertionError(
                f'a test tried to open an archive outside its own directory: {path}\n'
                f'(this test may only use {own})'
            )
        return real(cls, path)

    replace.on_class(Archive.open, classmethod(guarded))


@pytest.fixture(autouse=True)
def _clear_workspace_env(replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', not_there)  # tests opt in explicitly
    replace.in_environ('SHELL', not_there)  # keeps the shell-completion check inert
    replace.in_environ('CLAUDECODE', not_there)  # the suite itself often runs under an AI agent
    replace.in_environ('CLAUDE_CODE_ENTRYPOINT', not_there)  # …whose entrypoint must not leak in
    replace.in_environ('CLAUDE_CODE_SESSION_ID', not_there)  # …nor its id, into identity checks
    replace.in_environ('CHIMERA_ROLE', not_there)  # …possibly one chimera itself launched
    replace.in_environ('CHIMERA_ROLE_SCOPE', not_there)
    replace.in_environ('CHIMERA_SESSION', not_there)  # …whose address must not attribute logs


@pytest.fixture()
def tmpdir() -> Iterator[TempDir]:
    with TempDir(cwd=True) as d:
        yield d


@pytest.fixture()
def workspace(tmpdir: TempDir) -> Path:
    return init(tmpdir / 'lycia')


@pytest.fixture()
def git_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir / 'repo')
    repo.commit_content('seed')
    return repo


@pytest.fixture()
def bare_repo(tmpdir: TempDir) -> Path:
    """A bare `repo/` with one commit on main and no remote — what `ch project new` leaves,
    so (unlike `git_repo`) main isn't checked out anywhere and is free to check out."""
    source = Repo.make(tmpdir / 'source')
    source.commit_content('seed')
    repo = tmpdir / 'proj' / 'repo'
    Git(tmpdir.path)('init', '--bare', '-b', 'main', str(repo))
    bare = Git(repo)
    # worktrees share this config: commits made in them must not depend on the machine's
    # git identity, just as Repo.make ensures for the repos it creates
    bare('config', 'user.name', DEFAULT_USER.name)
    bare('config', 'user.email', DEFAULT_USER.email)
    source('push', str(repo), 'main')
    return repo


@pytest.fixture()
def workspace_with_env(workspace: Path, replace: Replacer) -> Path:
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    return workspace


@pytest.fixture()
def full_logs() -> Iterator[LogCapture]:
    with full_capture() as captured:
        yield captured


@pytest.fixture()
def command() -> Command:
    return Command(app, runner=Run)
