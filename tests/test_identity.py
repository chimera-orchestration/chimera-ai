from datetime import datetime, timezone
from pathlib import Path

import pytest
from testfixtures import Replacer, TempDir, compare

from chimera.archive import Archive, ArchiveSession
from chimera.identity import HUMAN, current_session, executor

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def _record(ws: Path, native_id: str, address: str | None = None) -> None:
    with Archive.open(ws / 'state' / 'archive.db') as store:
        store.record_session(
            ArchiveSession(
                platform='claude',
                native_id=native_id,
                status='startup',
                started_at=NOON,
                address=address,
            )
        )


class TestCurrentSession:
    def test_a_plain_shell_is_inside_no_session(self, workspace: Path) -> None:
        assert current_session(workspace) is None

    def test_the_harness_names_the_session_and_the_archive_describes_it(
        self, workspace: Path, replace: Replacer
    ) -> None:
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-1')
        _record(workspace, 'uuid-1', 'proj@g@agent')
        session = current_session(workspace)
        assert session is not None
        compare(session.address, expected='proj@g@agent')

    def test_a_session_the_archive_never_saw_stays_unidentified(
        self, workspace: Path, replace: Replacer
    ) -> None:
        # no row, no identity — never a guess from where the process happens to be
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-unknown')
        assert current_session(workspace) is None

    def test_outside_a_workspace_there_is_nothing_to_look_up(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-1')
        assert current_session(tmpdir.path / 'nowhere') is None


class TestExecutor:
    def test_a_human_is_a_human(self, workspace: Path) -> None:
        compare(executor(workspace), expected=HUMAN)

    def test_an_addressed_session_is_named_by_its_address(
        self, workspace: Path, replace: Replacer
    ) -> None:
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-1')
        _record(workspace, 'uuid-1', 'proj@g@agent')
        compare(executor(workspace), expected='proj@g@agent')

    def test_an_unaddressed_session_is_named_by_its_short_id(
        self, workspace: Path, replace: Replacer
    ) -> None:
        # says *something ran this, and which conversation* without implying a claim
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'e4d0a1b2-1111-2222-3333-444444444444')
        _record(workspace, 'e4d0a1b2-1111-2222-3333-444444444444')
        compare(executor(workspace), expected='e4d0a1b2')

    def test_standing_in_a_worktree_is_not_evidence(self, tmpdir: TempDir, workspace: Path) -> None:
        # the rule the whole design turns on: a location never names the executor
        tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
        worktree = workspace / 'proj' / 'worktrees' / 'g@agent'
        worktree.mkdir(parents=True)
        compare(executor(worktree), expected=HUMAN)
