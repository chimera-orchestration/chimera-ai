import shutil

import pytest
import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.commands.doctor.checks import (
    OrphanedWorktreeCheck,
    ProjectConfigCheck,
    StaleHumanWorktreeCheck,
    WorkspaceConfigCheck,
    WorkspaceEnvCheck,
)
from chimera.commands.doctor.core import Check, Finding
from chimera.worktrees import registered_worktrees


def _ws(tmpdir: TempDir):
    ws = tmpdir.makedir('lycia')
    (ws / '.beads').mkdir()
    return ws


def _repo(tmpdir: TempDir, name: str = 'repo') -> Repo:
    repo = Repo.make(tmpdir.path / name)
    repo.commit_content('seed')
    return repo


def _project(ws, repo_path, *, name='proj', kind=None):
    project = ws / name
    project.mkdir()
    data = {} if kind is None else {'kind': kind}
    data['repo'] = str(repo_path)
    (project / 'config.yaml').write_text(yaml.safe_dump(data, sort_keys=False))
    (project / 'worktrees').mkdir()
    return project


def _human_worktree(repo, project, goal, *, ahead=False, dirty=False):
    worktree = project / 'worktrees' / f'{goal}-human'
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/human', str(worktree), 'main')
    if ahead:
        Repo(worktree).commit_content('work')
    if dirty:
        (worktree / 'scratch.txt').write_text('wip')
    return worktree


def _run(check: Check, ws, fix: bool = False) -> list[Finding]:
    return list(check.run(ws, fix))


def _config(path):
    return yaml.safe_load((path / 'config.yaml').read_text())


# --- WorkspaceConfigCheck ---


def test_workspace_config_missing_reports_without_writing(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    [finding] = _run(WorkspaceConfigCheck(), ws)
    assert finding.fixable and not finding.resolved
    assert not (ws / 'config.yaml').exists()


def test_workspace_config_missing_fixed(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    [finding] = _run(WorkspaceConfigCheck(), ws, fix=True)
    assert finding.resolved
    assert _config(ws) == {'kind': 'workspace'}


def test_workspace_config_legacy_keeps_other_keys(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('name: lycia\n')
    [finding] = _run(WorkspaceConfigCheck(), ws, fix=True)
    assert finding.resolved
    assert _config(ws) == {'kind': 'workspace', 'name': 'lycia'}


def test_workspace_config_already_current_is_silent(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    assert _run(WorkspaceConfigCheck(), ws) == []


def test_workspace_config_wrong_kind_not_fixable(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: project\n')
    [finding] = _run(WorkspaceConfigCheck(), ws, fix=True)
    assert not finding.fixable and not finding.resolved
    assert _config(ws) == {'kind': 'project'}  # left untouched


def test_workspace_config_with_repo_is_not_stamped(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('repo: /some/repo\n')  # a project config, not a root
    [finding] = _run(WorkspaceConfigCheck(), ws, fix=True)
    assert not finding.fixable and not finding.resolved
    assert 'looks like a project' in finding.message
    assert _config(ws) == {'repo': '/some/repo'}  # never gets kind: workspace


# --- WorkspaceEnvCheck ---


def test_workspace_env_unset_reports_export_line(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _ws(tmpdir)
    monkeypatch.delenv('CHIMERA_WORKSPACE', raising=False)
    [finding] = _run(WorkspaceEnvCheck(), ws)
    assert not finding.fixable and not finding.resolved
    assert 'not set' in finding.message
    assert f'export CHIMERA_WORKSPACE="{ws}"' in finding.message


def test_workspace_env_set_to_this_workspace_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _ws(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    assert _run(WorkspaceEnvCheck(), ws) == []


def test_workspace_env_pointing_elsewhere_reported(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _ws(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(tmpdir.makedir('other')))
    [finding] = _run(WorkspaceEnvCheck(), ws)
    assert not finding.fixable and not finding.resolved
    assert 'not this workspace' in finding.message
    assert f'export CHIMERA_WORKSPACE="{ws}"' in finding.message


# --- ProjectConfigCheck ---


def test_project_config_legacy_upgraded(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    [finding] = _run(ProjectConfigCheck(), ws, fix=True)
    assert finding.resolved
    assert _config(project) == {'kind': 'project', 'repo': str(repo.path)}


def test_project_config_already_current_is_silent(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    _project(ws, repo.path, kind='project')
    assert _run(ProjectConfigCheck(), ws) == []


def test_project_config_wrong_kind_with_repo_fixed(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path, kind='workspace')  # repo: proves it's a project
    [finding] = _run(ProjectConfigCheck(), ws, fix=True)
    assert finding.resolved
    assert 'repo: marks it a project' in finding.message
    assert _config(project) == {'kind': 'project', 'repo': str(repo.path)}


def test_project_config_unexpected_kind_without_repo_not_fixable(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    project = ws / 'weird'
    project.mkdir()
    (project / 'config.yaml').write_text('kind: bogus\n')  # no repo to disambiguate
    [finding] = _run(ProjectConfigCheck(), ws, fix=True)
    assert not finding.fixable and not finding.resolved
    assert 'unexpected kind: bogus' in finding.message


def test_project_config_no_kind_no_repo_not_fixable(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    project = ws / 'weird'
    project.mkdir()
    (project / 'config.yaml').write_text('something: else\n')
    [finding] = _run(ProjectConfigCheck(), ws)
    assert not finding.fixable
    assert 'no kind and no repo' in finding.message


# --- StaleHumanWorktreeCheck ---


def test_human_worktree_clean_removed_branch_survives(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = _human_worktree(repo, project, 'g1')
    [finding] = _run(StaleHumanWorktreeCheck(), ws, fix=True)
    assert finding.resolved
    assert not worktree.exists()
    assert 'g1/human' in Git(repo.path).branches()


def test_human_worktree_clean_report_only_leaves_it(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = _human_worktree(repo, project, 'g1')
    [finding] = _run(StaleHumanWorktreeCheck(), ws)
    assert finding.fixable and not finding.resolved
    assert worktree.is_dir()


def test_human_worktree_dirty_left_in_place(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = _human_worktree(repo, project, 'g2', dirty=True)
    [finding] = _run(StaleHumanWorktreeCheck(), ws, fix=True)
    assert not finding.fixable and not finding.resolved
    assert 'uncommitted changes' in finding.message
    assert worktree.is_dir()


def test_human_worktree_unmerged_left_in_place(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = _human_worktree(repo, project, 'g3', ahead=True)
    [finding] = _run(StaleHumanWorktreeCheck(), ws, fix=True)
    assert not finding.fixable
    assert 'unmerged commits' in finding.message
    assert worktree.is_dir()


def test_human_worktree_skips_project_without_a_live_repo(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    project = ws / 'ref'
    project.mkdir()
    (project / 'config.yaml').write_text('kind: project\n')  # no repo
    assert _run(StaleHumanWorktreeCheck(), ws) == []


def test_human_worktree_ignores_unregistered_dir(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    (project / 'worktrees' / 'leftover-human').mkdir()  # looks human, not a worktree
    assert _run(StaleHumanWorktreeCheck(), ws, fix=True) == []


# --- OrphanedWorktreeCheck ---


def test_orphaned_registration_pruned(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = project / 'worktrees' / 'gone@agent'
    Git(repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
    shutil.rmtree(worktree)  # registration is now stale
    findings = _run(OrphanedWorktreeCheck(), ws, fix=True)
    assert [f.resolved for f in findings] == [True]
    assert worktree.resolve() not in registered_worktrees(Git(repo.path))


def test_orphaned_registration_report_only_keeps_it(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = project / 'worktrees' / 'gone@agent'
    Git(repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
    shutil.rmtree(worktree)
    [finding] = _run(OrphanedWorktreeCheck(), ws)
    assert finding.fixable and not finding.resolved
    assert worktree.resolve() in registered_worktrees(Git(repo.path))  # not pruned


def test_orphaned_leftover_dir_reported(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    (project / 'worktrees' / 'random').mkdir()  # never a git worktree
    [finding] = _run(OrphanedWorktreeCheck(), ws)
    assert not finding.fixable
    assert 'not a registered worktree' in finding.message


def test_orphaned_none_when_clean(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    _project(ws, repo.path)
    assert _run(OrphanedWorktreeCheck(), ws) == []


def test_orphaned_skips_project_without_a_live_repo(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    project = ws / 'ref'
    project.mkdir()
    (project / 'config.yaml').write_text('kind: project\n')
    assert _run(OrphanedWorktreeCheck(), ws) == []
