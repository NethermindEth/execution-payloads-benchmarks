import typer
from typing_extensions import Annotated

from expb.cli.version._version import __commit__, __version__
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def version(
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """Show the version and commit hash."""
    logger = setup_logging(log_level)
    logger.info("expb version", version=__version__, commit=__commit__)
