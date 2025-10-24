import typer

from pathlib import Path
from typing_extensions import Annotated

from expb.payloads import Compressor
from expb.configs.networks import Network
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def compress_payloads(
    nethermind_snapshot_dir: Annotated[
        Path, typer.Option(help="Nethermind snapshot directory")
    ],
    nethermind_docker_image: Annotated[
        str, typer.Option(help="Nethermind docker image")
    ],
    input_payloads_file: Annotated[
        Path, typer.Option(help="Input payloads jsonl file")
    ],
    output_payloads_dir: Annotated[
        Path,
        typer.Option(
            help="Output directory to use for compressed payloads and forkchoice messages"
        ),
    ],
    network: Annotated[Network, typer.Option(help="Network")] = Network.MAINNET,
    compression_factor: Annotated[int, typer.Option(help="Compress factor")] = 2,
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Generate execution payloads for a given block range.
    """
    logger = setup_logging(log_level)

    compressor = Compressor(
        network=network,
        compression_factor=compression_factor,
        nethermind_snapshot_dir=nethermind_snapshot_dir,
        nethermind_docker_image=nethermind_docker_image,
        input_payloads_file=input_payloads_file,
        output_payloads_dir=output_payloads_dir,
        logger=logger,
    )

    logger.info(
        "Starting payloads compression",
        network=network,
        compression_factor=compression_factor,
        nethermind_snapshot_dir=nethermind_snapshot_dir,
        nethermind_docker_image=nethermind_docker_image,
        input_payloads_file=input_payloads_file,
        output_payloads_dir=output_payloads_dir,
    )
    compressor.compress_payloads()
