import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.agents import Session
from chimera.commands.agent import live
from chimera.commands.goal.merge import MergeResult, merge
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import Checkout
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    replace.in_module(live, lambda worktree: [])


def _short(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref)


def _full(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref, short=False)


def _goal(tmpdir: TempDir, repo_path: Path) -> Path:
    worktrees = tmpdir / 'worktrees'
    add(repo_path, worktrees, goal='g')
    return worktrees


def _refs_log() -> LogCapture:
    return LogCapture(LoguruSource(('message', 'extra'), level='INFO'))


def test_lands_the_agent_branch_and_sweeps(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('work')
    tip = _short(git_repo.path, 'g/agent')
    result = merge(git_repo.path, worktrees, 'g')
    compare(
        result,
        expected=MergeResult('g/agent', 'main', tip, True, removed=(worktrees / 'g@agent',)),
    )
    compare(_short(git_repo.path, 'main'), expected=tip)
    compare(Git(git_repo.path).branches(), expected=['main'])
    tmpdir.compare(path='worktrees', expected=())


def test_noop_when_base_already_contains_the_work(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)  # agent branched at main's tip, no new work
    tip = _short(git_repo.path, 'main')
    result = merge(git_repo.path, worktrees, 'g')
    compare(
        result,
        expected=MergeResult('g/agent', 'main', tip, False, removed=(worktrees / 'g@agent',)),
    )
    compare(Git(git_repo.path).branches(), expected=['main'])


def _squashed_human(tmpdir: TempDir, git_repo: Repo, worktrees: Path) -> None:
    """g/human: one commit squashing g/agent's two, its temporary checkout removed after."""
    agent = Repo(worktrees / 'g@agent')
    agent.commit_content('one')
    agent.commit_content('two')
    git = Git(git_repo.path)
    checkout = tmpdir / 'human'
    git('branch', '--no-track', 'g/human', 'main')
    git('worktree', 'add', str(checkout), 'g/human')
    human = Git(checkout)
    human('merge', '--squash', 'g/agent')
    human('commit', '-m', 'squash')
    git('worktree', 'remove', str(checkout))


def test_prefers_the_branch_containing_the_others_work(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    _squashed_human(tmpdir, git_repo, worktrees)
    tip = _short(git_repo.path, 'g/human')
    result = merge(git_repo.path, worktrees, 'g')
    compare(
        result,
        expected=MergeResult('g/human', 'main', tip, True, removed=(worktrees / 'g@agent',)),
    )
    compare(_short(git_repo.path, 'main'), expected=tip)
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_equivalent_tips_prefer_the_human_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('work')
    Git(git_repo.path)('branch', '--no-track', 'g/human', 'g/agent')
    tip = _short(git_repo.path, 'g/human')
    result = merge(git_repo.path, worktrees, 'g')
    compare(
        result,
        expected=MergeResult('g/human', 'main', tip, True, removed=(worktrees / 'g@agent',)),
    )


def test_refuses_diverged_actors(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('agent-work')
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'main')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work')
    with ShouldRaise(
        UserError(
            'no actor branch contains all the others (g/agent, g/human) — '
            'ch goal sync g so one does, or --force to land the newest-committed '
            'and discard the rest'
        )
    ):
        merge(git_repo.path, worktrees, 'g')


def test_refuses_when_base_has_its_own_commits(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('agent-work')
    git_repo.commit_content('mainline')
    with ShouldRaise(
        UserError(
            'main has commits g/agent lacks — rebase g/agent onto main in its worktree '
            '(git rebase main), then re-run'
        )
    ):
        merge(git_repo.path, worktrees, 'g')


def test_refuses_a_dirty_goal_worktree(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('work')
    (agent_worktree / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        UserError(
            f'refusing to merge (use --force to discard):\n'
            f'  {agent_worktree} has uncommitted or untracked changes'
        )
    ):
        merge(git_repo.path, worktrees, 'g')


def test_refuses_a_dirty_base_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('work')
    (git_repo.path / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        UserError(
            f'main is checked out with uncommitted changes at {git_repo.path.resolve()} — '
            f'commit or stash there first'
        )
    ):
        merge(git_repo.path, worktrees, 'g')


def test_force_lands_the_newest_and_discards_the_rest(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'main')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work', datetime(2026, 1, 1))
    git('worktree', 'remove', str(checkout))
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('agent-work', datetime(2026, 2, 1))
    (agent_worktree / 'scratch.txt').write_text('wip')  # force discards dirt too
    tip = _short(git_repo.path, 'g/agent')
    result = merge(git_repo.path, worktrees, 'g', force=True)
    compare(
        result,
        expected=MergeResult('g/agent', 'main', tip, True, removed=(agent_worktree,)),
    )
    compare(_short(git_repo.path, 'main'), expected=tip)
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_lands_a_plain_checkout_on_the_base(tmpdir: TempDir, bare_repo: Path) -> None:
    worktrees = _goal(tmpdir, bare_repo)
    Repo(worktrees / 'g@agent').commit_content('work')
    git = Git(bare_repo)
    git('branch', '--no-track', 'g/human', 'g/agent')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    tip = _short(bare_repo, 'g/human')
    result = merge(bare_repo, worktrees, 'g')
    compare(
        result,
        expected=MergeResult(
            'g/human',
            'main',
            tip,
            True,
            landed=(Checkout(done=True, where=checkout.resolve(), branch='main', was='g/human'),),
            removed=(worktrees / 'g@agent',),
        ),
    )
    compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
    compare(_short(bare_repo, 'main'), expected=tip)
    compare(git.branches(), expected=['main'])


def test_refuses_a_dirty_plain_checkout(tmpdir: TempDir, bare_repo: Path) -> None:
    worktrees = _goal(tmpdir, bare_repo)
    git = Git(bare_repo)
    git('branch', '--no-track', 'g/human', 'g/agent')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    (checkout / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        UserError(
            f'g/human is checked out with uncommitted changes at {checkout.resolve()} — '
            f'commit or stash there first'
        )
    ):
        merge(bare_repo, worktrees, 'g')


def test_refuses_a_plain_checkout_when_the_base_is_busy(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'g/agent')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    with ShouldRaise(
        UserError(
            f'g/human is checked out at {checkout.resolve()}, but main is already checked out '
            f'at {git_repo.path.resolve()} — git checkout something else there, then re-run'
        )
    ):
        merge(git_repo.path, worktrees, 'g')


def test_refuses_a_goal_with_no_branches(tmpdir: TempDir, git_repo: Repo) -> None:
    with ShouldRaise(UserError("nothing to merge — no actor branches for goal 'ghost'")):
        merge(git_repo.path, tmpdir / 'worktrees', 'ghost')


def test_refuses_a_missing_base(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    with ShouldRaise(UserError('no branch release to merge into')):
        merge(git_repo.path, worktrees, 'g', into='release')


def test_refuses_a_goal_branch_as_base(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    with ShouldRaise(UserError("g/agent is one of g's own branches — name a base like main")):
        merge(git_repo.path, worktrees, 'g', into='g/agent')


def test_into_lands_on_the_named_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Git(git_repo.path)('branch', 'release', 'main')
    Repo(worktrees / 'g@agent').commit_content('work')
    tip = _short(git_repo.path, 'g/agent')
    main_before = _full(git_repo.path, 'main')
    result = merge(git_repo.path, worktrees, 'g', into='release', fetch=False)
    compare(
        result,
        expected=MergeResult('g/agent', 'release', tip, True, removed=(worktrees / 'g@agent',)),
    )
    compare(_short(git_repo.path, 'release'), expected=tip)
    compare(_full(git_repo.path, 'main'), expected=main_before)


def test_dry_previews_the_whole_landing(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('work')
    session = Session('x', 'p@g@agent', 'idle', agent_worktree, None, pid=4242)
    replace.in_module(live, lambda worktree: [session])
    main_before = _full(git_repo.path, 'main')
    tip = _short(git_repo.path, 'g/agent')
    result = merge(git_repo.path, worktrees, 'g', dry=Dry(True))
    compare(
        result,
        expected=MergeResult(
            'g/agent', 'main', tip, True, stopped=(session,), removed=(agent_worktree,)
        ),
    )
    compare(_full(git_repo.path, 'main'), expected=main_before)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])
    assert agent_worktree.is_dir()


def test_stops_the_live_agent_before_the_sweep(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('work')
    out = subprocess.run(  # not our child, so no zombie confuses the exit polling
        ['bash', '-c', 'sleep 60 & echo $!'], capture_output=True, text=True, check=True
    )
    pid = int(out.stdout)
    session = Session('x', 'p@g@agent', 'idle', agent_worktree, None, pid=pid)
    replace.in_module(live, lambda worktree: [session])
    try:
        result = merge(git_repo.path, worktrees, 'g')
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    compare(result.stopped, expected=(session,))
    with ShouldRaise(ProcessLookupError):
        os.kill(pid, 0)


def test_logs_the_landing(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo.path)
    Repo(worktrees / 'g@agent').commit_content('work')
    old_main = _full(git_repo.path, 'main')
    tip = _full(git_repo.path, 'g/agent')
    with _refs_log() as log:
        merge(git_repo.path, worktrees, 'g')
    log.check(
        ('goal merge: source', {'source': 'g/agent', 'candidates': ['g/agent']}),
        (
            'goal merge: refs',
            {
                'goal': 'g',
                'source': 'g/agent',
                'git': {'before': {'main': old_main}, 'after': {'main': tip}},
            },
        ),
        (
            'worktree rm: refs',
            {
                'goal': 'g',
                'force': True,
                'git': {'before': {'g/agent': tip}, 'after': {}},
            },
        ),
    )


def test_goal_merge_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    worktrees = Path.cwd() / 'worktrees'  # the CLI resolves paths from cwd
    Repo(worktrees / 'g@agent').commit_content('work')
    tip_short, tip = _short(git_repo.path, 'g/agent'), _full(git_repo.path, 'g/agent')
    old_main = _full(git_repo.path, 'main')
    start, end = action_logs(
        'goal merge',
        'chimera.commands.goal.merge.merge',
        {
            'goal': 'g',
            'into': None,
            'force': False,
            'offline': False,
            'dry': False,
            'project': None,
        },
    )
    command.run('goal', 'merge', 'g').check(
        output=(f'Fast-forwarded main to g/agent ({tip_short})\nRemoved {worktrees / "g@agent"}'),
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal merge: source',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'source': 'g/agent',
                'git': {'before': {'main': old_main}, 'after': {'main': tip}},
                'message': 'goal merge: refs',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'force': True,
                'git': {'before': {'g/agent': tip}, 'after': {}},
                'message': 'worktree rm: refs',
            },
            end,
        ],
    )
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_goal_merge_cli_already_contained(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    worktrees = Path.cwd() / 'worktrees'  # the CLI resolves paths from cwd
    tip = _short(git_repo.path, 'main')
    start, end = action_logs(
        'goal merge',
        'chimera.commands.goal.merge.merge',
        {
            'goal': 'g',
            'into': None,
            'force': False,
            'offline': False,
            'dry': False,
            'project': None,
        },
    )
    command.run('goal', 'merge', 'g').check(
        output=(f'main already contains g/agent ({tip})\nRemoved {worktrees / "g@agent"}'),
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal merge: source',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'force': True,
                'git': {
                    'before': {'g/agent': _full(git_repo.path, 'main')},
                    'after': {},
                },
                'message': 'worktree rm: refs',
            },
            end,
        ],
    )


def test_goal_merge_cli_dry_previews(
    tmpdir: TempDir, git_repo: Repo, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    worktrees = Path.cwd() / 'worktrees'  # the CLI resolves paths from cwd
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('work')
    session = Session('x', 'myproject@g@agent', 'idle', agent_worktree, None, pid=4242)
    replace.in_module(live, lambda worktree: [session])
    tip_short = _short(git_repo.path, 'g/agent')
    old_main = _full(git_repo.path, 'main')
    start, end = action_logs(
        'goal merge',
        'chimera.commands.goal.merge.merge',
        {'goal': 'g', 'into': None, 'force': False, 'offline': False, 'dry': True, 'project': None},
    )
    command.run('goal', 'merge', 'g', '--dry').check(
        output=(
            f'Would fast-forward main to g/agent ({tip_short})\n'
            f'Would stop myproject@g@agent (pid 4242)\n'
            f'Would remove {agent_worktree}'
        ),
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal merge: source',
            },
            end,
        ],
    )
    compare(_full(git_repo.path, 'main'), expected=old_main)
    assert agent_worktree.is_dir()


def test_goal_merge_cli_lands_a_plain_checkout(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    worktrees = Path.cwd() / 'worktrees'  # the CLI resolves paths from cwd
    Repo(worktrees / 'g@agent').commit_content('work')
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'g/agent')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    git('checkout', '--detach', 'main')  # free main so the plain checkout can land on it
    tip_short, tip = _short(git_repo.path, 'g/human'), _full(git_repo.path, 'g/human')
    old_main = _full(git_repo.path, 'main')
    start, end = action_logs(
        'goal merge',
        'chimera.commands.goal.merge.merge',
        {
            'goal': 'g',
            'into': None,
            'force': False,
            'offline': False,
            'dry': False,
            'project': None,
        },
    )
    command.run('goal', 'merge', 'g').check(
        output=(
            f'Fast-forwarded main to g/human ({tip_short})\n'
            f'Checked out main at {checkout.resolve()} (was g/human)\n'
            f'Removed {worktrees / "g@agent"}'
        ),
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/human',
                'candidates': ['g/agent', 'g/human'],
                'message': 'goal merge: source',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'source': 'g/human',
                'git': {'before': {'main': old_main}, 'after': {'main': tip}},
                'message': 'goal merge: refs',
            },
            {
                'level': 'INFO',
                'worktree': str(checkout.resolve()),
                'git': {'before': {'g/human': tip}, 'after': {'main': tip}},
                'message': 'goal merge: refs',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'force': True,
                'git': {'before': {'g/agent': tip, 'g/human': tip}, 'after': {}},
                'message': 'worktree rm: refs',
            },
            end,
        ],
    )
    compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')


def test_goal_merge_cli_refuses_a_diverged_base(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')
    git_repo.commit_content('mainline')
    error = (
        'main has commits g/agent lacks — rebase g/agent onto main in its worktree '
        '(git rebase main), then re-run'
    )
    start, end = action_logs(
        'goal merge',
        'chimera.commands.goal.merge.merge',
        {
            'goal': 'g',
            'into': None,
            'force': False,
            'offline': False,
            'dry': False,
            'project': None,
        },
        error=f'UserError: {error}',
    )
    command.run('goal', 'merge', 'g').check(
        output=f'Error: {error}',
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal merge: source',
            },
            end,
        ],
        return_code=1,
    )
