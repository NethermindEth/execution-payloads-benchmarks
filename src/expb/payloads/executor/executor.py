import json
import time
import shutil
import docker
import secrets
import requests
import subprocess
import docker.errors

from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor, Future
from urllib3.util.retry import Retry
from docker.models.containers import Container
from docker.models.networks import Network

from expb.logging import Logger
from expb.configs.exports import Pyroscope
from expb.payloads.utils.networking import limit_container_bandwidth
from expb.payloads.executor.services.k6 import (
    get_k6_script_content,
    build_k6_script_config,
)
from expb.payloads.executor.services.alloy import (
    get_alloy_config,
)
from expb.payloads.executor.executor_config import ExecutorConfig
from expb.payloads.executor.exports_utils import add_pyroscope_config


class Executor:
    def __init__(
        self,
        config: ExecutorConfig,
        logger=Logger(),
    ):
        self.config = config
        self.log = logger
        self.running_command_futures: list[Future] = []
        self.executor_pool: ThreadPoolExecutor | None = None

    # Scenario Setup
    def prepare_directories(self) -> None:
        # Create overlay required directories
        self.config.overlay_work_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self.config.overlay_upper_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self.config.overlay_merged_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        # run mount command
        device_name = self.config.executor_name
        mount_command: str = " ".join(
            [
                "mount",
                "-t",
                "overlay",
                device_name,
                "-o",
                ",".join(
                    [
                        f"lowerdir={self.config.snapshot_dir.resolve()}",
                        f"upperdir={self.config.overlay_upper_dir.resolve()}",
                        f"workdir={self.config.overlay_work_dir.resolve()}",
                    ]
                ),
                str(self.config.overlay_merged_dir.resolve()),
            ]
        )
        try:
            subprocess.run(mount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to mount overlay", error=e)
            raise e

    def pull_docker_images(self) -> None:
        self.log.info("updating docker images")
        self.config.docker_client.images.pull(self.config.execution_client_image)
        self.config.docker_client.images.pull(self.config.get_k6_container_image())
        self.config.docker_client.images.pull(self.config.get_alloy_container_image())
        self.log.info("docker images updated")

    # Execution Client Setup
    def prepare_jwt_secret_file(self) -> None:
        self.config.jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self.config.jwt_secret_file.write_text(secrets.token_bytes(32).hex())

    def start_execution_client(
        self,
        container_network: Network | None = None,
        pyroscope: Pyroscope | None = None,
    ) -> Container:
        # Command
        execution_container_command = self.config.get_execution_client_command()
        # Environment
        execution_container_environment = self.config.get_execution_client_env()
        # Volumes
        execution_container_volumes = self.config.get_execution_client_volumes()
        # Ports
        execution_container_ports = self.config.get_execution_client_ports()

        # Add pyroscope config if available
        if pyroscope:
            add_pyroscope_config(
                client=self.config.execution_client,
                executor_name=self.config.executor_name,
                scenario_name=self.config.scenario_name,
                pyroscope=pyroscope,
                command=execution_container_command,
                environment=execution_container_environment,
            )

        # Run execution container
        container = self.config.docker_client.containers.run(
            image=self.config.execution_client_image,
            name=self.config.get_execution_client_container_name(),
            volumes=execution_container_volumes,
            ports=execution_container_ports,
            command=execution_container_command,
            environment=execution_container_environment,
            network=container_network.name if container_network else None,
            detach=True,
            cpu_count=self.config.docker_container_cpus,  # Only works for windows
            nano_cpus=self.config.docker_container_cpus * 10**9,
            mem_limit=self.config.docker_container_mem_limit,
            user=self.config.docker_user,
            group_add=self.config.docker_group_add,
        )
        return container

    def wait_for_client_json_rpc(
        self,
        execution_client_rpc_url: str,
    ) -> None:
        time.sleep(30)
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
        self.config.alloy_config_file.write_text(
            get_alloy_config(
                scenario_name=self.config.scenario_name,
                execution_client=self.config.execution_client,
                execution_client_address=execution_client_metrics_address,
                # TODO: Make scrape parameters configurable
                execution_client_scrape_interval="4s",
                execution_client_scrape_timeout="3s",  # Has to be lower than the scrape interval
                prometheus_rw=self.config.exports.prometheus_remote_write,
                pyroscope=self.config.exports.pyroscope,
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
            scenario_name=self.config.executor_name,
            client=self.config.execution_client,
            iterations=self.config.k6_payloads_amount,
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
    ) -> Container:
        # Prepare k6 container volumes
        k6_container_volumes = self.config.get_k6_volumes()

        # Prepare k6 container command
        k6_container_command = self.config.get_k6_command(
            execution_client_engine_url=execution_client_engine_url,
            collect_per_payload_metrics=collect_per_payload_metrics,
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
            user=self.config.docker_user,
            group_add=self.config.docker_group_add,
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
            "Stopping extra commands execution",
            running_futures=len(self.running_command_futures),
        )

        # Cancel any pending futures
        for future in self.running_command_futures:
            if not future.done():
                future.cancel()

        # Clean up
        self.running_command_futures.clear()
        if self.executor_pool:
            self.executor_pool.shutdown(wait=True)
            self.executor_pool = None

    # Scenario Cleanup
    def remove_directories(self) -> None:
        umount_command = " ".join(
            ["umount", str(self.config.overlay_merged_dir.resolve())]
        )
        try:
            subprocess.run(umount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to umount overlay", error=e)
            raise e
        try:
            paths_to_remove = [
                self.config.overlay_upper_dir.resolve(),
                self.config.overlay_work_dir.resolve(),
                self.config.overlay_merged_dir.resolve(),
            ]
            for path in paths_to_remove:
                shutil.rmtree(path)
        except Exception as e:
            self.log.error("failed to cleanup work directory", error=e)
            raise e

    def cleanup_scenario(self) -> None:
        self.log.info("Cleaning up scenario", scenario=self.config.executor_name)

        # Stop all running extra commands first
        self.stop_extra_commands()

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
            logs_stream.close()
            k6_container.remove()
        except docker.errors.NotFound:
            pass

        # Clean execution client container
        try:
            execution_client_container = self.config.docker_client.containers.get(
                self.config.get_execution_client_container_name()
            )
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
            logs_stream.close()
            execution_client_container.remove()
        except docker.errors.NotFound:
            pass

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
        collect_per_payload_metrics: bool = False,
    ) -> None:
        try:
            self.log.info(
                "Preparing scenario",
                scenario=self.config.executor_name,
                execution_client=self.config.get_execution_client_name(),
            )
            self.prepare_directories()
            self.prepare_jwt_secret_file()
            if self.config.pull_images:
                self.pull_docker_images()

            self.log.info("Creating docker network")
            containers_network = self.config.docker_client.networks.create(
                name=self.config.get_containers_network_name(),
                driver="bridge",
            )

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
                    {
                        "endpoint": self.config.get_alloy_pyroscope_url(
                            container=alloy_container,
                            network=containers_network,
                        ),
                    }
                )
                if self.config.exports is not None
                and self.config.exports.pyroscope is not None
                else None
            )

            self.log.info(
                "Starting execution client",
                execution_client=self.config.get_execution_client_name(),
                execution_client_image=self.config.execution_client_image,
                docker_container_cpus=self.config.docker_container_cpus,
                docker_container_mem_limit=self.config.docker_container_mem_limit,
            )
            execution_client_container = self.start_execution_client(
                container_network=containers_network,
                pyroscope=alloy_pyroscope,
            )

            if self.config.limit_bandwidth:
                self.log.info(
                    "Limiting container bandwidth",
                    execution_client=self.config.get_execution_client_name(),
                    download_speed=self.config.docker_container_download_speed,
                    upload_speed=self.config.docker_container_upload_speed,
                )
                try:
                    limit_container_bandwidth(
                        execution_client_container,
                        self.config.docker_container_download_speed,
                        self.config.docker_container_upload_speed,
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
            _ = self.run_k6(
                execution_client_engine_url=execution_client_engine_url,
                container_network=containers_network,
                collect_per_payload_metrics=collect_per_payload_metrics,
            )

            self.log.info(
                "Payloads execution completed",
                execution_client=self.config.get_execution_client_name(),
            )
        except Exception as e:
            self.log.error("Failed to execute scenario", error=e)
            raise e
        finally:
            self.cleanup_scenario()
