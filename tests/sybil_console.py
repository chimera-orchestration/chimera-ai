"""
Sybil support for executing the human docs (docs/*.rst).

Each document gets a scratch ConsoleSession — a throwaway home directory with its own
git identity and environment, so nothing ever touches the user's real workspace. In a
``code-block:: console``, each ``$ `` line runs for real (``ch`` resolving to this
venv's chimera) and its output is compared, with doctest ``...`` ellipsis, after
normalising: the scratch home reads as the docs' ``/Users/you``, and commit shas map
consistently — the same real sha always maps to the same documented sha, so a sha the
docs repeat provably names one commit throughout.
"""

import doctest
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sybil import Example
from sybil.parsers.rest import CodeBlockParser
from testfixtures import compare

from chimera.config import AgentConfig

SHA = re.compile(r'\b[0-9a-f]{7,40}\b')
CHECKER = doctest.OutputChecker()


class ConsoleSession:
    """One document's scratch world: a fake home, its environment, and its sha mapping."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.cwd = home
        self.env: dict[str, str] = {
            'HOME': str(home),
            'PATH': '/usr/bin:/bin',
            'GIT_CONFIG_NOSYSTEM': '1',
        }
        self.shas: dict[str, str] = {}
        (home / '.gitconfig').write_text('[user]\nname = You\nemail = you@example.com\n')

    def _expand(self, token: str) -> str:
        if token.startswith('~'):
            token = str(self.home) + token[1:]
        return token.replace('$HOME', str(self.home))

    def run(self, command: str) -> str:
        program, *args = (self._expand(token) for token in shlex.split(command))
        if program == 'export':
            name, _, value = args[0].partition('=')
            self.env[name] = value
            return ''
        argv = [sys.executable, '-m', 'chimera', *args] if program == 'ch' else [program, *args]
        return subprocess.run(
            argv,
            cwd=self.cwd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        ).stdout

    def normalise(self, want: str, got: str) -> str:
        got = got.replace(str(self.home), '/Users/you')
        for documented, real in zip(SHA.findall(want), SHA.findall(got)):
            if real not in self.shas and documented not in self.shas.values():
                self.shas[real] = documented
        return SHA.sub(lambda match: self.shas.get(match.group(), match.group()), got)


def _steps(source: str) -> list[tuple[str, str]]:
    commands: list[tuple[str, list[str]]] = []
    for line in source.splitlines():
        if line.startswith('$ '):
            commands.append((line[2:], []))
        else:
            commands[-1][1].append(line)
    return [(command, ''.join(f'{line}\n' for line in lines)) for command, lines in commands]


class ConsoleCodeBlockParser(CodeBlockParser):
    language = 'console'

    def evaluate(self, example: Example) -> str | None:
        session: ConsoleSession = example.namespace['session']
        for command, want in _steps(example.parsed):
            got = session.normalise(want, session.run(command))
            if not CHECKER.check_output(want, got, doctest.ELLIPSIS):
                return f'$ {command}\nExpected:\n{want}Got:\n{got}'
        return None


def agent_config_block(example: Example) -> None:
    # the documented `agent:` block round-trips through the live config model, so a
    # renamed, added or dropped field fails here until the docs follow
    data = yaml.safe_load(example.parsed)
    compare(AgentConfig.model_validate(data['agent']).model_dump(), expected=data['agent'])


def console_setup(namespace: dict[str, Any]) -> None:
    namespace['session'] = ConsoleSession(Path(tempfile.mkdtemp()).resolve())


def console_teardown(namespace: dict[str, Any]) -> None:
    shutil.rmtree(namespace['session'].home)
