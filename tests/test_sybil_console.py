from pathlib import Path

from sybil import Sybil
from sybil.example import SybilFailure
from testfixtures import ShouldRaise

from tests.sybil_console import ConsoleCodeBlockParser, console_setup, console_teardown


def test_output_mismatch_fails_the_example(tmp_path: Path) -> None:
    # the failure path a green doc run never reaches: a command whose real output
    # stops matching what the doc shows must fail, naming command and both outputs
    (tmp_path / 'doc.rst').write_text(
        '.. code-block:: console\n\n    $ git --version\n    not what git says\n'
    )
    sybil = Sybil(parsers=[ConsoleCodeBlockParser()], pattern='*.rst')
    document = sybil.parse(tmp_path / 'doc.rst')
    console_setup(document.namespace)
    try:
        (example,) = document.examples()
        with ShouldRaise(SybilFailure, match='not what git says'):
            example.evaluate()
    finally:
        console_teardown(document.namespace)
