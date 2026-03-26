from pathlib import Path

import typer
import yaml
from typing_extensions import Annotated

from expb.configs.scenarios import Scenarios
from expb.logging import setup_logging
from expb.payloads import Executor, ExecutorExecuteOptions
from expb.utils import ExecutionLockError, acquire_execution_lock, get_default_lock_file

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
    per_payload_metrics_logs: Annotated[
        bool,
        typer.Option(
            "--per-payload-metrics-logs/--no-per-payload-metrics-logs",
            "--per-payloads-metrics-logs/--no-per-payloads-metrics-logs",
            help="Collect per-payload metric rows and print a table after execution completes (after execution client logs).",
        ),
    ] = False,
    print_logs: Annotated[
        bool,
        typer.Option(
            help="Print K6 and Execution Client logs to console.",
        ),
    ] = False,
    evm_warmup: Annotated[
        bool,
        typer.Option(
            "--evm-warmup/--no-evm-warmup",
            help="Per-block EVM warmup via eth_simulateV1 before each measured payload. Warms contract code, state trie, and DB block cache.",
        ),
    ] = False,
    drop_caches: Annotated[
        bool,
        typer.Option(
            "--drop-caches/--no-drop-caches",
            help="Drop OS page cache before each measured payload for cold storage reads.",
        ),
    ] = True,
    use_lock: Annotated[
        bool,
        typer.Option(
            help="Use execution lock to prevent concurrent runs. Enabled by default to avoid resource conflicts.",
        ),
    ] = True,
    lock_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to lock file. Defaults to platform-specific temp directory.",
        ),
    ] = None,
) -> None:
    """
    Execute payloads for a given execution client using Grafana K6.
    """
    logger = setup_logging(log_level)

    # Use default lock file if not specified
    lock_file_path = lock_file or get_default_lock_file()

    try:
        with acquire_execution_lock(
            lock_file=lock_file_path,
            enabled=use_lock,
            timeout=0,
            logger=logger,
        ):
            if not config_file.exists() or not config_file.is_file():
                raise FileNotFoundError(
                    f"Config file {config_file} not found or not a file"
                )

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
                        per_payload_metrics_logs=per_payload_metrics_logs,
                        evm_warmup=evm_warmup,
                        drop_caches=drop_caches,
                    ),
                )
    except ExecutionLockError as e:
        logger.error("Failed to acquire execution lock", error=str(e))
        raise typer.Exit(code=1) from e
