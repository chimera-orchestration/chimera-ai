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


def _project(tmpdir: TempDir, parent: Path, name: str = 'proj', repo: str = '/r') -> Path:
    project = parent / name
    tmpdir.dump(project / 'config.yaml', {'kind': 'project', 'repo': repo})
    return project


def _resolved(project_dir: Path, repo: str = '/r') -> Project:
    return Project(project_dir, ProjectConfig(kind='project', repo=Path(repo)))


def _project_with_goal(tmpdir: TempDir, parent: Path, repo: Repo) -> Project:
    project_dir = _project(tmpdir, parent, 'proj', repo=str(repo.path))
    add(repo.path, project_dir / 'worktrees', 'g')  # g@agent worktree + g/human branch
    return resolve_project(project_dir)


class TestResolveWorkspace:
    def test_prefers_env(self, tmpdir: TempDir, workspace_with_env: Path) -> None:
        # env wins over cwd
        compare(resolve_workspace(tmpdir / 'somewhere-else'), expected=workspace_with_env)

    def test_env_must_point_at_a_workspace(self, tmpdir: TempDir, replace: Replacer) -> None:
        nope = tmpdir.makedir('not-a-workspace')
        replace.in_environ('CHIMERA_WORKSPACE', str(nope))
        with ShouldRaise(NotInWorkspaceError(nope)):
            resolve_workspace(tmpdir.path)

    def test_falls_back_to_walk_up(self, tmpdir: TempDir, workspace: Path) -> None:
        # env unset (cleared by conftest)
        compare(resolve_workspace(_project(tmpdir, workspace)), expected=workspace)

    def test_raises_when_unfound(self, tmpdir: TempDir) -> None:
        with ShouldRaise(NotInWorkspaceError(tmpdir.path)):
            resolve_workspace(tmpdir.path)


class TestResolveProject:
    def test_infers_from_cwd(self, tmpdir: TempDir, workspace: Path) -> None:
        project = _project(tmpdir, workspace)
        compare(resolve_project(project), expected=_resolved(project))

    def test_by_name_overrides_cwd(self, tmpdir: TempDir, workspace: Path) -> None:
        foo = _project(tmpdir, workspace, 'foo')
        bar = _project(tmpdir, workspace, 'bar')
        compare(resolve_project(bar, 'foo'), expected=_resolved(foo))  # in bar, asked for foo

    def test_by_name_raises_when_absent(self, workspace: Path) -> None:
        with ShouldRaise(NotInProjectError(workspace / 'ghost')):
            resolve_project(workspace, 'ghost')

    def test_matches_repo_from_external_checkout(
        self, tmpdir: TempDir, workspace_with_env: Path
    ) -> None:
        repo = Repo.make(tmpdir / 'external')  # lives outside the workspace
        repo.commit_content('seed')
        project = _project(tmpdir, workspace_with_env, 'myproj', repo=str(repo.path))
        # cwd is outside lycia, so anchor by env
        compare(resolve_project(repo.path), expected=_resolved(project, str(repo.path)))

    def test_matches_repo_from_a_linked_worktree(
        self, tmpdir: TempDir, workspace_with_env: Path
    ) -> None:
        repo = Repo.make(tmpdir / 'external')
        repo.commit_content('seed')
        project = _project(tmpdir, workspace_with_env, 'myproj', repo=str(repo.path))
        checkout = tmpdir / 'review'
        Git(repo.path)('worktree', 'add', '-b', 'review', str(checkout), 'main')  # elsewhere
        compare(resolve_project(checkout), expected=_resolved(project, str(repo.path)))  # via repo

    def test_raises_when_repo_matches_nothing(
        self, tmpdir: TempDir, workspace_with_env: Path
    ) -> None:
        stranger = Repo.make(tmpdir / 'stranger')  # not registered as a project
        stranger.commit_content('seed')
        with ShouldRaise(CannotIdentifyProjectError(stranger.path)):
            resolve_project(stranger.path)

    def test_raises_at_the_workspace_root(self, workspace: Path) -> None:
        # walk-up reaches the workspace marker without crossing a project
        with ShouldRaise(CannotIdentifyProjectError(workspace)):
            resolve_project(workspace)

    def test_raises_outside_any_git_repo(self, tmpdir: TempDir, workspace_with_env: Path) -> None:
        bare = tmpdir.makedir('bare')  # not a git repo, no markers
        with ShouldRaise(CannotIdentifyProjectError(bare)):
            resolve_project(bare)


class TestIterProjects:
    def test_lists_only_projects_sorted(self, tmpdir: TempDir, workspace: Path) -> None:
        _project(tmpdir, workspace, 'beta')
        _project(tmpdir, workspace, 'alpha')
        tmpdir.makedir('lycia/stray')  # no config.yaml — ignored
        compare([p.name for p in iter_projects(workspace)], expected=['alpha', 'beta'])


class TestResolveGoal:
    def test_explicit_wins(self, tmpdir: TempDir, git_repo: Repo) -> None:
        project = _project_with_goal(tmpdir, tmpdir.path, git_repo)
        compare(resolve_goal(tmpdir.path, project, 'whatever'), expected='whatever')

    def test_infers_from_a_goal_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        project = _project_with_goal(tmpdir, tmpdir.path, git_repo)
        checkout = tmpdir / 'human'
        Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')  # on <goal>/<actor>
        compare(resolve_goal(checkout, project), expected='g')

    def test_ignores_a_non_goal_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        project = _project_with_goal(
            tmpdir, tmpdir.path, git_repo
        )  # repo itself is on plain 'main'
        with ShouldRaise(GoalRequiredError(git_repo.path)):
            resolve_goal(git_repo.path, project)

    def test_ignores_a_branch_that_is_not_a_real_goal(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        project = _project_with_goal(tmpdir, tmpdir.path, git_repo)
        checkout = tmpdir / 'review'
        Git(git_repo.path)('worktree', 'add', '-b', 'ghost/human', str(checkout), 'main')
        with ShouldRaise(GoalRequiredError(checkout)):  # shaped right, but 'ghost' is not a goal
            resolve_goal(checkout, project)

    def test_requires_one_outside_a_repo(self, tmpdir: TempDir, git_repo: Repo) -> None:
        project = _project_with_goal(tmpdir, tmpdir.path, git_repo)
        bare = tmpdir.makedir('bare')
        with ShouldRaise(GoalRequiredError(bare)):
            resolve_goal(bare, project)


class TestResolveScope:
    def test_widens_to_all_projects_at_the_workspace_root(
        self, tmpdir: TempDir, workspace: Path
    ) -> None:
        _project(tmpdir, workspace, 'a')
        # couldn't pin → list them all
        compare(resolve_scope(workspace), expected=Scope(workspace, None, None))

    def test_pins_the_project_from_within_it(self, tmpdir: TempDir, workspace: Path) -> None:
        project = _project(tmpdir, workspace)
        # no goal branch
        compare(resolve_scope(project), expected=Scope(workspace, _resolved(project), None))

    def test_pins_the_goal_from_a_goal_branch(
        self, tmpdir: TempDir, git_repo: Repo, workspace_with_env: Path
    ) -> None:
        project = _project_with_goal(tmpdir, workspace_with_env, git_repo)
        checkout = tmpdir / 'human'
        Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
        compare(resolve_scope(checkout), expected=Scope(workspace_with_env, project, 'g'))

    def test_external_checkout_pins_project_not_goal(
        self, tmpdir: TempDir, git_repo: Repo, workspace_with_env: Path
    ) -> None:
        project = _project_with_goal(tmpdir, workspace_with_env, git_repo)
        # repo is on plain 'main'
        compare(resolve_scope(git_repo.path), expected=Scope(workspace_with_env, project, None))

    def test_bad_explicit_project_still_raises(self, workspace_with_env: Path) -> None:
        with ShouldRaise(NotInProjectError(workspace_with_env / 'ghost')):
            resolve_scope(workspace_with_env, project='ghost')

    def test_without_infer_ignores_cwd(
        self, tmpdir: TempDir, git_repo: Repo, workspace_with_env: Path
    ) -> None:
        _project_with_goal(tmpdir, workspace_with_env, git_repo)
        checkout = tmpdir / 'human'
        Git(git_repo.path)('worktree', 'add', str(checkout), 'g/human')
        # standing in a goal worktree, but cwd is never read — stays workspace-wide
        compare(
            resolve_scope(checkout, infer=False), expected=Scope(workspace_with_env, None, None)
        )

    def test_without_infer_honors_explicit_flags(
        self, tmpdir: TempDir, workspace_with_env: Path
    ) -> None:
        project = _project(tmpdir, workspace_with_env)
        compare(
            resolve_scope(tmpdir.path, project=project.name, goal='g', infer=False),
            expected=Scope(workspace_with_env, _resolved(project), 'g'),
        )
        with ShouldRaise(NotInProjectError(workspace_with_env / 'ghost')):
            resolve_scope(workspace_with_env, project='ghost', infer=False)
