from testfixtures import ShouldRaise, compare

from chimera.agents.registry import AGENTS, AgentSpec, UnknownHarnessError, resolve_spec
from chimera.config import AgentConfig


def test_defaults_when_nothing_is_set() -> None:
    compare(resolve_spec(None, None), expected=AgentSpec('claude', None))


def test_flags_beat_every_level() -> None:
    level = AgentConfig(harness='claude', model='configured')
    compare(resolve_spec('claude', 'flagged', level), expected=AgentSpec('claude', 'flagged'))


def test_nearest_level_wins_per_field() -> None:
    project = AgentConfig(model='sonnet')
    workspace = AgentConfig(harness='claude', model='opus')
    # harness comes from the workspace, model from the nearer project level
    compare(resolve_spec(None, None, project, workspace), expected=AgentSpec('claude', 'sonnet'))


def test_missing_levels_are_skipped() -> None:
    workspace = AgentConfig(model='opus')
    compare(resolve_spec(None, None, None, workspace), expected=AgentSpec('claude', 'opus'))


def test_unknown_harness_raises() -> None:
    with ShouldRaise(UnknownHarnessError('codex')):
        resolve_spec('codex', None)


def test_unknown_harness_from_config_raises() -> None:
    with ShouldRaise(UnknownHarnessError('goose')):
        resolve_spec(None, None, AgentConfig(harness='goose'))


def test_spec_resolves_its_agent() -> None:
    assert AgentSpec().agent is AGENTS['claude']
