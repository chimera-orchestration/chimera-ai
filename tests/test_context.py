from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.config import NotInProjectError, NotInWorkspaceError, ProjectConfig
from chimera.context import (
    CannotIdentifyProjectError,
    GoalRequiredError,
    Project,
    Scope,
    iter_projects,
    resolve_goal,
    resolve_project,
    resolve_scope,
    resolve_workspace,
)
from chimera.commands.worktree.add import add


def _workspace(tmpdir: TempDir, name: str = 'lycia') -> Path:
    ws = tmpdir.makedir(name)
    tmpdir.dump(f'{name}/config.yaml', {'kind': 'workspace'})
    return ws


def _project(tmpdir: TempDir, parent: Path, name: str = 'proj', repo: str = '/r') -> Path:
    project = parent / name
    tmpdir.dump(
        str(project.relative_to(tmpdir.path) / 'config.yaml'), {'kind': 'project', 'repo': repo}
    )
    return project


def _resolved(project_dir: Path, repo: str = '/r') -> Project:
    return Project(project_dir, ProjectConfig(kind='project', repo=Path(repo)))


# ---- workspace -------------------------------------------------------------


def test_resolve_workspace_prefers_env(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    compare(resolve_workspace(tmpdir.path / 'somewhere-else'), expected=ws)  # env wins over cwd


def test_resolve_workspace_env_must_point_at_a_workspace(
    tmpdir: TempDir, replace: Replacer
) -> None:
    nope = tmpdir.makedir('not-a-workspace')
    replace.in_environ('CHIMERA_WORKSPACE', str(nope))
    with ShouldRaise(NotInWorkspaceError(nope)):
        resolve_workspace(tmpdir.path)


def test_resolve_workspace_falls_back_to_walk_up(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    compare(resolve_workspace(_project(tmpdir, ws)), expected=ws)  # env unset (cleared by conftest)


def test_resolve_workspace_raises_when_unfound(tmpdir: TempDir) -> None:
    with ShouldRaise(NotInWorkspaceError(tmpdir.path)):
        resolve_workspace(tmpdir.path)


# ---- project ---------------------------------------------------------------


def test_resolve_project_infers_from_cwd(tmpdir: TempDir) -> None:
    project = _project(tmpdir, _workspace(tmpdir))
    compare(resolve_project(project), expected=_resolved(project))


def test_resolve_project_by_name_overrides_cwd(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    foo = _project(tmpdir, ws, 'foo')
    bar = _project(tmpdir, ws, 'bar')
    compare(resolve_project(bar, 'foo'), expected=_resolved(foo))  # standing in bar, asked for foo


def test_resolve_project_by_name_raises_when_absent(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    with ShouldRaise(NotInProjectError(ws / 'ghost')):
        resolve_project(ws, 'ghost')


def test_resolve_project_matches_repo_from_external_checkout(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'external')  # lives outside the workspace
    repo.commit_content('seed')
    project = _project(tmpdir, ws, 'myproj', repo=str(repo.path))
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))  # cwd is outside lycia, so anchor by env
    compare(resolve_project(repo.path), expected=_resolved(project, str(repo.path)))


def test_resolve_project_matches_repo_from_a_linked_worktree(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'external')
    repo.commit_content('seed')
    project = _project(tmpdir, ws, 'myproj', repo=str(repo.path))
    checkout = tmpdir.path / 'review'
    Git(repo.path)('worktree', 'add', '-b', 'review', str(checkout), 'main')  # worktree elsewhere
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    compare(resolve_project(checkout), expected=_resolved(project, str(repo.path)))  # via main repo


def test_resolve_project_raises_when_repo_matches_nothing(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    stranger = Repo.make(tmpdir.path / 'stranger')  # not registered as a project
    stranger.commit_content('seed')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    with ShouldRaise(CannotIdentifyProjectError(stranger.path)):
        resolve_project(stranger.path)


def test_resolve_project_raises_at_the_workspace_root(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)  # walk-up reaches the workspace marker without crossing a project
    with ShouldRaise(CannotIdentifyProjectError(ws)):
        resolve_project(ws)


def test_resolve_project_raises_outside_any_git_repo(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    bare = tmpdir.makedir('bare')  # not a git repo, no markers
    with ShouldRaise(CannotIdentifyProjectError(bare)):
        resolve_project(bare)


def test_iter_projects_lists_only_projects_sorted(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(tmpdir, ws, 'beta')
    _project(tmpdir, ws, 'alpha')
    tmpdir.makedir('lycia/stray')  # no config.yaml — ignored
    compare([p.name for p in iter_projects(ws)], expected=['alpha', 'beta'])


# ---- goal ------------------------------------------------------------------


def _project_with_goal(tmpdir: TempDir) -> tuple[Project, Repo]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project_dir = _project(tmpdir, tmpdir.path, 'proj', repo=str(repo.path))
    add(repo.path, project_dir / 'worktrees', 'g')  # g@agent worktree + g/human branch
    return resolve_project(project_dir), repo


def test_resolve_goal_explicit_wins(tmpdir: TempDir) -> None:
    project, _repo = _project_with_goal(tmpdir)
    compare(resolve_goal(tmpdir.path, project, 'whatever'), expected='whatever')


def test_resolve_goal_infers_from_a_goal_branch(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)
    checkout = tmpdir.path / 'human'
    Git(repo.path)('worktree', 'add', str(checkout), 'g/human')  # on <goal>/<actor>
    compare(resolve_goal(checkout, project), expected='g')


def test_resolve_goal_ignores_a_non_goal_branch(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)  # repo itself is on plain 'main'
    with ShouldRaise(GoalRequiredError(repo.path)):
        resolve_goal(repo.path, project)


def test_resolve_goal_ignores_a_branch_that_is_not_a_real_goal(tmpdir: TempDir) -> None:
    project, repo = _project_with_goal(tmpdir)
    checkout = tmpdir.path / 'review'
    Git(repo.path)('worktree', 'add', '-b', 'ghost/human', str(checkout), 'main')
    with ShouldRaise(GoalRequiredError(checkout)):  # shaped right, but 'ghost' is not a goal
        resolve_goal(checkout, project)


def test_resolve_goal_requires_one_outside_a_repo(tmpdir: TempDir) -> None:
    project, _repo = _project_with_goal(tmpdir)
    bare = tmpdir.makedir('bare')
    with ShouldRaise(GoalRequiredError(bare)):
        resolve_goal(bare, project)


# ---- scope -----------------------------------------------------------------


def _scoped_project_with_goal(tmpdir: TempDir, replace: Replacer) -> tuple[Project, Repo, Path]:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project_dir = _project(tmpdir, ws, 'proj', repo=str(repo.path))
    add(repo.path, project_dir / 'worktrees', 'g')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return resolve_project(project_dir), repo, ws


def test_resolve_scope_widens_to_all_projects_at_the_workspace_root(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(tmpdir, ws, 'a')
    compare(resolve_scope(ws), expected=Scope(ws, None, None))  # couldn't pin → list them all


def test_resolve_scope_pins_the_project_from_within_it(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    project = _project(tmpdir, ws)
    compare(resolve_scope(project), expected=Scope(ws, _resolved(project), None))  # no goal branch


def test_resolve_scope_pins_the_goal_from_a_goal_branch(tmpdir: TempDir, replace: Replacer) -> None:
    project, repo, ws = _scoped_project_with_goal(tmpdir, replace)
    checkout = tmpdir.path / 'human'
    Git(repo.path)('worktree', 'add', str(checkout), 'g/human')
    compare(resolve_scope(checkout), expected=Scope(ws, project, 'g'))


def test_resolve_scope_external_checkout_pins_project_not_goal(
    tmpdir: TempDir, replace: Replacer
) -> None:
    project, repo, ws = _scoped_project_with_goal(tmpdir, replace)
    compare(resolve_scope(repo.path), expected=Scope(ws, project, None))  # repo is on plain 'main'


def test_resolve_scope_bad_explicit_project_still_raises(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    with ShouldRaise(NotInProjectError(ws / 'ghost')):
        resolve_scope(ws, project='ghost')


def test_resolve_scope_without_infer_ignores_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    _, repo, ws = _scoped_project_with_goal(tmpdir, replace)
    checkout = tmpdir.path / 'human'
    Git(repo.path)('worktree', 'add', str(checkout), 'g/human')
    # standing in a goal worktree, but cwd is never read — stays workspace-wide
    compare(resolve_scope(checkout, infer=False), expected=Scope(ws, None, None))


def test_resolve_scope_without_infer_honors_explicit_flags(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    project = _project(tmpdir, ws)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    compare(
        resolve_scope(tmpdir.path, project=project.name, goal='g', infer=False),
        expected=Scope(ws, _resolved(project), 'g'),
    )
    with ShouldRaise(NotInProjectError(ws / 'ghost')):
        resolve_scope(ws, project='ghost', infer=False)
