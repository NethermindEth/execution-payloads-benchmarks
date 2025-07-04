import typer

from pathlib import Path
from typing_extensions import Annotated

from expb.configs.scenarios import Scenarios
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def execute_scenario(
    scenario_name: Annotated[str, typer.Option(help="Scenario name")],
    config_file: Annotated[Path, typer.Option(help="Config file")] = "expb.yaml",
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
) -> None:
    """
    Execute payloads for a given execution client using Kute.
    """
    logger = setup_logging(log_level)

    if not config_file.exists() or not config_file.is_file():
        raise FileNotFoundError(f"Config file {config_file} not found or not a file")

    config = Scenarios(config_file)

    scenario = config.scenarios.get(scenario_name, None)
    if scenario is None:
        raise ValueError(
            f"Scenario {scenario_name} not found in config file {config_file}"
        )

    executor = config.get_scenario_executor(scenario, logger=logger)

    logger.info(
        "executing scenario",
        client=scenario.client.value.name.lower(),
        image=scenario.client_image,
        snapshot=scenario.snapshot_dir,
    )
    executor.execute_scenario()
