from datetime import datetime
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import ShouldRaise, TempDir, compare

from chimera.commands.worktree.add import add
from chimera.config import UserError
from tests.cli import Command, action_logs


def _head(path: Path) -> str:
    return Git(path).rev_parse('HEAD', short=False)


def _branch(repo_path: Path, name: str) -> str:
    return Git(repo_path).rev_parse(name, short=False)


class TestGoalMode:
    def test_creates_only_the_agent_by_default(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = tmpdir / 'worktrees'
        compare(
            add(git_repo.path, worktrees, goal='my-goal'), expected=[worktrees / 'my-goal@agent']
        )
        tmpdir.compare(['my-goal@agent'], path='worktrees', recursive=False)
        # no my-goal/human — it's materialised on demand by `goal sync`, not up front
        compare(Git(git_repo.path).branches(), expected=['main', 'my-goal/agent'])

    def test_creates_extra_named_actors(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = tmpdir / 'worktrees'
        created = add(git_repo.path, worktrees, goal='g', actors=('human', 'agent', 'reviewer'))
        compare(created, expected=[worktrees / 'g@agent', worktrees / 'g@reviewer'])
        tmpdir.compare(['g@agent', 'g@reviewer'], path='worktrees', recursive=False)  # not human
        compare(
            Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'g/reviewer', 'main']
        )

    def test_refuses_a_traversal_goal(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(
            UserError(
                "'../escape' is not a valid goal name: no path separators — "
                "goal names are single path segments, like 'feature-x' or 'pr-123'"
            )
        ):
            add(git_repo.path, tmpdir / 'worktrees', goal='../escape')
        assert not (tmpdir / 'worktrees').exists()

    def test_refuses_a_traversal_actor(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(
            UserError(
                "'../escape' is not a valid actor name: no path separators — "
                "actor names are single path segments, like 'agent' or 'reviewer'"
            )
        ):
            add(git_repo.path, tmpdir / 'worktrees', goal='g', actors=('../escape',))
        assert not (tmpdir / 'worktrees').exists()

    def test_checks_out_the_agent_branch_in_its_worktree(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = tmpdir / 'worktrees'
        add(git_repo.path, worktrees, goal='g')
        agent = Git(worktrees / 'g@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip()
        compare(agent, expected='g/agent')
        compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])

    def test_branches_from_main_not_checked_out_branch(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        main = _head(git_repo.path)
        git_repo('checkout', '-b', 'feature')
        git_repo.commit_content('feature-work')
        assert _head(git_repo.path) != main  # repo is parked on a different commit
        worktrees = tmpdir / 'worktrees'
        [created] = add(git_repo.path, worktrees, goal='g')
        compare(_head(created), expected=main)
        compare(_branch(git_repo.path, 'g/agent'), expected=main)

    def test_branches_from_origin_main_when_newer(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        local = Git.clone(origin, tmpdir / 'repo')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))
        local('fetch', 'origin')
        expected = local.rev_parse('origin/main', short=False)
        assert expected != local.rev_parse('main', short=False)
        worktrees = tmpdir / 'worktrees'
        [created] = add(local.path, worktrees, goal='g')
        compare(_head(created), expected=expected)
        compare(_branch(local.path, 'g/agent'), expected=expected)

    def test_branches_from_local_main_when_newer(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        local = Repo.clone(origin, tmpdir / 'repo')
        local.commit_content('local-ahead', datetime(2022, 1, 1))
        expected = local.rev_parse('main', short=False)
        assert expected != local.rev_parse('origin/main', short=False)
        worktrees = tmpdir / 'worktrees'
        [created] = add(local.path, worktrees, goal='g')
        compare(_head(created), expected=expected)
        compare(_branch(local.path, 'g/agent'), expected=expected)

    def test_branches_have_no_upstream_tracking(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        local = Git.clone(origin, tmpdir / 'repo')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))
        local('fetch', 'origin')  # base resolves to origin/main, a remote-tracking branch
        worktrees = tmpdir / 'worktrees'
        add(local.path, worktrees, goal='g')
        upstream = local('for-each-ref', '--format=%(upstream)', 'refs/heads/g/agent').strip()
        compare(upstream, expected='')

    def test_uses_explicit_from_start_point(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('checkout', '-b', 'release')
        release = git_repo.commit_content('release-work', short=False)
        git_repo('checkout', 'main')
        worktrees = tmpdir / 'worktrees'
        [created] = add(git_repo.path, worktrees, goal='g', frm='release')
        compare(_head(created), expected=release)
        compare(_branch(git_repo.path, 'g/agent'), expected=release)

    def test_branches_from_a_master_style_default(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('branch', '-m', 'main', 'master')  # master-style default branch
        master = _head(git_repo.path)
        git_repo('checkout', '-b', 'feature')
        git_repo.commit_content('feature-work')  # park the repo elsewhere
        [created] = add(git_repo.path, tmpdir / 'worktrees', goal='g')
        compare(_head(created), expected=master)  # the default branch, not the checked-out feature
        compare(_branch(git_repo.path, 'g/agent'), expected=master)

    def test_offline_uses_already_present_refs(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        local = Git.clone(origin, tmpdir / 'repo')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))  # never fetched into local
        [created] = add(local.path, tmpdir / 'worktrees', goal='g', fetch=False)
        compare(_head(created), expected=local.rev_parse('main', short=False))  # stale local main

    def test_fetches_origin_by_default(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        local = Git.clone(origin, tmpdir / 'repo')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))  # not yet in local
        [created] = add(local.path, tmpdir / 'worktrees', goal='g')  # fetch=True picks it up
        compare(_head(created), expected=_head(origin.path))

    def test_dead_origin_suggests_offline(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('remote', 'add', 'origin', str(tmpdir / 'gone'))  # fetch fails, fast
        with ShouldRaise(UserError, match='check network, or re-run with --offline'):
            add(git_repo.path, tmpdir / 'worktrees', goal='g')

    def test_refuses_without_a_resolvable_default_branch(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git_repo('branch', '-m', 'main', 'trunk')  # no main/master, local or origin
        worktrees = tmpdir / 'worktrees'
        with ShouldRaise(
            UserError(
                f'{git_repo.path}: no default branch (main/master) to branch from, '
                f'local or on origin — pass --from <ref>'
            )
        ):
            add(git_repo.path, worktrees, goal='g')
        assert not worktrees.exists()  # refused before touching anything

    def test_from_rescues_a_repo_without_a_default_branch(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git_repo('branch', '-m', 'main', 'trunk')  # no main/master to resolve a base from
        trunk = _head(git_repo.path)
        [created] = add(git_repo.path, tmpdir / 'worktrees', goal='g', frm='trunk')
        compare(_head(created), expected=trunk)
        compare(_branch(git_repo.path, 'g/agent'), expected=trunk)

    def test_refuses_repo_without_commits(self, tmpdir: TempDir) -> None:
        repo = Repo.make(tmpdir / 'repo')  # no commit → unborn HEAD
        worktrees = tmpdir / 'worktrees'
        with ShouldRaise(RuntimeError, match='no commits'):  # message embeds `git status` output
            add(repo.path, worktrees, goal='g')
        assert not worktrees.exists()

    def test_refuses_bare_repo_without_commits(self, tmpdir: TempDir) -> None:
        bare = tmpdir / 'bare.git'
        Git(tmpdir.path)('init', '--bare', str(bare))  # no work tree, unborn HEAD
        worktrees = tmpdir / 'worktrees'
        with ShouldRaise(RuntimeError, match='no commits'):  # bare repo: unborn HEAD
            add(bare, worktrees, goal='g')


class TestAdHocMode:
    def test_checks_out_an_existing_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('checkout', '--detach')  # free 'main' up — git refuses a branch checked out twice
        checkout = tmpdir / 'checkout'
        [created] = add(git_repo.path, tmpdir / 'worktrees', branch='main', path=checkout)
        compare(created, expected=checkout)
        compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
        compare(_head(checkout), expected=_head(git_repo.path))

    def test_creates_parent_directories_for_the_path(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('checkout', '--detach')
        checkout = tmpdir / 'nested' / 'checkout'
        [created] = add(git_repo.path, tmpdir / 'worktrees', branch='main', path=checkout)
        compare(created, expected=checkout)

    def test_sets_up_tracking_when_origin_has_the_branch(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed')
        local = Git.clone(origin, tmpdir / 'repo')
        local('checkout', '--detach')  # free 'main' up for the ad-hoc worktree
        checkout = tmpdir / 'checkout'
        add(local.path, tmpdir / 'worktrees', branch='main', path=checkout, fetch=False)
        upstream = Git(checkout)(
            'for-each-ref', '--format=%(upstream:short)', 'refs/heads/main'
        ).strip()
        compare(upstream, expected='origin/main')

    def test_leaves_existing_tracking_alone(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed')
        local = Git.clone(origin, tmpdir / 'repo')
        local('branch', '--set-upstream-to=origin/main', 'main')  # already tracking, deliberately
        local('checkout', '--detach')  # free 'main' up for the ad-hoc worktree
        checkout = tmpdir / 'checkout'
        add(local.path, tmpdir / 'worktrees', branch='main', path=checkout, fetch=False)
        upstream = Git(checkout)(
            'for-each-ref', '--format=%(upstream:short)', 'refs/heads/main'
        ).strip()
        compare(upstream, expected='origin/main')  # unchanged, not clobbered

    def test_creates_a_new_branch_when_it_doesnt_exist(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        checkout = tmpdir / 'checkout'
        [created] = add(git_repo.path, tmpdir / 'worktrees', branch='scratch', path=checkout)
        compare(created, expected=checkout)
        compare(_branch(git_repo.path, 'scratch'), expected=_head(git_repo.path))
        compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='scratch')

    def test_new_branch_uses_explicit_from(self, tmpdir: TempDir, git_repo: Repo) -> None:
        git_repo('checkout', '-b', 'release')
        release = git_repo.commit_content('release-work', short=False)
        git_repo('checkout', 'main')
        checkout = tmpdir / 'checkout'
        [created] = add(
            git_repo.path, tmpdir / 'worktrees', branch='scratch', path=checkout, frm='release'
        )
        compare(_head(created), expected=release)

    def test_new_branch_refuses_without_a_resolvable_default_branch(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        git_repo('branch', '-m', 'main', 'trunk')  # no main/master, local or origin
        checkout = tmpdir / 'checkout'
        with ShouldRaise(
            UserError(
                f'{git_repo.path}: no default branch (main/master) to branch from, '
                f'local or on origin — pass --from <ref>'
            )
        ):
            add(git_repo.path, tmpdir / 'worktrees', branch='scratch', path=checkout)

    def test_refuses_goal_with_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('--goal is mutually exclusive with <branch>/<path>')):
            add(git_repo.path, tmpdir / 'worktrees', goal='g', branch='main')

    def test_refuses_goal_with_path(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('--goal is mutually exclusive with <branch>/<path>')):
            add(git_repo.path, tmpdir / 'worktrees', goal='g', path=tmpdir / 'checkout')

    def test_refuses_actors_without_goal(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('--actor requires --goal')):
            add(git_repo.path, tmpdir / 'worktrees', actors=('agent',))

    def test_refuses_branch_without_path(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('<branch> and <path> are required unless --goal is given')):
            add(git_repo.path, tmpdir / 'worktrees', branch='main')

    def test_refuses_path_without_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('<branch> and <path> are required unless --goal is given')):
            add(git_repo.path, tmpdir / 'worktrees', path=tmpdir / 'checkout')

    def test_refuses_neither_goal_nor_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError('<branch> and <path> are required unless --goal is given')):
            add(git_repo.path, tmpdir / 'worktrees')

    def test_refuses_path_inside_worktrees_root(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = tmpdir / 'worktrees'
        path = worktrees / 'main@somewhere'
        with ShouldRaise(UserError(f'{path}: use --goal to create a worktree under {worktrees}')):
            add(git_repo.path, worktrees, branch='main', path=path)


def test_worktree_add_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('worktree', 'add', '--goal', 'feature-x').check(
        output=f'Created {worktree}',
        logging=action_logs(
            'worktree add',
            'chimera.commands.worktree.add.add',
            {
                'branch': None,
                'path': None,
                'goal': 'feature-x',
                'actor': (),
                'frm': None,
                'project': None,
                'offline': False,
            },
        ),
    )
    tmpdir.compare(['feature-x@agent'], path='worktrees', recursive=False)  # human gets no worktree
    compare(Git(git_repo.path).branches(), expected=['feature-x/agent', 'main'])


def test_worktree_add_cli_from_option(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    git_repo('checkout', '-b', 'release')
    release = git_repo.commit_content('release-work', short=False)
    git_repo('checkout', 'main')
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('worktree', 'add', '--goal', 'feature-x', '--from', 'release').check(
        output=f'Created {worktree}',
        logging=action_logs(
            'worktree add',
            'chimera.commands.worktree.add.add',
            {
                'branch': None,
                'path': None,
                'goal': 'feature-x',
                'actor': (),
                'frm': 'release',
                'project': None,
                'offline': False,
            },
        ),
    )
    compare(_head(project / 'worktrees' / 'feature-x@agent'), expected=release)


def test_worktree_add_cli_offline(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('worktree', 'add', '--goal', 'feature-x', '--offline').check(
        output=f'Created {worktree}',
        logging=action_logs(
            'worktree add',
            'chimera.commands.worktree.add.add',
            {
                'branch': None,
                'path': None,
                'goal': 'feature-x',
                'actor': (),
                'frm': None,
                'project': None,
                'offline': True,
            },
        ),
    )
    compare(Git(git_repo.path).branches(), expected=['feature-x/agent', 'main'])


def test_worktree_add_cli_ad_hoc(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    git_repo('checkout', '--detach')  # free 'main' up — git refuses a branch checked out twice
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    checkout = tmpdir / 'checkout'
    command.run('worktree', 'add', 'main', str(checkout)).check(
        output=f'Created {checkout}',
        logging=action_logs(
            'worktree add',
            'chimera.commands.worktree.add.add',
            {
                'branch': 'main',
                'path': str(checkout),
                'goal': None,
                'actor': (),
                'frm': None,
                'project': None,
                'offline': False,
            },
        ),
    )
    compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')


def test_worktree_ls_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', '--goal', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'ls').check(
        output=str(worktree),
        logging=action_logs('worktree ls', 'chimera.commands.worktree.ls.ls', {'project': None}),
    )
