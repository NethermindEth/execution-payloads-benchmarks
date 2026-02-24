import typer

from expb.cli.api.serve import app as serve_app
from expb.cli.api.tokens import app as tokens_app

app = typer.Typer(name="api", help="API server and token management commands.")
app.add_typer(serve_app)
app.add_typer(tokens_app)
