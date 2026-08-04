from datetime import datetime
from pathlib import Path

from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera import worktrees
from chimera.config import UserError
from chimera.git import Git
from chimera.worktrees import (
    Checkout,
    base_ref,
    branch,
    checkout_here,
    default_branch,
    fetch_origin,
    fetch_origin_or_offline,
    goal_actors,
    goals,
    is_dirty,
    is_goal_worktree,
    is_merged,
    registered_worktrees,
    require_valid_actor,
    require_valid_goal,
    session_name,
    worktree_actor,
    worktree_dirs,
    worktree_path,
)


def _log() -> LogCapture:
    return LogCapture(LoguruSource(('message', 'extra'), level='INFO'))


def _renamed(repo: Repo, to: str) -> Repo:
    repo.commit_content('seed')
    repo('branch', '-m', 'main', to)
    return repo


def test_branch_names_the_actor_under_the_goal() -> None:
    compare(branch('my-goal', 'agent'), expected='my-goal/agent')


def test_worktree_path_joins_goal_and_actor_with_an_at_sign() -> None:
    compare(worktree_path(Path('/wt'), 'my-goal', 'agent'), expected=Path('/wt/my-goal@agent'))


def test_session_name_joins_project_goal_and_actor() -> None:
    compare(session_name('proj', 'my-goal', 'agent'), expected='proj@my-goal@agent')


class TestRequireValidGoal:
    def test_accepts_the_shapes_in_use(self) -> None:
        for name in ('feature-x', 'pr-123', 'v1.2', 'goal_x'):
            compare(require_valid_goal(name), expected=name)

    def test_rejects_the_goal_actor_separator(self) -> None:
        with ShouldRaise(
            UserError("'a@b' is not a valid goal name: '@' separates goal from actor")
        ):
            require_valid_goal('a@b')

    def test_rejects_path_separators(self) -> None:
        for name in ('../x', 'a/b', 'a\\b', '../../projb/worktrees/g'):
            with ShouldRaise(
                UserError(
                    f'{name!r} is not a valid goal name: no path separators — '
                    f"goal names are single path segments, like 'feature-x' or 'pr-123'"
                )
            ):
                require_valid_goal(name)

    def test_rejects_names_git_refuses(self) -> None:
        for name in ('', '.', '..', 'a b', 'a..b'):
            with ShouldRaise(UserError(f'{name!r} is not a valid goal name')):
                require_valid_goal(name)


class TestRequireValidActor:
    def test_accepts_the_shapes_in_use(self) -> None:
        for name in ('agent', 'human', 'reviewer', 'pr'):
            compare(require_valid_actor(name), expected=name)

    def test_rejects_the_goal_actor_separator(self) -> None:
        with ShouldRaise(
            UserError("'a@b' is not a valid actor name: '@' separates goal from actor")
        ):
            require_valid_actor('a@b')

    def test_rejects_path_separators(self) -> None:
        with ShouldRaise(
            UserError(
                "'../x' is not a valid actor name: no path separators — "
                "actor names are single path segments, like 'agent' or 'reviewer'"
            )
        ):
            require_valid_actor('../x')

    def test_rejects_names_git_refuses(self) -> None:
        with ShouldRaise(UserError("'a b' is not a valid actor name")):
            require_valid_actor('a b')


def test_worktree_dirs_lists_only_dirs_sorted(tmpdir: TempDir) -> None:
    tmpdir.makedir('b@agent')
    tmpdir.makedir('a@agent')
    tmpdir.write('a-file', b'')  # files are ignored
    compare(worktree_dirs(tmpdir.path), expected=[tmpdir / 'a@agent', tmpdir / 'b@agent'])


def test_worktree_dirs_is_empty_when_root_is_absent(tmpdir: TempDir) -> None:
    compare(worktree_dirs(tmpdir / 'nope'), expected=[])


def test_goals_are_derived_from_agent_worktrees(tmpdir: TempDir) -> None:
    for name in ('g1@agent', 'g2@agent', 'g1@reviewer'):  # reviewer rides g1's agent
        tmpdir.makedir(name)
    compare(goals(tmpdir.path), expected={'g1', 'g2'})


class TestWorktreeActor:
    def test_reads_the_actor_from_the_worktree_dir(self, tmpdir: TempDir) -> None:
        cwd = tmpdir.makedir('worktrees/g@reviewer')
        assert worktree_actor(cwd, tmpdir / 'worktrees') == 'reviewer'

    def test_resolves_from_a_subdirectory_of_the_worktree(self, tmpdir: TempDir) -> None:
        cwd = tmpdir.makedir('worktrees/g@agent/src/sub')
        assert worktree_actor(cwd, tmpdir / 'worktrees') == 'agent'

    def test_none_outside_worktrees(self, tmpdir: TempDir) -> None:
        cwd = tmpdir.makedir('elsewhere')
        assert worktree_actor(cwd, tmpdir / 'worktrees') is None

    def test_none_for_worktrees_itself(self, tmpdir: TempDir) -> None:
        cwd = tmpdir.makedir('worktrees')
        assert worktree_actor(cwd, tmpdir / 'worktrees') is None

    def test_none_for_a_dir_directly_under_worktrees_without_the_separator(
        self, tmpdir: TempDir
    ) -> None:
        cwd = tmpdir.makedir('worktrees/not-a-goal-actor-dir')
        assert worktree_actor(cwd, tmpdir / 'worktrees') is None


class TestGoalActors:
    def test_unions_branch_and_worktree_actors(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        for actor in ('agent', 'human', 'reviewer'):  # human has a branch but no worktree dir
            git('branch', branch('g', actor), 'main')
        for name in ('g@agent', 'g@reviewer', 'g@scout'):  # scout has a worktree but no branch
            tmpdir.makedir(f'worktrees/{name}')
        compare(
            goal_actors(git, tmpdir / 'worktrees', 'g'),
            expected={'agent', 'human', 'reviewer', 'scout'},
        )

    def test_scoped_to_the_goal_namespace(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/agent', 'main')
        git('branch', 'g-other/agent', 'main')  # a different goal — must not leak in
        tmpdir.makedir('worktrees/g@agent')
        tmpdir.makedir('worktrees/g-other@agent')
        compare(goal_actors(git, tmpdir / 'worktrees', 'g'), expected={'agent'})

    def test_a_nested_goal_is_not_an_actor_of_its_parent(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git = Git(git_repo.path)
        git('branch', 'parent/agent', 'main')
        git('branch', 'parent/child/agent', 'main')  # a nested goal, not actor 'child/agent'
        compare(goal_actors(git, tmpdir / 'worktrees', 'parent'), expected={'agent'})

    def test_empty_for_an_unknown_goal(self, tmpdir: TempDir, git_repo: Repo) -> None:
        compare(goal_actors(Git(git_repo.path), tmpdir / 'worktrees', 'ghost'), expected=set())


def test_registered_worktrees_lists_repo_and_added(tmpdir: TempDir, git_repo: Repo) -> None:
    git = Git(git_repo.path)
    wt = tmpdir / 'wt'
    git('worktree', 'add', '-b', 'side', str(wt), 'main')
    compare(registered_worktrees(git), expected={git_repo.path.resolve(), wt.resolve()})


def _branched_then_advanced(repo: Repo) -> Repo:
    """A repo where ``feature`` has two commits and ``main`` moved on independently."""
    repo.commit_content('seed')
    repo('checkout', '-q', '-b', 'feature')
    repo.commit_content('b')
    repo.commit_content('c')
    repo('checkout', '-q', 'main')
    repo.commit_content('other-work')  # unrelated path, so the trees genuinely differ
    return repo


class TestIsMerged:
    def test_ancestor_of_base(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'feature', 'HEAD')  # points at main → reachable from it
        assert is_merged(git, 'feature', 'main')

    def test_branch_ahead_is_unmerged(self, tmpdir: TempDir) -> None:
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        assert not is_merged(Git(repo.path), 'feature', 'main')

    def test_regular_merge(self, tmpdir: TempDir) -> None:
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        repo('merge', '-q', '--no-ff', 'feature', '-m', 'merge')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_squash_merge_of_several_commits(self, tmpdir: TempDir) -> None:
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        repo('merge', '-q', '--squash', 'feature')
        repo('commit', '-qm', 'squash feature')  # one commit carrying the whole branch diff
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_rebase_merge(self, tmpdir: TempDir) -> None:
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        git = Git(repo.path)
        git('cherry-pick', *git('rev-list', '--reverse', 'main..feature').split())
        assert is_merged(git, 'feature', 'main')

    def test_rebase_merge_with_empty_commit_left_on_feature(self, tmpdir: TempDir) -> None:
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        git = Git(repo.path)
        git('cherry-pick', *git('rev-list', '--reverse', 'main..feature').split())
        repo('checkout', '-q', 'feature')
        repo('commit', '-q', '--allow-empty', '-m', 'marker')
        repo('checkout', '-q', 'main')
        assert is_merged(git, 'feature', 'main')

    def test_only_empty_commits_is_merged(self, tmpdir: TempDir) -> None:
        # feature's tree is the merge-base's own, so there is no content to lose
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '-b', 'feature')
        repo('commit', '-q', '--allow-empty', '-m', 'marker')
        repo('checkout', '-q', 'main')
        repo.commit_content('other-work')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_only_empty_commits_answers_the_same_when_base_has_one_too(
        self, tmpdir: TempDir
    ) -> None:
        # two empty commits share a patch-id, so cherry matches this pair where the test above
        # has nothing to match — the answer must not turn on base's own empty commits
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '-b', 'feature')
        repo('commit', '-q', '--allow-empty', '-m', 'marker')
        repo('checkout', '-q', 'main')
        repo('commit', '-q', '--allow-empty', '-m', 'base marker')
        repo.commit_content('other-work')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_net_zero_branch_is_merged(self, tmpdir: TempDir) -> None:
        # added then removed again: real commits, but the same tree as the merge-base
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '-b', 'feature')
        (repo.path / 'scratch.txt').write_text('scratch')
        repo('add', 'scratch.txt')
        repo('commit', '-qm', 'add scratch')
        repo('rm', '-q', 'scratch.txt')
        repo('commit', '-qm', 'drop scratch')
        repo('checkout', '-q', 'main')
        repo.commit_content('other-work')
        git = Git(repo.path)
        with _log() as log:
            assert is_merged(git, 'feature', 'main')
        log.check(
            (
                'is_merged: nets to nothing since the merge-base',
                {
                    'ref': 'feature',
                    'base': 'main',
                    'merge_base': git.rev_parse('main~1', short=False),
                },
            ),
        )

    def test_squash_carrying_extra_changes_still_contains_the_branch(self, tmpdir: TempDir) -> None:
        # the base commit is matched on its diff restricted to feature's paths, so folding
        # unrelated work into the squash doesn't hide that feature's own work landed
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        repo('merge', '-q', '--squash', 'feature')
        (repo.path / 'unrelated.txt').write_text('landed in the same commit')
        repo('add', 'unrelated.txt')
        repo('commit', '-qm', 'squash feature, plus a drive-by')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_unmerged_root_commit_is_not_mistaken_for_empty(self, tmpdir: TempDir) -> None:
        # a parentless commit shows no patch without --root — it must never read as harmless
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '--orphan', 'vendored')
        repo('rm', '-rfq', '.')
        (repo.path / 'important.txt').write_text('vendored')
        repo('add', 'important.txt')
        repo('commit', '-qm', 'vendored root')
        repo('checkout', '-q', '-b', 'feature', 'main')
        repo('merge', '-q', '--allow-unrelated-histories', '--no-edit', 'vendored')
        repo.commit_content('x')
        repo('checkout', '-q', 'main')
        repo('cherry-pick', repo('rev-parse', 'feature').strip())
        assert not is_merged(Git(repo.path), 'feature', 'main')

    def test_survives_an_ambiguous_refname_warning(self, tmpdir: TempDir) -> None:
        # a tag shadowing the branch name makes git warn on stderr before every answer
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        git = Git(repo.path)
        git('cherry-pick', *git('rev-list', '--reverse', 'main..feature').split())
        repo('tag', 'feature', 'refs/heads/feature')
        assert is_merged(git, 'feature', 'main')

    def test_squash_merge_of_pathspec_hostile_filenames(self, tmpdir: TempDir) -> None:
        # ':(bogus)…' is invalid pathspec magic and ':odd' an empty one — both are just files
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '-b', 'feature')
        (repo.path / ':(bogus)data').write_text('x')
        (repo.path / ':odd').write_text('y')
        repo('add', '.')
        repo('commit', '-qm', 'hostile names')
        repo('checkout', '-q', 'main')
        repo.commit_content('other-work')
        repo('merge', '-q', '--squash', 'feature')
        repo('commit', '-qm', 'squash feature')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_branch_too_wide_to_pathspec_searches_unscoped(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        replace(
            target=worktrees._PATHSPEC_LIMIT,
            container=worktrees,
            name='_PATHSPEC_LIMIT',
            replacement=0,
        )
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        repo('merge', '-q', '--squash', 'feature')
        repo('commit', '-qm', 'squash feature')
        with _log() as log:
            assert is_merged(Git(repo.path), 'feature', 'main')
        log.check(
            (
                'is_merged: too many paths to scope by, replaying all of base',
                {'ref': 'feature', 'base': 'main', 'paths': 2},
            ),
        )

    def test_squash_merge_when_base_history_carries_non_utf8(self, tmpdir: TempDir) -> None:
        # unrelated latin-1 content landing on main must never crash the containment check
        repo = _branched_then_advanced(Repo.make(tmpdir / 'r'))
        (repo.path / 'legacy.csv').write_bytes(b'M\xf6tley Cr\xfce\n')
        repo('add', 'legacy.csv')
        repo('commit', '-qm', 'latin-1 export')
        repo('merge', '-q', '--squash', 'feature')
        repo('commit', '-qm', 'squash feature')
        assert is_merged(Git(repo.path), 'feature', 'main')

    def test_squash_merge_of_non_utf8_content(self, tmpdir: TempDir) -> None:
        # the branch's own diff is latin-1, so the patch text itself can't be decoded
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('checkout', '-q', '-b', 'feature')
        (repo.path / 'legacy.csv').write_bytes(b'M\xf6tley Cr\xfce\n')
        repo('add', 'legacy.csv')
        repo('commit', '-qm', 'latin-1 export')
        repo.commit_content('more')
        repo('checkout', '-q', 'main')
        repo.commit_content('other-work')
        repo('merge', '-q', '--squash', 'feature')
        repo('commit', '-qm', 'squash feature')
        assert is_merged(Git(repo.path), 'feature', 'main')


def test_is_dirty(git_repo: Repo) -> None:
    assert not is_dirty(git_repo.path)
    (git_repo.path / 'scratch.txt').write_text('wip')
    assert is_dirty(git_repo.path)


class TestDefaultBranch:
    def test_main(self, git_repo: Repo) -> None:
        compare(default_branch(Git(git_repo.path)), expected='main')

    def test_master_style(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'm'), 'master')
        compare(default_branch(Git(repo.path)), expected='master')

    def test_resolves_via_origin_head(self, tmpdir: TempDir) -> None:
        source = _renamed(Repo.make(tmpdir / 'src'), 'trunk')  # neither main nor master
        compare(default_branch(Git.clone(source, tmpdir / 'clone')), expected='trunk')

    def test_falls_back_to_main(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'x'), 'trunk')  # no main/master, no origin
        compare(default_branch(Git(repo.path)), expected='main')


class TestBaseRef:
    def test_ties_favour_local(self, tmpdir: TempDir, git_repo: Repo) -> None:
        compare(base_ref(Git.clone(git_repo, tmpdir / 'clone')), expected='main')

    def test_prefers_origin_when_newer(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        clone = Git.clone(origin, tmpdir / 'clone')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))
        clone('fetch', 'origin')
        compare(base_ref(clone), expected='origin/main')

    def test_uses_the_default_branch(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'm'), 'master')
        compare(base_ref(Git(repo.path)), expected='master')

    def test_none_when_neither_ref_exists(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'x'), 'trunk')  # default resolves to main, but absent
        assert base_ref(Git(repo.path)) is None


class TestFetchOrigin:
    def test_no_op_without_an_origin(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        fetch_origin(git)  # must not raise when there is no origin remote
        compare(git('remote').split(), expected=[])

    def test_updates_remote_tracking_refs(self, tmpdir: TempDir, git_repo: Repo) -> None:
        clone = Git.clone(git_repo, tmpdir / 'clone')
        git_repo.commit_content('remote-ahead')
        fetch_origin(clone)
        compare(
            clone.rev_parse('origin/main', short=False),
            expected=Git(git_repo.path).rev_parse('main', short=False),
        )

    def test_or_offline_passes_a_fetch_through(self, tmpdir: TempDir, git_repo: Repo) -> None:
        clone = Git.clone(git_repo, tmpdir / 'clone')
        git_repo.commit_content('remote-ahead')
        fetch_origin_or_offline(clone)
        compare(
            clone.rev_parse('origin/main', short=False),
            expected=Git(git_repo.path).rev_parse('main', short=False),
        )

    def test_or_offline_turns_a_failure_into_the_offline_hint(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git = Git(git_repo.path)
        git('remote', 'add', 'origin', str(tmpdir / 'gone'))  # a dead remote — fetch fails fast
        with ShouldRaise(UserError, match='check network, or re-run with --offline'):
            fetch_origin_or_offline(git)


class TestIsGoalWorktree:
    def test_name_and_branch_agree(self) -> None:
        assert is_goal_worktree(Path('/x/worktrees/g@agent'), 'g/agent')

    def test_plain_checkout_with_an_at_in_its_name(self) -> None:
        assert not is_goal_worktree(Path('/x/proj@2'), 'g/human')

    def test_detached_head_in_a_managed_shape_counts(self) -> None:
        assert is_goal_worktree(Path('/x/worktrees/g@agent'), 'HEAD')

    def test_no_separator_at_all(self) -> None:
        assert not is_goal_worktree(Path('/x/plain'), 'g/agent')

    def test_two_separators_is_not_the_managed_shape(self) -> None:
        assert not is_goal_worktree(Path('/x/a@b@c'), 'a/b@c')


class TestCheckoutHere:
    def test_lands_the_branch_and_logs_the_head_move(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/human', 'main')  # a bare branch to land
        full = git.rev_parse('main', short=False)
        with _log() as log:
            result = checkout_here(git, 'g/human', git_repo.path, 'goal sync')
        compare(
            result,
            expected=Checkout(True, git_repo.path.resolve(), 'g/human', was='main'),
        )
        compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/human')
        log.check(
            (
                'goal sync: refs',
                {
                    'worktree': str(git_repo.path.resolve()),
                    'git': {'before': {'main': full}, 'after': {'g/human': full}},
                },
            ),
        )

    def test_records_a_detached_head_as_the_branch_left(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/human', 'main')
        git('checkout', '-q', '--detach', 'main')  # HEAD detached, not on a branch
        full = git.rev_parse('main', short=False)
        with _log() as log:
            result = checkout_here(git, 'g/human', git_repo.path, 'goal sync')
        compare(result, expected=Checkout(True, git_repo.path.resolve(), 'g/human', was=None))
        log.check(
            (
                'goal sync: refs',
                {
                    'worktree': str(git_repo.path.resolve()),
                    'git': {'before': {'HEAD': full}, 'after': {'g/human': full}},
                },
            ),
        )

    def test_leaves_a_dirty_checkout_untouched(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/human', 'main')
        (git_repo.path / 'scratch.txt').write_text('wip')
        with _log() as log:
            result = checkout_here(git, 'g/human', git_repo.path, 'goal sync')
        compare(result, expected=Checkout(False, git_repo.path.resolve(), 'g/human', was='main'))
        compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
        log.check_empty()  # nothing moved

    def test_skips_outside_a_git_repo(self, tmpdir: TempDir, git_repo: Repo) -> None:
        assert checkout_here(Git(git_repo.path), 'main', tmpdir.makedir('plain'), 'x') is None

    def test_skips_a_checkout_of_a_different_repo(self, tmpdir: TempDir, git_repo: Repo) -> None:
        other = Repo.make(tmpdir / 'other')
        other.commit_content('seed')
        assert checkout_here(Git(git_repo.path), 'main', other.path, 'x') is None

    def test_skips_a_managed_agent_worktree(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/human', 'main')
        git('worktree', 'add', '-b', 'g/agent', str(tmpdir / 'g@agent'), 'main')
        assert checkout_here(git, 'g/human', tmpdir / 'g@agent', 'x') is None
        compare(
            Git(tmpdir / 'g@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/agent'
        )

    def test_lands_in_a_plain_checkout_named_with_an_at(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git = Git(git_repo.path)
        git('branch', 'g/human', 'main')
        git('worktree', 'add', '-b', 'scratch', str(tmpdir / 'proj@2'), 'main')
        result = checkout_here(git, 'g/human', tmpdir / 'proj@2', 'goal sync')
        compare(
            result,
            expected=Checkout(True, (tmpdir / 'proj@2').resolve(), 'g/human', was='scratch'),
        )

    def test_skips_when_already_on_the_branch(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        git('checkout', '-q', '-b', 'g/human')  # already here
        with _log() as log:
            assert checkout_here(git, 'g/human', git_repo.path, 'x') is None
        log.check_empty()

    def test_skips_when_the_branch_is_checked_out_elsewhere(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git = Git(git_repo.path)
        git('worktree', 'add', str(tmpdir / 'other'), '-b', 'g/human', 'main')  # lives elsewhere
        assert checkout_here(git, 'g/human', git_repo.path, 'x') is None
        compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
