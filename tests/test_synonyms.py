from pathlib import Path

from giterator.testing import Repo
from testfixtures import Command, Replacer, TempDir, compare

from chimera.__main__ import app
from chimera.commands.agent import agent
from chimera.commands.goal import start as goal_start


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir / 'repo')
    repo.commit_content('seed')
    return repo


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    return tmpdir.path


def test_new_dispatches_to_start(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    # the synonym dispatches to the canonical command, which is what gets logged
    command.run('goal', 'new', 'feature-x').check(
        output=f'Started feature-x in {worktree}', logging=[('INFO', 'goal start')]
    )
    tmpdir.compare(['feature-x@agent'], path='worktrees', recursive=False)


def test_cleanup_dispatches_to_finish(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    command.run('goal', 'start', 'feature-x')
    worktree = (project / 'worktrees' / 'feature-x@agent').resolve()
    command.run('goal', 'cleanup', 'feature-x').check(
        output=f'Removed {worktree}', logging=[('INFO', 'goal finish')]
    )
    tmpdir.compare(path='worktrees', expected=())


def test_synonyms_are_hidden_from_help(command: Command) -> None:
    output = command.run('goal', '--help').output.captured  # --help is not a logged action
    assert ('new' in output) is False
    assert ('cleanup' in output) is False


def test_no_synonym_shadows_a_real_command() -> None:
    for registered in app.registered_groups:
        instance = registered.typer_instance
        assert instance is not None
        synonyms = getattr(instance.info.cls, 'synonyms', None)
        if not synonyms:
            continue
        names = {cmd.name for cmd in instance.registered_commands}
        compare(synonyms.keys() & names, expected=set())  # no synonym shadows a real command
