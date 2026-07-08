import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from testfixtures import Replacer, TempDir, compare

from chimera.agents import Session
from chimera.agents.claude import Claude, session_summary


def _registry(replace: Replacer, payload: str) -> dict[str, object]:
    """Stub the ``claude agents --json`` subprocess; captured argv lands in the return."""
    captured: dict[str, object] = {}

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout=payload)

    replace.in_module(subprocess.run, fake_run)
    return captured


def test_reported_queries_claude_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    captured = _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 4242}]')
    compare(
        Claude().reported(worktree),
        expected=[Session(id='x', name='x', status='idle', cwd=Path('.'), summary=None, pid=4242)],
    )
    compare(captured['cmd'], expected=['claude', 'agents', '--json', '--cwd', str(worktree)])


def test_reported_unscoped_queries_everywhere(replace: Replacer) -> None:
    captured = _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 4242}]')
    compare(
        Claude().reported(),
        expected=[Session(id='x', name='x', status='idle', cwd=Path('.'), summary=None, pid=4242)],
    )
    compare(captured['cmd'], expected=['claude', 'agents', '--json'])  # no --cwd → every project


def test_reported_parses_the_full_registry_record(replace: Replacer) -> None:
    full = 'abc12345-9f80-4c8e-b3d7-1234567890ab'
    _registry(
        replace,
        '[{"id": "abc12345", "sessionId": "%s", "status": "busy", "pid": 4242,'
        ' "kind": "interactive", "startedAt": 1781247747055,'
        ' "name": "proj@g@agent", "cwd": "/work/proj"}]' % full,
    )
    compare(
        Claude().reported(),
        expected=[
            Session(
                # the full sessionId (the transcript UUID) beats claude's short handle
                id=full,
                name='proj@g@agent',
                status='busy',
                cwd=Path('/work/proj'),
                summary=None,
                pid=4242,
                kind='interactive',
                started=datetime.fromtimestamp(1781247747055 / 1000),
            )
        ],
    )


def test_reported_tolerates_missing_fields(replace: Replacer) -> None:
    # a session without status/cwd (e.g. a foreground session) must not crash the listing;
    # status falls back to state, then '?'
    _registry(replace, '[{"sessionId": "lonely", "state": "working", "pid": 4242}]')
    compare(
        Claude().reported(),
        expected=[
            Session(
                id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None, pid=4242
            )
        ],
    )


def test_reported_marks_the_degraded_pidless_remnant_stale(replace: Replacer) -> None:
    # the degraded shape claude's registry reports briefly after a killed pid is pruned:
    # marked at the source (claude-registry knowledge), never silently dropped
    _registry(replace, '[{"kind": "background", "startedAt": 1781247747055, "name": "x"}]')
    compare(
        Claude().reported(),
        expected=[
            Session(
                id='?',
                name='x',
                status='?',
                cwd=Path('.'),
                summary=None,
                kind='background',
                started=datetime.fromtimestamp(1781247747055 / 1000),
                stale='no pid in the registry entry (degraded remnant)',
            )
        ],
    )
    compare(Claude().live(), expected=[])  # …and live() is what filters it


def _dead(pid: int, sig: int) -> None:
    raise ProcessLookupError


def _foreign(pid: int, sig: int) -> None:
    raise PermissionError


def test_live_filters_out_an_entry_whose_pid_has_died(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 999999}]')
    replace.in_module(os.kill, _dead, module=os)
    compare(Claude().live(worktree), expected=[])
    compare(  # checked() keeps the corpse, marked, for surfacing
        Claude().checked(worktree),
        expected=[
            Session(
                id='x',
                name='x',
                status='idle',
                cwd=Path('.'),
                summary=None,
                pid=999999,
                stale='claimed pid 999999 is not running',
            )
        ],
    )


def test_live_keeps_an_entry_whose_pid_belongs_to_another_user(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 1}]')
    replace.in_module(os.kill, _foreign, module=os)
    compare(
        Claude().live(worktree),
        expected=[Session(id='x', name='x', status='idle', cwd=Path('.'), summary=None, pid=1)],
    )


def _transcript(folder: Path, name: str, body: str, mtime: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text(body)
    os.utime(f, (mtime, mtime))
    return f


def test_session_summary_reads_newest_transcript_for_cwd(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    folder = projects / '-work-proj'  # munged from the cwd below
    _transcript(folder, 'old.jsonl', '{"type": "last-prompt", "lastPrompt": "stale"}\n', 1000)
    _transcript(
        folder,
        'live.jsonl',
        '{"type": "user", "message": "hi"}\n'
        '{"type": "last-prompt", "lastPrompt": "fix\\nthe   bug"}\n'
        '\n'  # blank lines are skipped (this one is reached first, in reverse)
        '{"type": "assistant", "message": "ok"}\n',
        2000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='fix the bug')


def test_session_summary_prefers_title_over_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n'
        '{"type": "ai-title", "aiTitle": "ai topic"}\n'
        '{"type": "custom-title", "customTitle": "my title"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='my title')


def test_session_summary_uses_ai_title_when_no_custom_title(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n'
        '{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='ai topic')


def test_session_summary_skips_title_equal_to_name(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        # Claude persists --name as a custom-title; it must not just echo the name.
        '{"type": "custom-title", "customTitle": "proj@goal@agent"}\n'
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'proj@goal@agent', projects), expected='fix the bug')


def test_session_summary_takes_latest_of_each_record(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "custom-title", "customTitle": "old name"}\n'
        '{"type": "custom-title", "customTitle": "new name"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='new name')


def test_session_summary_skips_typed_record_missing_its_value(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        # a last-prompt record may carry no lastPrompt field; fall through to what does
        '{"type": "last-prompt"}\n{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='ai topic')


def test_session_summary_when_no_folder(tmpdir: TempDir) -> None:
    assert session_summary('/work/proj', 'agent', tmpdir.path) is None


def test_session_summary_when_transcript_has_no_title_or_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(projects / '-work-proj', 'sess.jsonl', '{"type": "user", "message": "hi"}\n', 1000)
    assert session_summary('/work/proj', 'agent', projects) is None


def test_sessions_enriches_live_sessions_with_a_summary(tmpdir: TempDir, replace: Replacer) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj', 'a.jsonl', '{"type": "last-prompt", "lastPrompt": "do it"}\n', 1000
    )
    live = [
        Session(
            id='a', name='proj@goal@agent', status='busy', cwd=Path('/work/proj'), summary=None
        ),
        Session(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
    ]
    replace.on_class(Claude.live, lambda self, cwd=None: list(live))
    compare(
        Claude(projects).sessions(),
        expected=[
            Session(
                id='a',
                name='proj@goal@agent',
                status='busy',
                cwd=Path('/work/proj'),
                summary='do it',  # from the transcript; 'bare' has none to find
            ),
            Session(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
        ],
    )


def test_sessions_skips_enrichment_when_the_registry_had_no_cwd(replace: Replacer) -> None:
    # a cwd-less record parses to Path('.') — there is no transcript folder to read
    lonely = Session(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
    replace.on_class(Claude.live, lambda self, cwd=None: [lonely])
    compare(Claude().sessions(), expected=[lonely])
