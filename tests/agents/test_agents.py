from pathlib import Path

from testfixtures import Replacer, compare

from chimera.agents import Session


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
