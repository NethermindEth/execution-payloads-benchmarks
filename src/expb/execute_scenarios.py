import typer

from pathlib import Path
from typing_extensions import Annotated

from expb.logging import setup_logging
from expb.configs.scenarios import Scenarios

app = typer.Typer()


@app.command()
def execute_scenarios(
    loop: Annotated[bool, typer.Option(help="Run in infinite loop")] = False,
    config_file: Annotated[Path, typer.Option(help="Config file")] = "expb.yaml",
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Execute payloads for multiple execution clients.
    """
    logger = setup_logging(log_level)

    if not config_file.exists() or not config_file.is_file():
        raise FileNotFoundError(f"Config file {config_file} not found or not a file")

    config = Scenarios(config_file)

    while True:
        for scenario in config.scenarios.values():
            logger.info(
                "Executing scenario",
                client=scenario.client,
                image=scenario.client_image,
                snapshot=scenario.snapshot_dir,
            )
            executor = config.get_scenario_executor(scenario, logger=logger)
            executor.execute_scenario()
        if not loop:
            break
