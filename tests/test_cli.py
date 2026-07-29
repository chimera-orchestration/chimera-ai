from testfixtures import compare

from tests.cli import SESSION_ID


class TestSessionIdMatcher:
    # stands in for the uuid a foreground launch mints, in argv a test can't spell out.
    # It weakens every assertion that uses it, so what it accepts is pinned here.

    def test_matches_a_minted_uuid(self) -> None:
        assert SESSION_ID == '3fef7b3f-9dd1-4fa8-a77c-9f85ee83c5f0'

    def test_rejects_anything_that_is_not_one(self) -> None:
        assert SESSION_ID != 'proj@g@agent'  # …so a misplaced --name can't slip through
        assert SESSION_ID != '3fef7b3f'  # nor the short handle claude won't resume by
        assert SESSION_ID != ''
        assert SESSION_ID != None  # noqa: E711 — the point is the non-str branch

    def test_reads_as_itself_when_an_assertion_fails(self) -> None:
        compare(repr(SESSION_ID), expected='<a minted session id>')

    def test_is_hashable(self) -> None:
        # defining __eq__ drops __hash__ unless it's restored, and an unhashable value
        # blows up any comparison that happens to put it in a set
        assert {SESSION_ID}
