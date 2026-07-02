from testfixtures import compare

from chimera.dry import Dry


class TestDry:
    def test_off_by_default_runs_the_mutation(self) -> None:
        ran: list[str] = []
        Dry()(ran.append, 'x')
        compare(ran, expected=['x'])

    def test_on_skips_the_mutation(self) -> None:
        ran: list[str] = []
        Dry(on=True)(ran.append, 'x')
        compare(ran, expected=[])

    def test_passes_positional_and_keyword_args_through(self) -> None:
        seen: dict[str, object] = {}

        def record(*args: object, **kwargs: object) -> None:
            seen.update(args=args, kwargs=kwargs)

        Dry()(record, 1, 2, k=3)
        compare(seen, expected={'args': (1, 2), 'kwargs': {'k': 3}})

    def test_verb_is_the_ran_word_when_off(self) -> None:
        compare(Dry().verb('Removed', 'Would remove'), expected='Removed')

    def test_verb_is_the_would_word_when_on(self) -> None:
        compare(Dry(on=True).verb('Removed', 'Would remove'), expected='Would remove')
