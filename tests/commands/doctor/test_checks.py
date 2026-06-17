import shutil

import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, TempDir, compare, not_there

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
    (ws / 'processes').mkdir()
    return ws


def _repo(tmpdir: TempDir, name: str = 'repo') -> Repo:
    repo = Repo.make(tmpdir / name)
    repo.commit_content('seed')
    return repo


def _project(tmpdir, ws, repo_path, *, name='proj', kind=None):
    project = ws / name
    data = {} if kind is None else {'kind': kind}
    data['repo'] = str(repo_path)
    tmpdir.dump(project / 'config.yaml', data)
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


def _env_finding(ws, where: str) -> Finding:
    """The workspace-env finding text — where: the '… is not set'/'… not this workspace' clause."""
    return Finding(
        'workspace-env',
        f'$CHIMERA_WORKSPACE {where} — '
        'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):\n'
        f'    export CHIMERA_WORKSPACE="{ws}"',
        False,
        False,
    )


class TestWorkspaceConfig:
    def test_missing_reports_without_writing(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws),
            expected=[Finding('workspace-config', f'{ws}/config.yaml missing', False, True)],
        )
        assert (ws / 'config.yaml').exists() is False  # report only, nothing written

    def test_missing_fixed(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[Finding('workspace-config', f'{ws}/config.yaml missing', True, True)],
        )
        compare(_config(ws), expected={'kind': 'workspace'})

    def test_legacy_keeps_other_keys(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'name': 'lycia'})
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding('workspace-config', f'{ws}/config.yaml missing kind: workspace', True, True)
            ],
        )
        compare(_config(ws), expected={'kind': 'workspace', 'name': 'lycia'})

    def test_already_current_is_silent(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
        compare(_run(WorkspaceConfigCheck(), ws), expected=[])

    def test_wrong_kind_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'kind': 'project'})
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config',
                    f'{ws}/config.yaml has kind: project at the workspace root',
                    False,
                    False,
                )
            ],
        )
        compare(_config(ws), expected={'kind': 'project'})  # left untouched

    def test_with_repo_is_not_stamped(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'repo': '/some/repo'})  # a project config, not a root
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config',
                    f'{ws}/config.yaml looks like a project (has repo:), not a workspace root',
                    False,
                    False,
                )
            ],
        )
        compare(_config(ws), expected={'repo': '/some/repo'})  # never gets kind: workspace


class TestWorkspaceEnv:
    def test_unset_reports_export_line(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        replace.in_environ('CHIMERA_WORKSPACE', not_there)
        compare(_run(WorkspaceEnvCheck(), ws), expected=[_env_finding(ws, 'is not set')])

    def test_set_to_this_workspace_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        compare(_run(WorkspaceEnvCheck(), ws), expected=[])

    def test_pointing_elsewhere_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        other = tmpdir.makedir('other')
        replace.in_environ('CHIMERA_WORKSPACE', str(other))
        compare(
            _run(WorkspaceEnvCheck(), ws),
            expected=[_env_finding(ws, f'is {other}, not this workspace')],
        )


class TestProjectConfig:
    def test_legacy_upgraded(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config', f'{project}/config.yaml missing kind: project', True, True
                )
            ],
        )
        compare(_config(project), expected={'kind': 'project', 'repo': str(repo.path)})

    def test_already_current_is_silent(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        _project(tmpdir, ws, repo.path, kind='project')
        compare(_run(ProjectConfigCheck(), ws), expected=[])

    def test_wrong_kind_with_repo_fixed(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path, kind='workspace')  # repo: proves it's a project
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config',
                    f'{project}/config.yaml has kind: workspace but repo: marks it a project',
                    True,
                    True,
                )
            ],
        )
        compare(_config(project), expected={'kind': 'project', 'repo': str(repo.path)})

    def test_unexpected_kind_without_repo_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = ws / 'weird'
        tmpdir.dump('lycia/weird/config.yaml', {'kind': 'bogus'})  # no repo to disambiguate
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config',
                    f'{project}/config.yaml has unexpected kind: bogus',
                    False,
                    False,
                )
            ],
        )

    def test_no_kind_no_repo_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = ws / 'weird'
        tmpdir.dump('lycia/weird/config.yaml', {'something': 'else'})
        compare(
            _run(ProjectConfigCheck(), ws),
            expected=[
                Finding(
                    'project-config', f'{project}/config.yaml has no kind and no repo', False, False
                )
            ],
        )


class TestStaleHumanWorktree:
    def test_clean_removed_branch_survives(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = _human_worktree(repo, project, 'g1')
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[Finding('human-worktrees', f'stale human worktree {worktree}', True, True)],
        )
        tmpdir.compare(path='lycia/proj/worktrees', expected=())
        compare(Git(repo.path).branches(), expected=['g1/human', 'main'])

    def test_clean_report_only_leaves_it(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = _human_worktree(repo, project, 'g1')
        compare(
            _run(StaleHumanWorktreeCheck(), ws),
            expected=[Finding('human-worktrees', f'stale human worktree {worktree}', False, True)],
        )
        tmpdir.compare(['g1-human'], path='lycia/proj/worktrees', recursive=False)

    def test_dirty_left_in_place(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = _human_worktree(repo, project, 'g2', dirty=True)
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'human-worktrees',
                    f'{worktree} has uncommitted changes — left in place',
                    False,
                    False,
                )
            ],
        )
        tmpdir.compare(['g2-human'], path='lycia/proj/worktrees', recursive=False)

    def test_unmerged_left_in_place(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = _human_worktree(repo, project, 'g3', ahead=True)
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'human-worktrees',
                    f'{worktree} has unmerged commits — left in place',
                    False,
                    False,
                )
            ],
        )
        tmpdir.compare(['g3-human'], path='lycia/proj/worktrees', recursive=False)

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/ref/config.yaml', {'kind': 'project'})  # no repo
        compare(_run(StaleHumanWorktreeCheck(), ws), expected=[])

    def test_ignores_unregistered_dir(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        (project / 'worktrees' / 'leftover-human').mkdir()  # looks human, not a worktree
        compare(_run(StaleHumanWorktreeCheck(), ws, fix=True), expected=[])


def _legacy_worktree(repo, project, goal, actor='agent', *, dirty=False):
    worktree = project / 'worktrees' / f'{goal}-{actor}'  # old dash-joined dir name
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/{actor}', str(worktree), 'main')
    if dirty:
        (worktree / 'scratch.txt').write_text('wip')
    return worktree


class TestLegacyWorktreeSeparator:
    def test_migrated(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        _legacy_worktree(repo, project, 'my-goal')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-separator',
                    'legacy worktree my-goal-agent → my-goal@agent',
                    True,
                    True,
                )
            ],
        )
        canonical = (project / 'worktrees' / 'my-goal@agent').resolve()
        tmpdir.compare(['my-goal@agent'], path='lycia/proj/worktrees', recursive=False)  # renamed
        compare(registered_worktrees(Git(repo.path)), expected={repo.path.resolve(), canonical})

    def test_report_only_leaves_it(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        _legacy_worktree(repo, project, 'my-goal')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws),
            expected=[
                Finding(
                    'worktree-separator',
                    'legacy worktree my-goal-agent → my-goal@agent',
                    False,
                    True,
                )
            ],
        )
        tmpdir.compare(
            ['my-goal-agent'], path='lycia/proj/worktrees', recursive=False
        )  # left as-is

    def test_preserves_uncommitted_work(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        _legacy_worktree(repo, project, 'my-goal', dirty=True)
        _run(LegacyWorktreeSeparatorCheck(), ws, fix=True)
        compare(
            (project / 'worktrees' / 'my-goal@agent' / 'scratch.txt').read_text(), expected='wip'
        )

    def test_migrates_non_human_actors(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        _legacy_worktree(repo, project, 'g', actor='reviewer')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws, fix=True),
            expected=[
                Finding('worktree-separator', 'legacy worktree g-reviewer → g@reviewer', True, True)
            ],
        )
        tmpdir.compare(['g@reviewer'], path='lycia/proj/worktrees', recursive=False)

    def test_ignores_human_worktrees(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        _human_worktree(repo, project, 'g')  # the human-worktrees check owns these
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])

    def test_skips_already_canonical(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = project / 'worktrees' / 'g@agent'
        Git(repo.path)('worktree', 'add', '-b', 'g/agent', str(worktree), 'main')
        compare(_run(LegacyWorktreeSeparatorCheck(), ws, fix=True), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, ws / 'proj' / 'repo')  # repo path doesn't exist
        (project / 'worktrees' / 'g-agent').mkdir()
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])

    def test_ignores_non_goal_actor_branch(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = project / 'worktrees' / 'sidequest'
        Git(repo.path)('worktree', 'add', '-b', 'sidequest', str(worktree), 'main')  # no goal/actor
        compare(_run(LegacyWorktreeSeparatorCheck(), ws, fix=True), expected=[])

    def test_skips_stale_registration(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = _legacy_worktree(repo, project, 'g')
        shutil.rmtree(worktree)  # registered but the dir is gone — can't read its branch
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])


class TestOrphanedWorktree:
    def test_registration_pruned(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = project / 'worktrees' / 'gone@agent'
        Git(repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
        shutil.rmtree(worktree)  # registration is now stale
        compare(
            _run(OrphanedWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'orphaned-worktrees',
                    f'stale worktree registration for {worktree.resolve()}',
                    True,
                    True,
                )
            ],
        )
        compare(registered_worktrees(Git(repo.path)), expected={repo.path.resolve()})

    def test_registration_report_only_keeps_it(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        worktree = project / 'worktrees' / 'gone@agent'
        Git(repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
        shutil.rmtree(worktree)
        compare(
            _run(OrphanedWorktreeCheck(), ws),
            expected=[
                Finding(
                    'orphaned-worktrees',
                    f'stale worktree registration for {worktree.resolve()}',
                    False,
                    True,
                )
            ],
        )
        compare(
            registered_worktrees(Git(repo.path)),
            expected={repo.path.resolve(), worktree.resolve()},
        )

    def test_leftover_dir_reported(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        project = _project(tmpdir, ws, repo.path)
        leftover = project / 'worktrees' / 'random'  # a dir that was never a git worktree
        leftover.mkdir()
        compare(
            _run(OrphanedWorktreeCheck(), ws),
            expected=[
                Finding(
                    'orphaned-worktrees', f'{leftover} is not a registered worktree', False, False
                )
            ],
        )

    def test_none_when_clean(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo = _repo(tmpdir)
        _project(tmpdir, ws, repo.path)
        compare(_run(OrphanedWorktreeCheck(), ws), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/ref/config.yaml', {'kind': 'project'})
        compare(_run(OrphanedWorktreeCheck(), ws), expected=[])


def _shell_home(tmpdir: TempDir, replace: Replacer, shell: str):
    home = tmpdir.makedir('home')
    replace.in_environ('HOME', str(home))
    replace.in_environ('SHELL', f'/bin/{shell}')
    return home


class TestShellCompletion:
    def test_unknown_shell_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _shell_home(tmpdir, replace, 'fish')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_no_shell_is_silent(self, tmpdir: TempDir) -> None:
        # conftest clears $SHELL — nothing to verify
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_installed_script_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zfunc').mkdir()
        (home / '.zfunc' / '_ch').write_text('#compdef ch')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_eval_line_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zshrc').write_text('eval "$(env _CH_COMPLETE=source_zsh ch)"\n')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_missing_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zshrc').write_text('# no completion here\n')
        compare(
            _run(ShellCompletionCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'shell-completion',
                    'tab completion for ch is not installed for zsh — '
                    'run `ch --install-completion`, or add to ~/.zshrc:\n'
                    '    eval "$(env _CH_COMPLETE=source_zsh ch)"',
                    False,
                    False,
                )
            ],
        )

    def test_bash_installed_script_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'bash')
        (home / '.bash_completions').mkdir()
        (home / '.bash_completions' / 'ch.sh').write_text('complete')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_bash_eval_line_in_profile_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'bash')
        (home / '.bash_profile').write_text('eval "$(env _CH_COMPLETE=source_bash ch)"\n')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_bash_missing_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        _shell_home(tmpdir, replace, 'bash')
        compare(
            _run(ShellCompletionCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'shell-completion',
                    'tab completion for ch is not installed for bash — '
                    'run `ch --install-completion`, or add to ~/.bashrc:\n'
                    '    eval "$(env _CH_COMPLETE=source_bash ch)"',
                    False,
                    False,
                )
            ],
        )
