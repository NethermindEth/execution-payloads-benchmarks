import typer

from expb.cli import app as cli_app

app = typer.Typer()

# All commands (including the `api` sub-group) are registered via cli/
app.add_typer(cli_app)
