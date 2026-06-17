from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, Replacer, ShouldRaise, TempDir, compare

from chimera.commands.agent import live_sessions
from chimera.commands.project.rm import remove
from chimera.commands.worktree.add import add
from chimera.commands.worktree import rm as worktree_rm


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    replace.in_module(live_sessions, lambda worktree: [], module=worktree_rm)


def _project(tmpdir: TempDir, *, with_goal: bool = False) -> tuple[Path, Repo, Path]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    project = workspace / 'myproj'
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    if with_goal:
        add(repo.path, project / 'worktrees', 'g')
    return workspace, repo, project


def test_remove_is_a_noop_when_the_project_is_absent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    assert remove(workspace, 'ghost') is None


def test_remove_refuses_a_dir_that_is_not_a_tracked_project(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    stray = workspace / 'stray'
    stray.mkdir()
    with ShouldRaise(RuntimeError(f'{stray} is not a tracked project (no config.yaml)')):
        remove(workspace, 'stray')
    assert stray.is_dir() is True


def test_remove_takes_out_a_project_with_no_goals(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir)
    compare(remove(workspace, 'myproj'), expected=project)
    assert project.exists() is False
    assert repo.path.is_dir() is True  # the external tracked repo is left untouched


def test_remove_refuses_while_goals_exist(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    with ShouldRaise(
        RuntimeError('myproj still has goals (g); run `ch goal finish` on each or use --force')
    ):
        remove(workspace, 'myproj')
    assert (project / 'worktrees' / 'g@agent').is_dir() is True
    compare(Git(repo.path).branches(), expected=['g/agent', 'g/human', 'main'])


def test_remove_force_finishes_goals_then_removes_the_project(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    Repo(project / 'worktrees' / 'g@agent').commit_content('work')  # unmerged
    (project / 'worktrees' / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    compare(remove(workspace, 'myproj', force=True), expected=project)
    assert project.exists() is False
    compare(Git(repo.path).branches(), expected=['main'])


def test_remove_force_aborts_when_an_agent_is_running(tmpdir: TempDir, replace: Replacer) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    replace.in_module(
        live_sessions,
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
        module=worktree_rm,
    )
    with ShouldRaise(
        RuntimeError(
            f'an agent is live in {project / "worktrees" / "g@agent"}:\n'
            '  pid ?  idle\n'
            'find its terminal or kill the pid, then re-run'
        )
    ):
        remove(workspace, 'myproj', force=True)  # not even force nukes a live agent
    assert (project / 'worktrees' / 'g@agent').is_dir() is True


def test_project_rm_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace, repo, project = _project(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'myproj').check(
        output=f'Removed {project}', logging=[('INFO', 'project rm')]
    )
    assert project.exists() is False


def test_project_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    workspace = tmpdir.makedir('lycia')
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'ghost').check(
        output='No project named ghost to remove', logging=[('INFO', 'project rm')]
    )
