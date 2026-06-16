from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, Replacer, TempDir

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
    assert remove(repo.path, tmpdir.path / 'worktrees', 'ghost') == []


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
    with pytest.raises(RuntimeError) as excinfo:
        remove(repo.path, worktrees, 'g')
    message = str(excinfo.value)
    assert 'agent is live' in message
    assert 'pid 4242' in message
    assert 'interactive' in message
    assert 'idle' in message
    assert 'since' in message
    assert 'sybil@g@agent' in message
    assert (worktrees / 'g@agent').is_dir()  # nothing removed
    assert 'g/human' in Git(repo.path).branches()


def test_remove_force_bypasses_the_liveness_check(tmpdir: TempDir, replace: Replacer) -> None:
    repo, worktrees = _goal(tmpdir)
    replace.in_module(
        live_sessions,
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
        module=worktree_rm,
    )
    remove(repo.path, worktrees, 'g', force=True)
    assert not (worktrees / 'g@agent').exists()
    assert 'g/agent' not in Git(repo.path).branches()


def test_remove_takes_out_worktrees_and_branches(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    removed = remove(repo.path, worktrees, 'g')
    assert removed == [worktrees / 'g@agent']  # only the agent has a worktree
    assert not (worktrees / 'g@agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_remove_refuses_uncommitted_changes(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')
    with pytest.raises(RuntimeError, match='changes'):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_remove_refuses_unmerged_branch(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # branch now ahead of main
    with pytest.raises(RuntimeError, match='unmerged'):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_remove_force_discards_unsaved_work(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    remove(repo.path, worktrees, 'g', force=True)
    assert not (worktrees / 'g@agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_worktree_rm_cli(tmpdir: TempDir, command: Command) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = _project(tmpdir, repo)
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g').check(
        output=f'Removed {worktree}', logging=[('INFO', 'worktree rm')]
    )
    assert not (project / 'worktrees' / 'g@agent').exists()
    assert 'g/human' not in Git(repo.path).branches()


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
    assert not (project / 'worktrees' / 'g@agent').exists()
    assert 'g/agent' not in Git(repo.path).branches()
