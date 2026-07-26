import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from testfixtures import LogCapture, Replacer, compare

import chimera.__main__ as main
from chimera.commands.dump import dump
from tests.cli import Command, action_logs

type _Call = tuple[
    str | None, Path, int, int, list[dict[str, object]], list[str], dict[str, str], str | None
]


def _no_stdin(replace: Replacer) -> None:
    # a real terminal or piped hook payload is exercised by the pure-function tests below;
    # the CLI tests only need stdin to not block/error under pytest's own capture
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(''))


def test_dump_returns_and_logs_the_snapshot(full_logs: LogCapture) -> None:
    record = dump(
        'PreToolUse',
        Path('/ws/proj/worktrees/g@agent'),
        123,
        45,
        [{'pid': 45, 'name': 'sh'}, {'pid': 9, 'name': 'claude'}],
        ['ch', 'dump', 'PreToolUse'],
        {'CHIMERA_ROLE': 'agent'},
        '{"tool_name": "Bash"}',
    )
    compare(
        record,
        expected={
            'cwd': '/ws/proj/worktrees/g@agent',
            'pid': 123,
            'ppid': 45,
            'ancestry': [{'pid': 45, 'name': 'sh'}, {'pid': 9, 'name': 'claude'}],
            'argv': ['ch', 'dump', 'PreToolUse'],
            'env': {'CHIMERA_ROLE': 'agent'},
            'stdin': '{"tool_name": "Bash"}',
        },
    )
    full_logs.check({'level': 'INFO', 'message': 'dump: PreToolUse', **record})


def test_dump_with_no_stdin(full_logs: LogCapture) -> None:
    record = dump('manual check', Path('/ws'), 1, 0, [], ['ch', 'dump'], {}, None)
    compare(record['stdin'], expected=None)
    full_logs.check({'level': 'INFO', 'message': 'dump: manual check', **record})


def test_dump_with_no_context(full_logs: LogCapture) -> None:
    record = dump(None, Path('/ws'), 1, 0, [], ['ch', 'dump'], {}, None)
    full_logs.check({'level': 'INFO', 'message': 'dump', **record})


def test_cli_wires_the_real_process_snapshot_through(
    workspace: Path, replace: Replacer, command: Command
) -> None:
    # proves the wrapper's own job — gathering cwd/pid/ppid/ancestry/argv/env/stdin and
    # passing them through — rather than trusting placeholders; the pure function's own
    # behaviour (logging, return value) is already pinned exactly by the tests above
    os.chdir(workspace)
    _no_stdin(replace)
    replace(
        target=main._process_ancestry,
        container=main,
        name='_process_ancestry',
        replacement=lambda pid: [{'pid': pid, 'name': 'stub'}],
    )
    calls: list[_Call] = []

    def record(
        context: str | None,
        cwd: Path,
        pid: int,
        ppid: int,
        ancestry: Sequence[Mapping[str, object]],
        argv: Sequence[str],
        env: Mapping[str, str],
        stdin: str | None,
    ) -> dict[str, object]:
        calls.append(
            (
                context,
                cwd,
                pid,
                ppid,
                [dict(entry) for entry in ancestry],
                list(argv),
                dict(env),
                stdin,
            )
        )
        return {}

    replace(target=dump, container=main, name='_dump', replacement=record)
    expected_env = dict(os.environ)
    command.run('dump', 'manual check').check(
        output='dumped: manual check',
        logging=action_logs(
            'dump', 'chimera.commands.dump.dump', {'context': 'manual check', 'stdout': False}
        ),
    )
    [(context, cwd, pid, ppid, ancestry, argv, env, stdin)] = calls
    compare(context, expected='manual check')
    compare(cwd, expected=Path.cwd())
    compare(pid, expected=os.getpid())
    compare(ppid, expected=os.getppid())
    compare(ancestry, expected=[{'pid': pid, 'name': 'stub'}])
    compare(argv[1:], expected=['dump', 'manual check'])  # [0] is the harness's own program name
    compare(env, expected=expected_env)
    compare(stdin, expected='')


def test_cli_context_is_optional(workspace: Path, replace: Replacer, command: Command) -> None:
    os.chdir(workspace)
    _no_stdin(replace)
    calls: list[str | None] = []
    replace(
        target=dump,
        container=main,
        name='_dump',
        replacement=lambda context, *_a, **_kw: (calls.append(context), {})[1],
    )
    command.run('dump').check(
        output='dumped',
        logging=action_logs(
            'dump', 'chimera.commands.dump.dump', {'context': None, 'stdout': False}
        ),
    )
    compare(calls, expected=[None])


def test_cli_stdout_prints_whatever_dump_returns(
    workspace: Path, replace: Replacer, command: Command
) -> None:
    os.chdir(workspace)
    _no_stdin(replace)
    replace(target=dump, container=main, name='_dump', replacement=lambda *_a, **_kw: {'a': 1})
    command.run('dump', 'manual check', '--stdout').check(
        output=json.dumps({'a': 1}, indent=2),
        logging=action_logs(
            'dump', 'chimera.commands.dump.dump', {'context': 'manual check', 'stdout': True}
        ),
    )
