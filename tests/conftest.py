from collections.abc import Iterator

import pytest
from testfixtures import TempDir


@pytest.fixture()
def tmpdir() -> Iterator[TempDir]:
    with TempDir() as d:
        yield d
