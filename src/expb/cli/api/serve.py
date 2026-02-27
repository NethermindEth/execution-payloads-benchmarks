from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer()


@app.command()
def serve(
    config_file: Annotated[
        Path,
        typer.Option(help="Path to the expb YAML configuration file."),
    ] = Path("expb.yaml"),
    db_file: Annotated[
        Path,
        typer.Option(
            help="Path to the SQLite database file used for run history and API tokens."
        ),
    ] = Path("expb-api.db"),
    host: Annotated[
        str,
        typer.Option(help="Host address to bind the HTTP server."),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(help="Port to bind the HTTP server."),
    ] = 8080,
    log_level: Annotated[
        str,
        typer.Option(help="Log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = "INFO",
) -> None:
    """
    Start the expb benchmark queue API server.

    Launches a FastAPI HTTP server and a single background worker thread that
    executes benchmark runs sequentially. Only one run executes at a time.
    """
    import uvicorn

    if not config_file.exists() or not config_file.is_file():
        typer.echo(f"Error: config file '{config_file}' not found.", err=True)
        raise typer.Exit(code=1)

    from expb.api.app import create_app

    fastapi_app = create_app(
        config_file=config_file,
        db_path=db_file,
        log_level=log_level,
    )

    # workers must stay at 1: the background benchmark worker thread lives
    # inside this process. Multiple uvicorn workers would each start their own
    # thread, leading to concurrent benchmark executions.
    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        workers=1,
    )
