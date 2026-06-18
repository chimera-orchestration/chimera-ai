from datetime import datetime
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, ShouldRaise, TempDir, compare

from chimera.commands.worktree.add import add


def _head(path: Path) -> str:
    return Git(path).rev_parse('HEAD', short=False)


def _branch(repo_path: Path, name: str) -> str:
    return Git(repo_path).rev_parse(name, short=False)


def test_add_creates_agent_worktree_and_both_branches(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = tmpdir / 'worktrees'
    compare(add(git_repo.path, worktrees, 'my-goal'), expected=[worktrees / 'my-goal@agent'])
    tmpdir.compare(['my-goal@agent'], path='worktrees', recursive=False)  # human gets no worktree
    compare(Git(git_repo.path).branches(), expected=['main', 'my-goal/agent', 'my-goal/human'])


def test_add_creates_extra_named_actors(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = tmpdir / 'worktrees'
    created = add(git_repo.path, worktrees, 'g', actors=('human', 'agent', 'reviewer'))
    compare(created, expected=[worktrees / 'g@agent', worktrees / 'g@reviewer'])
    tmpdir.compare(['g@agent', 'g@reviewer'], path='worktrees', recursive=False)  # not human
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'g/reviewer', 'main'])


def test_add_checks_out_the_agent_branch_in_its_worktree(tmpdir: TempDir, git_repo: Repo) -> None:
    worktrees = tmpdir / 'worktrees'
    add(git_repo.path, worktrees, 'g')
    agent = Git(worktrees / 'g@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip()
    compare(agent, expected='g/agent')
    # g/human exists, but is checked out nowhere
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_add_branches_from_main_not_checked_out_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    main = _head(git_repo.path)
    git_repo('checkout', '-b', 'feature')
    git_repo.commit_content('feature-work')
    assert (_head(git_repo.path) == main) is False  # repo is parked on a different commit
    worktrees = tmpdir / 'worktrees'
    [created] = add(git_repo.path, worktrees, 'g')
    compare(_head(created), expected=main)
    compare(_branch(git_repo.path, 'g/human'), expected=main)


def test_add_branches_from_origin_main_when_newer(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir / 'repo')
    origin.commit_content('remote-ahead', datetime(2022, 1, 1))
    local('fetch', 'origin')
    expected = local.rev_parse('origin/main', short=False)
    assert (expected == local.rev_parse('main', short=False)) is False
    worktrees = tmpdir / 'worktrees'
    [created] = add(local.path, worktrees, 'g')
    compare(_head(created), expected=expected)
    compare(_branch(local.path, 'g/human'), expected=expected)


def test_add_branches_from_local_main_when_newer(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir / 'repo')
    Repo(local.path).commit_content('local-ahead', datetime(2022, 1, 1))
    expected = local.rev_parse('main', short=False)
    assert (expected == local.rev_parse('origin/main', short=False)) is False
    worktrees = tmpdir / 'worktrees'
    [created] = add(local.path, worktrees, 'g')
    compare(_head(created), expected=expected)
    compare(_branch(local.path, 'g/human'), expected=expected)


def test_add_branches_have_no_upstream_tracking(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir / 'repo')
    origin.commit_content('remote-ahead', datetime(2022, 1, 1))
    local('fetch', 'origin')  # base resolves to origin/main, a remote-tracking branch
    worktrees = tmpdir / 'worktrees'
    add(local.path, worktrees, 'g')
    upstreams = {
        ref: local('for-each-ref', '--format=%(upstream)', f'refs/heads/{ref}').strip()
        for ref in ('g/human', 'g/agent')
    }
    compare(upstreams, expected={'g/human': '', 'g/agent': ''})


def test_add_uses_explicit_from_start_point(tmpdir: TempDir, git_repo: Repo) -> None:
    git_repo('checkout', '-b', 'release')
    release = git_repo.commit_content('release-work', short=False)
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    [created] = add(git_repo.path, worktrees, 'g', frm='release')
    compare(_head(created), expected=release)
    compare(_branch(git_repo.path, 'g/human'), expected=release)


def test_add_falls_back_to_head_without_a_main_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    git_repo('branch', '-m', 'main', 'trunk')  # no main, no origin/main
    head = _head(git_repo.path)
    worktrees = tmpdir / 'worktrees'
    [created] = add(git_repo.path, worktrees, 'g')
    compare(_head(created), expected=head)
    compare(_branch(git_repo.path, 'g/human'), expected=head)


def test_add_refuses_repo_without_commits(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir / 'repo')  # no commit → unborn HEAD
    worktrees = tmpdir / 'worktrees'
    with ShouldRaise(RuntimeError, match='no commits'):  # message embeds `git status` output
        add(repo.path, worktrees, 'g')
    assert worktrees.exists() is False


def test_add_refuses_bare_repo_without_commits(tmpdir: TempDir) -> None:
    bare = tmpdir / 'bare.git'
    Git(tmpdir.path)('init', '--bare', str(bare))  # no work tree, unborn HEAD
    worktrees = tmpdir / 'worktrees'
    with ShouldRaise(RuntimeError, match='no commits'):  # bare repo: unborn HEAD
        add(bare, worktrees, 'g')


def test_worktree_add_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('worktree', 'add', 'feature-x').check(
        output=f'Created {worktree}', logging=[('INFO', 'worktree add')]
    )
    tmpdir.compare(['feature-x@agent'], path='worktrees', recursive=False)  # human gets no worktree
    compare(Git(git_repo.path).branches(), expected=['feature-x/agent', 'feature-x/human', 'main'])


def test_worktree_add_cli_from_option(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    git_repo('checkout', '-b', 'release')
    release = git_repo.commit_content('release-work', short=False)
    git_repo('checkout', 'main')
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('worktree', 'add', 'feature-x', '--from', 'release').check(
        output=f'Created {worktree}', logging=[('INFO', 'worktree add')]
    )
    compare(_head(project / 'worktrees' / 'feature-x@agent'), expected=release)


def test_worktree_ls_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = tmpdir.path
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    command.run('worktree', 'add', 'g')
    worktree = (project / 'worktrees' / 'g@agent').resolve()
    command.run('worktree', 'ls').check(output=str(worktree), logging=[('INFO', 'worktree ls')])
