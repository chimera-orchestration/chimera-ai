from sybil import Sybil
from sybil.parsers import markdown
from sybil.parsers.rest import CodeBlockParser, DocTestParser, PythonCodeBlockParser, SkipParser
from sybil.sybil import SybilCollection

from tests.sybil_console import (
    ConsoleCodeBlockParser,
    agent_config_block,
    console_setup,
    console_teardown,
)

# SybilCollection directly: this sybil's Sybil.__add__ only chains a pair, not three
pytest_collect_file = SybilCollection((
    Sybil(
        parsers=[DocTestParser()],
        pattern="**/*.py",
    ),
    Sybil(
        parsers=[
            SkipParser(),
            ConsoleCodeBlockParser(),
            PythonCodeBlockParser(),
            CodeBlockParser(language='yaml', evaluator=agent_config_block),
        ],
        pattern="docs/*.rst",
        setup=console_setup,
        teardown=console_teardown,
    ),
    Sybil(
        parsers=[
            markdown.SkipParser(),
            markdown.PythonCodeBlockParser(),
            markdown.CodeBlockParser(language='yaml', evaluator=agent_config_block),
        ],
        pattern="agent-docs/*.md",
    ),
)).pytest()
