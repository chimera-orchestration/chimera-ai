import os
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import pytest
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.popen import MockPopen

from chimera.agent_env import ROLE_CAPTAIN, role_env
from chimera.agents import Agent, Session
from chimera.agents.claude import (
    ENDED_STATES,
    READONLY_TOOLS,
    Claude,
    _print_args,
    _session_args,
    _warn_missing_binary,
    job_parked,
    session_summary,
)
from chimera.config import UserError


def _registry(replace: Replacer, payload: str) -> MockPopen:
    """Stub the ``claude agents --json`` subprocess with a fixed payload."""
    Popen = MockPopen()
    replace.in_module(subprocess.Popen, Popen)
    Popen.set_default(stdout=payload.encode())
    return Popen


def test_reported_queries_claude_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    Popen = _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 4242}]')
    compare(
        Claude().reported(worktree),
        expected=[Session(id='x', name='x', status='idle', cwd=Path('.'), summary=None, pid=4242)],
    )
    compare(
        Popen.all_calls[0].args[0],
        expected=['claude', 'agents', '--json', '--cwd', str(worktree)],
    )


def test_reported_unscoped_queries_everywhere(replace: Replacer) -> None:
    Popen = _registry(replace, '[{"sessionId": "x", "status": "idle", "pid": 4242}]')
    compare(
        Claude().reported(),
        expected=[Session(id='x', name='x', status='idle', cwd=Path('.'), summary=None, pid=4242)],
    )
    # no --cwd → every project
    compare(Popen.all_calls[0].args[0], expected=['claude', 'agents', '--json'])


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


def test_reported_marks_the_degraded_pidless_remnant_stale(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # the degraded shape claude's registry reports briefly after a killed pid is pruned:
    # marked at the source (claude-registry knowledge), never silently dropped
    _registry(replace, '[{"kind": "background", "startedAt": 1781247747055, "name": "x"}]')
    jobs = tmpdir.makedir('jobs')
    compare(
        Claude(jobs=jobs).reported(),
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
    compare(Claude(jobs=jobs).live(), expected=[])  # …and live() is what filters it


# A parked entry as claude's registry reports it: pid gone, the daemon's stale claim left.
PARKED_RECORD = (
    '[{"id": "ab12cd34", "sessionId": "ab12cd34-e776-4059-b67f-b3b9bb70b85e",'
    ' "state": "blocked", "kind": "background", "name": "proj@g@agent", "cwd": "/work"}]'
)


def _job(tmpdir: TempDir, state: dict[str, object], job_id: str = 'ab12cd34') -> Path:
    """A daemon jobs tree holding one job's ``state.json``; returns the jobs root."""
    tmpdir.dump(f'jobs/{job_id}/state.json', state)
    return tmpdir / 'jobs'


def test_reported_marks_a_revivable_pidless_job_parked(tmpdir: TempDir, replace: Replacer) -> None:
    _registry(replace, PARKED_RECORD)
    jobs = _job(tmpdir, {'state': 'blocked', 'respawnFlags': ['--name', 'proj@g@agent']})
    harness = Claude(jobs=jobs)
    compare(
        harness.reported(),
        expected=[
            Session(
                id='ab12cd34-e776-4059-b67f-b3b9bb70b85e',
                name='proj@g@agent',
                # the registry's own 'blocked' describes a worker that no longer exists
                status='parked',
                cwd=Path('/work'),
                summary=None,
                kind='background',
                parked=True,
            )
        ],
    )
    # parked still owns its worktree: live() keeps it, so launch guards and sweeps see it
    compare([session.parked for session in harness.live()], expected=[True])


def test_reported_ended_job_is_stale_not_parked(tmpdir: TempDir, replace: Replacer) -> None:
    # a done/stopped/failed job still carries respawnFlags — the state is what ends it
    _registry(replace, PARKED_RECORD)
    jobs = _job(tmpdir, {'state': 'done', 'respawnFlags': ['--name', 'proj@g@agent']})
    (session,) = Claude(jobs=jobs).reported()
    assert not session.parked
    compare(session.stale, expected='no pid in the registry entry (degraded remnant)')


def test_reported_job_without_a_respawn_recipe_is_stale(tmpdir: TempDir, replace: Replacer) -> None:
    _registry(replace, PARKED_RECORD)
    (session,) = Claude(jobs=_job(tmpdir, {'state': 'blocked'})).reported()
    assert not session.parked
    assert session.stale is not None


class TestJobParked:
    def test_parked(self, tmpdir: TempDir) -> None:
        assert job_parked(_job(tmpdir, {'state': 'blocked', 'respawnFlags': ['-x']}), 'ab12cd34')

    def test_working_job_also_counts(self, tmpdir: TempDir) -> None:
        # classification is only consulted for pid-less registry entries, so a 'working'
        # state here means the worker was reaped mid-flight — still revivable
        assert job_parked(_job(tmpdir, {'state': 'working', 'respawnFlags': ['-x']}), 'ab12cd34')

    @pytest.mark.parametrize('state', sorted(ENDED_STATES))
    def test_ended_states_are_dead(self, tmpdir: TempDir, state: str) -> None:
        assert not job_parked(_job(tmpdir, {'state': state, 'respawnFlags': ['-x']}), 'ab12cd34')

    def test_empty_respawn_recipe_is_dead(self, tmpdir: TempDir) -> None:
        assert not job_parked(_job(tmpdir, {'state': 'blocked', 'respawnFlags': []}), 'ab12cd34')

    def test_missing_job_dir_is_dead(self, tmpdir: TempDir) -> None:
        assert not job_parked(tmpdir.makedir('jobs'), 'ab12cd34')

    def test_unparseable_state_file_is_dead(self, tmpdir: TempDir) -> None:
        tmpdir.write('jobs/ab12cd34/state.json', 'not json')
        assert not job_parked(tmpdir / 'jobs', 'ab12cd34')


def _raising(error: Exception):
    """A MockPopen ``behaviour`` callable that raises ``error`` at construction time —
    the same point a real ``Popen`` would raise (e.g. binary not found)."""

    def behaviour(command: str, stdin: object) -> NoReturn:
        raise error

    return behaviour


@pytest.fixture()
def missing_binary(replace: Replacer) -> Iterator[None]:
    """A machine with no claude at all: the registry subprocess can't even spawn."""
    Popen = MockPopen()
    replace.in_module(subprocess.Popen, Popen)
    Popen.set_default(
        behaviour=_raising(FileNotFoundError(2, 'No such file or directory', 'claude'))
    )
    _warn_missing_binary.cache_clear()  # the once-per-process guard, reset either side
    yield
    _warn_missing_binary.cache_clear()


def test_reported_without_the_binary_is_no_sessions(
    missing_binary: None, full_logs: LogCapture
) -> None:
    compare(Claude().reported(), expected=[])
    full_logs.check(
        {'level': 'WARNING', 'message': 'agent: claude binary not found, reporting no sessions'}
    )


def test_missing_binary_warns_once_per_process(
    tmpdir: TempDir, missing_binary: None, full_logs: LogCapture
) -> None:
    harness = Claude()
    compare(harness.reported(), expected=[])
    compare(harness.reported(tmpdir.makedir('wt')), expected=[])  # a sweep asks per worktree…
    [entry] = full_logs.actual()  # …but the log gets one line, not one per call
    compare(entry['message'], expected='agent: claude binary not found, reporting no sessions')


def test_liveness_tiers_compose_over_the_missing_binary(missing_binary: None) -> None:
    compare(Claude().checked(), expected=[])
    compare(Claude().live(), expected=[])


def test_reported_with_a_failing_binary_still_raises(replace: Replacer) -> None:
    # present-but-broken is not "not installed": that needs attention, never an empty answer
    error = subprocess.CalledProcessError(1, ['claude', 'agents', '--json'])
    Popen = MockPopen()
    replace.in_module(subprocess.Popen, Popen)
    Popen.set_default(behaviour=_raising(error))
    with ShouldRaise(error):
        Claude().reported()


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


def _launched(replace: Replacer) -> MockPopen:
    """Stub the launch subprocess; the argv/env it ran with lands in ``Popen.all_calls``."""
    Popen = MockPopen()
    replace.in_module(subprocess.Popen, Popen)
    Popen.set_default()
    return Popen


def test_launch_overlays_env_with_the_overlay_winning(tmpdir: TempDir, replace: Replacer) -> None:
    # the launching session's own role must never leak into the child it launches
    replace.in_environ('CHIMERA_ROLE', 'captain')
    Popen = _launched(replace)
    Claude().start(tmpdir.makedir('wt'), 'n', env={'CHIMERA_ROLE': 'agent'}, exclusive=False)
    compare(Popen.all_calls[0].kwargs['env'], expected={**os.environ, 'CHIMERA_ROLE': 'agent'})


def test_launch_clears_a_stale_scope_for_an_unscoped_stamp(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # e.g. a shell inside an agent session launches the captain: the inherited scope must
    # be cleared by the overlay, not survive it — an unfenced session reporting itself
    # fenced would be wrong ('' reads as unset, see agent_env.role_scope)
    replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj@g')
    Popen = _launched(replace)
    Claude().start(tmpdir.makedir('wt'), 'n', env=role_env(ROLE_CAPTAIN), exclusive=False)
    compare(
        Popen.all_calls[0].kwargs['env'],
        expected={**os.environ, 'CHIMERA_ROLE': 'captain', 'CHIMERA_ROLE_SCOPE': ''},
    )


def test_launch_without_overlay_inherits_the_environment(
    tmpdir: TempDir, replace: Replacer
) -> None:
    Popen = _launched(replace)
    Claude().resume(tmpdir.makedir('wt'), 'n', exclusive=False)
    assert Popen.all_calls[0].kwargs['env'] is None  # inherits the parent environment wholesale


_ENVELOPE = (
    '{"type": "result", "result": "the report", "session_id": "abc-123",'
    ' "total_cost_usd": 0.014, "duration_ms": 1969}'
)

_READONLY = f'--allowedTools={",".join(READONLY_TOOLS)}'


def _printed(replace: Replacer, stdout: str = _ENVELOPE) -> MockPopen:
    """Stub the print-mode subprocess; the argv/cwd/env it ran with lands in ``all_calls``."""
    Popen = MockPopen()
    replace.in_module(subprocess.Popen, Popen)
    Popen.set_default(stdout=stdout.encode())
    return Popen


class TestRun:
    def test_readonly_argv_result_and_log(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        worktree = tmpdir.makedir('wt')
        Popen = _printed(replace)
        compare(
            Claude().run(worktree, 'proj@errand-abc123@agent', 'report on X'), expected='the report'
        )
        root = Popen.all_calls[0]
        compare(
            root.args[0],
            expected=['claude', '-p', '--output-format', 'json', _READONLY, 'report on X'],
        )
        compare(root.kwargs['cwd'], expected=worktree)
        assert root.kwargs['env'] is None  # no overlay: the parent environment, wholesale
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
        Popen = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', readonly=False)
        compare(
            Popen.all_calls[0].args[0], expected=['claude', '-p', '--output-format', 'json', 'p']
        )

    def test_model_and_context_lead_matches_a_live_session(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        Popen = _printed(replace)
        ctx = tmpdir / 'ctx.md'
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', model='opus', context=ctx)
        compare(
            Popen.all_calls[0].args[0],
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
        Popen = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', ['--model=sonnet'], model='opus')
        compare(
            Popen.all_calls[0].args[0],
            expected=['claude', '-p', '--output-format', 'json', _READONLY, '--model=sonnet', 'p'],
        )

    def test_env_overlay_wins_over_the_parent(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'captain')
        Popen = _printed(replace)
        Claude().run(tmpdir.makedir('wt'), 'n', 'p', env={'CHIMERA_ROLE': 'agent'})
        compare(Popen.all_calls[0].kwargs['env'], expected={**os.environ, 'CHIMERA_ROLE': 'agent'})

    def test_timeout_is_passed_and_raises_through(self, tmpdir: TempDir, replace: Replacer) -> None:
        # TimeoutExpired can only be raised from Popen.communicate(), a point MockPopen's
        # behaviour hook (resolved once, at construction) can't reach — a real subprocess.run
        # stub is the only way to simulate it.
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], *, timeout: float | None = None, **kw: object):
            captured['timeout'] = timeout
            raise subprocess.TimeoutExpired(cmd, timeout or 0)

        replace.in_module(subprocess.run, fake_run)
        with ShouldRaise(subprocess.TimeoutExpired):
            Claude().run(tmpdir.makedir('wt'), 'n', 'p', timeout=5)
        compare(captured['timeout'], expected=5)

    def test_nonzero_exit_logs_stderr_then_raises(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        error = subprocess.CalledProcessError(2, ['claude'], stderr='Invalid API key\n')
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(behaviour=_raising(error))
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
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(behaviour=_raising(error))
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


def _bg_session(kind: str | None = 'background', pid: int = 4242) -> Session:
    return Session(
        id='1ff2c8e3-54c6-4afe-9b24-f1c40d360770',
        name='proj@g@agent',
        status='busy',
        cwd=Path('/wt'),
        summary=None,
        pid=pid,
        kind=kind,
    )


class TestStop:
    # a bare SIGTERM to a --bg session's pid is respawned by claude's own supervisor
    # (verified live) — a background session must go through `claude stop <short-id>`
    # instead; the short id (not the full sessionId UUID) is what `claude stop` accepts.

    def test_background_session_stops_via_claude_stop_by_short_id(
        self, replace: Replacer, full_logs: LogCapture
    ) -> None:
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(returncode=0)
        session = _bg_session()
        Claude().stop(session)
        compare(Popen.all_calls[0].args[0], expected=['claude', 'stop', '1ff2c8e3'])
        full_logs.check(
            {
                'level': 'INFO',
                'message': 'agent stop',
                'session': 'proj@g@agent',
                'id': '1ff2c8e3',
            }
        )

    def test_background_session_stop_failure_raises(self, replace: Replacer) -> None:
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(returncode=1, stderr=b"No job matching '1ff2c8e3'.\n")
        with ShouldRaise(
            UserError(
                "proj@g@agent (job 1ff2c8e3): `claude stop` failed: No job matching '1ff2c8e3'."
            )
        ):
            Claude().stop(_bg_session())

    def test_interactive_session_falls_back_to_the_base_sigterm(self, replace: Replacer) -> None:
        calls: list[tuple[Session, float]] = []
        replace.on_class(
            Agent.stop, lambda self, session, timeout=10.0: calls.append((session, timeout))
        )
        session = _bg_session(kind='interactive')
        Claude().stop(session, timeout=5.0)
        compare(calls, expected=[(session, 5.0)])

    def test_session_with_no_kind_also_falls_back(self, replace: Replacer) -> None:
        # a plain foreground launch reports no `kind` at all, not just 'interactive'
        calls: list[Session] = []
        replace.on_class(Agent.stop, lambda self, session, timeout=10.0: calls.append(session))
        session = _bg_session(kind=None)
        Claude().stop(session)
        compare(calls, expected=[session])

    def test_parked_session_stops_via_the_daemon_despite_no_pid(self, replace: Replacer) -> None:
        # parked = worker reaped: nothing to SIGTERM, but `claude stop` acts on the job record
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(returncode=0)
        session = Session(
            id='1ff2c8e3-54c6-4afe-9b24-f1c40d360770',
            name='proj@g@agent',
            status='parked',
            cwd=Path('/wt'),
            summary=None,
            kind='background',
            parked=True,
        )
        Claude().stop(session)
        compare(Popen.all_calls[0].args[0], expected=['claude', 'stop', '1ff2c8e3'])


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
