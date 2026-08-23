import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.agent_env import ai_session
from chimera.agents import Session
from chimera.agents.claude import Claude
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.add import add
from chimera.commands.worktree.rm import RemoveResult, remove
from chimera.config import UserError
from chimera.dry import Dry
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    # both rm's own gathering and a forced stop() fan out through the registered harnesses
    replace.on_class(Claude.live, lambda self, cwd=None: [])


def _goal(tmpdir: TempDir, repo: Repo) -> Path:
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, goal='g')
    return worktrees


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    return tmpdir.path


def test_remove_is_a_noop_for_a_goal_that_was_never_created(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    compare(remove(git_repo.path, tmpdir / 'worktrees', 'ghost'), expected=RemoveResult())


def test_remove_aborts_when_an_agent_is_running(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    replace.on_class(
        Claude.live,
        lambda self, cwd=None: [
            Session(
                id='x',
                name='sybil@g@agent',
                status='idle',
                cwd=worktrees / 'g@agent',
                summary=None,
                pid=4242,
                kind='interactive',
                started=datetime.fromtimestamp(1781247747055 / 1000),
            )
        ],
        name='live',
    )
    since = f'{datetime.fromtimestamp(1781247747055 / 1000):%a %H:%M}'
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            f'  an agent is live in {worktrees / "g@agent"}: '
            f'pid 4242  interactive  idle  since {since}  sybil@g@agent\n'
            'use --force to stop the agents'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_force_stops_the_live_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    out = subprocess.run(  # not our child, so no zombie confuses the exit polling
        ['bash', '-c', 'sleep 60 & echo $!'], capture_output=True, text=True, check=True
    )
    pid = int(out.stdout)
    session = Session('x', 'p@g@agent', 'idle', worktrees / 'g@agent', None, pid=pid)
    replace.on_class(Claude.live, lambda self, cwd=None: [session], name='live')
    try:
        result = remove(git_repo.path, worktrees, 'g', force=True)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    compare(result, expected=RemoveResult((worktrees / 'g@agent',), stopped=(session,)))
    with ShouldRaise(ProcessLookupError):
        os.kill(pid, 0)
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_refuses_while_a_parked_session_owns_the_worktree(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    # parked (worker reaped, revivable) blocks a sweep exactly as live does — removing
    # the worktree would orphan the session the daemon can still respawn into it
    worktrees = _goal(tmpdir, git_repo)
    session = Session(
        id='ab12cd34-e776-4059',
        name='sybil@g@agent',
        status='parked',
        cwd=worktrees / 'g@agent',
        summary=None,
        kind='background',
        parked=True,
    )
    replace.on_class(Claude.live, lambda self, cwd=None: [session], name='live')
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            f'  an agent is parked in {worktrees / "g@agent"}: '
            'background  parked  sybil@g@agent — ch wake revives it\n'
            'use --force to stop the agents'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed


def test_remove_force_refuses_a_session_it_cannot_stop(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    session = Session('x', 'p@g@agent', 'idle', worktrees / 'g@agent', None)  # no pid to signal
    replace.on_class(Claude.live, lambda self, cwd=None: [session], name='live')
    with ShouldRaise(
        UserError('p@g@agent reports no pid — stop it from its own harness, then re-run')
    ):
        remove(git_repo.path, worktrees, 'g', force=True)
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_dry_force_previews_the_stop(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    session = Session('x', 'p@g@agent', 'idle', worktrees / 'g@agent', None, pid=4242)
    replace.on_class(Claude.live, lambda self, cwd=None: [session], name='live')
    compare(
        remove(git_repo.path, worktrees, 'g', force=True, dry=Dry(on=True)),
        expected=RemoveResult((worktrees / 'g@agent',), stopped=(session,)),
    )
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing stopped or removed
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_takes_out_worktrees_and_branches(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    compare(
        remove(git_repo.path, worktrees, 'g'),
        expected=RemoveResult((worktrees / 'g@agent',)),  # only the agent
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_takes_out_on_demand_actor_branches(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'g/agent')  # materialised later by `goal sync`
    git('branch', '--no-track', 'g/reviewer', 'g/agent')
    compare(remove(git_repo.path, worktrees, 'g'), expected=RemoveResult((worktrees / 'g@agent',)))
    tmpdir.compare(path='worktrees', expected=())
    compare(git.branches(), expected=['main'])  # every g/* branch gone, not just agent


def test_remove_sweeps_the_goals_sync_watermarks(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    git = Git(git_repo.path)
    git('update-ref', 'refs/chimera/synced/g/human', git.rev_parse('g/agent'))  # a `goal sync` mark
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    marker = common / 'chimera' / 'appending' / 'g@human'  # a conflicted-append marker
    marker.parent.mkdir(parents=True)
    marker.write_text('before=x\ntarget=y\n')
    description = common / 'chimera' / 'pr' / 'g'  # a `goal pr` cached description
    description.parent.mkdir(parents=True)
    description.write_text('key\ntitle\n\nbody')
    remove(git_repo.path, worktrees, 'g')
    compare(
        git('for-each-ref', '--format=%(refname)', 'refs/chimera/synced/g/').strip(), expected=''
    )
    assert not marker.exists()
    assert not description.exists()


def test_remove_refuses_uncommitted_changes(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            f'  {worktrees / "g@agent"} has uncommitted or untracked changes\n'
            'use --force to discard the work'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_refuses_unmerged_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # branch now ahead of main
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            '  branch g/agent has unmerged commits\n'
            'use --force to discard the work'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_force_discards_unsaved_work(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    remove(git_repo.path, worktrees, 'g', force=True)
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_sweeps_a_stray_branch_only_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Git(git_repo.path)('branch', 'g/reviewer', 'main')  # an extra actor, bare branch, no worktree
    remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])  # reviewer swept too


def test_remove_sweeps_a_stray_worktree_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    add(
        git_repo.path, worktrees, goal='g', actors=('scout',)
    )  # an extra actor with its own worktree
    compare(
        remove(git_repo.path, worktrees, 'g'),
        expected=RemoveResult((worktrees / 'g@agent', worktrees / 'g@scout')),  # both, sorted
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_refuses_an_unmerged_stray_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    add(git_repo.path, worktrees, goal='g', actors=('scout',))
    Repo(worktrees / 'g@scout').commit_content('work')  # scout ahead of main
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            '  branch g/scout has unmerged commits\n'
            'use --force to discard the work'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/scout', 'main'])


def test_remove_aborts_on_an_agent_live_in_a_stray_worktree(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    add(git_repo.path, worktrees, goal='g', actors=('scout',))
    scout = worktrees / 'g@scout'
    replace.on_class(
        Claude.live,
        lambda self, cwd=None: [Session('x', 'x', '?', scout, None)] if cwd == scout else [],
        name='live',
    )
    with ShouldRaise(
        UserError(
            f'refusing to clean up:\n  an agent is live in {scout}: pid ?\n'
            'use --force to stop the agents'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/scout', 'main'])


def test_remove_reports_every_problem_in_one_refusal(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_worktree = worktrees / 'g@agent'
    Repo(agent_worktree).commit_content('work')  # unmerged
    (agent_worktree / 'scratch.txt').write_text('wip')  # uncommitted
    replace.on_class(
        Claude.live,
        lambda self, cwd=None: [Session('x', 'x', '?', agent_worktree, None, pid=4242)],
        name='live',
    )
    with ShouldRaise(
        UserError(
            'refusing to clean up:\n'
            f'  an agent is live in {agent_worktree}: pid 4242\n'
            f'  {agent_worktree} has uncommitted or untracked changes\n'
            '  branch g/agent has unmerged commits\n'
            'use --force to stop the agents and discard the work'
        )
    ):
        remove(git_repo.path, worktrees, 'g')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed


def test_remove_refusal_never_signposts_force_to_an_ai_session(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    replace.in_module(ai_session, lambda: True, module=worktree_rm)
    with ShouldRaise(UserError('refusing to clean up:\n  branch g/agent has unmerged commits')):
        remove(git_repo.path, worktrees, 'g')


def test_remove_dry_previews_without_touching_anything(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    compare(
        remove(git_repo.path, worktrees, 'g', dry=Dry(on=True)),
        expected=RemoveResult((worktrees / 'g@agent',)),  # what would be removed
    )
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # still present
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_dry_logs_no_refs(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        remove(git_repo.path, worktrees, 'g', dry=Dry(on=True))
    log.check_empty()  # nothing deleted → no ref record


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
    local = Git.clone(origin, tmpdir / 'repo')
    worktrees = tmpdir / 'worktrees'
    add(local.path, worktrees, goal='g', fetch=False)
    Repo(worktrees / 'g@agent').commit_content('work', datetime(2022, 1, 1))  # newer than seed
    # the PR merges on origin: push the work to a side branch, then merge it into origin's main.
    # local's origin/main tracking ref stays stale until a fetch — pushing to main would update it.
    local('push', '-q', 'origin', 'g/agent:refs/heads/incoming')
    origin('merge', '-q', 'incoming')  # origin's main now carries g/agent
    with ShouldRaise(UserError, match='branch g/agent has unmerged commits'):
        remove(local.path, worktrees, 'g', fetch=False)  # stale refs don't see the merge
    remove(local.path, worktrees, 'g')  # fetch refreshes origin/main → merged
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(local.path).branches(), expected=['main'])


def test_remove_with_a_dead_origin_suggests_offline(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    git_repo('remote', 'add', 'origin', str(tmpdir / 'gone'))  # fetch fails, fast
    with ShouldRaise(UserError, match='check network, or re-run with --offline'):
        remove(git_repo.path, worktrees, 'g')


def test_remove_logs_the_refs_it_deletes(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    git = Git(git_repo.path)
    tip = git.rev_parse('main', short=False)  # the agent branch starts at main
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        remove(git_repo.path, worktrees, 'g')
    log.check(
        (
            'worktree rm: refs',
            {
                'goal': 'g',
                'git': {'before': {'g/agent': tip}, 'after': {}},
                'force': False,
            },
        ),
    )


def test_remove_force_logs_the_discarded_refs(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_tip = Repo(worktrees / 'g@agent').commit_content('work', short=False)  # unmerged
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        remove(git_repo.path, worktrees, 'g', force=True)
    log.check(
        (
            'worktree rm: refs',
            {
                'goal': 'g',
                # the unmerged agent commit is recorded by sha before being discarded
                'git': {'before': {'g/agent': agent_tip}, 'after': {}},
                'force': True,
            },
        ),
    )


def test_remove_a_ghost_goal_logs_nothing(tmpdir: TempDir, git_repo: Repo) -> None:
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        remove(git_repo.path, tmpdir / 'worktrees', 'ghost')
    log.check_empty()  # no refs changed → no ref record


def test_remove_uses_the_repos_default_branch(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir / 'repo')
    repo.commit_content('seed')
    repo('branch', '-m', 'main', 'master')  # master-style default
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, goal='g')  # branches off master; nothing added → merged
    remove(repo.path, worktrees, 'g')
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(repo.path).branches(), expected=['master'])


WR = 'chimera.commands.worktree.rm.remove'


def _rm_refs(cli: str, goal: str, base: str, *, offline: bool = False) -> list[dict[str, object]]:
    """start / `worktree rm: refs` event / end — the lines a rm or finish logs."""
    start, end = action_logs(
        cli, WR, {'goal': goal, 'force': False, 'offline': offline, 'dry': False, 'project': None}
    )
    refs = {f'{goal}/agent': base}
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
    command.run('worktree', 'add', '--goal', 'g')
    base = Git(git_repo.path)('rev-parse', 'g/agent').strip()
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g').check(
        output=f'Removed {worktree}',
        logging=_rm_refs('worktree rm', 'g', base),
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_worktree_rm_cli_dry_previews(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', '--goal', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'rm', 'g', '--dry').check(
        output=f'Would remove {worktree}',
        logging=action_logs(
            'worktree rm',
            WR,
            {'goal': 'g', 'force': False, 'offline': False, 'dry': True, 'project': None},
        ),
    )
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_worktree_rm_cli_force_dry_previews_the_stop(
    tmpdir: TempDir, git_repo: Repo, command: Command, replace: Replacer
) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', '--goal', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    session = Session('x', 'p@g@agent', 'idle', worktree, None, pid=4242)
    replace.on_class(Claude.live, lambda self, cwd=None: [session], name='live')
    command.run('worktree', 'rm', 'g', '--force', '--dry').check(
        output=f'Would stop p@g@agent (pid 4242)\nWould remove {worktree}',
        logging=action_logs(
            'worktree rm',
            WR,
            {'goal': 'g', 'force': True, 'offline': False, 'dry': True, 'project': None},
        ),
    )
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing removed


def test_worktree_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    _project(tmpdir, git_repo)
    command.run('worktree', 'rm', 'ghost').check(
        output='Nothing to remove for ghost',
        logging=action_logs(
            'worktree rm',
            WR,
            {'goal': 'ghost', 'force': False, 'offline': False, 'dry': False, 'project': None},
        ),
    )


def test_goal_finish_cli_offline(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    command.run('worktree', 'add', '--goal', 'g')
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
    command.run('worktree', 'add', '--goal', 'g')
    base = Git(git_repo.path)('rev-parse', 'g/agent').strip()
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    # finish is the lifecycle name for rm
    command.run('goal', 'finish', 'g').check(
        output=f'Removed {worktree}',
        logging=_rm_refs('goal finish', 'g', base),
    )
    tmpdir.compare(path='worktrees', expected=())
    compare(Git(git_repo.path).branches(), expected=['main'])
