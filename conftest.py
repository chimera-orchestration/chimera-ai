from sybil import Sybil
from sybil.parsers.rest import CodeBlockParser, DocTestParser, PythonCodeBlockParser, SkipParser

from tests.sybil_console import (
    ConsoleCodeBlockParser,
    agent_config_block,
    console_setup,
    console_teardown,
)

pytest_collect_file = (
    Sybil(
        parsers=[DocTestParser()],
        pattern="**/*.py",
    )
    + Sybil(
        parsers=[
            SkipParser(),
            ConsoleCodeBlockParser(),
            PythonCodeBlockParser(),
            CodeBlockParser(language='yaml', evaluator=agent_config_block),
        ],
        pattern="docs/*.rst",
        setup=console_setup,
        teardown=console_teardown,
    )
).pytest()
