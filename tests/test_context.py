from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.config import NotInProjectError, NotInWorkspaceError
from chimera.context import (
    CannotIdentifyProjectError,
    GoalRequiredError,
    Project,
    iter_projects,
    resolve_goal,
    resolve_project,
    resolve_scope,
    resolve_workspace,
)
from chimera.commands.worktree.add import add


def _workspace(tmpdir: TempDir, name: str = 'lycia') -> Path:
    ws = tmpdir.makedir(name)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    return ws


def _project(parent: Path, name: str = 'proj', repo: str = '/r') -> Path:
    project = parent / name
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo}\n')
    return project


# ---- workspace -------------------------------------------------------------


def test_resolve_workspace_prefers_env(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _workspace(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    assert resolve_workspace(tmpdir.path / 'somewhere-else') == ws  # env wins over cwd


def test_resolve_workspace_env_must_point_at_a_workspace(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(tmpdir.makedir('not-a-workspace')))
    with pytest.raises(NotInWorkspaceError):
        resolve_workspace(tmpdir.path)


def test_resolve_workspace_falls_back_to_walk_up(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    assert resolve_workspace(_project(ws)) == ws  # env unset (cleared by conftest)


def test_resolve_workspace_raises_when_unfound(tmpdir: TempDir) -> None:
    with pytest.raises(NotInWorkspaceError):
        resolve_workspace(tmpdir.path)


# ---- project ---------------------------------------------------------------


def test_resolve_project_infers_from_cwd(tmpdir: TempDir) -> None:
    project = _project(_workspace(tmpdir))
    resolved = resolve_project(project)
    assert resolved.dir == project
    assert resolved.name == 'proj'
    assert resolved.repo == Path('/r')
    assert resolved.worktrees == project / 'worktrees'


def test_resolve_project_by_name_overrides_cwd(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    foo = _project(ws, 'foo')
    bar = _project(ws, 'bar')
    assert resolve_project(bar, 'foo').dir == foo  # standing in bar, asked for foo


def test_resolve_project_by_name_raises_when_absent(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    with pytest.raises(NotInProjectError):
        resolve_project(ws, 'ghost')


def test_resolve_project_matches_repo_from_external_checkout(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'external')  # lives outside the workspace
    repo.commit_content('seed')
    project = _project(ws, 'myproj', repo=str(repo.path))
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))  # cwd is outside lycia, so anchor by env
    assert resolve_project(repo.path).dir == project


def test_resolve_project_matches_repo_from_a_linked_worktree(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'external')
    repo.commit_content('seed')
    project = _project(ws, 'myproj', repo=str(repo.path))
    checkout = tmpdir.path / 'review'
    Git(repo.path)('worktree', 'add', '-b', 'review', str(checkout), 'main')  # worktree elsewhere
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    assert resolve_project(checkout).dir == project  # matched via the main repo


def test_resolve_project_raises_when_repo_matches_nothing(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmpdir)
    stranger = Repo.make(tmpdir.path / 'stranger')  # not registered as a project
    stranger.commit_content('seed')
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    with pytest.raises(CannotIdentifyProjectError):
        resolve_project(stranger.path)


def test_resolve_project_raises_at_the_workspace_root(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)  # walk-up reaches the workspace marker without crossing a project
    with pytest.raises(CannotIdentifyProjectError):
        resolve_project(ws)


def test_resolve_project_raises_outside_any_git_repo(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    with pytest.raises(CannotIdentifyProjectError):
        resolve_project(tmpdir.makedir('bare'))  # not a git repo, no markers


def test_iter_projects_lists_only_projects_sorted(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'beta')
    _project(ws, 'alpha')
    tmpdir.makedir('lycia/stray')  # no config.yaml — ignored
    assert [p.name for p in iter_projects(ws)] == ['alpha', 'beta']


# ---- goal ------------------------------------------------------------------


def _project_with_goal(tmpdir: TempDir) -> tuple[Project, Repo]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project_dir = _project(tmpdir.path, 'proj', repo=str(repo.path))
    add(repo.path, project_dir / 'worktrees', 'g')  # g@agent worktree + g/human branch
    return resolve_project(project_dir), repo


def test_resolve_goal_explicit_wins(tmpdir: TempDir) -> None:
    project, _repo = _project_with_goal(tmpdir)
    assert resolve_goal(tmpdir.path, project, 'whatever') == 'whatever'


def test_resolve_goal_infers_from_a_goal_branch(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)
    checkout = tmpdir.path / 'human'
    Git(repo.path)('worktree', 'add', str(checkout), 'g/human')  # on <goal>/<actor>
    assert resolve_goal(checkout, project) == 'g'


def test_resolve_goal_ignores_a_non_goal_branch(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)  # repo itself is on plain 'main'
    with pytest.raises(GoalRequiredError):
        resolve_goal(repo.path, project)


def test_resolve_goal_ignores_a_branch_that_is_not_a_real_goal(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)
    checkout = tmpdir.path / 'review'
    Git(repo.path)('worktree', 'add', '-b', 'ghost/human', str(checkout), 'main')
    with pytest.raises(GoalRequiredError):  # shaped right, but 'ghost' is not a goal
        resolve_goal(checkout, project)


def test_resolve_goal_requires_one_outside_a_repo(tmpdir: TempDir) -> None:
    project, _repo = _project_with_goal(tmpdir)
    with pytest.raises(GoalRequiredError):
        resolve_goal(tmpdir.makedir('bare'), project)


# ---- scope -----------------------------------------------------------------


def _scoped_project_with_goal(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> tuple[Project, Repo, Path]:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project_dir = _project(ws, 'proj', repo=str(repo.path))
    add(repo.path, project_dir / 'worktrees', 'g')
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    return resolve_project(project_dir), repo, ws


def test_resolve_scope_widens_to_all_projects_at_the_workspace_root(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'a')
    scope = resolve_scope(ws)
    assert scope.workspace == ws
    assert scope.project is None  # couldn't pin one → list them all
    assert scope.goal is None


def test_resolve_scope_pins_the_project_from_within_it(tmpdir: TempDir) -> None:
    project = _project(_workspace(tmpdir))
    scope = resolve_scope(project)
    assert scope.project is not None and scope.project.dir == project
    assert scope.goal is None  # not on a goal branch


def test_resolve_scope_pins_the_goal_from_a_goal_branch(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, repo, _ws = _scoped_project_with_goal(tmpdir, monkeypatch)
    checkout = tmpdir.path / 'human'
    Git(repo.path)('worktree', 'add', str(checkout), 'g/human')
    scope = resolve_scope(checkout)
    assert scope.project is not None and scope.project.dir == project.dir
    assert scope.goal == 'g'


def test_resolve_scope_external_checkout_pins_project_not_goal(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, repo, _ws = _scoped_project_with_goal(tmpdir, monkeypatch)
    scope = resolve_scope(repo.path)  # the repo is on plain 'main'
    assert scope.project is not None and scope.project.dir == project.dir
    assert scope.goal is None


def test_resolve_scope_bad_explicit_project_still_raises(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    with pytest.raises(NotInProjectError):
        resolve_scope(ws, project='ghost')
