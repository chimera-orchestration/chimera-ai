from collections.abc import Iterator
from pathlib import Path

import pytest
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, TempDir, not_there

from chimera.__main__ import app
from chimera.commands.init import init
from tests.cli import Command, Run, full_capture


@pytest.fixture()
def replace() -> Iterator[Replacer]:
    with Replacer() as replacer:
        yield replacer


@pytest.fixture(autouse=True)
def _clear_workspace_env(replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', not_there)  # tests opt in explicitly
    replace.in_environ('SHELL', not_there)  # keeps the shell-completion check inert
    replace.in_environ('CLAUDECODE', not_there)  # the suite itself often runs under an AI agent


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
