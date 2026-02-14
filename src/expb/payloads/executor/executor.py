import json
import re
import secrets
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor

import docker
import docker.errors
import requests
from docker.models.containers import Container
from docker.models.networks import Network
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from expb.configs.exports import Pyroscope
from expb.configs.scenarios import Scenarios
from expb.logging import Logger
from expb.payloads.executor.executor_config import ExecutorConfig
from expb.payloads.executor.exports_utils import add_pyroscope_config
from expb.payloads.executor.services.alloy import (
    get_alloy_config,
)
from expb.payloads.executor.services.k6 import (
    build_k6_script_config,
    get_k6_script_content,
)
from expb.payloads.executor.services.snapshots import setup_snapshot_service
from expb.payloads.utils.networking import limit_container_bandwidth

PER_PAYLOAD_METRIC_LOG_PATTERN = re.compile(
    r'EXPB_PER_PAYLOAD_METRIC idx=(?P<idx>\d+) gas_used=(?P<gas_used>[^"\s]+) processing_ms=(?P<processing_ms>[^"\s]+)'
)


class ExecutorExecuteOptions:
    def __init__(
        self,
        collect_per_payload_metrics: bool = False,
        print_logs_to_console: bool = False,
        per_payload_metrics_logs: bool = False,
    ):
        self.collect_per_payload_metrics: bool = collect_per_payload_metrics
        self.print_logs_to_console: bool = print_logs_to_console
        self.per_payload_metrics_logs: bool = per_payload_metrics_logs


class Executor:
    def __init__(
        self,
        config: ExecutorConfig,
        logger=Logger(),
    ):
        self.config: ExecutorConfig = config
        self.log: Logger = logger
        self.running_command_futures: list[Future] = []
        self.executor_pool: ThreadPoolExecutor | None = None

    # Scenario Setup
    def prepare_directories(self) -> None:
        self.log.info("Preparing snapshot directory")
        try:
            self.config.snapshot_service.create_snapshot(
                name=self.config.executor_name, source=self.config.snapshot_source
            )
            self.log.info("Snapshot created successfully")
        except Exception as e:
            self.log.error("Failed to create snapshot", error=e)
            raise e

    def clean_system_cache(self) -> None:
        self.log.info("Cleaning system cache")
        try:
            subprocess.run("command -v sync", check=True, shell=True)
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("3")
            self.log.info("System cache cleaned")
        except subprocess.CalledProcessError as e:
            self.log.error("Failed to clean system cache", error=e)
            raise e

    def pull_docker_images(self) -> None:
        self.log.info("updating docker images")
        self.config.docker_client.images.pull(self.config.execution_client_image)
        self.config.docker_client.images.pull(self.config.get_k6_container_image())
        self.config.docker_client.images.pull(self.config.get_alloy_container_image())
        self.log.info("docker images updated")

    # Execution Client Setup
    def prepare_jwt_secret_file(self) -> None:
        self.config.jwt_secret_dir.mkdir(parents=True, exist_ok=True)
        self.config.jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self.config.jwt_secret_file.write_text(secrets.token_bytes(32).hex())

    def start_execution_client(
        self,
        container_network: Network | None = None,
        pyroscope: Pyroscope | None = None,
        stop_signal: str | None = None,
    ) -> Container:
        # Command
        execution_container_command = self.config.get_execution_client_command()
        # Environment
        execution_container_environment = self.config.get_execution_client_env()
        # Volumes
        execution_container_volumes_data = self.config.get_execution_client_volumes()
        execution_container_volumes = []
        for volume_data in execution_container_volumes_data:
            self.log.debug(
                "Creating execution client volume", volume=volume_data["config"]["name"]
            )
            volume = self.config.docker_client.volumes.create(**volume_data["config"])
            execution_container_volumes.append(f"{volume.name}:{volume_data['bind']}")
        # Ports
        execution_container_ports = self.config.get_execution_client_ports()

        # Add pyroscope config if available
        if pyroscope:
            add_pyroscope_config(
                client=self.config.execution_client,
                executor_name=self.config.executor_name,
                test_id=self.config.test_id,
                pyroscope=pyroscope,
                command=execution_container_command,
                environment=execution_container_environment,
            )

        # Run execution container
        cpu_count = self.config.resources.cpu if self.config.resources else None
        mem_limit = self.config.resources.mem if self.config.resources else None
        container = self.config.docker_client.containers.run(
            image=self.config.execution_client_image,
            name=self.config.get_execution_client_container_name(),
            volumes=execution_container_volumes,
            ports=execution_container_ports,
            command=execution_container_command,
            environment=execution_container_environment,
            network=container_network.name if container_network else None,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            cpu_count=cpu_count,  # Only works for windows
            nano_cpus=cpu_count * 10**9 if cpu_count else None,
            mem_limit=mem_limit,
            user=self.config.docker_user,
            group_add=self.config.docker_group_add,
            stop_signal=stop_signal,
        )
        return container

    def wait_for_client_json_rpc(
        self,
        execution_client_rpc_url: str,
    ) -> None:
        time.sleep(self.config.startup_wait)
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1,
        }
        s = requests.Session()
        retries = Retry(
            total=self.config.json_rpc_wait_max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        s.mount("http://", HTTPAdapter(max_retries=retries))
        s.mount("https://", HTTPAdapter(max_retries=retries))
        response: requests.Response = s.post(
            execution_client_rpc_url,
            json=payload,
            headers=headers,
        )
        if response.ok:
            self.log.info(
                "Client json rpc is available",
                latest_block=int(response.json()["result"], 16),
            )
        else:
            self.log.error(
                "Client json rpc is not available", status_code=response.status_code
            )
            raise Exception("Client json rpc is not available")

    # Grafana Alloy Setup
    def prepare_alloy_config(
        self,
        execution_client_metrics_address: str,
    ) -> None:
        # Create alloy config file
        self.config.alloy_config_file.touch(mode=0o666, exist_ok=True)
        # Write alloy config content
        prometheus_rw = (
            self.config.exports.prometheus_rw
            if self.config.exports is not None
            else None
        )
        scrape_interval = (
            self.config.exports.prometheus_rw.scrape_interval
            if self.config.exports is not None
            and self.config.exports.prometheus_rw is not None
            else None
        )
        scrape_timeout = (
            self.config.exports.prometheus_rw.scrape_timeout
            if self.config.exports is not None
            and self.config.exports.prometheus_rw is not None
            else None
        )
        pyroscope = (
            self.config.exports.pyroscope if self.config.exports is not None else None
        )
        self.config.alloy_config_file.write_text(
            get_alloy_config(
                test_id=self.config.test_id,
                execution_client=self.config.execution_client,
                execution_client_address=execution_client_metrics_address,
                scrape_interval=scrape_interval,
                scrape_timeout=scrape_timeout,
                prometheus_rw=prometheus_rw,
                pyroscope=pyroscope,
            )
        )
        self.log.info(
            "Alloy config prepared", alloy_config_file=self.config.alloy_config_file
        )

    def start_alloy(
        self,
        container_network: Network | None = None,
    ) -> Container:
        alloy_container = self.config.docker_client.containers.run(
            image=self.config.get_alloy_container_image(),
            name=self.config.get_alloy_container_name(),
            volumes=self.config.get_alloy_volumes(),
            ports=self.config.get_alloy_ports(),
            command=self.config.get_alloy_command(),
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=container_network.name if container_network else None,
        )
        return alloy_container

    # Grafana K6 Setup
    def prepare_k6_script(self) -> None:
        # Create k6 script file
        self.config.k6_script_file.touch(mode=0o666, exist_ok=True)
        # Write k6 script content
        self.config.k6_script_file.write_text(get_k6_script_content())
        # Write k6 script config file
        k6_config = build_k6_script_config(
            test_id=self.config.test_id,
            scenario_name=self.config.executor_name,
            client=self.config.execution_client,
            iterations=self.config.k6_payloads_amount,
            duration=self.config.k6_duration,
            setup_timeout=self.config.k6_warmup_duration,
        )
        self.config.k6_config_file.write_text(json.dumps(k6_config))
        self.log.info(
            "K6 script prepared",
            k6_script_file=self.config.k6_script_file,
            k6_config_file=self.config.k6_config_file,
        )

    def run_k6(
        self,
        execution_client_engine_url: str,
        container_network: Network | None = None,
        collect_per_payload_metrics: bool = False,
        enable_logging: bool = False,
        per_payload_metrics_logs: bool = False,
    ) -> Container:
        # Prepare k6 container volumes
        k6_container_volumes = self.config.get_k6_volumes()

        # Prepare k6 container command
        k6_container_command = self.config.get_k6_command(
            execution_client_engine_url=execution_client_engine_url,
            collect_per_payload_metrics=collect_per_payload_metrics,
            enable_logging=enable_logging,
            per_payload_metrics_logs=per_payload_metrics_logs,
        )

        # Prepare k6 container environment variables
        k6_container_environment = self.config.get_k6_environment()

        # Execute k6 container
        container = self.config.docker_client.containers.run(
            image=self.config.get_k6_container_image(),
            name=self.config.get_k6_container_name(),
            volumes=k6_container_volumes,
            environment=k6_container_environment,
            command=k6_container_command,
            network=container_network.name if container_network else None,
            detach=False,
            restart_policy={"Name": "unless-stopped"},
            user=self.config.docker_user,
            group_add=self.config.docker_group_add,
            stop_signal="SIGINT",
        )
        return container

    # Extra Commands Execution
    def _execute_single_command(
        self,
        container: Container,
        command: str,
        command_id: int,
    ) -> None:
        command_output_file = (
            self.config.extra_commands_outputs_dir / f"cmd-{command_id}.log"
        )
        with command_output_file.open("wb") as f:
            try:
                self.log.info(
                    "Starting extra command execution",
                    id=command_id,
                    output_file=str(command_output_file),
                )

                # Execute the command in the container
                result = container.exec_run(
                    cmd=command,
                    stdout=True,
                    stderr=True,
                    stream=True,
                    user=str(self.config.docker_user),
                )
                for line in result.output:
                    f.write(line)
                    f.flush()  # Write line to file as soon as possible
            except Exception as e:
                self.log.error(
                    "Command execution failed",
                    command_id=command_id,
                    error=str(e),
                )
            finally:
                f.flush()

    def start_extra_commands(
        self,
        execution_client_container: Container,
    ) -> None:
        # Check if there are any extra commands to execute
        if not self.config.execution_client_extra_commands:
            return

        self.log.info(
            "Starting extra commands execution",
            commands_count=len(self.config.execution_client_extra_commands),
        )

        # Create thread pool executor for parallel execution
        self.executor_pool = ThreadPoolExecutor(
            max_workers=len(self.config.execution_client_extra_commands),
            thread_name_prefix="extra-command",
        )

        # Submit all commands for parallel execution
        for command_id, command in enumerate(
            self.config.execution_client_extra_commands
        ):
            future = self.executor_pool.submit(
                self._execute_single_command,
                execution_client_container,
                command,
                command_id,
            )
            self.running_command_futures.append(future)

    def stop_extra_commands(self) -> None:
        # Check if there is any extra command
        if not self.running_command_futures:
            return

        self.log.info(
            "Cleaning up extra commands",
        )

        # Clean up
        self.running_command_futures.clear()
        if self.executor_pool:
            self.executor_pool.shutdown(wait=False, cancel_futures=True)
            self.executor_pool = None

    # Scenario Cleanup
    @staticmethod
    def _should_skip_console_k6_log_line(line: str) -> bool:
        return (
            "POST engine_newPayload" in line
            or "POST engine_forkchoiceUpdated" in line
            or "EXPB_PER_PAYLOAD_METRIC" in line
        )

    @staticmethod
    def _parse_per_payload_metric_row(line: str) -> tuple[int, str, str] | None:
        match = PER_PAYLOAD_METRIC_LOG_PATTERN.search(line)
        if match is None:
            return None
        idx = int(match.group("idx"))
        gas_used = match.group("gas_used")
        processing_ms = match.group("processing_ms")
        return (idx, gas_used, processing_ms)

    @staticmethod
    def _format_table_cell(value: str | int, width: int, align_right: bool = False) -> str:
        raw = str(value)
        if len(raw) >= width:
            return raw[:width]
        pad = " " * (width - len(raw))
        return f"{pad}{raw}" if align_right else f"{raw}{pad}"

    def _print_per_payload_metrics_table(self, rows: list[tuple[int, str, str]]) -> None:
        if not rows:
            print("No per-payload metrics rows were collected.")
            return

        rows_sorted = sorted(rows, key=lambda row: row[0])
        separator = "+---------+------------+-----------------+"
        print(separator)
        print(
            "| "
            f"{self._format_table_cell('payload', 7)} | "
            f"{self._format_table_cell('gas_used', 10)} | "
            f"{self._format_table_cell('processing_ms', 15)} |"
        )
        print(separator)
        for idx, gas_used, processing_ms in rows_sorted:
            print(
                "| "
                f"{self._format_table_cell(idx, 7, True)} | "
                f"{self._format_table_cell(gas_used, 10, True)} | "
                f"{self._format_table_cell(processing_ms, 15, True)} |"
            )
        print(separator)

    def remove_directories(self) -> None:
        try:
            self.config.snapshot_service.delete_snapshot(
                name=self.config.executor_name, source=self.config.snapshot_source
            )
            self.log.info("Snapshot deleted successfully")
        except Exception as e:
            self.log.error("Failed to delete snapshot", error=e)
            raise e

    def cleanup_scenario(
        self,
        print_logs_to_console: bool = False,
        print_per_payload_metrics_table: bool = False,
    ) -> None:
        self.log.info("Cleaning up scenario", scenario=self.config.executor_name)

        # Stop all running extra commands first
        self.stop_extra_commands()

        per_payload_metrics_rows: list[tuple[int, str, str]] = []

        # Clean k6 container
        try:
            k6_container = self.config.docker_client.containers.get(
                self.config.get_k6_container_name()
            )
            k6_container.stop()
            logs_file = self.config.outputs_dir / "k6.log"
            self.log.info("Saving k6 logs", logs_file=logs_file)
            logs_stream = k6_container.logs(
                stream=True,
                follow=False,
                stdout=True,
                stderr=True,
            )
            with open(logs_file, "wb") as f:
                for line in logs_stream:
                    f.write(line)
                    decoded_line = line.decode("utf-8", errors="replace")
                    metric_row = self._parse_per_payload_metric_row(decoded_line)
                    if metric_row is not None:
                        per_payload_metrics_rows.append(metric_row)
                    if print_logs_to_console:
                        if not self._should_skip_console_k6_log_line(decoded_line):
                            print(decoded_line, end="")
            logs_stream.close()
            k6_container.remove()
        except docker.errors.NotFound:
            pass

        # Clean execution client container
        try:
            execution_client_container = self.config.docker_client.containers.get(
                self.config.get_execution_client_container_name()
            )
            execution_client_container.reload()
            execution_client_volumes = execution_client_container.attrs["Mounts"]
            execution_client_container.stop()
            logs_file = (
                self.config.outputs_dir
                / f"{self.config.get_execution_client_name()}.log"
            )
            self.log.info("Saving execution client logs", logs_file=logs_file)
            logs_stream = execution_client_container.logs(
                stream=True,
                follow=False,
                stdout=True,
                stderr=True,
            )
            with open(logs_file, "wb") as f:
                for line in logs_stream:
                    f.write(line)
                    if print_logs_to_console:
                        print(line.decode("utf-8"), end="")
            logs_stream.close()
            execution_client_container.remove()
            # Clean execution client volumes
            for volume in execution_client_volumes:
                if volume["Type"] == "volume":
                    self.config.docker_client.volumes.get(volume["Name"]).remove()
                    self.log.debug(
                        "Cleaned execution client volume", volume=volume["Name"]
                    )
        except docker.errors.NotFound:
            pass

        if print_logs_to_console and print_per_payload_metrics_table:
            self._print_per_payload_metrics_table(per_payload_metrics_rows)

        # Clean alloy container
        try:
            alloy_container = self.config.docker_client.containers.get(
                self.config.get_alloy_container_name()
            )
            alloy_container.stop()
            alloy_container.remove()
        except docker.errors.NotFound:
            pass

        # Clean docker network
        try:
            containers_network = self.config.docker_client.networks.get(
                self.config.get_containers_network_name()
            )
            containers_network.remove()
        except docker.errors.NotFound:
            pass

        # Clean overlay directories
        self.remove_directories()
        self.log.info("Cleanup completed")

    # Scenario Execution
    def execute_scenario(
        self,
        options: ExecutorExecuteOptions = ExecutorExecuteOptions(),
    ) -> None:
        try:
            self.log.info(
                "Preparing scenario",
                scenario=self.config.executor_name,
                execution_client=self.config.get_execution_client_name(),
            )
            self.clean_system_cache()
            self.prepare_directories()
            self.prepare_jwt_secret_file()
            if self.config.pull_images:
                self.pull_docker_images()

            self.log.info("Creating docker network")
            containers_network = self.config.docker_client.networks.create(
                name=self.config.get_containers_network_name(),
                driver="bridge",
            )

            alloy_pyroscope: Pyroscope | None = None
            if self.config.exports is not None:
                self.log.info("Preparing Alloy config")
                self.prepare_alloy_config(
                    self.config.get_execution_metrics_address(),
                )

                self.log.info(
                    "Starting Grafana Alloy",
                    image=self.config.get_alloy_container_image(),
                )
                alloy_container = self.start_alloy(
                    container_network=containers_network,
                )
                alloy_pyroscope: Pyroscope | None = (
                    Pyroscope(
                        endpoint=self.config.get_alloy_pyroscope_url(
                            container=alloy_container,
                            network=containers_network,
                        ),
                    )
                    if self.config.exports is not None
                    and self.config.exports.pyroscope is not None
                    else None
                )

            self.log.info(
                "Starting execution client",
                execution_client=self.config.get_execution_client_name(),
                execution_client_image=self.config.execution_client_image,
                docker_container_cpus=self.config.resources.cpu
                if self.config.resources
                else None,
                docker_container_mem_limit=self.config.resources.mem
                if self.config.resources
                else None,
            )
            stop_signal = (
                # If there are extra commands to execute, use SIGINT to stop the execution client
                # instead of SIGTERM
                "SIGINT" if self.config.execution_client_extra_commands else None
            )
            execution_client_container = self.start_execution_client(
                container_network=containers_network,
                pyroscope=alloy_pyroscope,
                stop_signal=stop_signal,
            )

            if self.config.resources and self.config.limit_bandwidth:
                self.log.info(
                    "Limiting container bandwidth",
                    execution_client=self.config.get_execution_client_name(),
                    download_speed=self.config.resources.download_speed
                    if self.config.resources
                    else None,
                    upload_speed=self.config.resources.upload_speed
                    if self.config.resources
                    else None,
                )
                try:
                    limit_container_bandwidth(
                        execution_client_container,
                        self.config.resources.download_speed,
                        self.config.resources.upload_speed,
                    )
                except Exception as e:
                    self.log.error("Failed to limit container bandwidth", error=e)
                    raise e

            self.log.info("Waiting for client json rpc to be available")
            try:
                execution_client_rpc_url = self.config.get_execution_client_rpc_url(
                    execution_client_container,
                    containers_network,
                )
                self.wait_for_client_json_rpc(
                    execution_client_rpc_url=execution_client_rpc_url,
                )
            except Exception as e:
                self.log.error("Failed to wait for client json rpc", error=e)
                raise e

            # Start extra commands in parallel
            self.start_extra_commands(execution_client_container)

            self.log.info("Preparing K6 script")
            self.prepare_k6_script()

            self.log.info(
                "Running K6",
                k6_docker_image=self.config.get_k6_container_image(),
            )
            execution_client_engine_url = self.config.get_execution_client_engine_url(
                execution_client_container,
                containers_network,
            )
            enable_k6_logging = (
                options.print_logs_to_console or options.per_payload_metrics_logs
            )
            _ = self.run_k6(
                execution_client_engine_url=execution_client_engine_url,
                container_network=containers_network,
                collect_per_payload_metrics=options.collect_per_payload_metrics,
                enable_logging=enable_k6_logging,
                per_payload_metrics_logs=options.per_payload_metrics_logs,
            )

            self.log.info(
                "Payloads execution completed",
                execution_client=self.config.get_execution_client_name(),
            )
        except Exception as e:
            self.log.error("Failed to execute scenario", error=e)
            raise e
        finally:
            self.cleanup_scenario(
                print_logs_to_console=(
                    options.print_logs_to_console or options.per_payload_metrics_logs
                ),
                print_per_payload_metrics_table=options.per_payload_metrics_logs,
            )

    @classmethod
    def from_scenarios(
        self,
        scenarios: Scenarios,
        scenario_name: str,
        logger: Logger = Logger(),
    ) -> "Executor":
        scenario = scenarios.scenarios_configs.get(scenario_name, None)
        if scenario is None:
            raise ValueError(f"Scenario {scenario_name} not found")
        if scenario.name is None:
            scenario.name = scenario_name
        snapshot_service = setup_snapshot_service(
            scenarios,
            scenario,
        )
        executor = Executor(
            config=ExecutorConfig(
                scenario=scenario,
                snapshot_service=snapshot_service,
                paths=scenarios.paths,
                resources=scenarios.resources,
                pull_images=scenarios.pull_images,
                docker_images=scenarios.docker_images,
                exports=scenarios.exports,
            ),
            logger=logger,
        )
        return executor
