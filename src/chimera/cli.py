import typer

app = typer.Typer()


@app.command()
def main() -> None:
    typer.echo("Hello from chimera!")
