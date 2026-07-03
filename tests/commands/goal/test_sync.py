from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.__main__ import _sync_line
from chimera.commands.goal.sync import Outcome, SyncResult, sync
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.worktrees import Checkout
from tests.cli import Command, action_logs


def _short(repo: Repo, ref: str) -> str:
    return Git(repo.path).rev_parse(ref)


def _full(repo: Repo, ref: str) -> str:
    return Git(repo.path).rev_parse(ref, short=False)


def _goal(tmpdir: TempDir, repo: Repo) -> Path:
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, 'g')  # g@agent worktree + g/agent branch, no human
    return worktrees


def _refs_log() -> LogCapture:
    return LogCapture(LoguruSource(('message', 'extra')))


def test_materialises_the_mover_at_the_target(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    with _refs_log() as log:
        result = sync(git_repo.path, 'g')
    compare(
        result, expected=SyncResult(Outcome.CREATED, 'human', 'agent', _short(git_repo, 'g/agent'))
    )
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))
    log.check(
        (
            'goal sync: refs',
            {'goal': 'g', 'git': {'before': {}, 'after': {'g/human': _full(git_repo, 'g/human')}}},
        ),
    )


def test_is_a_noop_when_already_at_the_target(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # create g/human at the agent tip
    with _refs_log() as log:
        result = sync(git_repo.path, 'g')  # nothing to do
    compare(
        result, expected=SyncResult(Outcome.NOOP, 'human', 'agent', _short(git_repo, 'g/agent'))
    )
    log.check()  # no ref changed → no ref record


def test_fast_forwards_a_bare_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # g/human created bare at the agent tip
    Repo(worktrees / 'g@agent').commit_content('work')  # agent advances past human
    old = _full(git_repo, 'g/human')
    with _refs_log() as log:
        result = sync(git_repo.path, 'g')
    compare(
        result,
        expected=SyncResult(Outcome.FASTFORWARDED, 'human', 'agent', _short(git_repo, 'g/agent')),
    )
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))
    log.check(
        (
            'goal sync: refs',
            {
                'goal': 'g',
                'git': {
                    'before': {'g/human': old},
                    'after': {'g/human': _full(git_repo, 'g/agent')},
                },
            },
        ),
    )


def test_fast_forwards_a_checked_out_clean_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')  # checked out, clean
    Repo(worktrees / 'g@agent').commit_content('work')
    result = sync(git_repo.path, 'g')
    compare(
        result,
        expected=SyncResult(Outcome.FASTFORWARDED, 'human', 'agent', _short(git_repo, 'g/agent')),
    )
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))
    # the work tree moved too, not just the ref
    compare(Git(checkout)('rev-parse', 'HEAD').strip(), expected=_full(git_repo, 'g/agent'))


def test_refuses_a_dirty_checked_out_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
    (checkout / 'scratch.txt').write_text('wip')  # uncommitted
    Repo(worktrees / 'g@agent').commit_content('work')  # agent ahead → a FF is wanted
    with ShouldRaise(
        UserError(
            f'g/human is checked out with uncommitted changes at {checkout.resolve()} — '
            f'commit or stash there first'
        )
    ):
        sync(git_repo.path, 'g')


def test_leaves_a_mover_that_leads_the_target(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work')  # human now leads agent by one
    with _refs_log() as log:
        result = sync(git_repo.path, 'g')
    compare(
        result,
        expected=SyncResult(
            Outcome.AHEAD, 'human', 'agent', _short(git_repo, 'g/human'), ahead_by=1
        ),
    )
    log.check()  # nothing moved


def test_refuses_a_diverged_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work')  # human diverges…
    Repo(worktrees / 'g@agent').commit_content('agent-work')  # …from agent
    with ShouldRaise(UserError('g/human has diverged from g/agent — rebase it first')):
        sync(git_repo.path, 'g')


def test_refuses_when_the_target_is_missing(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    with ShouldRaise(UserError('no branch g/ghost to sync from')):
        sync(git_repo.path, 'g', target='ghost')


def test_refuses_when_move_and_to_are_the_same(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    with ShouldRaise(UserError("nothing to sync — --move and --to are both 'agent'")):
        sync(git_repo.path, 'g', mover='agent', target='agent')


def test_materialises_a_custom_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    result = sync(git_repo.path, 'g', mover='reviewer', target='agent')
    compare(
        result,
        expected=SyncResult(Outcome.CREATED, 'reviewer', 'agent', _short(git_repo, 'g/agent')),
    )
    compare(_full(git_repo, 'g/reviewer'), expected=_full(git_repo, 'g/agent'))


def test_lands_the_mover_in_a_clean_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    result = sync(git_repo.path, 'g', into=git_repo.path)  # the repo's own clean checkout
    compare(
        result.checkout, expected=Checkout(True, git_repo.path.resolve(), 'g/human', was='main')
    )
    compare(Git(git_repo.path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/human')


def test_does_not_land_the_mover_in_a_dirty_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    (git_repo.path / 'scratch.txt').write_text('wip')
    result = sync(git_repo.path, 'g', into=git_repo.path)
    compare(
        result.checkout, expected=Checkout(False, git_repo.path.resolve(), 'g/human', was='main')
    )
    compare(Git(git_repo.path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')


def test_sync_line_renders_each_outcome() -> None:
    compare(
        _sync_line(SyncResult(Outcome.CREATED, 'human', 'agent', 'abc123')),
        expected='Created human at agent (abc123)',
    )
    compare(
        _sync_line(SyncResult(Outcome.NOOP, 'human', 'agent', 'abc123')),
        expected='human already at agent (abc123)',
    )
    compare(
        _sync_line(SyncResult(Outcome.FASTFORWARDED, 'human', 'agent', 'abc123')),
        expected='Fast-forwarded human to agent (abc123)',
    )
    compare(
        _sync_line(SyncResult(Outcome.AHEAD, 'human', 'agent', 'abc123', ahead_by=2)),
        expected='human leads agent by 2 — nothing to sync',
    )


def test_sync_line_appends_the_checkout_outcome() -> None:
    compare(
        _sync_line(
            SyncResult(
                Outcome.CREATED,
                'human',
                'agent',
                'abc123',
                checkout=Checkout(True, Path('/w/repo'), 'g/human', was='main'),
            )
        ),
        expected='Created human at agent (abc123)\nChecked out g/human here (was main)',
    )
    compare(  # detached: no "(was …)" tail
        _sync_line(
            SyncResult(
                Outcome.CREATED,
                'human',
                'agent',
                'abc123',
                checkout=Checkout(True, Path('/w/repo'), 'g/human', was=None),
            )
        ),
        expected='Created human at agent (abc123)\nChecked out g/human here',
    )
    compare(
        _sync_line(
            SyncResult(
                Outcome.CREATED,
                'human',
                'agent',
                'abc123',
                checkout=Checkout(False, Path('/w/repo'), 'g/human', was='main'),
            )
        ),
        expected=(
            'Created human at agent (abc123)\n(note: uncommitted changes — g/human not '
            'checked out; commit/stash then `git checkout g/human`)'
        ),
    )


def test_goal_sync_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', 'g')
    agent = _full(git_repo, 'g/agent')
    start, end = action_logs(
        'goal sync',
        'chimera.commands.goal.sync.sync',
        {'goal': 'g', 'move': 'human', 'to': 'agent', 'project': None},
    )
    command.run('goal', 'sync', 'g').check(
        output=f'Created human at agent ({_short(git_repo, "g/agent")})',
        logging=[
            start,
            {
                'level': 'INFO',
                'goal': 'g',
                'git': {'before': {}, 'after': {'g/human': agent}},
                'message': 'goal sync: refs',
            },
            end,
        ],
    )
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])
