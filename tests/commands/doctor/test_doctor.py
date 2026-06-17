import os
from collections.abc import Iterator
from pathlib import Path

import yaml
from testfixtures import Command, Replacer, ShouldRaise, TempDir, compare, not_there

from chimera.commands.doctor import doctor, find_workspace_root, resolve_root
from chimera.commands.doctor.core import Finding
from chimera.config import NotInWorkspaceError


def _env_not_set(workspace: Path) -> str:
    """The workspace-env finding's exact text when $CHIMERA_WORKSPACE is unset."""
    return (
        '[workspace-env] (needs attention) $CHIMERA_WORKSPACE is not set — '
        'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):\n'
        f'    export CHIMERA_WORKSPACE="{workspace}"'
    )


class _FakeCheck:
    name = 'fake'

    def __init__(self, *findings: Finding) -> None:
        self._findings = findings
        self.seen: list[tuple] = []

    def run(self, workspace, fix) -> Iterator[Finding]:
        self.seen.append((workspace, fix))
        yield from self._findings


def _ws(tmpdir: TempDir):
    ws = tmpdir.makedir('lycia')
    (ws / 'processes').mkdir()
    return ws


def _project(ws, *, name='proj', config='repo: /some/repo\n'):
    project = ws / name
    project.mkdir()
    (project / 'config.yaml').write_text(config)
    return project


def test_find_workspace_root_at_a_marked_root(tmpdir: TempDir) -> None:
    root = tmpdir.makedir('ws')
    (root / 'config.yaml').write_text('kind: workspace\n')
    compare(find_workspace_root(root), expected=root)


def test_find_workspace_root_by_legacy_evidence(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)  # processes/, no config yet
    compare(find_workspace_root(ws), expected=ws)


def test_find_workspace_root_navigates_up_from_a_project(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = _project(ws)
    compare(find_workspace_root(project), expected=ws)


def test_find_workspace_root_skips_a_project_even_when_mislabeled(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = _project(ws, config='kind: workspace\nrepo: /some/repo\n')  # corrupted
    compare(find_workspace_root(project), expected=ws)  # repo: marks it a project, walk past it


def test_find_workspace_root_raises_when_none(tmpdir: TempDir) -> None:
    with ShouldRaise(NotInWorkspaceError(tmpdir.path)):  # no processes / config.yaml
        find_workspace_root(tmpdir.path)


def test_resolve_root_prefers_explicit_path(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    elsewhere = tmpdir.makedir('elsewhere')
    compare(resolve_root(ws, cwd=elsewhere, env=str(elsewhere)), expected=ws.resolve())


def test_resolve_root_trusts_env_over_walking_up(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    outside = tmpdir.makedir('outside')  # not under any workspace
    compare(resolve_root(None, cwd=outside, env=str(ws)), expected=ws.resolve())


def test_resolve_root_walks_up_without_env(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    compare(resolve_root(None, cwd=ws, env=None), expected=ws.resolve())


def test_doctor_aggregates_findings_and_passes_fix_through(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    check = _FakeCheck(Finding('fake', 'a thing', resolved=True, fixable=True))
    findings = doctor(ws, fix=True, checks=(check,))
    compare(findings, expected=[Finding('fake', 'a thing', resolved=True, fixable=True)])
    compare(check.seen, expected=[(ws, True)])


def test_doctor_cli_all_clean(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor').check(  # cwd is the tmpdir, not ws, so the note appears
        output=f'note: resolved workspace root: {ws.resolve()}\nAll checks passed!',
        logging=[('INFO', 'doctor')],
    )


def test_doctor_cli_verbose_lists_every_check(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', '--verbose').check(
        output='\n'.join(
            [
                f'note: resolved workspace root: {ws.resolve()}',
                '[workspace-config] (ok)',
                '[project-config] (ok)',
                '[human-worktrees] (ok)',
                '[worktree-separator] (ok)',
                '[orphaned-worktrees] (ok)',
                '[workspace-env] (ok)',
                '[shell-completion] (ok)',
                'All checks passed!',
            ]
        ),
        logging=[('INFO', 'doctor')],
    )


def test_doctor_cli_flags_unset_workspace_env(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    replace.in_environ('CHIMERA_WORKSPACE', not_there)
    os.chdir(ws)  # no env: doctor finds the workspace by walking up from cwd
    command.run('doctor').check(
        output=_env_not_set(ws.resolve()),
        return_code=1,
        logging=[('INFO', 'doctor')],
    )


def test_doctor_cli_reports_and_exits_nonzero(tmpdir: TempDir, command: Command) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml → a fixable finding
    os.chdir(ws)  # no env: doctor finds the workspace by walking up from cwd
    command.run('doctor').check(
        output='\n'.join(
            [
                f'[workspace-config] (would fix — run with --fix) {ws.resolve()}/config.yaml missing',
                _env_not_set(ws.resolve()),
            ]
        ),
        return_code=1,
        logging=[('INFO', 'doctor')],
    )
    assert (ws / 'config.yaml').exists() is False  # report only, nothing written


def test_doctor_cli_fix_resolves_and_exits_zero(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', str(ws), '--fix').check(
        output=f'[workspace-config] (fixed) {ws.resolve()}/config.yaml missing',
        logging=[('INFO', 'doctor')],
    )
    compare(yaml.safe_load((ws / 'config.yaml').read_text()), expected={'kind': 'workspace'})


def test_doctor_cli_fix_leaves_manual_items_nonzero(tmpdir: TempDir, command: Command) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: nonsense\n')  # not auto-fixable
    command.run('doctor', str(ws), '--fix').check(
        output='\n'.join(
            [
                f'[workspace-config] (needs attention) {ws.resolve()}/config.yaml '
                'has kind: nonsense at the workspace root',
                _env_not_set(ws.resolve()),
            ]
        ),
        return_code=1,
        logging=[('INFO', 'doctor')],
    )


def test_doctor_cli_navigates_from_a_project(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = _project(ws, name='chimera')  # legacy repo:-only config
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', '--fix').check(  # cwd is the tmpdir, not ws, so the note appears
        output='\n'.join(
            [
                f'note: resolved workspace root: {ws.resolve()}',
                f'[project-config] (fixed) {ws.resolve()}/chimera/config.yaml missing kind: project',
            ]
        ),
        logging=[('INFO', 'doctor')],
    )
    config = yaml.safe_load((project / 'config.yaml').read_text())
    compare(config, expected={'kind': 'project', 'repo': '/some/repo'})  # fixed, not corrupted
