from pathlib import Path

from testfixtures import Replacer, compare

import os

from chimera.agents import Agent, Session, _distrusted
from chimera.agents.claude import Claude


def test_short_is_the_leading_block_of_a_full_id() -> None:
    compare(Session('abc12345-9f80-4c8e', 'n', 'idle', Path('/w'), None).short, expected='abc12345')
    compare(Session('abc', 'n', 'idle', Path('/w'), None).short, expected='abc')


def test_detail_falls_back_to_tilde_cwd(replace: Replacer) -> None:
    replace.on_class(Path.home, lambda cls: Path('/home/me'))
    compare(
        Session('i', 'n', 'idle', Path('/home/me/work'), 'a prompt').detail, expected='a prompt'
    )
    compare(Session('i', 'n', 'idle', Path('/home/me/work'), None).detail, expected='~/work')
    compare(Session('i', 'n', 'idle', Path('/other'), None).detail, expected='/other')


def _dead(pid: int, sig: int) -> None:
    raise ProcessLookupError


def test_a_pidless_claim_stands_on_the_adapters_word() -> None:
    # a server-backed harness may have no pid to claim — nothing to disprove
    session = Session('i', 'n', 'idle', Path('/w'), None, pid=None)
    compare(_distrusted(session), expected=session)


def test_a_claimed_live_pid_verifies() -> None:
    session = Session('i', 'n', 'idle', Path('/w'), None, pid=os.getpid())
    compare(_distrusted(session), expected=session)


def test_a_claimed_dead_pid_marks_the_session_stale(replace: Replacer) -> None:
    replace.in_module(os.kill, _dead, module=os)
    session = Session('i', 'n', 'idle', Path('/w'), None, pid=999999)
    compare(
        _distrusted(session).stale,
        expected='claimed pid 999999 is not running',
    )


def test_an_already_marked_session_is_not_probed(replace: Replacer) -> None:
    # the adapter's own verdict stands; a probe of its (absent or dead) pid would be noise
    replace.in_module(os.kill, _dead, module=os)
    session = Session('i', 'n', 'idle', Path('/w'), None, pid=999999, stale='registry remnant')
    compare(_distrusted(session), expected=session)


def test_credentials_default_is_none() -> None:
    # the ABC's default: a harness with no readable credential store stays invisible
    # to the auth check rather than falsely healthy or falsely broken
    assert Agent.credentials(Claude()) is None
