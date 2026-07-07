from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.commands.project.add import add
from chimera.config import UserError
from tests.cli import Command, action_logs


def test_add_tracks_an_existing_local_path(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    compare(add(workspace, str(repo)), expected=workspace / 'myrepo')
    compare(
        tmpdir.parse('lycia/myrepo/config.yaml'),
        expected={'kind': 'project', 'repo': str(repo.resolve())},
    )


def test_add_clones_a_url_into_the_workspace(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    compare(add(workspace, f'file://{origin.path}'), expected=workspace / 'origin')
    repo = workspace / 'origin' / 'repo'
    git = Git(repo)
    compare(git('rev-parse', '--is-bare-repository').strip(), expected='true')  # no working tree
    compare(git.branches(), expected=['main'])
    compare(
        git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD').strip(),
        expected='origin/main',
    )
    compare(
        tmpdir.parse('lycia/origin/config.yaml'), expected={'kind': 'project', 'repo': str(repo)}
    )


def test_add_strips_git_suffix_from_the_cloned_name(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'thing.git')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    # .git stripped from the project name
    compare(add(workspace, f'file://{origin.path}'), expected=workspace / 'thing')


def test_add_checkout_stands_up_a_plain_worktree_of_the_default_branch(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    checkout = tmpdir / 'checkout'
    compare(
        add(workspace, f'file://{origin.path}', checkout=checkout), expected=workspace / 'origin'
    )
    assert (checkout / 'seed').is_file()  # a real working tree, files present
    git = Git(checkout)
    compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
    upstream = git('for-each-ref', '--format=%(upstream:short)', 'refs/heads/main').strip()
    compare(upstream, expected='origin/main')  # push/pull work without -u


def test_add_checkout_refuses_for_a_local_path(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    checkout = tmpdir / 'checkout'
    with ShouldRaise(
        UserError(
            f'--checkout only applies when cloning a git URL, not tracking a local path: {repo}'
        )
    ):
        add(workspace, str(repo), checkout=checkout)
    assert not checkout.exists()
    assert not (workspace / 'myrepo').exists()  # refused before touching anything


def test_add_checkout_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    checkout = tmpdir / 'checkout'
    command.run('project', 'add', f'file://{origin.path}', '--checkout', str(checkout)).check(
        output=f'Added {workspace / "origin"}\nChecked out at {checkout}',
        logging=action_logs(
            'project add',
            'chimera.commands.project.add.add',
            {'source': f'file://{origin.path}', 'checkout': str(checkout)},
        ),
    )
    assert (checkout / 'seed').is_file()
