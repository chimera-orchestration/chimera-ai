from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.commands.msg.store import caller


def test_caller_uses_the_chimera_session_stamp(replace: Replacer) -> None:
    replace.in_environ('CHIMERA_SESSION', 'chimera@fix@agent')
    assert caller(Path('/wherever')) == 'chimera@fix@agent'


def test_caller_at_the_bare_workspace_is_the_captain(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    assert caller(tmpdir.path / 'ws') == 'pegasus'


def test_caller_in_a_project_dir_is_the_manager(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    assert caller(tmpdir.path / 'ws' / 'proj') == 'proj@manager'


def test_caller_in_a_goal_worktree_is_the_agent(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    assert caller(worktree) == 'proj@g@agent'
