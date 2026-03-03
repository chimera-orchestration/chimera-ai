from sybil import Sybil
from sybil.parsers.rest import DocTestParser

pytest_collect_file = Sybil(
    parsers=[DocTestParser()],
    pattern="**/*.py",
).pytest()
