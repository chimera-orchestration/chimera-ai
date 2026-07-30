from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.archive import Archive, ArchiveSession, Event
from chimera.commands.session.show import show
from chimera.config import UserError
from tests.cli import Command, action_logs

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
SHOW = 'chimera.commands.session.show.show'


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
        store.record_event(Event(at=NOON, kind='startup', platform='claude', native_id=native_id))
        store.record_event(
            Event(
                at=NOON + timedelta(hours=1),
                kind='end',
                detail='prompt_input_exit',
                platform='claude',
                native_id=native_id,
            )
        )


def test_shows_the_row_and_its_timeline(workspace: Path, tmpdir: TempDir) -> None:
    transcript = tmpdir.write('t.jsonl', '')
    _record(
        workspace,
        'uuid-1',
        address='proj@g@agent',
        model='opus',
        harness_version='claude-code_2-1-220_agent',
        cwd=workspace,
        transcript=transcript,
        workspace='ws',
        project='proj',
        goal='g',
        actor='agent',
    )
    compare(
        show(workspace, 'uuid-1'),
        expected='\n'.join(
            [
                'proj@g@agent  claude uuid-1',
                'status: startup',
                f'where: {workspace}',
                'axes: ws / proj / g / agent',
                'model: opus',
                'harness: claude-code_2-1-220_agent',
                f'transcript: {transcript}',
                'timeline:',
                f'  {NOON.isoformat()}  startup',
                f'  {(NOON + timedelta(hours=1)).isoformat()}  end  prompt_input_exit',
            ]
        ),
    )


def test_a_pruned_transcript_says_the_session_is_beyond_reviving(
    workspace: Path, tmpdir: TempDir
) -> None:
    _record(workspace, 'uuid-1', transcript=tmpdir.path / 'gone.jsonl')
    assert 'gone — this session can no longer be resumed' in show(workspace, 'uuid-1')


def test_a_leading_block_is_enough(workspace: Path) -> None:
    # the short form a listing shows pastes straight back
    _record(workspace, 'abc12345-9f80-4c8e-b3d7-1234567890ab')
    assert 'abc12345-9f80-4c8e-b3d7-1234567890ab' in show(workspace, 'abc12345')


def test_an_ambiguous_prefix_refuses_rather_than_guessing(workspace: Path) -> None:
    _record(workspace, 'abc-one')
    _record(workspace, 'abc-two')
    with ShouldRaise(UserError("'abc' matches several sessions: abc-one, abc-two")):
        show(workspace, 'abc')


def test_an_unknown_session_refuses(workspace: Path) -> None:
    with ShouldRaise(UserError("no claude session here starting 'nope'")):
        show(workspace, 'nope')


def test_show_cli(workspace: Path, command: Command) -> None:
    _record(workspace, 'uuid-1', address='proj@g@agent')
    command.run('session', 'show', 'uuid-1').check(
        output='\n'.join(
            [
                'proj@g@agent  claude uuid-1',
                'status: startup',
                'where: None',
                'axes: - / - / - / -',
                'timeline:',
                f'  {NOON.isoformat()}  startup',
                f'  {(NOON + timedelta(hours=1)).isoformat()}  end  prompt_input_exit',
            ]
        ),
        logging=action_logs('session show', SHOW, {'session': 'uuid-1'}),
    )
