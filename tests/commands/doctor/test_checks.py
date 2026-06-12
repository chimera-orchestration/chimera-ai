import shutil

import pytest
import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.commands.doctor.checks import (
    LegacyWorktreeSeparatorCheck,
    OrphanedWorktreeCheck,
    ProjectConfigCheck,
    ShellCompletionCheck,
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


# --- LegacyWorktreeSeparatorCheck ---


def _legacy_worktree(repo, project, goal, actor='agent', *, dirty=False):
    worktree = project / 'worktrees' / f'{goal}-{actor}'  # old dash-joined dir name
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/{actor}', str(worktree), 'main')
    if dirty:
        (worktree / 'scratch.txt').write_text('wip')
    return worktree


def test_legacy_separator_migrated(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    _legacy_worktree(repo, project, 'my-goal')
    [finding] = _run(LegacyWorktreeSeparatorCheck(), ws, fix=True)
    assert finding.resolved
    canonical = (project / 'worktrees' / 'my-goal@agent').resolve()
    assert canonical.is_dir()
    assert not (project / 'worktrees' / 'my-goal-agent').exists()
    assert canonical in registered_worktrees(Git(repo.path))


def test_legacy_separator_report_only_leaves_it(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    _legacy_worktree(repo, project, 'my-goal')
    [finding] = _run(LegacyWorktreeSeparatorCheck(), ws)
    assert finding.fixable and not finding.resolved
    assert (project / 'worktrees' / 'my-goal-agent').is_dir()


def test_legacy_separator_preserves_uncommitted_work(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    _legacy_worktree(repo, project, 'my-goal', dirty=True)
    _run(LegacyWorktreeSeparatorCheck(), ws, fix=True)
    assert (project / 'worktrees' / 'my-goal@agent' / 'scratch.txt').read_text() == 'wip'


def test_legacy_separator_migrates_non_human_actors(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    _legacy_worktree(repo, project, 'g', actor='reviewer')
    [finding] = _run(LegacyWorktreeSeparatorCheck(), ws, fix=True)
    assert finding.resolved
    assert (project / 'worktrees' / 'g@reviewer').is_dir()


def test_legacy_separator_ignores_human_worktrees(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    _human_worktree(repo, project, 'g')  # the human-worktrees check owns these
    assert _run(LegacyWorktreeSeparatorCheck(), ws) == []


def test_legacy_separator_skips_already_canonical(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = project / 'worktrees' / 'g@agent'
    Git(repo.path)('worktree', 'add', '-b', 'g/agent', str(worktree), 'main')
    assert _run(LegacyWorktreeSeparatorCheck(), ws, fix=True) == []


def test_legacy_separator_skips_project_without_a_live_repo(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    project = _project(ws, ws / 'proj' / 'repo')  # repo path doesn't exist
    (project / 'worktrees' / 'g-agent').mkdir()
    assert _run(LegacyWorktreeSeparatorCheck(), ws) == []


def test_legacy_separator_ignores_non_goal_actor_branch(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = project / 'worktrees' / 'sidequest'
    Git(repo.path)('worktree', 'add', '-b', 'sidequest', str(worktree), 'main')  # no <goal>/<actor>
    assert _run(LegacyWorktreeSeparatorCheck(), ws, fix=True) == []


def test_legacy_separator_skips_stale_registration(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    repo = _repo(tmpdir)
    project = _project(ws, repo.path)
    worktree = _legacy_worktree(repo, project, 'g')
    shutil.rmtree(worktree)  # registered but the dir is gone — can't read its branch
    assert _run(LegacyWorktreeSeparatorCheck(), ws) == []


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


# --- ShellCompletionCheck ---


def _shell_home(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, shell: str):
    home = tmpdir.makedir('home')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('SHELL', f'/bin/{shell}')
    return home


def test_completion_unknown_shell_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shell_home(tmpdir, monkeypatch, 'fish')
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_no_shell_is_silent(tmpdir: TempDir) -> None:
    # conftest clears $SHELL — nothing to verify
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_zsh_installed_script_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _shell_home(tmpdir, monkeypatch, 'zsh')
    (home / '.zfunc').mkdir()
    (home / '.zfunc' / '_ch').write_text('#compdef ch')
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_zsh_eval_line_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _shell_home(tmpdir, monkeypatch, 'zsh')
    (home / '.zshrc').write_text('eval "$(env _CH_COMPLETE=source_zsh ch)"\n')
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_zsh_missing_reported(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _shell_home(tmpdir, monkeypatch, 'zsh')
    (home / '.zshrc').write_text('# no completion here\n')
    [finding] = _run(ShellCompletionCheck(), _ws(tmpdir))
    assert not finding.fixable and not finding.resolved
    assert 'ch --install-completion' in finding.message
    assert '~/.zshrc' in finding.message
    assert 'eval "$(env _CH_COMPLETE=source_zsh ch)"' in finding.message


def test_completion_bash_installed_script_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _shell_home(tmpdir, monkeypatch, 'bash')
    (home / '.bash_completions').mkdir()
    (home / '.bash_completions' / 'ch.sh').write_text('complete')
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_bash_eval_line_in_profile_is_silent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _shell_home(tmpdir, monkeypatch, 'bash')
    (home / '.bash_profile').write_text('eval "$(env _CH_COMPLETE=source_bash ch)"\n')
    assert _run(ShellCompletionCheck(), _ws(tmpdir)) == []


def test_completion_bash_missing_reported(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    _shell_home(tmpdir, monkeypatch, 'bash')
    [finding] = _run(ShellCompletionCheck(), _ws(tmpdir))
    assert not finding.fixable and not finding.resolved
    assert '~/.bashrc' in finding.message
    assert 'eval "$(env _CH_COMPLETE=source_bash ch)"' in finding.message
