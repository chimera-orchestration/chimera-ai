from pathlib import Path

import pytest
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app

runner = CliRunner()


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def _project(tmpdir: TempDir, repo: Repo, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    return project


def test_new_dispatches_to_start(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo, monkeypatch)
    monkeypatch.setattr('chimera.commands.goal.start.agent', lambda *a, **k: None)
    result = runner.invoke(app, ['goal', 'new', 'feature-x'])
    assert result.exit_code == 0
    assert (project / 'worktrees' / 'feature-x@agent').is_dir()


def test_cleanup_dispatches_to_finish(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo, monkeypatch)
    monkeypatch.setattr('chimera.commands.goal.start.agent', lambda *a, **k: None)
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
