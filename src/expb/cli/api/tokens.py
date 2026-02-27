import hashlib
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="tokens", help="Manage API tokens for the expb API server.")
console = Console()

_DEFAULT_DB = Path("expb-api.db")


def _db_file_option() -> Path:
    # Helper only used as a default factory in the type annotations below.
    return _DEFAULT_DB


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _init(db_file: Path) -> None:
    from expb.api.db.engine import init_db

    init_db(db_file)


@app.command("add")
def add_token(
    name: Annotated[str, typer.Argument(help="Friendly name for the new token.")],
    db_file: Annotated[
        Path,
        typer.Option(help="Path to the SQLite database file."),
    ] = _DEFAULT_DB,
) -> None:
    """
    Generate a new API token, store its hash in the DB, and print the raw
    token value once. The value is never stored and cannot be recovered.
    """
    from expb.api.db.engine import get_session
    from expb.api.db.models import ApiToken

    _init(db_file)
    db = get_session()
    try:
        existing = db.query(ApiToken).filter(ApiToken.name == name).first()
        if existing:
            console.print(f"[red]Error:[/red] A token named '[bold]{name}[/bold]' already exists.")
            raise typer.Exit(code=1)

        raw_token = secrets.token_hex(32)  # 256 bits of entropy
        token = ApiToken(
            token_id=str(uuid.uuid4()),
            name=name,
            token_hash=_hash_token(raw_token),
            created_at=datetime.utcnow(),
        )
        db.add(token)
        db.commit()

        console.print(f"\n[green]Token '[bold]{name}[/bold]' created successfully.[/green]")
        console.print("[yellow bold]Copy the token below — it will not be shown again:[/yellow bold]")
        console.print(f"\n  {raw_token}\n")
    finally:
        db.close()


@app.command("list")
def list_tokens(
    db_file: Annotated[
        Path,
        typer.Option(help="Path to the SQLite database file."),
    ] = _DEFAULT_DB,
) -> None:
    """List all token names and creation dates. Token values are never shown."""
    from expb.api.db.engine import get_session
    from expb.api.db.models import ApiToken

    _init(db_file)
    db = get_session()
    try:
        tokens = db.query(ApiToken).order_by(ApiToken.created_at.asc()).all()
        if not tokens:
            console.print("[yellow]No API tokens found.[/yellow]")
            return

        table = Table(title="API Tokens")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Created At", style="green")
        table.add_column("Last Used At", style="yellow")
        for t in tokens:
            table.add_row(t.name, str(t.created_at), str(t.last_used_at) if t.last_used_at else "never")
        console.print(table)
    finally:
        db.close()


@app.command("revoke")
def revoke_token(
    name: Annotated[str, typer.Argument(help="Name of the token to revoke.")],
    db_file: Annotated[
        Path,
        typer.Option(help="Path to the SQLite database file."),
    ] = _DEFAULT_DB,
) -> None:
    """Revoke (permanently delete) an API token by name."""
    from expb.api.db.engine import get_session
    from expb.api.db.models import ApiToken

    _init(db_file)
    db = get_session()
    try:
        token = db.query(ApiToken).filter(ApiToken.name == name).first()
        if token is None:
            console.print(f"[red]Error:[/red] Token '[bold]{name}[/bold]' not found.")
            raise typer.Exit(code=1)
        db.delete(token)
        db.commit()
        console.print(f"[green]Token '[bold]{name}[/bold]' revoked successfully.[/green]")
    finally:
        db.close()
