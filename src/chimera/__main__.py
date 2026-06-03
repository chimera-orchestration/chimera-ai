import typer

from chimera.commands.init import init

app = typer.Typer()


@app.callback()
def callback() -> None:
    """Chimera — AI agent orchestration."""


app.command()(init)


if __name__ == '__main__':  # pragma: no cover
    app()
