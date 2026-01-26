from pathlib import Path

import typer
import yaml
from typing_extensions import Annotated

from expb.configs.scenarios import Scenarios
from expb.logging import setup_logging
from expb.payloads import Executor, ExecutorExecuteOptions

app = typer.Typer()


@app.command()
def execute_scenario(
    scenario_name: Annotated[str, typer.Option(help="Scenario name")],
    config_file: Annotated[Path, typer.Option(help="Config file")] = Path(
        "expb.yaml",
    ),
    log_level: Annotated[
        str, typer.Option(help="Log level (e.g., DEBUG, INFO, WARNING)")
    ] = "INFO",
    per_payload_metrics: Annotated[
        bool,
        typer.Option(
            help="Collect per-payload metric. This generates a metric for each payload, which can overload the configured outputs.",
        ),
    ] = False,
    print_logs: Annotated[
        bool,
        typer.Option(
            help="Print K6 and Execution Client logs to console.",
        ),
    ] = False,
) -> None:
    """
    Execute payloads for a given execution client using Grafana K6.
    """
    logger = setup_logging(log_level)

    if not config_file.exists() or not config_file.is_file():
        raise FileNotFoundError(f"Config file {config_file} not found or not a file")

    with config_file.open() as f:
        config = yaml.safe_load(f)

    scenarios = Scenarios(**config)
    scenario = scenarios.scenarios_configs.get(scenario_name, None)
    if scenario is None:
        raise ValueError(
            f"Scenario {scenario_name} not found in config file {config_file}"
        )
    for iteration in range(scenario.repeat):
        executor = Executor.from_scenarios(
            scenarios,
            scenario_name=scenario_name,
            logger=logger,
        )

        logger.info(
            "Executing scenario",
            iteration=iteration + 1,
            client=scenario.client,
            image=scenario.client_image,
            snapshot=scenario.snapshot_source,
        )
        executor.execute_scenario(
            options=ExecutorExecuteOptions(
                print_logs_to_console=print_logs,
                collect_per_payload_metrics=per_payload_metrics,
            ),
        )
