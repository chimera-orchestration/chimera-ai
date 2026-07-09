import os
from pathlib import Path

from giterator import Git
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.commands.project.checkout import checkout
from chimera.config import UserError
from tests.cli import Command, action_logs


def _cli_project(tmpdir: TempDir, repo: Path, replace: Replacer) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('lycia/myproj/config.yaml', {'kind': 'project', 'repo': str(repo)})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    os.chdir(workspace / 'myproj')  # the CLI infers the project from cwd


def test_checkout_defaults_to_the_default_branch(tmpdir: TempDir, bare_repo: Path) -> None:
    path = tmpdir / 'checkout'
    compare(checkout(bare_repo, tmpdir / 'proj' / 'worktrees', path), expected='main')
    compare(Git(path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')


def test_checkout_of_an_explicit_new_branch(tmpdir: TempDir, bare_repo: Path) -> None:
    path = tmpdir / 'checkout'
    compare(
        checkout(bare_repo, tmpdir / 'proj' / 'worktrees', path, branch='feature'),
        expected='feature',
    )
    git = Git(path)
    compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='feature')
    # a new branch starts where worktree add's base resolution says: the default branch
    compare(
        git.rev_parse('feature', short=False),
        expected=Git(bare_repo).rev_parse('main', short=False),
    )


def test_checkout_refuses_a_path_under_worktrees(tmpdir: TempDir, bare_repo: Path) -> None:
    worktrees = tmpdir / 'proj' / 'worktrees'
    path = worktrees / 'sneaky'
    with ShouldRaise(UserError(f'{path}: use --goal to create a worktree under {worktrees}')):
        checkout(bare_repo, worktrees, path)


def test_project_checkout_cli(
    tmpdir: TempDir, bare_repo: Path, replace: Replacer, command: Command
) -> None:
    _cli_project(tmpdir, bare_repo, replace)
    path = tmpdir / 'checkout'
    command.run('project', 'checkout', str(path)).check(
        output=f'Checked out main at {path}',
        logging=action_logs(
            'project checkout',
            'chimera.commands.project.checkout.checkout',
            {'path': str(path), 'branch': None, 'offline': False, 'project': None},
        ),
    )
    compare(Git(path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')


def test_project_checkout_cli_branch(
    tmpdir: TempDir, bare_repo: Path, replace: Replacer, command: Command
) -> None:
    _cli_project(tmpdir, bare_repo, replace)
    path = tmpdir / 'checkout'
    command.run('project', 'checkout', str(path), '--branch', 'feature').check(
        output=f'Checked out feature at {path}',
        logging=action_logs(
            'project checkout',
            'chimera.commands.project.checkout.checkout',
            {'path': str(path), 'branch': 'feature', 'offline': False, 'project': None},
        ),
    )
    compare(Git(path)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='feature')
