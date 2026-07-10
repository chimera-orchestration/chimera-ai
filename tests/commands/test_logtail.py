import os
import shutil
import subprocess
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare
from testfixtures.mock import Mock

from chimera.commands.logtail import (
    FORMAT,
    FblogMissingError,
    NoLogError,
    fblog_args,
    logtail,
    pipeline,
    tail_args,
)
from chimera.logging import log_path
from tests.cli import Command, action_logs


def _log(workspace: Path) -> Path:
    log = log_path(workspace)
    log.parent.mkdir()
    log.write_text('{"time": "2026-07-10T00:00:00", "pid": 1, "level": "INFO"}\n')
    return log


def _fblog_present(replace: Replacer) -> None:
    replace.in_module(shutil.which, lambda name: f'/opt/homebrew/bin/{name}')


def _capture_pipeline(replace: Replacer, code: int = 0) -> Mock:
    run = Mock(return_value=code)
    replace.in_module(pipeline, run)
    return run


class TestArgs:
    def test_tail_follows(self) -> None:
        compare(
            tail_args(Path('/ws/logs/x.jsonl'), 20, True),
            expected=['tail', '-n', '20', '-F', '/ws/logs/x.jsonl'],
        )

    def test_tail_one_shot(self) -> None:
        compare(
            tail_args(Path('/ws/logs/x.jsonl'), 5, False),
            expected=['tail', '-n', '5', '/ws/logs/x.jsonl'],
        )

    def test_fblog_tuned_view(self) -> None:
        compare(fblog_args(False), expected=['fblog', '--main-line-format', FORMAT])

    def test_fblog_dump(self) -> None:
        compare(fblog_args(True), expected=['fblog', '-d'])


class TestLogtail:
    def test_runs_pipeline(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        log = _log(ws)
        _fblog_present(replace)
        run = _capture_pipeline(replace, code=3)
        compare(logtail(ws, lines=50, follow=False, dump=True), expected=3)
        run.assert_called_once_with(tail_args(log, 50, False), fblog_args(True))

    def test_missing_fblog(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_module(shutil.which, lambda name: None)
        with ShouldRaise(FblogMissingError()):
            logtail(tmpdir.makedir('lycia'))

    def test_nothing_logged_yet(self, tmpdir: TempDir, replace: Replacer) -> None:
        _fblog_present(replace)
        ws = tmpdir.makedir('lycia')
        with ShouldRaise(NoLogError(log_path(ws))):
            logtail(ws)


class TestPipeline:
    def test_streams_producer_through_consumer(self, capfd) -> None:
        compare(pipeline(['printf', 'a\nb\n'], ['cat']), expected=0)
        compare(capfd.readouterr().out, expected='a\nb\n')

    def test_returns_consumer_exit_code(self) -> None:
        compare(pipeline(['echo', 'x'], ['sh', '-c', 'exit 3']), expected=3)

    def test_ctrl_c_is_a_clean_exit(self, replace: Replacer) -> None:
        def interrupted(cmd, **kw):
            raise KeyboardInterrupt()

        replace.in_module(subprocess.run, interrupted)
        # the producer would run for a minute — the finally must reap it or this hangs
        compare(pipeline(['sleep', '60'], ['cat']), expected=0)


def test_cli(workspace: Path, replace: Replacer, command: Command) -> None:
    _log(workspace)
    log = log_path(workspace.resolve())  # the command resolves the workspace from cwd
    os.chdir(workspace)
    _fblog_present(replace)
    run = _capture_pipeline(replace)
    command.run('logtail').check(
        logging=action_logs(
            'logtail',
            'chimera.commands.logtail.logtail',
            {'lines': 20, 'follow': True, 'dump': False},
        ),
    )
    run.assert_called_once_with(tail_args(log, 20, True), fblog_args(False))


def test_cli_flags_and_exit_code(workspace: Path, replace: Replacer, command: Command) -> None:
    _log(workspace)
    log = log_path(workspace.resolve())
    os.chdir(workspace)
    _fblog_present(replace)
    run = _capture_pipeline(replace, code=2)
    command.run('logtail', '-n', '5', '--no-follow', '-d').check(
        return_code=2,
        logging=action_logs(
            'logtail',
            'chimera.commands.logtail.logtail',
            {'lines': 5, 'follow': False, 'dump': True},
        ),
    )
    run.assert_called_once_with(tail_args(log, 5, False), fblog_args(True))


def test_cli_missing_fblog(workspace: Path, replace: Replacer, command: Command) -> None:
    os.chdir(workspace)
    replace.in_module(shutil.which, lambda name: None)
    message = 'fblog is not installed — `brew install fblog` or `ch doctor --fix`'
    command.run('logtail').check(
        output=f'Error: {message}',
        return_code=1,
        logging=action_logs(
            'logtail',
            'chimera.commands.logtail.logtail',
            {'lines': 20, 'follow': True, 'dump': False},
            error=f'FblogMissingError: {message}',
        ),
    )
