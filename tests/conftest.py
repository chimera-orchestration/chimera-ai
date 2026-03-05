import pytest
from testfixtures import TempDir


@pytest.fixture()
def tmpdir() -> TempDir:
    with TempDir() as d:
        yield d
