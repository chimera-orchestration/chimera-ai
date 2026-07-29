from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.agents import AgentSession
from chimera.agents.claude import Claude
from chimera.commands.agent import live
from chimera.commands.project.new import new
from chimera.commands.project.rm import remove
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.dry import Dry
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    # at the adapter, so every consumer is covered: the module-level live() the sweep
    # calls *and* the harness.live() stop() reaches for directly, which would otherwise
    # shell out to the real `claude agents --json`
    replace.on_class(Claude.reported, lambda self, cwd=None: [])
    replace.in_module(live, lambda worktree: [])
    replace.in_module(live, lambda worktree: [], module=worktree_rm)


def _project(tmpdir: TempDir, repo: Repo, *, with_goal: bool = False) -> tuple[Path, Path]:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    project = workspace / 'myproj'
    tmpdir.dump('lycia/myproj/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    if with_goal:
        add(repo.path, project / 'worktrees', goal='g')
    return workspace, project


def test_remove_is_a_noop_when_the_project_is_absent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    assert remove(workspace, 'ghost') is None


def test_remove_refuses_a_dir_that_is_not_a_tracked_project(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    stray = workspace / 'stray'
    stray.mkdir()
    with ShouldRaise(RuntimeError(f'{stray} is not a tracked project (no config.yaml)')):
        remove(workspace, 'stray')
    assert stray.is_dir()


def test_remove_takes_out_a_project_with_no_goals(tmpdir: TempDir, git_repo: Repo) -> None:
    workspace, project = _project(tmpdir, git_repo)
    compare(remove(workspace, 'myproj'), expected=project)
    assert not project.exists()
    # the external tracked repo is left untouched — and, though it has commits and no
    # remote, being outside the project dir means the sole-copy guard never applies
    assert git_repo.path.is_dir()


def test_remove_refuses_while_goals_exist(tmpdir: TempDir, git_repo: Repo) -> None:
    workspace, _ = _project(tmpdir, git_repo, with_goal=True)
    with ShouldRaise(
        RuntimeError('myproj still has goals (g); run `ch goal finish` on each or use --force')
    ):
        remove(workspace, 'myproj')
    tmpdir.compare(['g@agent'], path='lycia/myproj/worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


def test_remove_force_finishes_goals_then_removes_the_project(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    workspace, project = _project(tmpdir, git_repo, with_goal=True)
    Repo(project / 'worktrees' / 'g@agent').commit_content('work')  # unmerged
    (project / 'worktrees' / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    compare(remove(workspace, 'myproj', force=True), expected=project)
    assert not project.exists()
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_remove_force_aborts_when_an_agent_is_running(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    workspace, project = _project(tmpdir, git_repo, with_goal=True)
    replace.in_module(
        live,
        lambda worktree: (
            [AgentSession('x', 'x', 'idle', worktree, None)] if worktree.name == 'g@agent' else []
        ),
        module=worktree_rm,
    )
    with ShouldRaise(
        UserError(
            f'an agent is live in {project / "worktrees" / "g@agent"}: pid ?  idle\n'
            'find its terminal or kill the pid, then re-run'
        )
    ):
        remove(workspace, 'myproj', force=True)  # not even force nukes a live agent
    tmpdir.compare(['g@agent'], path='lycia/myproj/worktrees', recursive=False)


def test_remove_aborts_when_a_project_chat_is_live(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    workspace, project = _project(tmpdir, git_repo)  # no goals: the dir is still swept
    replace.in_module(
        live,
        lambda worktree: [AgentSession('x', 'myproj@manager', 'idle', worktree, None)],
        module=worktree_rm,
    )
    message = (
        f'an agent is live in {project}: pid ?  idle  myproj@manager\n'
        'find its terminal or kill the pid, then re-run'
    )
    with ShouldRaise(UserError(message)):
        remove(workspace, 'myproj')
    with ShouldRaise(UserError(message)):  # a live chat blocks force too
        remove(workspace, 'myproj', force=True)
    assert project.is_dir()


def test_remove_dry_previews_the_whole_teardown(tmpdir: TempDir, git_repo: Repo) -> None:
    workspace, project = _project(tmpdir, git_repo, with_goal=True)
    Repo(project / 'worktrees' / 'g@agent').commit_content('work')  # unmerged, would need force
    compare(remove(workspace, 'myproj', force=True, dry=Dry(on=True)), expected=project)
    assert project.is_dir()  # nothing removed
    tmpdir.compare(['g@agent'], path='lycia/myproj/worktrees', recursive=False)  # goal intact
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])


@pytest.fixture()
def git_identity(replace: Replacer) -> None:
    for name in 'GIT_AUTHOR_NAME', 'GIT_COMMITTER_NAME':
        replace.in_environ(name, 'Test')
    for email in 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_EMAIL':
        replace.in_environ(email, 'test@example.com')


def _workspace_only_project(tmpdir: TempDir) -> tuple[Path, Path]:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    return workspace, new(workspace, 'myproj')


def _push_real_work(tmpdir: TempDir, repo: Path) -> None:
    clone = Repo.clone(repo, tmpdir / 'clone')
    clone.commit_content('work')
    clone('push', 'origin', 'main')


def _sole_copy_refusal(name: str) -> RuntimeError:
    return RuntimeError(
        f'{name} holds the only copy of its work (no remote to recover from); '
        f'publish it first (ch project push) or use --force to discard it'
    )


def test_remove_refuses_the_sole_copy_of_real_work(tmpdir: TempDir, git_identity: None) -> None:
    workspace, project = _workspace_only_project(tmpdir)
    _push_real_work(tmpdir, project / 'repo')
    with ShouldRaise(_sole_copy_refusal('myproj')):
        remove(workspace, 'myproj')
    assert (project / 'repo').is_dir()  # project left intact


def test_remove_force_discards_the_sole_copy(tmpdir: TempDir, git_identity: None) -> None:
    workspace, project = _workspace_only_project(tmpdir)
    _push_real_work(tmpdir, project / 'repo')
    compare(remove(workspace, 'myproj', force=True), expected=project)
    assert not project.exists()


def test_remove_takes_out_a_fresh_workspace_only_project(
    tmpdir: TempDir, git_identity: None
) -> None:
    workspace, project = _workspace_only_project(tmpdir)  # only the empty seed commit
    compare(remove(workspace, 'myproj'), expected=project)
    assert not project.exists()


def test_remove_takes_out_an_internal_repo_with_no_commits(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    Git(tmpdir.makedir('lycia/myproj/repo'))('init', '--bare')
    tmpdir.dump(
        'lycia/myproj/config.yaml',
        {'kind': 'project', 'repo': str(workspace / 'myproj' / 'repo')},
    )
    compare(remove(workspace, 'myproj'), expected=workspace / 'myproj')
    assert not (workspace / 'myproj').exists()


def test_remove_takes_out_a_workspace_only_repo_with_a_remote(
    tmpdir: TempDir, git_identity: None
) -> None:
    workspace, project = _workspace_only_project(tmpdir)
    _push_real_work(tmpdir, project / 'repo')
    Git(project / 'repo')('remote', 'add', 'origin', str(tmpdir / 'elsewhere'))
    compare(remove(workspace, 'myproj'), expected=project)  # recoverable elsewhere
    assert not project.exists()


def test_remove_dry_still_reports_the_sole_copy_refusal(
    tmpdir: TempDir, git_identity: None
) -> None:
    workspace, project = _workspace_only_project(tmpdir)
    _push_real_work(tmpdir, project / 'repo')
    with ShouldRaise(_sole_copy_refusal('myproj')):
        remove(workspace, 'myproj', dry=Dry(on=True))
    assert (project / 'repo').is_dir()


def test_remove_dry_force_previews_discarding_the_sole_copy(
    tmpdir: TempDir, git_identity: None
) -> None:
    workspace, project = _workspace_only_project(tmpdir)
    _push_real_work(tmpdir, project / 'repo')
    compare(remove(workspace, 'myproj', force=True, dry=Dry(on=True)), expected=project)
    assert project.is_dir()  # nothing removed


def test_project_rm_cli(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    workspace, project = _project(tmpdir, git_repo)
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'myproj').check(
        output=f'Removed {project}',
        logging=action_logs(
            'project rm',
            'chimera.commands.project.rm.remove',
            {'name': 'myproj', 'force': False, 'dry': False},
        ),
    )
    assert not project.exists()


def test_project_rm_cli_dry_previews(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    workspace, project = _project(tmpdir, git_repo)
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'myproj', '--dry').check(
        output=f'Would remove {project}',
        logging=action_logs(
            'project rm',
            'chimera.commands.project.rm.remove',
            {'name': 'myproj', 'force': False, 'dry': True},
        ),
    )
    assert project.is_dir()  # untouched


def test_project_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'rm', 'ghost').check(
        output='No project named ghost to remove',
        logging=action_logs(
            'project rm',
            'chimera.commands.project.rm.remove',
            {'name': 'ghost', 'force': False, 'dry': False},
        ),
    )
