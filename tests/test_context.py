from pathlib import Path

import pytest
from testfixtures import TempDir

from chimera.config import NotInProjectError
from chimera.context import resolve_project


def _workspace(tmpdir: TempDir, name: str = 'lycia') -> Path:
    ws = tmpdir.makedir(name)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    return ws


def _project(parent: Path, name: str = 'proj', repo: str = '/r') -> Path:
    project = parent / name
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo}\n')
    return project


def test_resolve_infers_project_from_cwd(tmpdir: TempDir) -> None:
    project = _project(_workspace(tmpdir))
    resolved = resolve_project(project)
    assert resolved.dir == project
    assert resolved.name == 'proj'
    assert resolved.repo == Path('/r')
    assert resolved.worktrees == project / 'worktrees'


def test_resolve_by_name_overrides_cwd(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    foo = _project(ws, 'foo')
    bar = _project(ws, 'bar')
    assert resolve_project(bar, 'foo').dir == foo  # standing in bar, asked for foo


def test_resolve_by_name_raises_when_absent(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    with pytest.raises(NotInProjectError):
        resolve_project(ws, 'ghost')
