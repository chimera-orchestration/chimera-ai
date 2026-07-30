import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from giterator.testing import Repo
from loguru import logger
from testfixtures import LogCapture, OutputCapture, Replacer, TempDir, compare
from testfixtures.mock import Mock

import chimera.logging
from chimera.archive import Archive, ArchiveSession
from chimera.commands.init import init
from chimera.context import seat
from chimera.identity import executor
from chimera.git import Git
from chimera.logging import (
    configure,
    log_failure,
    log_finish,
    log_path,
    log_start,
    log_user_error,
)
from tests.cli import general_capture

FUNC = 'chimera.commands.init.init'


@pytest.fixture()
def frozen_clock(replace: Replacer) -> None:
    replace.in_module(time.perf_counter, lambda: 0.0)  # every duration_ms becomes 0.0


@pytest.fixture()
def sink(tmpdir: TempDir, replace: Replacer) -> Path:
    """A real workspace with the file sink isolated: configure() writes here, and loguru's
    handlers and default extra (the caller identity) are restored afterwards so neither
    leaks into other tests."""
    ws = init(tmpdir / 'ws')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    core = getattr(logger, '_core')  # no typed helper fits an instance attribute
    replace(target=core.handlers, container=core, name='handlers', replacement={})
    replace(target=core.extra, container=core, name='extra', replacement={})
    return ws


def _start() -> dict[str, object]:
    return {'level': 'INFO', 'command': 'init', 'phase': 'start', 'function': FUNC, 'params': {}}


def test_log_path(tmpdir: TempDir) -> None:
    compare(log_path(tmpdir.path), expected=tmpdir / 'state' / 'log.jsonl')


class TestActionLines:
    def test_start_then_finish(self, full_logs: LogCapture, frozen_clock: None) -> None:
        log_finish('init', log_start('init', FUNC, {}))
        full_logs.check(
            _start(),
            {'level': 'INFO', 'command': 'init', 'phase': 'end', 'duration_ms': 0.0},
        )

    def test_crash_ends_at_error(self, full_logs: LogCapture, frozen_clock: None) -> None:
        # log_failure reads the live exception; error/traceback ride record['exception'], not
        # extra, so the extra-based capture sees only the ERROR end line — the file test pins them.
        started = log_start('init', FUNC, {})
        try:
            raise FileExistsError('/ws')
        except FileExistsError:
            log_failure('init', started)
        full_logs.check(
            _start(),
            {'level': 'ERROR', 'command': 'init', 'phase': 'end', 'duration_ms': 0.0},
        )

    def test_user_error_carries_the_message(
        self, full_logs: LogCapture, frozen_clock: None
    ) -> None:
        log_user_error('init', log_start('init', FUNC, {}), ValueError('bad path'))
        full_logs.check(
            _start(),
            {
                'level': 'ERROR',
                'command': 'init',
                'phase': 'end',
                'duration_ms': 0.0,
                'error': 'ValueError: bad path',
            },
        )


class TestDomainEvent:
    def test_a_bound_event_flows_through_whole(self, full_logs: LogCapture) -> None:
        # arbitrary logging anywhere: message + whatever it binds, no command/phase framing.
        logger.bind(git={'before': {'g/agent': 'abc'}, 'after': {}}, force=False).info(
            'worktree rm: refs'
        )
        full_logs.check(
            {
                'level': 'INFO',
                'git': {'before': {'g/agent': 'abc'}, 'after': {}},
                'force': False,
                'message': 'worktree rm: refs',
            }
        )


class TestGeneralCapture:
    def test_keeps_everything_but_duration(self, frozen_clock: None) -> None:
        with general_capture() as captured:
            log_finish('init', log_start('init', FUNC, {}))
            logger.bind(git={'before': {}, 'after': {}}).info('demo: refs')
        captured.check(
            _start(),
            {'level': 'INFO', 'command': 'init', 'phase': 'end'},  # duration_ms dropped
            {'level': 'INFO', 'git': {'before': {}, 'after': {}}, 'message': 'demo: refs'},
        )


NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class TestFileSink:
    def test_writes_a_flat_start_end_pair(self, sink: Path, frozen_clock: None) -> None:
        configure()
        log_finish('init', log_start('init', FUNC, {'path': '/ws'}))
        text = re.sub(r'"time": "[^"]*"', '"time": "<TIME>"', log_path(sink).read_text())
        compare(
            [json.loads(line) for line in text.splitlines()],
            expected=[
                {
                    'time': '<TIME>',
                    'pid': os.getpid(),
                    'command': 'init',
                    'level': 'INFO',
                    'caller': 'human',  # no session ran this…
                    'seat': '@@captain',  # …and cwd is neither a project nor a repo
                    'phase': 'start',
                    'function': FUNC,
                    'params': {'path': '/ws'},
                },
                {
                    'time': '<TIME>',
                    'pid': os.getpid(),
                    'command': 'init',
                    'level': 'INFO',
                    'caller': 'human',
                    'seat': '@@captain',
                    'phase': 'end',
                    'duration_ms': 0.0,
                },
            ],
        )

    def test_crash_end_line_carries_error_and_traceback(
        self, sink: Path, frozen_clock: None
    ) -> None:
        configure()
        started = log_start('init', FUNC, {})
        try:
            raise FileExistsError('/ws')
        except FileExistsError:
            log_failure('init', started)
        end = json.loads(log_path(sink).read_text().splitlines()[1])
        compare(
            {key: end[key] for key in ('level', 'phase', 'error')},
            expected={'level': 'ERROR', 'phase': 'end', 'error': 'FileExistsError: /ws'},
        )
        compare(end['traceback'].splitlines()[0], expected='Traceback (most recent call last):')

    def test_user_error_end_line_has_no_traceback(self, sink: Path, frozen_clock: None) -> None:
        configure()
        log_user_error('init', log_start('init', FUNC, {}), ValueError('nope'))
        end = json.loads(log_path(sink).read_text().splitlines()[1])
        compare(end['error'], expected='ValueError: nope')
        assert 'traceback' not in end

    def test_a_domain_event_writes_its_bound_context(self, sink: Path) -> None:
        configure()
        logger.bind(git={'before': {'g': 'a'}, 'after': {'g': 'b'}}).info('adopt: refs')
        event = json.loads(log_path(sink).read_text().splitlines()[0])
        compare(
            {key: event[key] for key in ('level', 'message', 'git')},
            expected={
                'level': 'INFO',
                'message': 'adopt: refs',
                'git': {'before': {'g': 'a'}, 'after': {'g': 'b'}},
            },
        )

    def test_configure_is_idempotent(self, sink: Path) -> None:
        configure()
        configure()  # a second sink would duplicate every line
        log_finish('init', log_start('init', FUNC, {}))
        compare(len(log_path(sink).read_text().splitlines()), expected=2)

    def test_a_session_attributes_every_line_to_itself(self, sink: Path, replace: Replacer) -> None:
        # the executor is proven, not assumed: the harness names the session, the archive
        # names its address, and only then does a line claim to be that agent's doing
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'uuid-1')
        with Archive.open(sink / 'state' / 'archive.db') as store:
            store.record_session(
                ArchiveSession(
                    platform='claude',
                    native_id='uuid-1',
                    status='startup',
                    started_at=NOON,
                    address='proj@g@agent',
                )
            )
        configure()
        logger.info('hello')
        line = json.loads(log_path(sink).read_text())
        compare(line['caller'], expected='proj@g@agent')
        compare(line['seat'], expected='@@captain')  # …while the seat is still the location

    def test_a_session_with_no_address_is_named_by_its_id(
        self, sink: Path, replace: Replacer
    ) -> None:
        # a hand-launched claude: something ran this and here is which conversation,
        # without implying a claim it doesn't hold
        replace.in_environ('CLAUDE_CODE_SESSION_ID', 'e4d0a1b2-0000-0000-0000-000000000000')
        with Archive.open(sink / 'state' / 'archive.db') as store:
            store.record_session(
                ArchiveSession(
                    platform='claude',
                    native_id='e4d0a1b2-0000-0000-0000-000000000000',
                    status='startup',
                    started_at=NOON,
                )
            )
        configure()
        logger.info('hello')
        compare(json.loads(log_path(sink).read_text())['caller'], expected='e4d0a1b2')

    def test_a_human_is_not_the_captain(self, sink: Path) -> None:
        # standing at the workspace root is how a human reads the captain's mail; it has
        # never been evidence of *being* the captain, and the log now says so
        configure()
        logger.info('hello')
        line = json.loads(log_path(sink).read_text())
        compare(line['caller'], expected='human')
        compare(line['seat'], expected='@@captain')

    def test_a_line_about_a_session_never_displaces_the_caller(self, sink: Path) -> None:
        configure()
        logger.bind(session='proj@g@agent').info('agent stop')  # the session acted *on*
        line = json.loads(log_path(sink).read_text())
        compare(line['caller'], expected='human')
        compare(line['session'], expected='proj@g@agent')

    def test_unresolvable_identity_still_logs(self, sink: Path, replace: Replacer) -> None:
        replace.in_module(executor, Mock(side_effect=RuntimeError('boom')), module=chimera.logging)
        replace.in_module(seat, Mock(side_effect=RuntimeError('boom')), module=chimera.logging)
        configure()
        logger.info('hello')
        line = json.loads(log_path(sink).read_text())
        assert 'caller' not in line
        assert 'seat' not in line
        compare(line['message'], expected='hello')

    def test_a_git_trace_line_lands_at_debug(self, sink: Path) -> None:
        configure()
        Git(sink)('rev-parse', '--git-dir')
        trace = json.loads(log_path(sink).read_text().splitlines()[0])
        del trace['time']
        compare(
            trace,
            expected={
                'pid': os.getpid(),
                'level': 'DEBUG',
                'caller': 'human',  # even the trace says who ran the command
                'seat': '@@captain',
                'message': 'git rev-parse --git-dir',
                'git_cwd': str(sink),
            },
        )


def test_configure_outside_a_workspace_goes_quiet(tmpdir: TempDir, replace: Replacer) -> None:
    # cwd is the temp root and the env is cleared, so no workspace resolves: configure must
    # not raise, and must still clear the sinks — loguru's default would otherwise spew the
    # DEBUG git trace at the console — so a git command emits nothing anywhere.
    core = getattr(logger, '_core')  # no typed helper fits an instance attribute
    replace(target=core.handlers, container=core, name='handlers', replacement={0: Mock()})
    repo = Repo.make(tmpdir / 'repo')
    with OutputCapture(separate=True) as output:
        configure()
        Git(repo.path)('status', '--porcelain')
    compare(core.handlers, expected={})
    output.compare(stderr='')
