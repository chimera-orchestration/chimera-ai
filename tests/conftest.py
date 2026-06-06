from collections.abc import Iterator

import pytest
from testfixtures import TempDir


@pytest.fixture(autouse=True)
def _clear_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('CHIMERA_WORKSPACE', raising=False)  # tests opt in explicitly


@pytest.fixture()
def tmpdir() -> Iterator[TempDir]:
    with TempDir() as d:
        yield d
