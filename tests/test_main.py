from typer.testing import CliRunner

from chimera.cli import app

runner = CliRunner()


def test_main() -> None:
    result = runner.invoke(app)
    assert result.exit_code == 0
    assert "Hello from chimera!" in result.output
