import typer

from pathlib import Path
from typing_extensions import Annotated

from expb.payloads import Executor
from expb.configs.networks import Network
from expb.configs.clients import Client
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def execute_payloads(
    execution_client: Annotated[Client, typer.Option(help="Execution Client")],
    snapshot_dir: Annotated[Path, typer.Option(help="Network client snapshot")],
    network: Annotated[Network, typer.Option(help="Network")] = Network.MAINNET,
    execution_client_image: Annotated[
        str | None,
        typer.Option(
            help="Execution client image to use. Leave empty for client default"
        ),
    ] = None,
    payloads_dir: Annotated[Path, typer.Option(help="Payloads directory")] = "payloads",
    work_dir: Annotated[Path, typer.Option(help="Work directory")] = "work",
    logs_dir: Annotated[Path, typer.Option(help="Logs directory")] = "logs",
    docker_container_cpus: Annotated[
        float, typer.Option(help="Docker container CPUs")
    ] = 4.0,
    docker_container_download_speed: Annotated[
        str, typer.Option(help="Docker container download speed")
    ] = "50mbit",
    docker_container_upload_speed: Annotated[
        str, typer.Option(help="Docker container upload speed")
    ] = "15mbit",
    docker_container_mem_limit: Annotated[
        str, typer.Option(help="Docker container memory limit")
    ] = "32g",
    json_rpc_wait_max_retries: Annotated[
        int, typer.Option(help="JSON-RPC wait max retries")
    ] = 10,
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Execute payloads for a given execution client using Kute.
    """
    logger = setup_logging(log_level)

    executor = Executor(
        network=network,
        execution_client=execution_client,
        execution_client_image=execution_client_image,
        payloads_dir=payloads_dir,
        work_dir=work_dir,
        snapshot_dir=snapshot_dir,
        docker_container_cpus=docker_container_cpus,
        docker_container_download_speed=docker_container_download_speed,
        docker_container_upload_speed=docker_container_upload_speed,
        docker_container_mem_limit=docker_container_mem_limit,
        json_rpc_wait_max_retries=json_rpc_wait_max_retries,
        logs_dir=logs_dir,
        logger=logger,
    )

    executor.execute_scenarios()
