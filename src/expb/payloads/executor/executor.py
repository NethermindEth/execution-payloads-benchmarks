import os
import json
import time
import shutil
import docker
import secrets
import requests
import subprocess

import docker.errors

from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from docker.models.containers import Container

from expb.logging import Logger
from expb.configs.exports import Exports, Pyroscope
from expb.configs.networks import Network
from expb.configs.clients import (
    Client,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_FILE,
    CLIENT_RPC_PORT,
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
)
from expb.payloads.utils.networking import limit_container_bandwidth
from expb.payloads.executor.services.k6 import (
    get_k6_script_content,
    build_k6_script_config,
)
from expb.payloads.executor.services.alloy import (
    get_alloy_config,
    ALLOY_PYROSCOPE_PORT,
)
from expb.payloads.executor.exports_utils import add_pyroscope_config
from expb.configs.defaults import (
    K6_DEFAULT_IMAGE,
    ALLOY_DEFAULT_IMAGE,
    PAYLOADS_DEFAULT_FILE,
    FCUS_DEFAULT_FILE,
    WORK_DEFAULT_DIR,
    OUTPUTS_DEFAULT_DIR,
    DOCKER_CONTAINER_DEFAULT_CPUS,
    DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
    DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED,
    DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED,
)


class Executor:
    def __init__(
        self,
        scenario_name: str,
        network: Network,
        execution_client: Client,
        snapshot_dir: Path,
        k6_payloads_amount: int,
        k6_payloads_delay: float,
        k6_payloads_start: int = 1,
        docker_images: dict[str, str] = {},
        payloads_file: Path = PAYLOADS_DEFAULT_FILE,
        fcus_file: Path = FCUS_DEFAULT_FILE,
        work_dir: Path = WORK_DEFAULT_DIR,
        outputs_dir: Path = OUTPUTS_DEFAULT_DIR,
        docker_container_cpus: int = DOCKER_CONTAINER_DEFAULT_CPUS,
        docker_container_mem_limit: str = DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
        docker_container_download_speed: str = DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED,
        docker_container_upload_speed: str = DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED,
        execution_client_image: str | None = None,
        execution_client_extra_flags: list[str] = [],
        json_rpc_wait_max_retries: int = 16,
        pull_images: bool = False,
        limit_bandwidth: bool = False,
        exports: Exports | None = None,
        logger=Logger(),
    ):
        self.scenario_name = scenario_name
        self.executor_name = f"expb-executor-{scenario_name}"
        self.network = network
        self.execution_client = execution_client
        self.execution_client_image = (
            execution_client_image or self.execution_client.value.default_image
        )
        self.execution_client_extra_flags = execution_client_extra_flags

        self.docker_images = docker_images
        self.k6_payloads_amount = k6_payloads_amount
        self.k6_payloads_delay = k6_payloads_delay
        self.k6_payloads_start = k6_payloads_start
        self.docker_container_cpus = docker_container_cpus
        self.docker_container_mem_limit = docker_container_mem_limit
        self.docker_container_download_speed = docker_container_download_speed
        self.docker_container_upload_speed = docker_container_upload_speed
        self.json_rpc_wait_max_retries = json_rpc_wait_max_retries
        self.limit_bandwidth = limit_bandwidth

        self.docker_client = docker.from_env()
        self.pull_images = pull_images

        self.payloads_file = payloads_file
        self.fcus_file = fcus_file
        self.work_dir = work_dir
        self._overlay_work_dir = self.work_dir / "work"
        self._overlay_upper_dir = self.work_dir / "upper"
        self._overlay_merged_dir = self.work_dir / "merged"
        self._jwt_secret_file = self.work_dir / "jwtsecret.hex"
        self.snapshot_dir = snapshot_dir
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.outputs_dir = outputs_dir / f"{self.executor_name}-{timestamp}"
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self._k6_script_file = self.outputs_dir / "k6-script.js"
        self._k6_config_file = self.outputs_dir / "k6-config.json"
        self._alloy_config_file = self.outputs_dir / "config.alloy"
        self.exports = exports

        self.log = logger

    # Scenario Setup
    def prepare_directories(self) -> None:
        # Create overlay required directories
        self._overlay_work_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self._overlay_upper_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self._overlay_merged_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        # run mount command
        device_name = self.executor_name
        mount_command: str = " ".join(
            [
                "mount",
                "-t",
                "overlay",
                device_name,
                "-o",
                ",".join(
                    [
                        f"lowerdir={self.snapshot_dir.absolute()}",
                        f"upperdir={self._overlay_upper_dir.absolute()}",
                        f"workdir={self._overlay_work_dir.absolute()}",
                    ]
                ),
                str(self._overlay_merged_dir.absolute()),
            ]
        )
        try:
            subprocess.run(mount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to mount overlay", error=e)
            raise e

    def pull_docker_images(self) -> None:
        self.log.info("updating docker images")
        self.docker_client.images.pull(self.execution_client_image)
        self.docker_client.images.pull(self.docker_images.get("k6", K6_DEFAULT_IMAGE))
        self.docker_client.images.pull(
            self.docker_images.get("alloy", ALLOY_DEFAULT_IMAGE)
        )
        self.log.info("docker images updated")

    # Execution Client Setup
    def prepare_jwt_secret_file(self) -> None:
        self._jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self._jwt_secret_file.write_text(secrets.token_bytes(32).hex())

    def start_execution_client(
        self,
        container_network: Network | None = None,
        pyroscope: Pyroscope | None = None,
    ) -> Container:
        execution_container_command = self.execution_client.value.get_command(
            instance=self.scenario_name,
            network=self.network,
            extra_flags=self.execution_client_extra_flags,
        )
        execution_container_environment = {}
        if pyroscope:
            add_pyroscope_config(
                client=self.execution_client,
                executor_name=self.executor_name,
                scenario_name=self.scenario_name,
                pyroscope=pyroscope,
                command=execution_container_command,
                environment=execution_container_environment,
            )

        container = self.docker_client.containers.run(
            image=self.execution_client_image,
            name=f"{self.executor_name}-{self.execution_client.value.name.lower()}",
            volumes={
                self._overlay_merged_dir.absolute(): {
                    "bind": CLIENTS_DATA_DIR,
                    "mode": "rw",
                },
                self._jwt_secret_file.absolute(): {
                    "bind": CLIENTS_JWT_SECRET_FILE,
                    "mode": "rw",
                },
            },
            ports={
                f"{CLIENT_RPC_PORT}/tcp": f"{CLIENT_RPC_PORT}",
                f"{CLIENT_ENGINE_PORT}/tcp": f"{CLIENT_ENGINE_PORT}",
                f"{CLIENT_METRICS_PORT}/tcp": f"{CLIENT_METRICS_PORT}",
                # Disable p2p
                # f"{CLIENT_P2P_PORT}/tcp": f"{CLIENT_P2P_PORT}",
                # f"{CLIENT_P2P_PORT}/udp": f"{CLIENT_P2P_PORT}",
            },
            command=execution_container_command,
            environment=execution_container_environment,
            network=container_network.name if container_network else None,
            detach=True,
            cpu_count=self.docker_container_cpus,  # Only works for windows
            nano_cpus=self.docker_container_cpus * 10**9,
            mem_limit=self.docker_container_mem_limit,
            user=os.getuid(),
            group_add=[os.getgid()],
        )
        return container

    def wait_for_client_json_rpc(self) -> None:
        time.sleep(30)
        json_rpc_url = f"http://localhost:{CLIENT_RPC_PORT}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1,
        }
        s = requests.Session()
        retries = Retry(
            total=self.json_rpc_wait_max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        s.mount("http://", HTTPAdapter(max_retries=retries))
        s.mount("https://", HTTPAdapter(max_retries=retries))
        response: requests.Response = s.post(
            json_rpc_url,
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
    def prepare_alloy_config(self) -> None:
        # Create alloy config file
        self._alloy_config_file.touch(mode=0o666, exist_ok=True)
        # Write alloy config content
        execution_client_container_name = (
            f"{self.executor_name}-{self.execution_client.value.name.lower()}"
        )
        self._alloy_config_file.write_text(
            get_alloy_config(
                scenario_name=self.scenario_name,
                execution_client=self.execution_client,
                execution_client_address=f"{execution_client_container_name}:{CLIENT_METRICS_PORT}",
                execution_client_scrape_interval="4s",  # TODO: Make it configurable
                prometheus_rw=self.exports.prometheus_remote_write,
                pyroscope=self.exports.pyroscope,
            )
        )
        self.log.info(
            "Alloy config prepared", alloy_config_file=self._alloy_config_file
        )

    def start_alloy(
        self,
        container_network: Network | None = None,
    ) -> Container:
        alloy_container = self.docker_client.containers.run(
            image=self.docker_images.get("alloy", ALLOY_DEFAULT_IMAGE),
            name=f"{self.executor_name}-alloy",
            volumes={
                self._alloy_config_file.absolute(): {
                    "bind": "/etc/alloy/config.alloy",
                    "mode": "rw",
                },
            },
            ports={
                # f"{ALLOY_PYROSCOPE_PORT}/tcp": f"{ALLOY_PYROSCOPE_PORT}",
            },
            command=["run", "/etc/alloy/config.alloy"],
            detach=True,
            network=container_network.name if container_network else None,
        )
        return alloy_container

    # Grafana K6 Setup
    def prepare_k6_script(self) -> None:
        # Create k6 script file
        self._k6_script_file.touch(mode=0o666, exist_ok=True)
        # Write k6 script content
        self._k6_script_file.write_text(get_k6_script_content())
        # Write k6 script config file
        k6_config = build_k6_script_config(
            scenario_name=self.executor_name,
            client=self.execution_client,
            iterations=self.k6_payloads_amount,
        )
        self._k6_config_file.write_text(json.dumps(k6_config))
        self.log.info(
            "K6 script prepared",
            k6_script_file=self._k6_script_file,
            k6_config_file=self._k6_config_file,
        )

    def run_k6(
        self,
        execution_client_container: Container,
        container_network: Network | None = None,
        collect_per_payload_metrics: bool = False,
    ) -> Container:
        # Get execution client ip
        execution_client_container.reload()
        execution_client_ip = execution_client_container.attrs["NetworkSettings"][
            "Networks"
        ][container_network.name]["IPAddress"]
        engine_url = f"http://{execution_client_ip}:{CLIENT_ENGINE_PORT}"

        # Prepare k6 container volumes
        k6_container_work_dir = "/expb"
        k6_container_payloads_file = f"/payloads/{self.payloads_file.name}"
        k6_container_fcus_file = f"/payloads/{self.fcus_file.name}"
        k6_container_jwt_secret_file = f"/{self._jwt_secret_file.name}"
        k6_container_script_file = (
            f"{k6_container_work_dir}/{self._k6_script_file.name}"
        )
        k6_container_config_file = (
            f"{k6_container_work_dir}/{self._k6_config_file.name}"
        )
        k6_container_summary_file = f"{k6_container_work_dir}/k6-summary.json"
        k6_container_volumes = {
            self.payloads_file.absolute(): {
                "bind": k6_container_payloads_file,
                "mode": "rw",
            },
            self.fcus_file.absolute(): {
                "bind": k6_container_fcus_file,
                "mode": "rw",
            },
            self._jwt_secret_file.absolute(): {
                "bind": k6_container_jwt_secret_file,
                "mode": "rw",
            },
            self.outputs_dir.absolute(): {
                "bind": k6_container_work_dir,
                "mode": "rw",
            },
        }

        # Prepare k6 container command
        k6_container_command = [
            "run",
            k6_container_script_file,
            "--summary-mode=full",
            f"--summary-export={k6_container_summary_file}",
            f"--tag=testid={self.scenario_name}",
            f"--env=EXPB_CONFIG_FILE_PATH={k6_container_config_file}",
            f"--env=EXPB_PAYLOADS_FILE_PATH={k6_container_payloads_file}",
            f"--env=EXPB_FCUS_FILE_PATH={k6_container_fcus_file}",
            f"--env=EXPB_JWTSECRET_FILE_PATH={k6_container_jwt_secret_file}",
            f"--env=EXPB_PAYLOADS_DELAY={self.k6_payloads_delay}",
            f"--env=EXPB_PAYLOADS_START={self.k6_payloads_start}",
            f"--env=EXPB_ENGINE_ENDPOINT={engine_url}",
            f"--env=EXPB_USE_PERPAYLOAD_METRIC={str(collect_per_payload_metrics).lower()}",
        ]

        # Prepare k6 container environment variables
        k6_container_environment = {}

        # Prepare k6 outputs
        if (
            self.exports is not None
            and self.exports.prometheus_remote_write is not None
        ):
            k6_container_command.append("--out=experimental-prometheus-rw")
            k6_container_environment["K6_PROMETHEUS_RW_TREND_STATS"] = (
                "min,max,avg,med,p(90),p(95),p(99)"
            )
            k6_container_environment["K6_PROMETHEUS_RW_SERVER_URL"] = (
                self.exports.prometheus_remote_write.endpoint
            )
            if self.exports.prometheus_remote_write.basic_auth is not None:
                k6_container_environment["K6_PROMETHEUS_RW_USERNAME"] = (
                    self.exports.prometheus_remote_write.basic_auth.username
                )
                k6_container_environment["K6_PROMETHEUS_RW_PASSWORD"] = (
                    self.exports.prometheus_remote_write.basic_auth.password
                )
            for tag in self.exports.prometheus_remote_write.tags:
                k6_container_command.append(f"--tag={tag}")
        else:
            k6_results_jsonl_file = f"{k6_container_work_dir}/k6-results.jsonl"
            k6_container_command.append(f"--out=json={k6_results_jsonl_file}")

        # Execute k6 container
        container = self.docker_client.containers.run(
            image=self.docker_images.get("k6", K6_DEFAULT_IMAGE),
            name=f"{self.executor_name}-k6",
            volumes=k6_container_volumes,
            command=k6_container_command,
            network=container_network.name if container_network else None,
            detach=False,
            user=os.getuid(),
            group_add=[os.getgid()],
            environment=k6_container_environment,
        )
        return container

    # Scenario Cleanup
    def remove_directories(self) -> None:
        umount_command = " ".join(["umount", str(self._overlay_merged_dir.absolute())])
        try:
            subprocess.run(umount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to umount overlay", error=e)
            raise e
        try:
            paths_to_remove = [
                self._overlay_upper_dir.absolute(),
                self._overlay_work_dir.absolute(),
                self._overlay_merged_dir.absolute(),
            ]
            for path in paths_to_remove:
                shutil.rmtree(path)
        except Exception as e:
            self.log.error("failed to cleanup work directory", error=e)
            raise e

    def cleanup_scenario(self) -> None:
        self.log.info("Cleaning up scenario", scenario=self.executor_name)
        # Clean k6 container
        try:
            k6_container = self.docker_client.containers.get(f"{self.executor_name}-k6")
            k6_container.stop()
            logs_file = self.outputs_dir / "k6.log"
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
            execution_client_container = self.docker_client.containers.get(
                f"{self.executor_name}-{self.execution_client.value.name.lower()}"
            )
            execution_client_container.stop()
            logs_file = (
                self.outputs_dir / f"{self.execution_client.value.name.lower()}.log"
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
            alloy_container = self.docker_client.containers.get(
                f"{self.executor_name}-alloy"
            )
            alloy_container.stop()
            alloy_container.remove()
        except docker.errors.NotFound:
            pass

        # Clean docker network
        try:
            containers_network = self.docker_client.networks.get(
                f"{self.executor_name}-network"
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
                scenario=self.executor_name,
                execution_client=self.execution_client.value.name.lower(),
            )
            self.prepare_directories()
            self.prepare_jwt_secret_file()
            if self.pull_images:
                self.pull_docker_images()

            self.log.info("Creating docker network")
            containers_network = self.docker_client.networks.create(
                name=f"{self.executor_name}-network",
                driver="bridge",
            )

            self.log.info("Preparing Alloy config")
            self.prepare_alloy_config()

            self.log.info(
                "Starting Grafana Alloy",
                image=self.docker_images.get("alloy", ALLOY_DEFAULT_IMAGE),
            )
            alloy_container = self.start_alloy(
                container_network=containers_network,
            )
            alloy_container.reload()
            alloy_container_ip = alloy_container.attrs["NetworkSettings"]["Networks"][
                containers_network.name
            ]["IPAddress"]
            alloy_pyroscope: Pyroscope | None = (
                Pyroscope(
                    {
                        "endpoint": f"http://{alloy_container_ip}:{ALLOY_PYROSCOPE_PORT}",
                    }
                )
                if self.exports is not None and self.exports.pyroscope is not None
                else None
            )

            self.log.info(
                "Starting execution client",
                execution_client=self.execution_client.value.name.lower(),
                execution_client_image=self.execution_client_image,
                docker_container_cpus=self.docker_container_cpus,
                docker_container_mem_limit=self.docker_container_mem_limit,
            )
            execution_client_container = self.start_execution_client(
                container_network=containers_network,
                pyroscope=alloy_pyroscope,
            )

            if self.limit_bandwidth:
                self.log.info(
                    "Limiting container bandwidth",
                    execution_client=self.execution_client.value.name.lower(),
                    download_speed=self.docker_container_download_speed,
                    upload_speed=self.docker_container_upload_speed,
                )
                try:
                    limit_container_bandwidth(
                        execution_client_container,
                        self.docker_container_download_speed,
                        self.docker_container_upload_speed,
                    )
                except Exception as e:
                    self.log.error("Failed to limit container bandwidth", error=e)
                    raise e

            self.log.info("Waiting for client json rpc to be available")
            try:
                self.wait_for_client_json_rpc()
            except Exception as e:
                self.log.error("Failed to wait for client json rpc", error=e)
                raise e

            self.log.info("Preparing K6 script")
            self.prepare_k6_script()

            self.log.info(
                "Running K6",
                k6_docker_image=self.docker_images.get("k6", K6_DEFAULT_IMAGE),
            )
            _ = self.run_k6(
                execution_client_container=execution_client_container,
                container_network=containers_network,
                collect_per_payload_metrics=collect_per_payload_metrics,
            )

            self.log.info(
                "Payloads execution completed",
                execution_client=self.execution_client.value.name.lower(),
            )
        except Exception as e:
            self.log.error("Failed to execute scenario", error=e)
            raise e
        finally:
            self.cleanup_scenario()
