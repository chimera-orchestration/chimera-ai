from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, Replacer, TempDir

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
    with pytest.raises(RuntimeError, match='not a tracked project'):
        remove(workspace, 'stray')
    assert stray.is_dir()


def test_remove_takes_out_a_project_with_no_goals(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir)
    assert remove(workspace, 'myproj') == project
    assert not project.exists()
    assert repo.path.is_dir()  # the external tracked repo is left untouched


def test_remove_refuses_while_goals_exist(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    with pytest.raises(RuntimeError, match='still has goals'):
        remove(workspace, 'myproj')
    assert (project / 'worktrees' / 'g@agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_remove_force_finishes_goals_then_removes_the_project(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    Repo(project / 'worktrees' / 'g@agent').commit_content('work')  # unmerged
    (project / 'worktrees' / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    assert remove(workspace, 'myproj', force=True) == project
    assert not project.exists()
    branches = Git(repo.path).branches()
    assert 'g/agent' not in branches
    assert 'g/human' not in branches


def test_remove_force_aborts_when_an_agent_is_running(tmpdir: TempDir, replace: Replacer) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    replace.in_module(
        live_sessions,
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
        module=worktree_rm,
    )
    with pytest.raises(RuntimeError, match='agent is live'):
        remove(workspace, 'myproj', force=True)  # not even force nukes a live agent
    assert (project / 'worktrees' / 'g@agent').is_dir()


def test_project_rm_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace, repo, project = _project(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'myproj').check(
        output=f'Removed {project}', logging=[('INFO', 'project rm')]
    )
    assert not project.exists()


def test_project_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    workspace = tmpdir.makedir('lycia')
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'ghost').check(
        output='No project named ghost to remove', logging=[('INFO', 'project rm')]
    )
