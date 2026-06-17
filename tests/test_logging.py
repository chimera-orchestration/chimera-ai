import json
from typing import Any

import pytest
from loguru import logger
from testfixtures import Replacer, TempDir, compare

from chimera.commands.init import init
from chimera.logging import configure, log_action, log_path


@pytest.fixture()
def workspace(tmpdir: TempDir, replace: Replacer) -> TempDir:
    replace.in_environ('CHIMERA_WORKSPACE', str(init(tmpdir / 'ws')))
    # No typed helper fits an instance attribute; loguru keeps its handlers here.
    core = getattr(logger, '_core')
    replace(target=core.handlers, container=core, name='handlers', replacement={})
    return tmpdir


def _records(path: TempDir) -> list[Any]:
    log = log_path(path.path / 'ws')
    return [json.loads(line)['record'] for line in log.read_text().splitlines()]


def test_log_path(tmpdir: TempDir) -> None:
    compare(log_path(tmpdir.path), expected=tmpdir / 'logs' / 'chimera.jsonl')


def test_configure_writes_json(workspace: TempDir) -> None:
    configure()
    log_action('project ls', {'force': True})
    (record,) = _records(workspace)
    compare(
        {'level': record['level']['name'], 'message': record['message'], 'extra': record['extra']},
        expected={'level': 'INFO', 'message': 'project ls', 'extra': {'params': {'force': True}}},
    )


def test_configure_is_idempotent(workspace: TempDir) -> None:
    configure()
    configure()  # a second sink would duplicate every line
    log_action('project ls', {})
    compare(len(_records(workspace)), expected=1)


def test_configure_outside_a_workspace_keeps_the_default(tmpdir: TempDir) -> None:
    # cwd is the temp root and the env is cleared, so no workspace resolves: configure
    # must not raise and must leave loguru's existing sinks in place (better than none).
    core = getattr(logger, '_core')
    with Replacer() as replace:
        sentinel = {'kept': object()}
        replace(target=core.handlers, container=core, name='handlers', replacement=sentinel)
        configure()
        compare(core.handlers, expected=sentinel)
