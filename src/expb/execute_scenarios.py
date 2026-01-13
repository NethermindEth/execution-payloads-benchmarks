from pathlib import Path

import typer
import yaml
from typing_extensions import Annotated

from expb.configs.scenarios import Scenarios
from expb.logging import setup_logging

app = typer.Typer()


@app.command()
def execute_scenarios(
    loop: Annotated[bool, typer.Option(help="Run in infinite loop")] = False,
    config_file: Annotated[Path, typer.Option(help="Config file")] = Path("expb.yaml"),
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
    per_payload_metrics: Annotated[
        bool,
        typer.Option(
            help="Collect per-payload metric. This generates a metric for each payload, which can overload the configured outputs.",
        ),
    ] = False,
) -> None:
    """
    Execute payloads for multiple execution clients using Grafana K6.
    """
    logger = setup_logging(log_level)

    if not config_file.exists() or not config_file.is_file():
        raise FileNotFoundError(f"Config file {config_file} not found or not a file")

    with config_file.open() as f:
        config = yaml.safe_load(f)

    scenarios = Scenarios(**config)

    while True:
        for scenario in scenarios.scenarios_configs.values():
            logger.info(
                "Executing scenario",
                client=scenario.client,
                image=scenario.client_image,
                snapshot=scenario.snapshot_source,
            )
            executor = config.get_scenario_executor(scenario, logger=logger)
            executor.execute_scenario(
                collect_per_payload_metrics=per_payload_metrics,
            )
        if not loop:
            break
