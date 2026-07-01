from collections.abc import Iterator
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, TempDir, compare
from typer import Typer

from chimera import __main__ as chimera_main
from chimera.__main__ import app
from chimera.commands.agent import agent, agents
from chimera.commands.goal import start as goal_start
from tests.cli import Command, action_logs


def _groups() -> Iterator[Typer]:
    yield app  # the root app carries synonyms too, but isn't in registered_groups
    for registered in app.registered_groups:
        assert registered.typer_instance is not None
        yield registered.typer_instance


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    return tmpdir.path


def test_new_dispatches_to_start(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    project = _project(tmpdir, git_repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    # the synonym dispatches to the canonical command, which is what gets logged
    command.run('goal', 'new', 'feature-x').check(
        output=f'Started feature-x in {worktree}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'offline': False,
            },
        ),
    )
    tmpdir.compare(['feature-x@agent'], path='worktrees', recursive=False)


def test_cleanup_dispatches_to_finish(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    project = _project(tmpdir, git_repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    command.run('goal', 'start', 'feature-x')
    base = Git(git_repo.path)('rev-parse', 'feature-x/agent').strip()
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    start, end = action_logs(
        'goal finish',
        'chimera.commands.worktree.rm.remove',
        {'goal': 'feature-x', 'force': False, 'project': None, 'offline': False},
    )
    command.run('goal', 'cleanup', 'feature-x').check(
        output=f'Removed {worktree}',
        logging=[
            start,
            {
                'level': 'INFO',
                'goal': 'feature-x',
                'git': {
                    'before': {'feature-x/agent': base, 'feature-x/human': base},
                    'after': {},
                },
                'force': False,
                'message': 'worktree rm: refs',
            },
            end,
        ],
    )
    tmpdir.compare(path='worktrees', expected=())


def test_synonyms_are_hidden_from_help(command: Command) -> None:
    output = command.run('goal', '--help').output.captured  # --help is not a logged action
    assert ('new' in output) is False
    assert ('cleanup' in output) is False


def _synonyms(group: Typer) -> dict[str, str]:
    return getattr(group.info.cls, 'synonyms', None) or {}


def test_no_synonym_shadows_a_real_command() -> None:
    for group in _groups():
        names = {cmd.name for cmd in group.registered_commands}
        compare(_synonyms(group).keys() & names, expected=set())  # no synonym shadows a command


def test_list_is_a_synonym_for_every_ls() -> None:
    # every group offering `ls` maps `list` onto it, so `list` works wherever `ls` does
    mapped = {
        _synonyms(group).get('list')
        for group in _groups()
        if 'ls' in {cmd.name for cmd in group.registered_commands}
    }
    compare(mapped, expected={'ls'})


def test_list_dispatches_to_ls(
    workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    replace.in_module(agents, list, module=chimera_main)
    # the synonym runs the canonical `ls`, which is what gets logged
    command.run('list').check(
        output='lycia',
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )
