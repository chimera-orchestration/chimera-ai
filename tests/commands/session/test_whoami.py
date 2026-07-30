from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from testfixtures import Replacer, TempDir, compare

from chimera.archive import Archive, ArchiveSession
from chimera.commands.session.whoami import whoami
from tests.cli import Command, action_logs

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
WHOAMI = 'chimera.commands.session.whoami.whoami'


@pytest.fixture()
def workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def _record(ws: Path, native_id: str, **kw: Any) -> None:
    with Archive.open(ws / 'state' / 'archive.db') as store:
        store.record_session(
            ArchiveSession(
                platform='claude', native_id=native_id, status='startup', started_at=NOON, **kw
            )
        )


def test_a_human_is_told_so_and_shown_the_seat(workspace: Path) -> None:
    compare(
        whoami(workspace),
        expected='human\nseat: @@captain\n(no session of a registered harness is running this)',
    )


def test_an_addressed_session_leads_with_its_address(workspace: Path, replace: Replacer) -> None:
    replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-1')
    _record(workspace, 'uuid-1', address='proj@g@agent')
    compare(
        whoami(workspace),
        expected='proj@g@agent\nseat: @@captain\nsession: claude uuid-1\nstatus: startup',
    )


def test_an_unaddressed_session_is_told_what_it_lacks(workspace: Path, replace: Replacer) -> None:
    # the question an agent asks when it isn't sure — answered with the same evidence
    # the fence and mail use, so it can't be told one thing here and treated as another
    replace.in_environ('CLAUDE_CODE_SESSION_ID', 'e4d0a1b2-0000-0000-0000-000000000000')
    _record(workspace, 'e4d0a1b2-0000-0000-0000-000000000000')
    lines = whoami(workspace).splitlines()
    compare(lines[0], expected='unaddressed (e4d0a1b2)')
    assert 'no mail routes to it' in lines[-1]


def test_a_non_conversation_says_so(workspace: Path, replace: Replacer) -> None:
    replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-p')
    _record(workspace, 'uuid-p', addressable=False)
    assert 'not a conversation' in whoami(workspace)


def test_whoami_cli(workspace: Path, command: Command) -> None:
    command.run('session', 'whoami').check(
        output='human\nseat: @@captain\n(no session of a registered harness is running this)',
        logging=action_logs('session whoami', WHOAMI, {}),
    )
