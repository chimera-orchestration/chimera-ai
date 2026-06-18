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


def _goal(tmpdir: TempDir, repo: Repo) -> Path:
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, 'g')
    return worktrees


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    return tmpdir.path


def test_remove_is_a_noop_for_a_goal_that_was_never_created(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    compare(remove(git_repo.path, tmpdir / 'worktrees', 'ghost'), expected=[])


def test_remove_aborts_when_an_agent_is_running(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
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
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_force_bypasses_the_liveness_check(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    replace.in_module(
        live_sessions,
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
        module=worktree_rm,
    )
    remove(git_repo.path, worktrees, 'g', force=True)
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_takes_out_worktrees_and_branches(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    compare(
        remove(git_repo.path, worktrees, 'g'), expected=[worktrees / 'g@agent']
    )  # only the agent
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_refuses_uncommitted_changes(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        RuntimeError(
            'refusing to clean up (use --force to discard):\n'
            f'  {worktrees / "g@agent"} has uncommitted or untracked changes'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_refuses_unmerged_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # branch now ahead of main
    with ShouldRaise(
        RuntimeError(
            'refusing to clean up (use --force to discard):\n  branch g/agent has unmerged commits'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_force_discards_unsaved_work(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    remove(git_repo.path, worktrees, 'g', force=True)
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_worktree_rm_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g').check(
        output=f'Removed {worktree}', logging=[('INFO', 'worktree rm')]
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_worktree_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    _project(tmpdir, git_repo)
    command.run('worktree', 'rm', 'ghost').check(
        output='Nothing to remove for ghost', logging=[('INFO', 'worktree rm')]
    )


def test_goal_finish_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    # finish is the lifecycle name for rm
    command.run('goal', 'finish', 'g').check(
        output=f'Removed {worktree}', logging=[('INFO', 'goal finish')]
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])
