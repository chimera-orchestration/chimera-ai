from datetime import datetime
from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.agent import live_sessions
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.add import add
from chimera.commands.worktree.rm import remove
from tests.cli import Command, action_logs


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


def test_remove_allows_a_squash_merged_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # g/agent ahead of main
    git = Git(git_repo.path)
    git('merge', '-q', '--squash', 'g/agent')
    git('commit', '-qm', 'squash g')  # main now carries g/agent's diff under a new commit
    remove(git_repo.path, worktrees, 'g')  # recognised as merged → no --force needed
    tmpdir.compare(path='worktrees', expected=())
    compare(git.branches(), expected=['main'])


def test_remove_recognises_an_upstream_merge_only_after_fetch(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir / 'repo')
    worktrees = tmpdir / 'worktrees'
    add(local.path, worktrees, 'g', fetch=False)
    Repo(worktrees / 'g@agent').commit_content('work', datetime(2022, 1, 1))  # newer than seed
    # the PR merges on origin: push the work to a side branch, then merge it into origin's main.
    # local's origin/main tracking ref stays stale until a fetch — pushing to main would update it.
    local('push', '-q', 'origin', 'g/agent:refs/heads/incoming')
    origin('merge', '-q', 'incoming')  # origin's main now carries g/agent
    with ShouldRaise(RuntimeError, match='branch g/agent has unmerged commits'):
        remove(local.path, worktrees, 'g', fetch=False)  # stale refs don't see the merge
    remove(local.path, worktrees, 'g')  # fetch refreshes origin/main → merged
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(local.path).branches(), expected=['main'])


def test_remove_logs_the_refs_it_deletes(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    git = Git(git_repo.path)
    tip = git.rev_parse('main', short=False)  # both actor branches start at main
    with LogCapture(LoguruSource(('message', 'extra'))) as log:
        remove(git_repo.path, worktrees, 'g')
    log.check(
        (
            'worktree rm: refs',
            {
                'goal': 'g',
                'git': {'before': {'g/human': tip, 'g/agent': tip}, 'after': {}},
                'force': False,
            },
        ),
    )


def test_remove_force_logs_the_discarded_refs(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_tip = Repo(worktrees / 'g@agent').commit_content('work', short=False)  # unmerged
    git = Git(git_repo.path)
    human_tip = git.rev_parse('g/human', short=False)
    with LogCapture(LoguruSource(('message', 'extra'))) as log:
        remove(git_repo.path, worktrees, 'g', force=True)
    log.check(
        (
            'worktree rm: refs',
            {
                'goal': 'g',
                # the unmerged agent commit is recorded by sha before being discarded
                'git': {'before': {'g/human': human_tip, 'g/agent': agent_tip}, 'after': {}},
                'force': True,
            },
        ),
    )


def test_remove_a_ghost_goal_logs_nothing(tmpdir: TempDir, git_repo: Repo) -> None:
    with LogCapture(LoguruSource(('message', 'extra'))) as log:
        remove(git_repo.path, tmpdir / 'worktrees', 'ghost')
    log.check()  # no refs changed → no ref record


def test_remove_uses_the_repos_default_branch(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir / 'repo')
    repo.commit_content('seed')
    repo('branch', '-m', 'main', 'master')  # master-style default
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, 'g')  # branches off master; nothing added → merged
    remove(repo.path, worktrees, 'g')
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(repo.path).branches(), expected=['master'])


WR = 'chimera.commands.worktree.rm.remove'


def _rm_refs(cli: str, goal: str, base: str, *, offline: bool = False) -> list[dict[str, object]]:
    """start / `worktree rm: refs` event / end — the lines a rm or finish logs."""
    start, end = action_logs(
        cli, WR, {'goal': goal, 'force': False, 'project': None, 'offline': offline}
    )
    refs = {f'{goal}/agent': base, f'{goal}/human': base}
    event = {
        'level': 'INFO',
        'goal': goal,
        'git': {'before': refs, 'after': {}},
        'force': False,
        'message': f'{cli}: refs' if cli == 'worktree rm' else 'worktree rm: refs',
    }
    return [start, event, end]


def test_worktree_rm_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', 'g')
    base = Git(git_repo.path)('rev-parse', 'g/agent').strip()
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g').check(
        output=f'Removed {worktree}',
        logging=_rm_refs('worktree rm', 'g', base),
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_worktree_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    _project(tmpdir, git_repo)
    command.run('worktree', 'rm', 'ghost').check(
        output='Nothing to remove for ghost',
        logging=action_logs(
            'worktree rm', WR, {'goal': 'ghost', 'force': False, 'project': None, 'offline': False}
        ),
    )


def test_goal_finish_cli_offline(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', 'g')
    base = Git(git_repo.path)('rev-parse', 'g/agent').strip()
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('goal', 'finish', 'g', '--offline').check(
        output=f'Removed {worktree}',
        logging=_rm_refs('goal finish', 'g', base, offline=True),
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_goal_finish_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', 'g')
    base = Git(git_repo.path)('rev-parse', 'g/agent').strip()
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    # finish is the lifecycle name for rm
    command.run('goal', 'finish', 'g').check(
        output=f'Removed {worktree}',
        logging=_rm_refs('goal finish', 'g', base),
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])
