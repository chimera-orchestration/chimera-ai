from dataclasses import replace as replace_field
from pathlib import Path

from testfixtures import Replacer, compare

import os

from chimera import agents
from chimera.agents import Agent, AgentSession, _distrusted
from chimera.agents.claude import Claude
from chimera.processes import process_create_time


def test_short_is_the_leading_block_of_a_full_id() -> None:
    compare(
        AgentSession('abc12345-9f80-4c8e', 'n', 'idle', Path('/w'), None).short, expected='abc12345'
    )
    compare(AgentSession('abc', 'n', 'idle', Path('/w'), None).short, expected='abc')


def test_detail_falls_back_to_tilde_cwd(replace: Replacer) -> None:
    replace.on_class(Path.home, lambda cls: Path('/home/me'))
    compare(
        AgentSession('i', 'n', 'idle', Path('/home/me/work'), 'a prompt').detail,
        expected='a prompt',
    )
    compare(AgentSession('i', 'n', 'idle', Path('/home/me/work'), None).detail, expected='~/work')
    compare(AgentSession('i', 'n', 'idle', Path('/other'), None).detail, expected='/other')


def _dead(pid: int, sig: int) -> None:
    raise ProcessLookupError


def test_a_pidless_claim_stands_on_the_adapters_word() -> None:
    # a server-backed harness may have no pid to claim — nothing to disprove
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=None)
    compare(_distrusted(session), expected=session)


def test_a_claimed_live_pid_verifies_and_gains_its_creation_time() -> None:
    # the claim carries no creation time, so the probe supplies one: whoever captures
    # this session can pair-match it later, which is what catches a reused pid
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=os.getpid())
    compare(
        _distrusted(session),
        expected=replace_field(session, create_time=process_create_time(os.getpid())),
    )


def test_a_matching_creation_time_stays_live() -> None:
    session = AgentSession(
        'i',
        'n',
        'idle',
        Path('/w'),
        None,
        pid=os.getpid(),
        create_time=process_create_time(os.getpid()),
    )
    compare(_distrusted(session), expected=session)


def test_a_reused_pid_marks_the_session_stale() -> None:
    # the pid is alive, but it is not the process this session named — the case a bare
    # existence probe passes and `agent stop` would then SIGTERM
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=os.getpid(), create_time=1.0)
    compare(
        _distrusted(session).stale,
        expected=f'claimed pid {os.getpid()} was reused by another process',
    )


def test_an_unreadable_creation_time_keeps_the_claim(replace: Replacer) -> None:
    # another user's process: the pid exists, the pair is unknowable, and absent
    # evidence must never be read as proof of reuse
    replace.in_module(process_create_time, lambda pid: None, module=agents)
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=os.getpid(), create_time=1.0)
    compare(_distrusted(session), expected=session)


def test_a_claimed_dead_pid_marks_the_session_stale(replace: Replacer) -> None:
    replace.in_module(os.kill, _dead, module=os)
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=999999)
    compare(
        _distrusted(session).stale,
        expected='claimed pid 999999 is not running',
    )


def test_an_already_marked_session_is_not_probed(replace: Replacer) -> None:
    # the adapter's own verdict stands; a probe of its (absent or dead) pid would be noise
    replace.in_module(os.kill, _dead, module=os)
    session = AgentSession('i', 'n', 'idle', Path('/w'), None, pid=999999, stale='registry remnant')
    compare(_distrusted(session), expected=session)


def test_a_harness_is_available_unless_its_adapter_says_otherwise() -> None:
    # the base class's default, reached past Claude's override: an adapter with no way of
    # being unreachable never blocks reconciliation, while one that knows better can
    assert Agent.available(Claude())
