import os
from pathlib import Path

from testfixtures import Replacer, TempDir, compare
from typer.completion import completion_init
from typer.main import get_command
from typer._click.shell_completion import get_completion_class

from chimera.__main__ import app
from chimera.completions import complete_actor

completion_init()


def _complete(args: list[str], incomplete: str = '') -> list[str]:
    shell = get_completion_class('zsh')
    assert shell is not None
    completion = shell(get_command(app), {}, 'ch', '_CH_COMPLETE')
    return [item.value for item in completion.get_completions(args, incomplete)]


def _workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    for project, goal in (('alpha', 'fix-login'), ('beta', 'fix-search')):
        directory = ws / project
        (directory / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
        tmpdir.dump(f'lycia/{project}/config.yaml', {'kind': 'project', 'repo': str(directory)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def test_project_option_value(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['goal', 'ls', '-p']), expected=['alpha', 'beta'])


def test_project_value_prefix_filtered(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['project', 'rm'], 'al'), expected=['alpha'])


def test_goal_argument_widens_to_all_projects(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['goal', 'finish']), expected=['fix-login', 'fix-search'])


def test_goal_scoped_by_project_flag_at_root(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['-p', 'alpha', 'goal', 'finish']), expected=['fix-login'])


def test_goal_scoped_by_project_flag_on_leaf(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['worktree', 'rm', '-p', 'beta']), expected=['fix-search'])


def test_goal_scoped_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _workspace(tmpdir, replace)
    os.chdir(ws / 'beta')  # cwd inside a project scopes the goal completion to it
    compare(_complete(['goal', 'finish']), expected=['fix-search'])


def test_goal_option_value(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['agent', 'ls', '-g'], 'fix-lo'), expected=['fix-login'])


def test_ghost_project_completes_to_nothing(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['-p', 'ghost', 'goal', 'finish']), expected=[])


def test_outside_workspace_is_silent(tmpdir: TempDir) -> None:
    compare(_complete(['goal', 'finish']), expected=[])
    compare(_complete(['project', 'rm']), expected=[])


def test_actor_option_value() -> None:
    compare(_complete(['agent', 'start', '-a']), expected=['human', 'agent'])


def test_actors_positional_on_worktree_add() -> None:
    compare(_complete(['worktree', 'add', 'some-goal'], 'h'), expected=['human'])


def test_new_goal_arguments_do_not_complete(tmpdir: TempDir, replace: Replacer) -> None:
    _workspace(tmpdir, replace)
    compare(_complete(['goal', 'start']), expected=[])
    compare(_complete(['worktree', 'add']), expected=[])


def test_synonyms_not_offered() -> None:
    offered = _complete(['goal'])
    # the canonical 'finish' is offered; its synonyms 'new'/'cleanup' never are
    compare({'finish', 'new', 'cleanup'} & set(offered), expected={'finish'})


def test_zsh_and_bash_scripts_emit() -> None:
    for shell_name in ('zsh', 'bash'):
        shell = get_completion_class(shell_name)
        assert shell is not None
        script = shell(get_command(app), {}, 'ch', '_CH_COMPLETE').source()
        assert ('_CH_COMPLETE' in script) is True  # generated script too large to pin exactly


def test_complete_actor_directly() -> None:
    compare(complete_actor(''), expected=['human', 'agent'])
    compare(complete_actor('ag'), expected=['agent'])
