from collections.abc import Iterator

import pytest
import yaml
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.doctor import doctor
from chimera.commands.doctor.core import Finding
from chimera.config import NotInWorkspaceError

runner = CliRunner()


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
    (ws / '.beads').mkdir()
    return ws


def test_doctor_raises_outside_a_workspace(tmpdir: TempDir) -> None:
    with pytest.raises(NotInWorkspaceError):
        doctor(tmpdir.path)  # no .beads / processes / config.yaml


def test_doctor_runs_when_only_a_config_marks_the_root(tmpdir: TempDir) -> None:
    root = tmpdir.makedir('ws')
    (root / 'config.yaml').write_text('kind: workspace\n')
    assert doctor(root, checks=()) == []  # evidence via config.yaml alone


def test_doctor_aggregates_findings_and_passes_fix_through(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    check = _FakeCheck(Finding('fake', 'a thing', resolved=True, fixable=True))
    findings = doctor(ws, fix=True, checks=(check,))
    assert findings == [Finding('fake', 'a thing', resolved=True, fixable=True)]
    assert check.seen == [(ws, True)]


def test_doctor_cli_all_clean(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ['doctor'])
    assert result.exit_code == 0
    assert 'All checks passed!' in result.output


def test_doctor_cli_reports_and_exits_nonzero(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _ws(tmpdir)  # missing root config.yaml → a fixable finding
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ['doctor'])
    assert result.exit_code == 1
    assert 'would fix — run with --fix' in result.output
    assert not (ws / 'config.yaml').exists()  # report only, nothing written


def test_doctor_cli_fix_resolves_and_exits_zero(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    result = runner.invoke(app, ['doctor', str(ws), '--fix'])
    assert result.exit_code == 0
    assert 'fixed' in result.output
    assert yaml.safe_load((ws / 'config.yaml').read_text()) == {'kind': 'workspace'}


def test_doctor_cli_fix_leaves_manual_items_nonzero(tmpdir: TempDir) -> None:
    ws = _ws(tmpdir)
    (ws / 'config.yaml').write_text('kind: nonsense\n')  # not auto-fixable
    result = runner.invoke(app, ['doctor', str(ws), '--fix'])
    assert result.exit_code == 1
    assert 'needs attention' in result.output
