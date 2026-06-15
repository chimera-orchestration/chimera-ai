from pathlib import Path

from giterator.testing import Repo
from testfixtures import Replacer, TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import agent
from chimera.commands.goal import start as goal_start

runner = CliRunner()


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    (tmpdir.path / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    return tmpdir.path


def test_new_dispatches_to_start(tmpdir: TempDir, replace: Replacer) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    result = runner.invoke(app, ['goal', 'new', 'feature-x'])
    assert result.exit_code == 0
    assert (project / 'worktrees' / 'feature-x@agent').is_dir()


def test_cleanup_dispatches_to_finish(tmpdir: TempDir, replace: Replacer) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo)
    replace.in_module(agent, lambda *a, **k: None, module=goal_start)
    runner.invoke(app, ['goal', 'start', 'feature-x'])
    result = runner.invoke(app, ['goal', 'cleanup', 'feature-x'])
    assert result.exit_code == 0
    assert not (project / 'worktrees' / 'feature-x@agent').exists()


def test_synonyms_are_hidden_from_help() -> None:
    result = runner.invoke(app, ['goal', '--help'])
    assert 'new' not in result.output
    assert 'cleanup' not in result.output


def test_no_synonym_shadows_a_real_command() -> None:
    for registered in app.registered_groups:
        instance = registered.typer_instance
        assert instance is not None
        synonyms = getattr(instance.info.cls, 'synonyms', None)
        if not synonyms:
            continue
        names = {cmd.name for cmd in instance.registered_commands}
        assert not (synonyms.keys() & names), (
            f'synonym shadows a command: {synonyms.keys() & names}'
        )
