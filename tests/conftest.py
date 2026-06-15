from collections.abc import Iterator, Sequence

import pytest
from testfixtures import Command, LogCapture, Replacer, TempDir, not_there
from testfixtures.command import AbstractRun
from testfixtures.loguru import LoguruSource
from testfixtures.mock import Mock, call

from chimera.__main__ import app
from chimera.logging import configure


@pytest.fixture()
def replace() -> Iterator[Replacer]:
    with Replacer() as replacer:
        yield replacer


@pytest.fixture(autouse=True)
def _clear_workspace_env(replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', not_there)  # tests opt in explicitly
    replace.in_environ('SHELL', not_there)  # keeps the shell-completion check inert


@pytest.fixture()
def tmpdir() -> Iterator[TempDir]:
    with TempDir(cwd=True) as d:
        yield d


@pytest.fixture()
def logs() -> Iterator[LogCapture]:
    with LogCapture(LoguruSource()) as captured:
        yield captured


class Run(AbstractRun):
    """A run of the ``ch`` CLI, tailored to how Chimera logs.

    Logging is loguru, captured via :class:`LoguruSource`. The sink setup
    (:func:`chimera.logging.configure`) is mocked away — it's one call to one file, so
    we just assert it happened on every run rather than restating it in each test.
    """

    @classmethod
    def setup_logging(cls) -> LogCapture:
        return LogCapture(LoguruSource())

    @classmethod
    def setup_mocks(cls, replace: Replacer) -> Mock:
        mocks = Mock()
        replace.in_module(configure, mocks.configure)
        return mocks

    def check(
        self,
        logging: Sequence[tuple[str, str]],
        output: str = '',
        return_code: int = 0,
    ) -> None:
        __tracebackhide__ = True
        self.check_results(
            self.check_output(output, self.output),
            self.check_return_code(return_code, self.return_code),
            self.check_logging(logging, self.logging),
            self.check_mock_calls([call.configure()], self.mocks),
        )


@pytest.fixture()
def command() -> Command[Run]:
    return Command(app, runner=Run)
