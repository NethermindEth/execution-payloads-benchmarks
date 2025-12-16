from pathlib import Path

import typer
from typing_extensions import Annotated

from expb.configs.defaults import (
    DOCKER_CONTAINER_DEFAULT_CPUS,
    DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
)
from expb.configs.networks import Network
from expb.logging import setup_logging
from expb.payloads import Compressor

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
    target_gas_limit: Annotated[
        int, typer.Option(help="Target Gas limit for compressed blocks")
    ] = 4000000000,  # 4 Giga gas
    cpu_count: Annotated[
        int, typer.Option(help="CPU count for the Nethermind container")
    ] = DOCKER_CONTAINER_DEFAULT_CPUS,
    mem_limit: Annotated[
        str, typer.Option(help="Memory limit for the Nethermind container")
    ] = DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
    include_blobs: Annotated[
        bool, typer.Option(help="Include blobs in the compressed payloads transactions")
    ] = False,
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Compress execution payloads transactions for a given block range into bigger blocks.
    """
    logger = setup_logging(log_level)

    compressor = Compressor(
        network=network,
        cpu_count=cpu_count,
        mem_limit=mem_limit,
        compression_factor=compression_factor,
        target_gas_limit=target_gas_limit,
        nethermind_snapshot_dir=nethermind_snapshot_dir,
        nethermind_docker_image=nethermind_docker_image,
        input_payloads_file=input_payloads_file,
        output_payloads_dir=output_payloads_dir,
        include_blobs=include_blobs,
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
