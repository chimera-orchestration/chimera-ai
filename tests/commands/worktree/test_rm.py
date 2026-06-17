from datetime import datetime
from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, Replacer, ShouldRaise, TempDir, compare

from chimera.commands.agent import live_sessions
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.add import add
from chimera.commands.worktree.rm import remove


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    replace.in_module(live_sessions, lambda worktree: [], module=worktree_rm)


def _goal(tmpdir: TempDir) -> tuple[Repo, Path]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    worktrees = tmpdir.path / 'worktrees'
    add(repo.path, worktrees, 'g')
    return repo, worktrees


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    (tmpdir.path / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    return tmpdir.path


def test_remove_is_a_noop_for_a_goal_that_was_never_created(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    compare(remove(repo.path, tmpdir.path / 'worktrees', 'ghost'), expected=[])


def test_remove_aborts_when_an_agent_is_running(tmpdir: TempDir, replace: Replacer) -> None:
    repo, worktrees = _goal(tmpdir)
    replace.in_module(
        live_sessions,
        lambda worktree: [
            {
                'pid': 4242,
                'kind': 'interactive',
                'status': 'idle',
                'startedAt': 1781247747055,
                'name': 'sybil@g@agent',
                'sessionId': 'x',
            }
        ],
        module=worktree_rm,
    )
    since = f'{datetime.fromtimestamp(1781247747055 / 1000):%a %H:%M}'
    with ShouldRaise(
        RuntimeError(
            f'an agent is live in {worktrees / "g@agent"}:\n'
            f'  pid 4242  interactive  idle  since {since}  sybil@g@agent\n'
            'find its terminal or kill the pid, then re-run'
        )
    ):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir() is True  # nothing removed
    compare(Git(repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_force_bypasses_the_liveness_check(tmpdir: TempDir, replace: Replacer) -> None:
    repo, worktrees = _goal(tmpdir)
    replace.in_module(
        live_sessions,
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
        module=worktree_rm,
    )
    remove(repo.path, worktrees, 'g', force=True)
    assert (worktrees / 'g@agent').exists() is False
    compare(Git(repo.path).branches(), expected=['main'])


def test_remove_takes_out_worktrees_and_branches(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    compare(remove(repo.path, worktrees, 'g'), expected=[worktrees / 'g@agent'])  # only the agent
    assert (worktrees / 'g@agent').exists() is False
    compare(Git(repo.path).branches(), expected=['main'])


def test_remove_refuses_uncommitted_changes(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        RuntimeError(
            'refusing to clean up (use --force to discard):\n'
            f'  {worktrees / "g@agent"} has uncommitted or untracked changes'
        )
    ):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir() is True
    compare(Git(repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_refuses_unmerged_branch(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # branch now ahead of main
    with ShouldRaise(
        RuntimeError(
            'refusing to clean up (use --force to discard):\n  branch g/agent has unmerged commits'
        )
    ):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir() is True
    compare(Git(repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_force_discards_unsaved_work(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    remove(repo.path, worktrees, 'g', force=True)
    assert (worktrees / 'g@agent').exists() is False
    compare(Git(repo.path).branches(), expected=['main'])


def test_worktree_rm_cli(tmpdir: TempDir, command: Command) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = _project(tmpdir, repo)
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g').check(
        output=f'Removed {worktree}', logging=[('INFO', 'worktree rm')]
    )
    assert (project / 'worktrees' / 'g@agent').exists() is False
    compare(Git(repo.path).branches(), expected=['main'])


def test_worktree_rm_cli_reports_nothing_to_remove(tmpdir: TempDir, command: Command) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    _project(tmpdir, repo)
    command.run('worktree', 'rm', 'ghost').check(
        output='Nothing to remove for ghost', logging=[('INFO', 'worktree rm')]
    )


def test_goal_finish_cli(tmpdir: TempDir, command: Command) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = _project(tmpdir, repo)
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    # finish is the lifecycle name for rm
    command.run('goal', 'finish', 'g').check(
        output=f'Removed {worktree}', logging=[('INFO', 'goal finish')]
    )
    assert (project / 'worktrees' / 'g@agent').exists() is False
    compare(Git(repo.path).branches(), expected=['main'])
