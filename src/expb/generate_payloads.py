import os
import typer

from pathlib import Path
from typing_extensions import Annotated

from expb.payloads import Generator
from expb.configs.networks import Network
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def generate_payloads(
    rpc_url: Annotated[str, typer.Option(help="Ethereum RPC URL")],
    network: Annotated[Network, typer.Option(help="Network")] = Network.MAINNET,
    start_block: Annotated[int, typer.Option(help="Start block")] = 0,
    end_block: Annotated[int | None, typer.Option(help="End block")] = None,
    output_dir: Annotated[Path, typer.Option(help="Output directory")] = Path(
        "payloads",
    ),
    join_payloads: Annotated[
        bool,
        typer.Option(
            help="Join payloads and FCUs into a single file (payloads.jsonl and fcus.jsonl)"
        ),
    ] = True,
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
    threads: Annotated[
        int, typer.Option(help="Number of threads for parallel processing")
    ] = 10,
    workers: Annotated[
        int, typer.Option(help="Number of workers per thread for parallel processing")
    ] = 30,
) -> None:
    """
    Generate execution payloads for a given block range.
    """
    logger = setup_logging(log_level)

    logger.info(
        "Creating output directory",
        output_dir=output_dir,
    )
    os.makedirs(output_dir, exist_ok=True)

    generator = Generator(
        rpc_url=rpc_url,
        network=network,
        start_block=start_block,
        end_block=end_block,
        output_dir=output_dir,
        join_payloads=join_payloads,
        threads=threads,
        workers=workers,
        logger=logger,
    )

    logger.info(
        "Starting payloads generation",
        network=network.value,
        rpc_url=rpc_url,
        start_block=start_block,
        end_block=end_block,
    )
    generator.generate_payloads()
