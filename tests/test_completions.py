import os
from pathlib import Path

from giterator.testing import Repo
from testfixtures import TempDir, compare
from typer._click.shell_completion import get_completion_class
from typer.completion import completion_init
from typer.main import get_command

from chimera.__main__ import app
from chimera.commands.doctor import CHECKS
from chimera.completions import (
    complete_actor,
    complete_check,
    complete_harness,
    complete_template,
)

completion_init()


def _complete(args: list[str], incomplete: str = '') -> list[str]:
    shell = get_completion_class('zsh')
    assert shell is not None
    completion = shell(get_command(app), {}, 'ch', '_CH_COMPLETE')
    return [item.value for item in completion.get_completions(args, incomplete)]


def _projects(tmpdir: TempDir, workspace: Path) -> None:
    for project, goal in (('alpha', 'fix-login'), ('beta', 'fix-search')):
        directory = workspace / project
        (directory / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
        tmpdir.dump(
            directory / 'config.yaml',
            {'kind': 'project', 'repo': str(directory)},
        )


def test_project_option_value(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['goal', 'ls', '-p']), expected=['alpha', 'beta'])


def test_project_value_prefix_filtered(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['project', 'rm'], 'al'), expected=['alpha'])


def test_goal_argument_widens_to_all_projects(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['goal', 'finish']), expected=['fix-login', 'fix-search'])


def test_goal_scoped_by_project_flag_at_root(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['-p', 'alpha', 'goal', 'finish']), expected=['fix-login'])


def test_goal_scoped_by_project_flag_on_leaf(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['worktree', 'rm', '-p', 'beta']), expected=['fix-search'])


def test_goal_scoped_by_cwd(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    os.chdir(workspace_with_env / 'beta')  # cwd inside a project scopes the goal completion to it
    compare(_complete(['goal', 'finish']), expected=['fix-search'])


def test_goal_option_value(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['agent', 'ls', '-g'], 'fix-lo'), expected=['fix-login'])


def test_ghost_project_completes_to_nothing(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['-p', 'ghost', 'goal', 'finish']), expected=[])


def test_outside_workspace_is_silent(tmpdir: TempDir) -> None:
    compare(_complete(['goal', 'finish']), expected=[])
    compare(_complete(['project', 'rm']), expected=[])


def test_actor_option_value() -> None:
    compare(_complete(['agent', 'start', '-a']), expected=['human', 'agent'])


def test_actor_option_value_on_worktree_add() -> None:
    compare(_complete(['worktree', 'add', '--goal', 'some-goal', '-a'], 'h'), expected=['human'])


def test_new_goal_arguments_do_not_complete(tmpdir: TempDir, workspace_with_env: Path) -> None:
    _projects(tmpdir, workspace_with_env)
    compare(_complete(['goal', 'start']), expected=[])
    compare(_complete(['worktree', 'add']), expected=[])


def test_synonyms_are_offered_alongside_canonical() -> None:
    offered = _complete(['goal'])
    # the canonical 'finish' and its synonyms 'new'/'cleanup' all complete
    assert {'finish', 'new', 'cleanup'} <= set(offered)


def test_synonym_prefix_filtered() -> None:
    compare(_complete(['goal'], 'clea'), expected=['cleanup'])


def test_zsh_and_bash_scripts_emit() -> None:
    for shell_name in ('zsh', 'bash'):
        shell = get_completion_class(shell_name)
        assert shell is not None
        script = shell(get_command(app), {}, 'ch', '_CH_COMPLETE').source()
        assert '_CH_COMPLETE' in script  # generated script too large to pin exactly


def test_complete_actor_directly() -> None:
    compare(complete_actor(''), expected=['human', 'agent'])
    compare(complete_actor('ag'), expected=['agent'])


def test_check_option_value() -> None:
    compare(
        _complete(['doctor', '-c'], 'worktree-'),
        expected=['worktree-separator', 'worktree-branch'],
    )


def test_complete_harness_directly() -> None:
    compare(complete_harness(''), expected=['claude'])
    compare(complete_harness('co'), expected=[])


def test_complete_template_directly() -> None:
    compare(complete_template(''), expected=['pr', 'review'])
    compare(complete_template('r'), expected=['review'])


def test_complete_check_directly() -> None:
    compare(complete_check(''), expected=[check.name for check in CHECKS])
    compare(complete_check('git'), expected=['gitignore'])


def test_remote_option_value(tmpdir: TempDir, workspace_with_env: Path) -> None:
    repo = Repo.make(tmpdir / 'repo')
    repo('remote', 'add', 'origin', str(tmpdir / 'o'))
    repo('remote', 'add', 'fork', str(tmpdir / 'f'))
    tmpdir.dump(
        workspace_with_env / 'proj' / 'config.yaml',
        {'kind': 'project', 'repo': str(repo.path)},
    )
    compare(_complete(['-p', 'proj', 'goal', 'pr', 'g', '--to']), expected=['fork', 'origin'])
    compare(_complete(['-p', 'proj', 'goal', 'pr', 'g', '--to'], 'or'), expected=['origin'])


def test_remote_without_a_repo_is_silent(tmpdir: TempDir, workspace_with_env: Path) -> None:
    tmpdir.dump(
        workspace_with_env / 'proj' / 'config.yaml',
        {'kind': 'project', 'repo': str(tmpdir / 'ghost')},  # repo path doesn't exist
    )
    compare(_complete(['-p', 'proj', 'goal', 'pr', 'g', '--to']), expected=[])
