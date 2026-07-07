from pathlib import Path

from giterator import GitError
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.__main__ import _sync_line
from chimera.commands.goal.sync import Outcome, SyncResult, _replay, sync
from chimera.git import Git
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
    add(repo.path, worktrees, goal='g')  # g@agent worktree + g/agent branch, no human
    return worktrees


def _refs_log() -> LogCapture:
    return LogCapture(LoguruSource(('message', 'extra'), level='INFO'))


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


def test_repoints_a_mover_thats_a_full_squash_of_the_target(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    """The reverse direction: bring a raw branch up to a curated one that already squashes it."""
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    _squash_human(git_repo, tmpdir)  # human = a faithful, zero-diff squash of agent's tip
    result = sync(git_repo.path, 'g', mover='agent', target='human')
    compare(
        result,
        expected=SyncResult(Outcome.REPOINTED, 'agent', 'human', _short(git_repo, 'g/human')),
    )
    compare(_full(git_repo, 'g/agent'), expected=_full(git_repo, 'g/human'))
    compare(_full(git_repo, _wm('agent')), expected=_full(git_repo, 'g/human'))
    # agent is now literally at human's sha, so a re-run is the plain direct-equality NOOP
    compare(sync(git_repo.path, 'g', mover='agent', target='human').outcome, expected=Outcome.NOOP)


def test_repoints_a_bare_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    _squash_human(git_repo, tmpdir)
    Git(git_repo.path)('worktree', 'remove', str(worktrees / 'g@agent'))  # agent now bare
    result = sync(git_repo.path, 'g', mover='agent', target='human')
    compare(result.outcome, expected=Outcome.REPOINTED)
    compare(_full(git_repo, 'g/agent'), expected=_full(git_repo, 'g/human'))


def test_repoint_refuses_a_dirty_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_wt = worktrees / 'g@agent'
    Repo(agent_wt).commit_content('a1')
    _squash_human(git_repo, tmpdir)
    (agent_wt / 'scratch.txt').write_text('wip')  # uncommitted work in the agent checkout
    with ShouldRaise(
        UserError(
            f'g/agent is checked out with uncommitted changes at {agent_wt.resolve()} — '
            f'commit or stash there first'
        )
    ):
        sync(git_repo.path, 'g', mover='agent', target='human')


def test_repoint_never_fires_once_the_mover_has_a_watermark(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    """An already-tracked mover finding nothing new to append is the ordinary idempotent NOOP —
    it must never be repointed, or a real curated commit would be clobbered by target's raw tip."""
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    Repo(worktrees / 'g@agent').commit_content('a2')
    _squash_human(git_repo, tmpdir)  # human squashed; watermark already tracks agent's tip
    human_sha = _full(git_repo, 'g/human')
    result = sync(git_repo.path, 'g')
    compare(result.outcome, expected=Outcome.NOOP)
    compare(_full(git_repo, 'g/human'), expected=human_sha)  # human's own squash commit untouched


def _diverged_no_record(tmpdir: TempDir, git_repo: Repo) -> Path:
    """A human that diverged from the agent with nothing to append from: a squash carrying an
    edit of the human's own (tree matches nothing) and no watermark. Returns the human checkout."""
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    (checkout / 'own.txt').write_text('mine')
    Git(checkout)('add', '-A')
    Git(checkout)('commit', '-qm', 'own edit')
    Git(git_repo.path)('update-ref', '-d', _wm())
    Repo(worktrees / 'g@agent').commit_content('a2')
    return checkout


def test_diverged_with_no_record_refuses(tmpdir: TempDir, git_repo: Repo) -> None:
    _diverged_no_record(tmpdir, git_repo)
    with ShouldRaise(
        UserError(
            'g/human has diverged from g/agent with no integration record — '
            'rebase or cherry-pick by hand, or --force to repoint g/human onto g/agent, '
            'discarding its own commits'
        )
    ):
        sync(git_repo.path, 'g')


def test_force_repoints_a_diverged_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _diverged_no_record(tmpdir, git_repo)
    old = _full(git_repo, 'g/human')
    with _refs_log() as log:
        result = sync(git_repo.path, 'g', force=True)
    compare(
        result,
        expected=SyncResult(
            Outcome.FORCED, 'human', 'agent', _short(git_repo, 'g/agent'), discarded=2
        ),
    )
    tip = _full(git_repo, 'g/agent')
    compare(_full(git_repo, 'g/human'), expected=tip)  # ref and work tree moved together
    compare(Git(checkout)('rev-parse', 'HEAD').strip(), expected=tip)
    compare(_full(git_repo, _wm()), expected=tip)  # watermark seeded for future appends
    log.check(
        (
            'goal sync: refs',
            {
                'goal': 'g',
                'discarded': 2,
                'git': {'before': {'g/human': old}, 'after': {'g/human': tip, _wm(): tip}},
            },
        ),
    )
    compare(sync(git_repo.path, 'g', force=True).outcome, expected=Outcome.NOOP)  # idempotent


def test_force_beats_the_append(tmpdir: TempDir, git_repo: Repo) -> None:
    """With a watermark an un-forced sync would append; --force still means repoint —
    the escape hatch when a replay would only conflict (e.g. the target was rebased)."""
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    _squash_human(git_repo, tmpdir)  # human squashed, watermark tracks agent's tip
    Repo(worktrees / 'g@agent').commit_content('a2')  # a commit an append would replay
    result = sync(git_repo.path, 'g', force=True)
    compare(result.outcome, expected=Outcome.FORCED)
    compare(result.discarded, expected=1)  # the squash commit dropped, not appended onto
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))


def test_force_refuses_a_dirty_mover(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _diverged_no_record(tmpdir, git_repo)
    (checkout / 'scratch.txt').write_text('wip')  # uncommitted — not recoverable from any log
    with ShouldRaise(
        UserError(
            f'g/human is checked out with uncommitted changes at {checkout.resolve()} — '
            f'commit or stash there first'
        )
    ):
        sync(git_repo.path, 'g', force=True)


def test_force_never_discards_a_lead(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')
    checkout = tmpdir / 'human'
    Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work')  # human strictly leads — no divergence
    lead = _full(git_repo, 'g/human')
    result = sync(git_repo.path, 'g', force=True)
    compare(result.outcome, expected=Outcome.AHEAD)
    compare(_full(git_repo, 'g/human'), expected=lead)  # the lead survives


def test_refuses_to_append_across_merges(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_wt = worktrees / 'g@agent'
    Repo(agent_wt).commit_content('a1')
    _squash_human(git_repo, tmpdir)
    Repo(git_repo.path).commit_content('mainline')  # main moves on…
    Git(agent_wt)('merge', '-m', 'merge main', 'main')  # …and the agent pulls it in
    with ShouldRaise(
        UserError(
            'the 2 commit(s) to append include 1 merge(s) — g/agent was rebased or merged '
            'other work in, so an append would replay history that is not its own; sync by '
            'hand, or --force to repoint g/human onto g/agent, discarding its own commits'
        )
    ):
        sync(git_repo.path, 'g')
    compare(
        sync(git_repo.path, 'g', force=True).outcome, expected=Outcome.FORCED
    )  # the way through


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
    refusal = UserError(
        'check out g/human to append 1 commit(s) (git checkout g/human …), then re-run'
    )
    with ShouldRaise(refusal):
        sync(git_repo.path, 'g')
    with ShouldRaise(refusal):  # a cwd outside any checkout can't host the append
        sync(git_repo.path, 'g', into=tmpdir.path)
    with ShouldRaise(refusal):  # an agent's worktree is never claimed for it
        sync(git_repo.path, 'g', into=worktrees / 'g@agent')


def test_append_claims_the_callers_checkout_for_a_bare_mover(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Git(git_repo.path)('worktree', 'remove', str(checkout))  # g/human now bare
    Repo(worktrees / 'g@agent').commit_content('a2')
    result = sync(git_repo.path, 'g', into=git_repo.path)  # standing in the repo's own checkout
    compare(
        result,
        expected=SyncResult(
            Outcome.APPENDED,
            'human',
            'agent',
            _short(git_repo, 'g/human'),
            appended=1,
            checkout=Checkout(True, git_repo.path.resolve(), 'g/human', was='main'),
        ),
    )
    compare(Git(git_repo.path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/human')
    compare(_full(git_repo, 'g/human^{tree}'), expected=_full(git_repo, 'g/agent^{tree}'))


def test_append_refuses_to_claim_a_dirty_callers_checkout(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Git(git_repo.path)('worktree', 'remove', str(checkout))  # g/human now bare
    Repo(worktrees / 'g@agent').commit_content('a2')
    (git_repo.path / 'scratch.txt').write_text('wip')
    with ShouldRaise(
        UserError(
            f'{git_repo.path.resolve()} has uncommitted changes — commit or stash them so '
            f'g/human can be checked out here for the append, then re-run'
        )
    ):
        sync(git_repo.path, 'g', into=git_repo.path)
    compare(  # left where it stood
        Git(git_repo.path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main'
    )


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
    compare(Git(checkout).ref_exists('CHERRY_PICK_HEAD'), expected=True)  # left mid cherry-pick
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


def _stray_merge(checkout: Path) -> str:
    """A merge commit of the checkout's HEAD — cherry-picking it always dies (no -m is given)."""
    git = Git(checkout)
    head = git('rev-parse', 'HEAD').strip()
    tree = git('rev-parse', f'{head}^{{tree}}').strip()
    parent = git('rev-parse', f'{head}^').strip()
    return git('commit-tree', tree, '-p', head, '-p', parent, '-m', 'stray').strip()


def _sequencer(checkout: Path) -> Path:
    return Path(
        Git(checkout)('rev-parse', '--path-format=absolute', '--git-path', 'sequencer').strip()
    )


def test_stranded_cherry_pick_blocks_an_append(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    a2 = Repo(worktrees / 'g@agent').commit_content('a2', short=False)
    with ShouldRaise(GitError, match='is a merge'):  # a stray replay strands mid-sequence:
        Git(checkout)('cherry-pick', a2, _stray_merge(checkout))  # clean tree, live sequencer
    with ShouldRaise(
        UserError(
            f'a cherry-pick is already in progress at {checkout.resolve()} — finish or abort it '
            f'(git cherry-pick --abort), then re-run'
        )
    ):
        sync(git_repo.path, 'g')


def test_force_cleans_up_a_stranded_append(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = _goal(tmpdir, git_repo)
    agent_wt = worktrees / 'g@agent'
    Repo(agent_wt).commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Repo(git_repo.path).commit_content('mainline')  # main moves on…
    Git(agent_wt)('merge', '-m', 'merge main', 'main')  # …the agent pulls it in…
    with ShouldRaise(GitError, match='is a merge'):  # …and a hand replay strands at the merge
        Git(checkout)('cherry-pick', f'{_full(git_repo, _wm())}..g/agent')
    result = sync(git_repo.path, 'g', force=True)
    compare(
        result,
        expected=SyncResult(
            Outcome.FORCED, 'human', 'agent', _short(git_repo, 'g/agent'), discarded=2
        ),
    )
    tip = _full(git_repo, 'g/agent')
    compare(_full(git_repo, 'g/human'), expected=tip)
    compare(Git(checkout)('rev-parse', 'HEAD').strip(), expected=tip)
    compare(Git(checkout)('status', '--porcelain').strip(), expected='')  # no mess left behind
    compare(_sequencer(checkout).exists(), expected=False)  # the stray sequence is gone
    compare(sync(git_repo.path, 'g').outcome, expected=Outcome.NOOP)  # sync is healthy again


def test_force_cleans_up_a_conflicted_append(tmpdir: TempDir, git_repo: Repo) -> None:
    checkout = _case_b(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # leaves the conflict, marker written
    result = sync(git_repo.path, 'g', force=True)
    compare(
        result,
        expected=SyncResult(
            Outcome.FORCED, 'human', 'agent', _short(git_repo, 'g/agent'), discarded=2
        ),
    )
    compare(_full(git_repo, 'g/human'), expected=_full(git_repo, 'g/agent'))
    compare(Git(checkout)('status', '--porcelain').strip(), expected='')  # conflict backed out
    compare(sync(git_repo.path, 'g').outcome, expected=Outcome.NOOP)  # marker cleared with it


def test_replay_death_without_a_conflict_rolls_back(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    a2 = Repo(worktrees / 'g@agent').commit_content('a2', short=False)
    human, watermark = _full(git_repo, 'g/human'), _full(git_repo, _wm())

    def die_mid_replay(checkout: Path, point: str, target_branch: str) -> None:
        Git(checkout)('cherry-pick', a2, _stray_merge(checkout))  # one applied, dead at the merge

    replace.in_module(_replay, die_mid_replay)
    with ShouldRaise(UserError, match='append onto g/human failed and was rolled back'):
        sync(git_repo.path, 'g')
    compare(_full(git_repo, 'g/human'), expected=human)  # the half-applied replay was backed out
    compare(Git(checkout)('status', '--porcelain').strip(), expected='')
    compare(_sequencer(checkout).exists(), expected=False)
    compare(_full(git_repo, _wm()), expected=watermark)  # nothing recorded as integrated


def test_replay_failure_leaving_no_state_reports(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = _goal(tmpdir, git_repo)
    Repo(worktrees / 'g@agent').commit_content('a1')
    checkout = _squash_human(git_repo, tmpdir)
    Repo(worktrees / 'g@agent').commit_content('a2')
    human = _full(git_repo, 'g/human')

    def die_at_once(checkout: Path, point: str, target_branch: str) -> None:
        raise GitError('fatal: could not even start')

    replace.in_module(_replay, die_at_once)
    with ShouldRaise(UserError, match='could not even start'):
        sync(git_repo.path, 'g')
    compare(_full(git_repo, 'g/human'), expected=human)  # untouched — nothing to roll back
    compare(Git(checkout)('status', '--porcelain').strip(), expected='')


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


def test_infers_to_from_the_goals_only_other_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)  # only g/agent exists — the sole candidate for --to
    result = sync(git_repo.path, 'g', mover='reviewer')
    compare(
        result,
        expected=SyncResult(Outcome.CREATED, 'reviewer', 'agent', _short(git_repo, 'g/agent')),
    )


def test_infers_move_from_the_goals_only_other_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)  # only g/agent exists
    sync(git_repo.path, 'g', mover='reviewer', target='agent')  # actors now: agent, reviewer
    result = sync(git_repo.path, 'g', target='agent')  # --move omitted, only other actor: reviewer
    compare(
        result, expected=SyncResult(Outcome.NOOP, 'reviewer', 'agent', _short(git_repo, 'g/agent'))
    )


def test_to_inference_refuses_with_no_other_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)  # only g/agent exists — 'agent' has no other actor to sync with
    with ShouldRaise(
        UserError("no other actor branch exists for 'agent' to sync with — pass --to explicitly")
    ):
        sync(git_repo.path, 'g', mover='agent')


def test_move_inference_refuses_with_no_other_actor(tmpdir: TempDir, git_repo: Repo) -> None:
    _goal(tmpdir, git_repo)
    with ShouldRaise(
        UserError("no other actor branch exists for 'agent' to sync with — pass --move explicitly")
    ):
        sync(git_repo.path, 'g', target='agent')


def _three_actors(tmpdir: TempDir, git_repo: Repo) -> None:
    """A goal with three actor branches: agent, human, reviewer."""
    _goal(tmpdir, git_repo)
    sync(git_repo.path, 'g')  # materialises human at agent
    sync(git_repo.path, 'g', mover='reviewer', target='agent')  # materialises reviewer at agent


def test_to_inference_refuses_when_ambiguous(tmpdir: TempDir, git_repo: Repo) -> None:
    _three_actors(tmpdir, git_repo)  # agent's other actors: human, reviewer — can't pick
    with ShouldRaise(
        UserError(
            "goal 'g' has multiple actors besides 'agent' ('human', 'reviewer') — "
            '--to must be given explicitly'
        )
    ):
        sync(git_repo.path, 'g', mover='agent')


def test_move_inference_refuses_when_ambiguous(tmpdir: TempDir, git_repo: Repo) -> None:
    _three_actors(tmpdir, git_repo)
    with ShouldRaise(
        UserError(
            "goal 'g' has multiple actors besides 'agent' ('human', 'reviewer') — "
            '--move must be given explicitly'
        )
    ):
        sync(git_repo.path, 'g', target='agent')


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
        expected='human already has everything from agent (abc123)',
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
        _sync_line(SyncResult(Outcome.REPOINTED, 'agent', 'human', 'abc123')),
        expected='Repointed agent onto human (abc123) — tips already matched exactly',
    )
    compare(
        _sync_line(SyncResult(Outcome.FORCED, 'human', 'agent', 'abc123', discarded=2)),
        expected='Forced human onto agent (abc123) — discarded 2 commit(s), shas in the log',
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
    command.run('worktree', 'add', '--goal', 'g')
    agent = _full(git_repo, 'g/agent')
    start, end = action_logs(
        'goal sync',
        'chimera.commands.goal.sync.sync',
        {'goal': 'g', 'move': None, 'to': None, 'force': False, 'project': None},
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


def test_goal_sync_cli_infers_the_omitted_flag(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')  # only g/agent exists yet
    agent = _full(git_repo, 'g/agent')
    start, end = action_logs(
        'goal sync',
        'chimera.commands.goal.sync.sync',
        {'goal': 'g', 'move': 'reviewer', 'to': None, 'force': False, 'project': None},
    )
    command.run('goal', 'sync', 'g', '--move', 'reviewer').check(
        output=f'Created reviewer at agent ({_short(git_repo, "g/agent")})',
        logging=[
            start,
            {
                'level': 'INFO',
                'goal': 'g',
                'git': {'before': {}, 'after': {'g/reviewer': agent, _wm('reviewer'): agent}},
                'message': 'goal sync: refs',
            },
            end,
        ],
    )


def test_goal_sync_cli_ambiguous_inference_reports_the_actors(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    command.run('goal', 'sync', 'g')  # materialises human at agent
    command.run('goal', 'sync', 'g', '--move', 'reviewer', '--to', 'agent')  # and reviewer
    error = (
        "goal 'g' has multiple actors besides 'agent' ('human', 'reviewer') — "
        '--to must be given explicitly'
    )
    command.run('goal', 'sync', 'g', '--move', 'agent').check(
        output=f'Error: {error}',
        logging=action_logs(
            'goal sync',
            'chimera.commands.goal.sync.sync',
            {'goal': 'g', 'move': 'agent', 'to': None, 'force': False, 'project': None},
            error=f'UserError: {error}',
        ),
        return_code=1,
    )


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
            {'goal': 'g', 'move': None, 'to': None, 'force': False, 'project': None},
        ),
        return_code=1,
    )


def test_goal_sync_cli_force(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    _diverged_no_record(tmpdir, git_repo)
    old, tip = _full(git_repo, 'g/human'), _full(git_repo, 'g/agent')
    start, end = action_logs(
        'goal sync',
        'chimera.commands.goal.sync.sync',
        {'goal': 'g', 'move': None, 'to': None, 'force': True, 'project': None},
    )
    command.run('goal', 'sync', 'g', '--force').check(
        output=f'Forced human onto agent ({_short(git_repo, "g/agent")}) — '
        f'discarded 2 commit(s), shas in the log',
        logging=[
            start,
            {
                'level': 'INFO',
                'goal': 'g',
                'discarded': 2,
                'git': {'before': {'g/human': old}, 'after': {'g/human': tip, _wm(): tip}},
                'message': 'goal sync: refs',
            },
            end,
        ],
    )
    compare(_full(git_repo, 'g/human'), expected=tip)
