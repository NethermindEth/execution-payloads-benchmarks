import os
import time
import docker

from pathlib import Path
from docker.models.containers import Container
from docker.models.networks import Network

from expb.configs.exports import Exports
from expb.configs.networks import Network as EthNetwork
from expb.configs.clients import (
    Client,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_DIR,
    CLIENT_RPC_PORT,
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
)
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
from expb.payloads.executor.services.alloy import ALLOY_PYROSCOPE_PORT


# ExecutorConfig class is a collection of helper functions and configuration options for the Executor class
class ExecutorConfig:
    def __init__(
        self,
        scenario_name: str,
        network: EthNetwork,
        execution_client: Client,
        snapshot_dir: Path,
        k6_payloads_amount: int,
        k6_duration: str = "10m",
        k6_payloads_skip: int = 0,
        k6_payloads_warmup: int = 0,
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
        execution_client_extra_env: dict[str, str] = {},
        execution_client_extra_volumes: dict[str, dict[str, str]] = {},
        execution_client_extra_commands: list[str] = [],
        json_rpc_wait_max_retries: int = 16,
        pull_images: bool = False,
        limit_bandwidth: bool = False,
        exports: Exports | None = None,
    ) -> None:
        # Executor Basic config
        self.scenario_name = scenario_name
        self.executor_name = f"expb-executor-{scenario_name}"
        self.test_id = f"{scenario_name}-{time.strftime('%Y%m%d-%H%M%S')}"

        # Executor Client config
        self.network = network
        self.execution_client = execution_client
        self.execution_client_image = (
            execution_client_image or self.execution_client.value.default_image
        )
        self.execution_client_extra_flags = execution_client_extra_flags
        self.execution_client_extra_env = execution_client_extra_env
        self.execution_client_extra_volumes = execution_client_extra_volumes
        self.execution_client_extra_commands = execution_client_extra_commands

        # Executor Additional Tooling config
        ## Docker client
        self.docker_client = docker.from_env()
        self.docker_images = docker_images
        self.pull_images = pull_images
        self.docker_user = os.getuid()
        self.docker_group_add = [os.getgid()]
        ## Docker container config
        self.docker_container_cpus = docker_container_cpus
        self.docker_container_mem_limit = docker_container_mem_limit
        self.docker_container_download_speed = docker_container_download_speed
        self.docker_container_upload_speed = docker_container_upload_speed
        self.limit_bandwidth = limit_bandwidth
        self.json_rpc_wait_max_retries = json_rpc_wait_max_retries
        ## K6 script config
        self.k6_payloads_amount = k6_payloads_amount
        self.k6_duration = k6_duration
        self.k6_payloads_skip = k6_payloads_skip
        self.k6_payloads_warmup = k6_payloads_warmup

        # Executor Directories
        ## Payloads and FCUs
        self.payloads_file = payloads_file
        self.fcus_file = fcus_file

        ## Work directories
        self.work_dir = work_dir
        ### Overlay directories
        self.overlay_work_dir = self.work_dir / "work"
        self.overlay_upper_dir = self.work_dir / "upper"
        self.overlay_merged_dir = self.work_dir / "merged"
        ### JWT secret file
        self.jwt_secret_dir = self.work_dir / "jwt-secret"
        self.jwt_secret_file = self.jwt_secret_dir / "jwtsecret.hex"

        ## Snapshot directory
        self.snapshot_dir = snapshot_dir

        ## Outputs directory
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.outputs_dir = outputs_dir / f"{self.executor_name}-{timestamp}"
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

        ## Client Additional Volumes directory
        self.volumes_dir = self.outputs_dir / "volumes"
        if self.execution_client_extra_volumes and any(
            [
                volume
                for volume in self.execution_client_extra_volumes.values()
                if volume.get("source") is None
            ]
        ):
            self.volumes_dir.mkdir(parents=True, exist_ok=True)

        ## Client extra Commands outputs directory
        self.extra_commands_outputs_dir = self.outputs_dir / "commands"
        if self.execution_client_extra_commands:
            self.extra_commands_outputs_dir.mkdir(parents=True, exist_ok=True)

        ## K6 script and config files
        self.k6_script_file = self.outputs_dir / "k6-script.js"
        self.k6_config_file = self.outputs_dir / "k6-config.json"
        ### K6 container directories
        self._k6_container_work_dir = "/expb"
        self._k6_container_payloads_file = f"/payloads/{self.payloads_file.name}"
        self._k6_container_fcus_file = f"/payloads/{self.fcus_file.name}"
        self._k6_container_jwt_secret_file = f"/{self.jwt_secret_file.name}"
        self._k6_container_script_file = (
            f"{self._k6_container_work_dir}/{self.k6_script_file.name}"
        )
        self._k6_container_config_file = (
            f"{self._k6_container_work_dir}/{self.k6_config_file.name}"
        )
        self._k6_container_summary_file = (
            f"{self._k6_container_work_dir}/k6-summary.json"
        )

        ## Alloy config file
        self.alloy_config_file = self.outputs_dir / "config.alloy"

        # Executor Exports config
        self.exports = exports

    # Executor Helper functions
    def get_execution_client_name(self) -> str:
        return self.execution_client.value.name.lower()

    ## Docker
    def get_container_name(self, service: str) -> str:
        return f"{self.executor_name}-{service}"

    def get_containers_network_name(self) -> str:
        return self.get_container_name("network")

    ### Execution client
    def get_execution_client_container_name(self) -> str:
        return self.get_container_name(self.execution_client.value.name.lower())

    def get_execution_client_command(self) -> list[str]:
        return self.execution_client.value.get_command(
            instance=self.scenario_name,
            network=self.network,
            extra_flags=self.execution_client_extra_flags,
        )

    def get_execution_client_env(self) -> dict[str, str]:
        return self.execution_client_extra_env.copy()

    def get_execution_client_ports(self) -> dict[str, str]:
        return {
            f"{CLIENT_RPC_PORT}/tcp": f"{CLIENT_RPC_PORT}",
            f"{CLIENT_ENGINE_PORT}/tcp": f"{CLIENT_ENGINE_PORT}",
            f"{CLIENT_METRICS_PORT}/tcp": f"{CLIENT_METRICS_PORT}",
            # Disable p2p
            # f"{CLIENT_P2P_PORT}/tcp": f"{CLIENT_P2P_PORT}",
            # f"{CLIENT_P2P_PORT}/udp": f"{CLIENT_P2P_PORT}",
        }

    def get_execution_metrics_address(self) -> str:
        # Metrics endpoint is required before the actual execution container is started
        return f"{self.get_execution_client_container_name()}:{CLIENT_METRICS_PORT}"

    def get_execution_client_engine_url(
        self,
        container: Container,
        network: Network,
    ) -> str:
        container.reload()
        container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
            "IPAddress"
        ]
        return f"http://{container_ip}:{CLIENT_ENGINE_PORT}"

    def get_execution_client_rpc_url(
        self,
        container: Container,
        network: Network,
    ) -> str:
        container.reload()
        container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
            "IPAddress"
        ]
        return f"http://{container_ip}:{CLIENT_RPC_PORT}"

    def get_execution_client_volumes(self) -> list[dict[str, dict]]:
        execution_container_volumes = []
        container_name = self.get_execution_client_container_name()
        for volume_name, volume_config in self.execution_client_extra_volumes.items():
            source_path = volume_config.get("source", None)
            if source_path is None:
                source_path = self.volumes_dir / volume_name
            execution_container_volumes.append(
                {
                    "bind": volume_config["bind"],
                    "config": {
                        "name": f"{container_name}-{volume_name}",
                        "driver": "local",
                        "driver_opts": {
                            "type": "none",
                            "o": f"bind,{volume_config['mode']}",
                            "device": str(source_path.resolve()),
                        },
                        "labels": {},
                    },
                }
            )

        execution_container_volumes.append(
            {
                "bind": CLIENTS_DATA_DIR,
                "config": {
                    "name": f"{container_name}-overlay-merged",
                    "driver": "local",
                    "driver_opts": {
                        "type": "none",
                        "o": "bind,rw,dirsync,noatime",
                        "device": str(self.overlay_merged_dir.resolve()),
                    },
                },
            }
        )
        execution_container_volumes.append(
            {
                "bind": CLIENTS_JWT_SECRET_DIR,
                "config": {
                    "name": f"{container_name}-jwt-secret",
                    "driver": "local",
                    "driver_opts": {
                        "type": "none",
                        "o": "bind,rw",
                        "device": str(self.jwt_secret_dir.resolve()),
                    },
                },
            }
        )
        return execution_container_volumes

    ### Grafana Alloy
    def get_alloy_container_name(self) -> str:
        return self.get_container_name("alloy")

    def get_alloy_container_image(self) -> str:
        return self.docker_images.get("alloy", ALLOY_DEFAULT_IMAGE)

    def get_alloy_volumes(self) -> dict[str, dict[str, str]]:
        return {
            self.alloy_config_file.resolve(): {
                "bind": "/etc/alloy/config.alloy",
                "mode": "rw",
            },
        }

    def get_alloy_ports(self) -> dict[str, str]:
        return {
            # f"{ALLOY_PYROSCOPE_PORT}/tcp": f"{ALLOY_PYROSCOPE_PORT}",
        }

    def get_alloy_pyroscope_url(
        self,
        container: Container,
        network: Network,
    ) -> str:
        container.reload()
        container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
            "IPAddress"
        ]
        return f"http://{container_ip}:{ALLOY_PYROSCOPE_PORT}"

    def get_alloy_command(self) -> list[str]:
        return ["run", "/etc/alloy/config.alloy"]

    ### Grafana K6
    def get_k6_container_name(self) -> str:
        return self.get_container_name("k6")

    def get_k6_container_image(self) -> str:
        return self.docker_images.get("k6", K6_DEFAULT_IMAGE)

    def get_k6_volumes(self) -> dict[str, dict[str, str]]:
        return {
            self.payloads_file.resolve(): {
                "bind": self._k6_container_payloads_file,
                "mode": "rw",
            },
            self.fcus_file.resolve(): {
                "bind": self._k6_container_fcus_file,
                "mode": "rw",
            },
            self.jwt_secret_file.resolve(): {
                "bind": self._k6_container_jwt_secret_file,
                "mode": "rw",
            },
            self.outputs_dir.resolve(): {
                "bind": self._k6_container_work_dir,
                "mode": "rw",
            },
        }

    def get_k6_environment(self) -> dict[str, str]:
        environment = {}
        if (
            self.exports is not None
            and self.exports.prometheus_remote_write is not None
        ):
            environment["K6_PROMETHEUS_RW_TREND_STATS"] = (
                "min,max,avg,med,p(90),p(95),p(99)"
            )
            environment["K6_PROMETHEUS_RW_SERVER_URL"] = (
                self.exports.prometheus_remote_write.endpoint
            )
            if self.exports.prometheus_remote_write.basic_auth is not None:
                environment["K6_PROMETHEUS_RW_USERNAME"] = (
                    self.exports.prometheus_remote_write.basic_auth.username
                )
                environment["K6_PROMETHEUS_RW_PASSWORD"] = (
                    self.exports.prometheus_remote_write.basic_auth.password
                )
        return environment

    def get_k6_command(
        self,
        execution_client_engine_url: str,
        collect_per_payload_metrics: bool,
    ) -> list[str]:
        command = [
            "run",
            self._k6_container_script_file,
            "--summary-mode=full",
            f"--summary-export={self._k6_container_summary_file}",
            f"--tag=testid={self.test_id}",
            f"--env=EXPB_CONFIG_FILE_PATH={self._k6_container_config_file}",
            f"--env=EXPB_PAYLOADS_FILE_PATH={self._k6_container_payloads_file}",
            f"--env=EXPB_FCUS_FILE_PATH={self._k6_container_fcus_file}",
            f"--env=EXPB_JWTSECRET_FILE_PATH={self._k6_container_jwt_secret_file}",
            f"--env=EXPB_PAYLOADS_SKIP={self.k6_payloads_skip}",
            f"--env=EXPB_PAYLOADS_WARMUP={self.k6_payloads_warmup}",
            f"--env=EXPB_ENGINE_ENDPOINT={execution_client_engine_url}",
            f"--env=EXPB_PER_PAYLOAD_METRICS={int(collect_per_payload_metrics)}",
        ]
        if (
            self.exports is not None
            and self.exports.prometheus_remote_write is not None
        ):
            command.append("--out=experimental-prometheus-rw")
            for tag in self.exports.prometheus_remote_write.tags:
                command.append(f"--tag={tag}")
        else:
            k6_results_jsonl_file = f"{self._k6_container_work_dir}/k6-results.jsonl"
            command.append(f"--out=json={k6_results_jsonl_file}")
        return command
