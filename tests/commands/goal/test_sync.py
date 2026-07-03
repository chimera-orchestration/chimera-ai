from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.__main__ import _sync_line
from chimera.commands.goal.sync import Outcome, SyncResult, _exists, sync
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


def _wm(mover: str = 'human') -> str:
    return f'refs/chimera/synced/g/{mover}'


def test_materialises_the_mover_at_the_target(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    with _refs_log() as log:
        result = sync(git_repo.path, 'g')
    compare(
        result, expected=SyncResult(Outcome.CREATED, 'human', 'agent', _short(git_repo, 'g/agent'))
    )
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))
    tip = _full(git_repo, 'g/human')
    log.check(
        (
            'goal sync: refs',
            {'goal': 'g', 'git': {'before': {}, 'after': {'g/human': tip, _wm(): tip}}},
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
    new = _full(git_repo, 'g/agent')
    log.check(
        (
            'goal sync: refs',
            {
                'goal': 'g',
                'git': {
                    'before': {'g/human': old, _wm(): old},
                    'after': {'g/human': new, _wm(): new},
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


def _squash_human(git_repo: Repo, tmpdir: TempDir) -> Path:
    """Materialise g/human at the agent tip, then squash agent's work into one commit on it.

    Returns the checkout g/human sits in. ``reset --soft main`` keeps the agent tip's tree while
    moving the branch back to main, so the follow-up commit is a faithful squash (same tree).
    """
    sync(git_repo.path, 'g')  # create g/human at agent tip, watermark recorded
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
    Git(checkout)('reset', '--soft', Git(git_repo.path).rev_parse('main'))
    Git(checkout)('commit', '-qm', 'H1 squash of agent work')
    return checkout


def _case_b(tmpdir: TempDir, git_repo: Repo) -> Path:
    """A squash-plus-own-edit human whose next agent commit conflicts with the human's edit."""
    agent_wt = _goal(tmpdir, git_repo) / 'g@agent'
    (agent_wt / 'shared.txt').write_text('line\n')
    Git(agent_wt)('add', '-A')
    Git(agent_wt)('commit', '-qm', 'a1')
    checkout = _squash_human(git_repo, tmpdir)  # human squash contains shared.txt == 'line'
    (checkout / 'shared.txt').write_text('line-HUMAN\n')  # the human's own edit
    Git(checkout)('add', '-A')
    Git(checkout)('commit', '-qm', 'own edit')
    (agent_wt / 'shared.txt').write_text('line-AGENT\n')  # agent touches the same line
    Git(agent_wt)('add', '-A')
    Git(agent_wt)('commit', '-qm', 'a2')
    return checkout


def _resume(checkout: Path, *args: str) -> None:
    Git(checkout)('-c', 'core.editor=true', 'cherry-pick', *args)


def test_appends_new_agent_commits_onto_a_squash(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    Repo(worktrees / 'g@agent').commit_content('a2')  # agent has real work
    checkout = _squash_human(git_repo, tmpdir)  # human = one squashed commit
    a3 = Repo(worktrees / 'g@agent').commit_content('a3', short=False)  # new agent commit
    result = sync(git_repo.path, 'g')
    compare(
        result,
        expected=SyncResult(
            Outcome.APPENDED, 'human', 'agent', _short(git_repo, 'g/human'), appended=1
        ),
    )
    # human tip reproduces agent's tree (a3 applied cleanly onto the squash), watermark advanced
    compare(
        Git(checkout)('rev-parse', 'HEAD^{tree}').strip(),
        expected=_full(git_repo, 'g/agent^{tree}'),
    )
    compare(_full(git_repo, _wm()), expected=a3)
    compare(sync(git_repo.path, 'g').outcome, expected=Outcome.NOOP)  # idempotent


def test_appends_via_tree_match_without_a_watermark(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Git(git_repo.path)('update-ref', '-d', _wm())  # legacy: no watermark → tree-match seeds it
    Repo(worktrees / 'g@agent').commit_content('a2')
    result = sync(git_repo.path, 'g')
    compare(result.outcome, expected=Outcome.APPENDED)
    compare(
        Git(checkout)('rev-parse', 'HEAD^{tree}').strip(),
        expected=_full(git_repo, 'g/agent^{tree}'),
    )


def test_diverged_with_no_record_refuses(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    (checkout / 'own.txt').write_text('mine')  # an edit of the human's own → tree matches nothing
    Git(checkout)('add', '-A')
    Git(checkout)('commit', '-qm', 'own edit')
    Git(git_repo.path)('update-ref', '-d', _wm())  # and no watermark to fall back on
    Repo(worktrees / 'g@agent').commit_content('a2')
    with ShouldRaise(
        UserError(
            'g/human has diverged from g/agent with no integration record — '
            'rebase or cherry-pick by hand this time'
        )
    ):
        sync(git_repo.path, 'g')


def test_diverged_append_refuses_a_dirty_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    (checkout / 'scratch.txt').write_text('wip')  # uncommitted work in the human checkout
    Repo(worktrees / 'g@agent').commit_content('a2')  # a commit to append
    with ShouldRaise(
        UserError(
            f'g/human is checked out with uncommitted changes at {checkout.resolve()} — '
            f'commit or stash there first'
        )
    ):
        sync(git_repo.path, 'g')


def test_bare_diverged_mover_needs_a_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Git(git_repo.path)('worktree', 'remove', str(checkout))  # g/human now bare
    Repo(worktrees / 'g@agent').commit_content('a2')
    with ShouldRaise(
        UserError('check out g/human to append 1 commit(s) (git checkout g/human …), then re-run')
    ):
        sync(git_repo.path, 'g')


def test_conflict_leaves_the_cherry_pick_in_the_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _case_b(tmpdir, git_repo)
    watermark = _full(git_repo, _wm())
    result = sync(git_repo.path, 'g')
    compare(
        result,
        expected=SyncResult(
            Outcome.CONFLICT,
            'human',
            'agent',
            _short(git_repo, 'g/human'),
            conflict=checkout.resolve(),
        ),
    )
    compare(_exists(Git(checkout), 'CHERRY_PICK_HEAD'), expected=True)  # left mid cherry-pick
    compare(_full(git_repo, _wm()), expected=watermark)  # watermark NOT advanced


def test_in_progress_append_blocks_a_rerun(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _case_b(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # leaves a conflict
    with ShouldRaise(
        UserError(
            f'an append is in progress at {checkout.resolve()} — resolve and '
            f'`git cherry-pick --continue`, then re-run'
        )
    ):
        sync(git_repo.path, 'g')


def test_finished_append_is_reconciled_on_rerun(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _case_b(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # conflict
    (checkout / 'shared.txt').write_text('line-BOTH\n')  # human resolves…
    Git(checkout)('add', '-A')
    _resume(checkout, '--continue')  # …and finishes the cherry-pick by hand
    result = sync(
        git_repo.path, 'g'
    )  # reconcile: mover moved → advance watermark, then nothing new
    compare(result.outcome, expected=Outcome.NOOP)
    compare(_full(git_repo, _wm()), expected=_full(git_repo, 'g/agent'))
    compare(sync(git_repo.path, 'g').outcome, expected=Outcome.NOOP)  # marker gone, still clean


def test_aborted_append_is_reconciled_and_retried(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _case_b(tmpdir, git_repo)
    watermark = _full(git_repo, _wm())
    sync(git_repo.path, 'g')  # conflict
    _resume(checkout, '--abort')  # human backs out
    result = sync(git_repo.path, 'g')  # reconcile: mover unchanged → clear marker, retry the append
    compare(result.outcome, expected=Outcome.CONFLICT)  # same conflict, freshly attempted
    compare(_full(git_repo, _wm()), expected=watermark)  # still not advanced


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
    compare(
        _sync_line(SyncResult(Outcome.APPENDED, 'human', 'agent', 'abc123', appended=2)),
        expected='Appended 2 commit(s) from agent onto human (abc123)',
    )
    compare(
        _sync_line(
            SyncResult(Outcome.CONFLICT, 'human', 'agent', 'abc123', conflict=Path('/repo'))
        ),
        expected='Conflict appending agent onto human — resolve in /repo, '
        '`git cherry-pick --continue`, then re-run',
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
                'git': {'before': {}, 'after': {'g/human': agent, _wm(): agent}},
                'message': 'goal sync: refs',
            },
            end,
        ],
    )
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_goal_sync_cli_conflict_exits_nonzero(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    checkout = _case_b(tmpdir, git_repo)
    command.run('goal', 'sync', 'g').check(
        output=f'Conflict appending agent onto human — resolve in {checkout.resolve()}, '
        '`git cherry-pick --continue`, then re-run',
        # the conflict is on the first new commit, so no ref moves — just the start/end pair
        logging=action_logs(
            'goal sync',
            'chimera.commands.goal.sync.sync',
            {'goal': 'g', 'move': 'human', 'to': 'agent', 'project': None},
        ),
        return_code=1,
    )
