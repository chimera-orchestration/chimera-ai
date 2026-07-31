from datetime import datetime, timezone
from pathlib import Path

import pytest
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.agent_env import (
    CrossScopeError,
    ai_session,
    fenced_project,
    refuse_cross_scope,
    running_under_ai_agent,
    session_address,
    session_role,
)
from chimera.archive import Archive, ArchiveSession

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def _session(ws: Path, replace: Replacer, address: str | None, native_id: str = 'uuid-1') -> None:
    """Put this process inside a recorded session holding ``address``."""
    replace.in_environ('CLAUDE_CODE_SESSION_ID', native_id)
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


class TestRunningUnderAiAgent:
    def test_true_when_claudecode_set(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', '1')
        assert running_under_ai_agent()

    def test_false_when_unset(self) -> None:
        assert not running_under_ai_agent()  # conftest clears it


class TestAiSession:
    def test_true_under_a_harness_marker(self, workspace: Path, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', '1')
        assert ai_session(workspace)

    def test_true_for_a_recorded_session_without_a_marker(
        self, workspace: Path, replace: Replacer
    ) -> None:
        # a future harness that sets no marker of its own is still caught, provided
        # chimera launched it — the archive knows this process to be a session
        _session(workspace, replace, address='proj@@manager')
        assert ai_session(workspace)

    def test_false_for_a_human(self, workspace: Path) -> None:
        assert not ai_session(workspace)


class TestSessionRole:
    def test_a_captains_address_is_the_captain(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='@@captain')
        compare(session_role(workspace), expected='captain')

    def test_a_managers_address_is_the_manager(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='proj@@manager')
        compare(session_role(workspace), expected='manager')

    def test_an_actors_address_is_the_agent(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='proj@g@agent')
        compare(session_role(workspace), expected='agent')

    def test_a_human_has_no_role(self, workspace: Path) -> None:
        assert session_role(workspace) is None

    def test_an_unaddressed_session_has_no_role(self, workspace: Path, replace: Replacer) -> None:
        # a hand-launched claude: recorded, but holding no claim to act as anyone
        _session(workspace, replace, address=None)
        assert session_role(workspace) is None

    def test_an_unparseable_address_is_no_address_at_all(
        self, workspace: Path, replace: Replacer
    ) -> None:
        _session(workspace, replace, address='not-an-address')
        assert session_address(workspace) is None
        assert session_role(workspace) is None


class TestFencedProject:
    def test_a_manager_is_fenced_to_its_project(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='proj@@manager')
        compare(fenced_project(workspace), expected='proj')

    def test_an_agent_is_fenced_to_its_goals_project(
        self, workspace: Path, replace: Replacer
    ) -> None:
        _session(workspace, replace, address='proj@g@agent')
        compare(fenced_project(workspace), expected='proj')

    def test_the_captain_is_unfenced(self, workspace: Path, replace: Replacer) -> None:
        # by construction, not by exception: its address names no project
        _session(workspace, replace, address='@@captain')
        assert fenced_project(workspace) is None

    def test_a_human_is_unfenced(self, workspace: Path) -> None:
        assert fenced_project(workspace) is None

    def test_an_unaddressed_session_is_unfenced(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address=None)
        assert fenced_project(workspace) is None


class TestRefuseCrossScope:
    def test_out_of_scope_refuses(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='proj@@manager')
        with ShouldRaise(CrossScopeError('proj')):
            refuse_cross_scope(workspace, 'other')

    def test_in_scope_passes(self, workspace: Path, replace: Replacer) -> None:
        _session(workspace, replace, address='proj@@manager')
        refuse_cross_scope(workspace, 'proj')

    def test_unfenced_passes_anything(self, workspace: Path) -> None:
        refuse_cross_scope(workspace, 'other')

    def test_error_signposts_depth_never_privilege(self) -> None:
        compare(str(CrossScopeError('proj')), expected='scoped to proj; ask the captain')


def test_a_background_agent_is_fenced_like_any_other(workspace: Path, replace: Replacer) -> None:
    # the whole point of retiring the env stamp: a `--bg` session runs in a pooled worker
    # that never saw the launcher's environment, and passing a prompt is exactly what
    # makes a launch background — so the unattended agents were the unfenced ones
    _session(workspace, replace, address='proj@g@agent')
    compare(session_role(workspace), expected='agent')
    compare(fenced_project(workspace), expected='proj')
