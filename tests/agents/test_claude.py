import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from testfixtures import Replacer, TempDir, compare

from chimera.agents import Session
from chimera.agents.claude import Claude, all_sessions, live_sessions, session_summary


def test_live_sessions_queries_claude_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    captured: dict[str, object] = {}
    pid = os.getpid()

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout=f'[{{"sessionId": "x", "status": "idle", "pid": {pid}}}]')

    replace.in_module(subprocess.run, fake_run)
    compare(live_sessions(worktree), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': pid}])
    compare(captured['cmd'], expected=['claude', 'agents', '--json', '--cwd', str(worktree)])


def test_all_sessions_queries_claude_unscoped(replace: Replacer) -> None:
    captured: dict[str, object] = {}
    pid = os.getpid()

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout=f'[{{"sessionId": "x", "status": "idle", "pid": {pid}}}]')

    replace.in_module(subprocess.run, fake_run)
    compare(all_sessions(), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': pid}])
    compare(captured['cmd'], expected=['claude', 'agents', '--json'])  # no --cwd → every project


def _dead(pid: int, sig: int) -> None:
    raise ProcessLookupError


def _foreign(pid: int, sig: int) -> None:
    raise PermissionError


def test_sessions_filters_out_an_entry_whose_pid_has_died(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"sessionId": "x", "status": "idle", "pid": 999999}]'
        ),
    )
    replace.in_module(os.kill, _dead, module=os)
    compare(live_sessions(worktree), expected=[])


def test_sessions_filters_out_an_entry_with_no_pid_at_all(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"kind": "background", "startedAt": 1781247747055, "name": "x"}]'
        ),
    )  # the degraded shape claude's registry reports briefly after a killed pid is pruned
    compare(live_sessions(worktree), expected=[])


def test_sessions_keeps_an_entry_whose_pid_belongs_to_another_user(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"sessionId": "x", "status": "idle", "pid": 1}]'
        ),
    )
    replace.in_module(os.kill, _foreign, module=os)
    compare(live_sessions(worktree), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': 1}])


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


def test_sessions_enriches_with_name_cwd_and_summary(tmpdir: TempDir, replace: Replacer) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj', 'a.jsonl', '{"type": "last-prompt", "lastPrompt": "do it"}\n', 1000
    )
    full = 'abc12345-9f80-4c8e-b3d7-1234567890ab'
    replace.in_module(
        all_sessions,
        lambda: [
            # the full sessionId (the transcript UUID) beats claude's short handle
            {
                'id': full[:8],
                'sessionId': full,
                'status': 'busy',
                'name': 'proj@goal@agent',
                'cwd': '/work/proj',
            },
            {'sessionId': 'bare', 'status': 'idle', 'cwd': '/elsewhere'},  # no name, no transcript
        ],
    )
    compare(
        Claude(projects).sessions(),
        expected=[
            Session(
                id=full,
                name='proj@goal@agent',
                status='busy',
                cwd=Path('/work/proj'),
                summary='do it',
            ),
            Session(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
        ],
    )


def test_sessions_tolerates_sessions_missing_fields(replace: Replacer) -> None:
    replace.in_module(
        all_sessions,
        # a session without status/cwd (e.g. a foreground session) must not crash the listing;
        # status falls back to state, then '?', and a missing cwd yields no summary
        lambda: [{'sessionId': 'lonely', 'state': 'working'}],
    )
    compare(
        Claude().sessions(),
        expected=[
            Session(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
        ],
    )
