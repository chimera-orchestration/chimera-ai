import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare

from chimera.agent_env import ROLE_CAPTAIN, role_env
from chimera.agents import Session
from chimera.agents.claude import (
    READONLY_TOOLS,
    Claude,
    _print_args,
    _session_args,
    session_summary,
)


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


def _launched(replace: Replacer) -> dict[str, object]:
    """Stub the launch subprocess, capturing the argv and env it would run with."""
    captured: dict[str, object] = {}

    def fake_run(
        cmd: list[str],
        cwd: Path | None = None,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        captured['cmd'], captured['env'] = cmd, env
        return SimpleNamespace(returncode=0)

    replace.in_module(subprocess.run, fake_run)
    return captured


def test_launch_overlays_env_with_the_overlay_winning(tmpdir: TempDir, replace: Replacer) -> None:
    # the launching session's own role must never leak into the child it launches
    replace.in_environ('CHIMERA_ROLE', 'captain')
    captured = _launched(replace)
    Claude().start(tmpdir.makedir('wt'), 'n', env={'CHIMERA_ROLE': 'agent'}, exclusive=False)
    compare(captured['env'], expected={**os.environ, 'CHIMERA_ROLE': 'agent'})


def test_launch_clears_a_stale_scope_for_an_unscoped_stamp(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # e.g. a shell inside an agent session launches the captain: the inherited scope must
    # be cleared by the overlay, not survive it — an unfenced session reporting itself
    # fenced would be wrong ('' reads as unset, see agent_env.role_scope)
    replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj@g')
    captured = _launched(replace)
    Claude().start(tmpdir.makedir('wt'), 'n', env=role_env(ROLE_CAPTAIN), exclusive=False)
    compare(
        captured['env'],
        expected={**os.environ, 'CHIMERA_ROLE': 'captain', 'CHIMERA_ROLE_SCOPE': ''},
    )


def test_launch_without_overlay_inherits_the_environment(
    tmpdir: TempDir, replace: Replacer
) -> None:
    captured = _launched(replace)
    Claude().resume(tmpdir.makedir('wt'), 'n', exclusive=False)
    assert captured['env'] is None  # subprocess inherits the parent environment wholesale


_ENVELOPE = (
    '{"type": "result", "result": "the report", "session_id": "abc-123",'
    ' "total_cost_usd": 0.014, "duration_ms": 1969}'
)

_READONLY = f'--allowedTools={",".join(READONLY_TOOLS)}'


def _printed(
    replace: Replacer, stdout: str = _ENVELOPE, raises: Exception | None = None
) -> dict[str, object]:
    """Stub the print-mode subprocess, capturing the argv, cwd, timeout and env."""
    captured: dict[str, object] = {}

    def fake_run(
        cmd: list[str],
        cwd: Path | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        captured.update(cmd=cmd, cwd=cwd, timeout=timeout, env=env)
        if raises is not None:
            raise raises
        return SimpleNamespace(stdout=stdout, returncode=0)

    replace.in_module(subprocess.run, fake_run)
    return captured


class TestRun:
    def test_readonly_argv_result_and_log(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        worktree = tmpdir.makedir('wt')
        captured = _printed(replace)
        compare(
            Claude().run(worktree, 'proj@errand-abc123@agent', 'report on X'), expected='the report'
        )
        compare(
            captured['cmd'],
            expected=['claude', '-p', '--output-format', 'json', _READONLY, 'report on X'],
        )
        compare(captured['cwd'], expected=worktree)
        assert captured['env'] is None  # no overlay: the parent environment, wholesale
        full_logs.check(
            {
                'level': 'INFO',
                'message': 'errand: run',
                'session': 'proj@errand-abc123@agent',
                'session_id': 'abc-123',
                'cost_usd': 0.014,
                'duration_ms': 1969,
            }
        )

    def test_writable_run_carries_no_tool_wall(self, tmpdir: TempDir, replace: Replacer) -> None:
        captured = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', readonly=False)
        compare(captured['cmd'], expected=['claude', '-p', '--output-format', 'json', 'p'])

    def test_model_and_context_lead_matches_a_live_session(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        captured = _printed(replace)
        ctx = tmpdir / 'ctx.md'
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', model='opus', context=ctx)
        compare(
            captured['cmd'],
            expected=[
                'claude',
                '-p',
                '--output-format',
                'json',
                '--model',
                'opus',
                '--append-system-prompt-file',
                str(ctx),
                _READONLY,
                'p',
            ],
        )

    def test_extra_model_beats_the_spec_model(self, tmpdir: TempDir, replace: Replacer) -> None:
        captured = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', ['--model=sonnet'], model='opus')
        compare(
            captured['cmd'],
            expected=['claude', '-p', '--output-format', 'json', _READONLY, '--model=sonnet', 'p'],
        )

    def test_env_overlay_wins_over_the_parent(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'captain')
        captured = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', env={'CHIMERA_ROLE': 'agent'})
        compare(captured['env'], expected={**os.environ, 'CHIMERA_ROLE': 'agent'})

    def test_timeout_is_passed_and_raises_through(self, tmpdir: TempDir, replace: Replacer) -> None:
        captured = _printed(replace, raises=subprocess.TimeoutExpired(['claude'], 5))
        with ShouldRaise(subprocess.TimeoutExpired):
            Claude().run(tmpdir.makedir('wt'), 'n', 'p', timeout=5)
        compare(captured['timeout'], expected=5)

    def test_nonzero_exit_logs_stderr_then_raises(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        error = subprocess.CalledProcessError(2, ['claude'], stderr='Invalid API key\n')
        _printed(replace, raises=error)
        worktree = tmpdir.makedir('wt')
        with ShouldRaise(error):
            Claude().run(worktree, 'n', 'p')
        # the exception's own text omits stderr, so this line is the only record of it
        full_logs.check(
            {
                'level': 'ERROR',
                'message': 'errand: run failed',
                'session': 'n',
                'cwd': str(worktree),
                'returncode': 2,
                'stderr': 'Invalid API key',
            }
        )

    def test_failure_stderr_is_trimmed_to_its_tail(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        error = subprocess.CalledProcessError(1, ['claude'], stderr='x' * 5000 + ' the cause')
        _printed(replace, raises=error)
        with ShouldRaise(error):
            Claude().run(tmpdir.makedir('wt'), 'n', 'p')
        [entry] = full_logs.actual()
        stderr = entry['stderr']
        assert isinstance(stderr, str)
        compare(len(stderr), expected=4000)
        assert stderr.endswith(' the cause')

    def test_unparseable_envelope_degrades_to_raw_stdout(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        _printed(replace, stdout='not json at all')
        compare(Claude().run(tmpdir.makedir('wt'), 'n', 'p'), expected='not json at all')
        full_logs.check(
            {
                'level': 'WARNING',
                'message': 'errand: run envelope did not parse, raw stdout',
                'session': 'n',
            }
        )

    def test_envelope_without_a_result_field_degrades_too(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        _printed(replace, stdout='{"type": "result"}')
        compare(Claude().run(tmpdir.makedir('wt'), 'n', 'p'), expected='{"type": "result"}')
        full_logs.check(
            {
                'level': 'WARNING',
                'message': 'errand: run envelope did not parse, raw stdout',
                'session': 'n',
            }
        )


def test_print_args_defaults() -> None:
    compare(
        _print_args((), None, None, True), expected=['-p', '--output-format', 'json', _READONLY]
    )


def test_session_args_passthrough_model_beats_spec_model() -> None:
    compare(
        _session_args(['--name', 'n'], None, ['--model', 'sonnet'], False, model='opus'),
        expected=['--name', 'n', '--model', 'sonnet'],
    )


def test_session_args_passthrough_model_equals_form_beats_spec_model() -> None:
    compare(
        _session_args(['--name', 'n'], None, ['--model=sonnet'], False, model='opus'),
        expected=['--name', 'n', '--model=sonnet'],
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


def test_sessions_enriches_checked_sessions_with_a_summary(
    tmpdir: TempDir, replace: Replacer
) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj', 'a.jsonl', '{"type": "last-prompt", "lastPrompt": "do it"}\n', 1000
    )
    checked = [
        Session(
            id='a', name='proj@goal@agent', status='busy', cwd=Path('/work/proj'), summary=None
        ),
        Session(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
        Session(  # a marked corpse rides through enrichment, mark intact — never dropped
            id='ghost', name='ghost', status='?', cwd=Path('/gone'), summary=None, stale='dead pid'
        ),
    ]
    replace.on_class(Claude.checked, lambda self, cwd=None: list(checked))
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
            Session(
                id='ghost',
                name='ghost',
                status='?',
                cwd=Path('/gone'),
                summary=None,
                stale='dead pid',
            ),
        ],
    )


def test_sessions_skips_enrichment_when_the_registry_had_no_cwd(replace: Replacer) -> None:
    # a cwd-less record parses to Path('.') — there is no transcript folder to read
    lonely = Session(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
    replace.on_class(Claude.checked, lambda self, cwd=None: [lonely])
    compare(Claude().sessions(), expected=[lonely])
