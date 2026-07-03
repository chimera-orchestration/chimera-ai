import os
import shutil
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare, not_there

from chimera.commands.doctor import (
    CHECKS,
    Exclusions,
    UnknownCheckError,
    doctor,
    find_workspace_root,
    resolve_root,
    select_checks,
)
from chimera.commands.doctor import checks as doctor_checks
from chimera.commands.doctor.core import Finding
from chimera.commands.init import TEMPLATE
from chimera.config import NotInWorkspaceError
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _no_chimera_checkout(replace: Replacer) -> None:
    """These CLI tests run the real CHECKS tuple — keep chimera-up-to-date inert.

    Without this, it would hit this very repo's actual git state (and network), which
    these tests don't control and shouldn't depend on. The check itself is covered in
    tests/commands/doctor/test_checks.py.
    """
    replace.in_module(doctor_checks.chimera_repo, lambda: None)


def _env_not_set_message(workspace: Path) -> str:
    """The workspace-env finding's raw message when $CHIMERA_WORKSPACE is unset."""
    return (
        '$CHIMERA_WORKSPACE is not set — '
        'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):\n'
        f'    export CHIMERA_WORKSPACE="{workspace}"'
    )


def _env_not_set(workspace: Path) -> str:
    """The same finding as doctor's CLI output renders it."""
    return f'[workspace-env] (needs attention) {_env_not_set_message(workspace)}'


class _FakeCheck:
    name = 'fake'

    def __init__(self, *findings: Finding) -> None:
        self._findings = findings
        self.seen: list[tuple] = []

    def run(self, workspace, fix, exclude) -> Iterator[Finding]:
        self.seen.append((workspace, fix))
        yield from self._findings


def _ws(tmpdir: TempDir):
    ws = tmpdir.makedir('lycia')
    (ws / 'processes').mkdir()
    shutil.copy(TEMPLATE / '.gitignore', ws / '.gitignore')  # a healthy workspace
    return ws


def _project(tmpdir, ws, *, name='proj', config=None):
    tmpdir.dump(
        f'lycia/{name}/config.yaml', config if config is not None else {'repo': '/some/repo'}
    )
    return ws / name


def test_find_workspace_root_at_a_marked_root(tmpdir: TempDir) -> None:
    root = tmpdir.makedir('ws')
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    compare(find_workspace_root(root), expected=root)


def test_find_workspace_root_by_legacy_evidence(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)  # processes/, no config yet
    compare(find_workspace_root(ws), expected=ws)


def test_find_workspace_root_navigates_up_from_a_project(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    project = _project(tmpdir, ws)
    compare(find_workspace_root(project), expected=ws)


def test_find_workspace_root_skips_a_project_even_when_mislabeled(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    project = _project(tmpdir, ws, config={'kind': 'workspace', 'repo': '/some/repo'})  # corrupted
    compare(find_workspace_root(project), expected=ws)  # repo: marks it a project, walk past it


def test_find_workspace_root_raises_when_none(tmpdir: TempDir) -> None:
    with ShouldRaise(NotInWorkspaceError(tmpdir.path)):  # no processes / config.yaml
        find_workspace_root(tmpdir.path)


def test_resolve_root_prefers_explicit_path(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    elsewhere = tmpdir.makedir('elsewhere')
    compare(resolve_root(ws, cwd=elsewhere, env=str(elsewhere)), expected=ws.resolve())


def test_resolve_root_trusts_env_over_walking_up(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    outside = tmpdir.makedir('outside')  # not under any workspace
    compare(resolve_root(None, cwd=outside, env=str(ws)), expected=ws.resolve())


def test_resolve_root_walks_up_without_env(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    compare(resolve_root(None, cwd=ws, env=None), expected=ws.resolve())


def test_doctor_aggregates_findings_and_passes_fix_through(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    check = _FakeCheck(Finding('fake', 'a thing', resolved=True, fixable=True))
    compare(
        doctor(ws, fix=True, checks=(check,)),
        expected=[Finding('fake', 'a thing', resolved=True, fixable=True)],
    )
    compare(check.seen, expected=[(ws, True)])


def test_doctor_drops_findings_matching_a_message_substring(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    keep = Finding('fake', 'keep me', resolved=False, fixable=False)
    drop = Finding('fake', 'drop me please', resolved=False, fixable=False)
    exclude = Exclusions(('drop me',))
    compare(doctor(ws, checks=(_FakeCheck(keep, drop),), exclude=exclude), expected=[keep])
    compare(exclude.excluded, expected=1)
    compare(exclude.unmatched, expected=())


def test_doctor_drops_findings_matching_a_check_name(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    findings = (
        Finding('fake', 'one', resolved=False, fixable=False),
        Finding('fake', 'two', resolved=False, fixable=False),
    )
    exclude = Exclusions(('fake',))
    compare(doctor(ws, checks=(_FakeCheck(*findings),), exclude=exclude), expected=[])
    compare(exclude.excluded, expected=2)


def test_doctor_reports_an_exclusion_that_matched_nothing(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    keep = Finding('fake', 'keep me', resolved=False, fixable=False)
    exclude = Exclusions(('ghost',))
    compare(doctor(ws, checks=(_FakeCheck(keep),), exclude=exclude), expected=[keep])
    compare(exclude.excluded, expected=0)
    compare(exclude.unmatched, expected=('ghost',))


def test_select_checks_no_names_gives_all() -> None:
    compare(select_checks(()), expected=tuple(CHECKS))


def test_select_checks_keeps_registry_order() -> None:
    selected = select_checks(['workspace-clean', 'gitignore'])  # reversed vs the registry
    compare([check.name for check in selected], expected=['gitignore', 'workspace-clean'])


def test_select_checks_unknown_name_raises() -> None:
    valid = [check.name for check in CHECKS]
    with ShouldRaise(UnknownCheckError(['bogus'], valid)):
        select_checks(['bogus', 'gitignore'])


def test_doctor_logs_a_clean_check(tmpdir: TempDir, full_logs: LogCapture) -> None:
    doctor(_ws(tmpdir), checks=(_FakeCheck(),))
    full_logs.check({'level': 'INFO', 'message': 'fake: checked', 'findings': 0})


def test_doctor_logs_each_finding_at_its_level(tmpdir: TempDir, full_logs: LogCapture) -> None:
    check = _FakeCheck(
        Finding('fake', 'left broken', resolved=False, fixable=True),
        Finding('fake', 'now fixed', resolved=True, fixable=True),
    )
    doctor(_ws(tmpdir), fix=True, checks=(check,))
    full_logs.check(
        {'level': 'INFO', 'message': 'fake: checked', 'findings': 2},
        {'level': 'ERROR', 'message': 'fake: left broken', 'fixable': True, 'resolved': False},
        {'level': 'INFO', 'message': 'fake: now fixed', 'fixable': True, 'resolved': True},
    )


def _check_line(name: str, findings: int) -> dict[str, object]:
    return {'level': 'INFO', 'message': f'{name}: checked', 'findings': findings}


def _finding_line(
    check: str, message: str, *, fixable: bool, resolved: bool = False
) -> dict[str, object]:
    return {
        'level': 'INFO' if resolved else 'ERROR',
        'message': f'{check}: {message}',
        'fixable': fixable,
        'resolved': resolved,
    }


def _env_finding(workspace: Path) -> dict[str, object]:
    return _finding_line('workspace-env', _env_not_set_message(workspace), fixable=False)


def _doctor_logs(
    path: str | None,
    *,
    fix: bool,
    verbose: bool = False,
    repo: str | None = None,
    check: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    findings: Mapping[str, Sequence[dict[str, object]]] = {},
    excluded: Mapping[str, Sequence[dict[str, object]]] = {},
) -> list[dict[str, object]]:
    """doctor start / per-check lines with their findings / end, with its CLI params.

    Lines appear only for the checks a ``-c`` selection actually runs (so the
    chimera-up-to-date checkout event vanishes with the check); ``excluded`` lines land
    before their check's checked line, mirroring the driver's order.
    """
    start, end = action_logs(
        'doctor',
        'chimera.commands.doctor.doctor',
        {'path': path, 'fix': fix, 'check': check, 'exclude': exclude, 'verbose': verbose},
    )
    lines: list[dict[str, object]] = [start]
    for selected in select_checks(check):
        if selected.name == 'chimera-up-to-date':
            lines.append({'level': 'INFO', 'message': 'chimera-up-to-date: checkout', 'repo': repo})
        lines.extend(excluded.get(selected.name, ()))
        found = findings.get(selected.name, ())
        lines.append(_check_line(selected.name, len(found)))
        lines.extend(found)
    lines.append(end)
    return lines


def test_doctor_cli_all_clean(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor').check(  # cwd is the tmpdir, not ws, so the note appears
        output=(
            f'note: resolved workspace root: {ws.resolve()}\n'
            'All checks passed! (ch doctor -v lists the 12 checks run)'
        ),
        logging=_doctor_logs(None, fix=False),
    )


def test_doctor_cli_verbose_lists_every_check(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', '--verbose').check(
        output='\n'.join(
            [
                f'note: resolved workspace root: {ws.resolve()}',
                '[workspace-config] (ok)',
                '[gitignore] (ok)',
                '[project-config] (ok)',
                '[human-worktrees] (ok)',
                '[inert-branches] (ok)',
                '[worktree-separator] (ok)',
                '[worktree-branch] (ok)',
                '[orphaned-worktrees] (ok)',
                '[chimera-up-to-date] (ok)',
                '[workspace-env] (ok)',
                '[shell-completion] (ok)',
                '[workspace-clean] (ok)',
                'All checks passed!',
            ]
        ),
        logging=_doctor_logs(None, fix=False, verbose=True),
    )


def test_doctor_cli_verbose_notes_the_chimera_checkout(
    tmpdir: TempDir, replace: Replacer, command: Command, git_repo: Repo
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    # the autouse fixture above already patched chimera_repo via this same Replacer,
    # so its live attribute is no longer the original function in_module keys off of —
    # name the container/attr explicitly instead.
    replace(
        target=doctor_checks,
        container=doctor_checks,
        name='chimera_repo',
        replacement=lambda: git_repo.path,
    )
    command.run('doctor', '--verbose').check(
        output='\n'.join(
            [
                f'note: resolved workspace root: {ws.resolve()}',
                f'note: chimera checkout: {git_repo.path}',
                '[workspace-config] (ok)',
                '[gitignore] (ok)',
                '[project-config] (ok)',
                '[human-worktrees] (ok)',
                '[inert-branches] (ok)',
                '[worktree-separator] (ok)',
                '[worktree-branch] (ok)',
                '[orphaned-worktrees] (ok)',
                '[chimera-up-to-date] (ok)',
                '[workspace-env] (ok)',
                '[shell-completion] (ok)',
                '[workspace-clean] (ok)',
                'All checks passed!',
            ]
        ),
        logging=_doctor_logs(None, fix=False, verbose=True, repo=str(git_repo.path)),
    )


def test_doctor_cli_flags_unset_workspace_env(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', not_there)
    os.chdir(ws)  # no env: doctor finds the workspace by walking up from cwd
    command.run('doctor').check(
        output=_env_not_set(ws.resolve()) + '\n(+11 checks passed — ch doctor -v to list)',
        return_code=1,
        logging=_doctor_logs(
            None, fix=False, findings={'workspace-env': [_env_finding(ws.resolve())]}
        ),
    )


def test_doctor_cli_reports_and_exits_nonzero(tmpdir: TempDir, command: Command) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml → a fixable finding
    os.chdir(ws)  # no env: doctor finds the workspace by walking up from cwd
    command.run('doctor').check(
        output='\n'.join(
            [
                f'[workspace-config] (would fix — run with --fix) {ws.resolve()}/config.yaml missing',
                _env_not_set(ws.resolve()),
                '(+10 checks passed — ch doctor -v to list)',
            ]
        ),
        return_code=1,
        logging=_doctor_logs(
            None,
            fix=False,
            findings={
                'workspace-config': [
                    _finding_line(
                        'workspace-config', f'{ws.resolve()}/config.yaml missing', fixable=True
                    )
                ],
                'workspace-env': [_env_finding(ws.resolve())],
            },
        ),
    )
    assert (ws / 'config.yaml').exists() is False  # report only, nothing written


def test_doctor_cli_fix_resolves_and_exits_zero(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', str(ws), '--fix').check(
        output=(
            f'[workspace-config] (fixed) {ws.resolve()}/config.yaml missing\n'
            '(+11 checks passed — ch doctor -v to list)'
        ),
        logging=_doctor_logs(
            str(ws),
            fix=True,
            findings={
                'workspace-config': [
                    _finding_line(
                        'workspace-config',
                        f'{ws.resolve()}/config.yaml missing',
                        fixable=True,
                        resolved=True,
                    )
                ]
            },
        ),
    )
    compare(tmpdir.parse('lycia/config.yaml'), expected={'kind': 'workspace'})


def test_doctor_cli_fix_leaves_manual_items_nonzero(tmpdir: TempDir, command: Command) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'nonsense'})  # not auto-fixable
    command.run('doctor', str(ws), '--fix').check(
        output='\n'.join(
            [
                f'[workspace-config] (needs attention) {ws.resolve()}/config.yaml '
                'has kind: nonsense at the workspace root',
                _env_not_set(ws.resolve()),
                '(+10 checks passed — ch doctor -v to list)',
            ]
        ),
        return_code=1,
        logging=_doctor_logs(
            str(ws),
            fix=True,
            findings={
                'workspace-config': [
                    _finding_line(
                        'workspace-config',
                        f'{ws.resolve()}/config.yaml has kind: nonsense at the workspace root',
                        fixable=False,
                    )
                ],
                'workspace-env': [_env_finding(ws.resolve())],
            },
        ),
    )


def test_doctor_cli_check_runs_only_the_named_checks(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml → a workspace-config finding
    replace.in_environ('CHIMERA_WORKSPACE', not_there)  # workspace-env would flag — not selected
    command.run('doctor', str(ws), '-c', 'workspace-config').check(
        output=(
            f'[workspace-config] (would fix — run with --fix) {ws.resolve()}/config.yaml missing'
        ),
        return_code=1,
        logging=_doctor_logs(
            str(ws),
            fix=False,
            check=('workspace-config',),
            findings={
                'workspace-config': [
                    _finding_line(
                        'workspace-config', f'{ws.resolve()}/config.yaml missing', fixable=True
                    )
                ]
            },
        ),
    )


def test_doctor_cli_check_fixes_only_the_named_checks(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml, and:
    (ws / '.gitignore').write_text('')  # every template gitignore entry missing
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    entries = [
        line.strip() for line in (TEMPLATE / '.gitignore').read_text().splitlines() if line.strip()
    ]
    command.run('doctor', str(ws), '--fix', '-c', 'gitignore').check(
        output='\n'.join(
            f'[gitignore] (fixed) {ws.resolve()}/.gitignore missing {entry!r}' for entry in entries
        ),
        logging=_doctor_logs(
            str(ws),
            fix=True,
            check=('gitignore',),
            findings={
                'gitignore': [
                    _finding_line(
                        'gitignore',
                        f'{ws.resolve()}/.gitignore missing {entry!r}',
                        fixable=True,
                        resolved=True,
                    )
                    for entry in entries
                ]
            },
        ),
    )
    assert (ws / 'config.yaml').exists() is False  # the unselected check touched nothing


def test_doctor_cli_check_unknown_name(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    available = ', '.join(check.name for check in CHECKS)
    message = f'unknown check: bogus (available: {available})'
    command.run('doctor', str(ws), '-c', 'bogus').check(
        output=f'Error: {message}',
        return_code=1,
        logging=action_logs(
            'doctor',
            'chimera.commands.doctor.doctor',
            {'path': str(ws), 'fix': False, 'check': ('bogus',), 'exclude': (), 'verbose': False},
            error=f'UnknownCheckError: {message}',
        ),
    )


def _excluded_log(check: str, finding: str) -> dict[str, object]:
    """The log line doctor emits for each finding an -x token suppressed."""
    return {'level': 'INFO', 'message': 'doctor: excluded', 'check': check, 'finding': finding}


def test_doctor_cli_exclude_mutes_a_finding(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', not_there)  # workspace-env would flag and exit 1
    os.chdir(ws)
    dropped = _excluded_log('workspace-env', _env_not_set_message(ws.resolve()))
    command.run('doctor', '-x', 'workspace-env').check(
        output=('(+12 checks passed — ch doctor -v to list)\n(1 finding excluded by -x)'),
        logging=_doctor_logs(
            None, fix=False, exclude=('workspace-env',), excluded={'workspace-env': [dropped]}
        ),
    )


def test_doctor_cli_exclude_prevents_the_fix(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml → a fixable workspace-config finding
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    dropped = _excluded_log('workspace-config', f'{ws.resolve()}/config.yaml missing')
    command.run('doctor', str(ws), '--fix', '-x', 'workspace-config').check(
        output=('(+12 checks passed — ch doctor -v to list)\n(1 finding excluded by -x)'),
        logging=_doctor_logs(
            str(ws),
            fix=True,
            exclude=('workspace-config',),
            excluded={'workspace-config': [dropped]},
        ),
    )
    assert (ws / 'config.yaml').exists() is False  # excluded, so --fix never wrote it


def test_doctor_cli_exclude_unmatched_warns(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', str(ws), '-x', 'bogus').check(
        output=(
            "warning: -x 'bogus' matched nothing\n"
            'All checks passed! (ch doctor -v lists the 12 checks run)'
        ),
        logging=_doctor_logs(str(ws), fix=False, exclude=('bogus',)),
    )


def test_doctor_cli_navigates_from_a_project(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _ws(tmpdir)
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    _project(tmpdir, ws, name='chimera')  # legacy repo:-only config
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('doctor', '--fix').check(  # cwd is the tmpdir, not ws, so the note appears
        output='\n'.join(
            [
                f'note: resolved workspace root: {ws.resolve()}',
                f'[project-config] (fixed) {ws.resolve()}/chimera/config.yaml missing kind: project',
                '(+11 checks passed — ch doctor -v to list)',
            ]
        ),
        logging=_doctor_logs(
            None,
            fix=True,
            findings={
                'project-config': [
                    _finding_line(
                        'project-config',
                        f'{ws.resolve()}/chimera/config.yaml missing kind: project',
                        fixable=True,
                        resolved=True,
                    )
                ]
            },
        ),
    )
    # fixed, not corrupted
    compare(
        tmpdir.parse('lycia/chimera/config.yaml'),
        expected={'kind': 'project', 'repo': '/some/repo'},
    )
