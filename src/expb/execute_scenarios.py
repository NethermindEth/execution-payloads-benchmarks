import re
from pathlib import Path

import typer
import yaml
from typing_extensions import Annotated

from expb.configs.scenarios import Scenarios
from expb.logging import setup_logging
from expb.payloads import Executor, ExecutorExecuteOptions

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
    per_payload_metrics_logs: Annotated[
        bool,
        typer.Option(
            help="Emit per-payload metric logs as a table (payload, gas used, processing time) alongside verbose K6 logs.",
        ),
    ] = False,
    filter: Annotated[
        str | None,
        typer.Option(
            help="Filter scenarios by name using a Python regex pattern. Only scenarios matching the pattern will be executed.",
        ),
    ] = None,
    print_logs: Annotated[
        bool,
        typer.Option(
            help="Print K6 and Execution Client logs to console.",
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

    # Filter scenarios by regex pattern if provided
    filtered_scenarios = scenarios.scenarios_configs
    if filter is not None:
        try:
            pattern = re.compile(filter)
            filtered_scenarios = {
                name: scenario
                for name, scenario in scenarios.scenarios_configs.items()
                if pattern.search(name) is not None
            }
            if not filtered_scenarios:
                logger.warning(
                    "No scenarios matched the filter pattern",
                    filter=filter,
                )
                return
            logger.info(
                f"Filtered scenarios: {len(filtered_scenarios)} out of {len(scenarios.scenarios_configs)}",
                filter=filter,
            )
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {filter}. Error: {e}")

    while True:
        for scenario in filtered_scenarios.values():
            for iteration in range(scenario.repeat):
                logger.info(
                    "Executing scenario",
                    iteration=iteration + 1,
                    client=scenario.client,
                    image=scenario.client_image,
                    snapshot=scenario.snapshot_source,
                )
                if scenario.name is None:
                    raise ValueError(
                        "Invalid scenario configuration: scenario has no name"
                    )
                executor = Executor.from_scenarios(
                    scenarios,
                    scenario.name,
                    logger=logger,
                )
                executor.execute_scenario(
                    options=ExecutorExecuteOptions(
                        print_logs_to_console=print_logs,
                        collect_per_payload_metrics=per_payload_metrics,
                        per_payload_metrics_logs=per_payload_metrics_logs,
                    ),
                )
        if not loop:
            break
