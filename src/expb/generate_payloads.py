import os
import typer
import asyncio

from pathlib import Path
from typing_extensions import Annotated

from expb.payloads import Generator
from expb.networks import Network

app = typer.Typer()


@app.command()
def generate_payloads(
    rpc_url: Annotated[str, typer.Option(help="Ethereum RPC URL")],
    network: Annotated[Network, typer.Option(help="Network")] = Network.MAINNET,
    start_block: Annotated[int, typer.Option(help="Start block")] = 0,
    end_block: Annotated[int | None, typer.Option(help="End block")] = None,
    output_dir: Annotated[Path, typer.Option(help="Output directory")] = "payloads",
    workers: Annotated[
        int, typer.Option(help="Number of workers for parallel processing")
    ] = 10,
) -> None:
    """
    Generate execution payloads for a given block range.
    """
    os.makedirs(output_dir, exist_ok=True)

    generator = Generator(
        rpc_url=rpc_url,
        network=network,
        start_block=start_block,
        end_block=end_block,
        output_dir=output_dir,
        workers=workers,
    )

    asyncio.run(generator.generate_payloads())
