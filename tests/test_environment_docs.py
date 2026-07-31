import re
from pathlib import Path

from testfixtures import compare

ROOT = Path(__file__).parents[1]
REFERENCE = ROOT / 'agent-docs' / 'environment.md'

# what chimera might plausibly call its own: its namespace, and the completion vars named
# after its console scripts. Quoted, because that is what an environment variable looks
# like in source — a bare CHIMERA_SOMETHING is a Python constant and none of our business.
OURS = re.compile(r'''['"](CHIMERA_[A-Z_]+|_CH_COMPLETE|_CHIMERA_COMPLETE)['"]''')
NAMED = re.compile(r'\b(CHIMERA_[A-Z_]+|_CH_COMPLETE|_CHIMERA_COMPLETE)\b')


def _named_in_source() -> set[str]:
    return {
        name for path in (ROOT / 'src').rglob('*.py') for name in OURS.findall(path.read_text())
    }


def test_every_variable_chimera_names_is_documented() -> None:
    # a hand-written inventory is only true while something makes it true
    documented = set(NAMED.findall(REFERENCE.read_text()))
    compare(_named_in_source() - documented, expected=set())


def test_the_reference_names_nothing_that_left_the_code() -> None:
    # …and the reverse, so a retired variable is described as retired rather than current
    section = REFERENCE.read_text().split('## Retired')
    current = set(NAMED.findall(section[0]))
    compare(current - _named_in_source(), expected=set())
