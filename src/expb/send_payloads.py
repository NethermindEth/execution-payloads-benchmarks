from pathlib import Path

import typer
from typing_extensions import Annotated

from expb.logging import setup_logging
from expb.payloads.utils.engine import RPCError, engine_request
from expb.payloads.utils.jwt import JWTProvider

app = typer.Typer()


@app.command()
def send_payloads(
    engine_url: Annotated[str, typer.Option(help="Ethereum Execution Engine URL")],
    payloads_file: Annotated[Path, typer.Option(help="Payloads file")],
    fcus_file: Annotated[Path, typer.Option(help="FCUs file")],
    jwt_secret_file: Annotated[Path, typer.Option(help="JWT secret file")],
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Send payloads to an Ethereum Execution Engine endpoint.
    """
    logger = setup_logging(log_level)

    logger.info("Preparing JWT provider", jwt_secret_file=jwt_secret_file)
    jwt_provider = JWTProvider(jwt_secret_file)

    with payloads_file.open("r") as pf, fcus_file.open("r") as ff:
        logger.info(
            "Sending payloads and FCU",
            payloads_file=payloads_file,
            fcus_file=fcus_file,
        )
        while True:
            payload = pf.readline().strip()
            fcu = ff.readline().strip()
            if not payload or not fcu:
                break
            try:
                engine_request(engine_url, jwt_provider, payload)
                engine_request(engine_url, jwt_provider, fcu)
            except RPCError as e:
                logger.error(
                    "Failed to send payload", error=e.error, status_code=e.status_code
                )
                raise e
