import os
import time
from pathlib import Path

import docker
from docker.client import DockerClient
from docker.models.containers import Container
from docker.models.networks import Network

from expb.clients import (
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_RPC_PORT,
    CLIENT_RPC_WS_PORT,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_DIR,
    Client,
)
from expb.configs.exports import Exports
from expb.configs.scenarios import (
    Scenario,
    ScenarioExtraVolume,
    ScenariosImages,
    ScenariosPaths,
    ScenariosResources,
)
from expb.payloads.executor.services.alloy import ALLOY_PYROSCOPE_PORT
from expb.payloads.executor.services.snapshots import SnapshotService


# ExecutorConfig class is a collection of helper functions and configuration options for the Executor class
class ExecutorConfig:
    def __init__(
        self,
        scenario: Scenario,
        snapshot_service: SnapshotService,
        paths: ScenariosPaths,
        resources: ScenariosResources | None = None,
        pull_images: bool = False,
        docker_images: ScenariosImages = ScenariosImages(),
        exports: Exports | None = None,
        json_rpc_wait_max_retries: int = 16,
        limit_bandwidth: bool = False,
    ) -> None:
        # Executor Basic config
        self.scenario_name: str = scenario.name
        self.executor_name: str = f"expb-executor-{self.scenario_name}"
        self.test_id: str = f"{self.scenario_name}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.startup_wait = scenario.startup_wait
        # Executor Client config
        self.network: Network = scenario.network
        self.execution_client: Client = scenario.client
        execution_client_image = scenario.client_image
        if execution_client_image is None:
            execution_client_image = self.execution_client.value.default_image
        self.execution_client_image = execution_client_image
        self.execution_client_extra_flags = scenario.extra_flags
        self.execution_client_extra_env = scenario.extra_env
        self.execution_client_extra_volumes: dict[str, ScenarioExtraVolume] = (
            scenario.extra_volumes
        )
        self.execution_client_extra_commands = scenario.extra_commands

        # Executor Additional Tooling config
        ## Docker client
        self.docker_client: DockerClient = docker.from_env()
        self.docker_images: ScenariosImages = docker_images
        self.pull_images: bool = pull_images
        self.docker_user: int = os.getuid()
        self.docker_group_add: list[int] = [os.getgid()]
        ## Docker container config
        self.resources: ScenariosResources | None = resources
        self.limit_bandwidth: bool = limit_bandwidth
        self.json_rpc_wait_max_retries: int = json_rpc_wait_max_retries
        ## K6 script config
        self.k6_payloads_amount: int = scenario.payloads_amount
        self.k6_payloads_delay: float = scenario.payloads_delay
        self.k6_payloads_warmup_delay: float | None = scenario.payloads_warmup_delay
        self.k6_duration: str = scenario.duration
        self.k6_warmup_duration: str = scenario.warmup_duration
        self.k6_warmup_wait: int = scenario.warmup_wait
        self.k6_payloads_skip: int | None = scenario.payloads_skip
        self.k6_payloads_warmup: int | None = scenario.payloads_warmup

        # Executor Directories
        ## Payloads and FCUs
        self.payloads_file: Path = scenario.payloads_file
        self.fcus_file: Path = scenario.fcus_file

        ## Work directories
        self.work_dir: Path = paths.work
        ### JWT secret file
        self.jwt_secret_dir: Path = self.work_dir / "jwt-secret"
        self.jwt_secret_file: Path = self.jwt_secret_dir / "jwtsecret.hex"

        ## Snapshot config
        self.snapshot_source: str = scenario.snapshot_source
        self.snapshot_service: SnapshotService = snapshot_service

        ## Outputs directory
        timestamp: str = time.strftime("%Y%m%d-%H%M%S")
        self.outputs_dir: Path = paths.outputs / f"{self.executor_name}-{timestamp}"
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

        ## Client Additional Volumes directory
        self.volumes_dir: Path = self.outputs_dir / "volumes"
        if self.execution_client_extra_volumes and any(
            volume
            for volume in self.execution_client_extra_volumes.values()
            if volume.source is None
        ):
            self.volumes_dir.mkdir(parents=True, exist_ok=True)

        ## Client extra Commands outputs directory
        self.extra_commands_outputs_dir: Path = self.outputs_dir / "commands"
        if self.execution_client_extra_commands:
            self.extra_commands_outputs_dir.mkdir(parents=True, exist_ok=True)

        ## K6 script and config files
        self.k6_script_file: Path = self.outputs_dir / "k6-script.js"
        self.k6_config_file: Path = self.outputs_dir / "k6-config.json"
        ### K6 container directories
        self._k6_container_work_dir: str = "/expb"
        self._k6_container_payloads_file: str = f"/payloads/{self.payloads_file.name}"
        self._k6_container_fcus_file: str = f"/payloads/{self.fcus_file.name}"
        self._k6_container_jwt_secret_file: str = f"/{self.jwt_secret_file.name}"
        self._k6_container_script_file: str = (
            f"{self._k6_container_work_dir}/{self.k6_script_file.name}"
        )
        self._k6_container_config_file: str = (
            f"{self._k6_container_work_dir}/{self.k6_config_file.name}"
        )
        self._k6_container_summary_file: str = (
            f"{self._k6_container_work_dir}/k6-summary.json"
        )

        ## Alloy config file
        self.alloy_config_file: Path = self.outputs_dir / "config.alloy"

        # Executor Exports config
        self.exports: Exports | None = exports

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

    def get_execution_client_ports(self) -> dict[str, tuple[str, str]]:
        return {
            f"{CLIENT_RPC_PORT}/tcp": ("127.0.0.1", f"{CLIENT_RPC_PORT}"),
            f"{CLIENT_RPC_WS_PORT}/tcp": ("127.0.0.1", f"{CLIENT_RPC_WS_PORT}"),
            f"{CLIENT_ENGINE_PORT}/tcp": ("127.0.0.1", f"{CLIENT_ENGINE_PORT}"),
            f"{CLIENT_METRICS_PORT}/tcp": ("127.0.0.1", f"{CLIENT_METRICS_PORT}"),
            # Disable p2p
            # f"{CLIENT_P2P_PORT}/tcp": ("127.0.0.1", f"{CLIENT_P2P_PORT}"),
            # f"{CLIENT_P2P_PORT}/udp": ("127.0.0.1", f"{CLIENT_P2P_PORT}"),
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
        if container.attrs is not None:
            container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
                "IPAddress"
            ]
            return f"http://{container_ip}:{CLIENT_ENGINE_PORT}"
        else:
            raise ValueError("Container attributes are not available")

    def get_execution_client_rpc_url(
        self,
        container: Container,
        network: Network,
    ) -> str:
        container.reload()
        if container.attrs is not None:
            container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
                "IPAddress"
            ]
            return f"http://{container_ip}:{CLIENT_RPC_PORT}"
        else:
            raise ValueError("Container attributes are not available")

    def get_execution_client_volumes(self) -> list[dict[str, dict]]:
        execution_container_volumes = []
        container_name = self.get_execution_client_container_name()
        for volume_name, volume_config in self.execution_client_extra_volumes.items():
            if not volume_config:
                continue
            source_path: Path | None = None
            source_path_raw = volume_config.source
            if source_path_raw is None:
                source_path = self.volumes_dir / volume_name
                source_path.mkdir(parents=True, exist_ok=True)
            else:
                source_path = Path(source_path_raw)
            execution_container_volumes.append(
                {
                    "bind": volume_config.bind,
                    "config": {
                        "name": f"{container_name}-{volume_name}",
                        "driver": "local",
                        "driver_opts": {
                            "type": "none",
                            "o": f"bind,{volume_config.mode}",
                            "device": str(source_path.resolve()),
                        },
                        "labels": {},
                    },
                }
            )

        # Add execution client snapshot volume, this must have been created beforehand
        snapshot_path = self.snapshot_service.get_snapshot(
            name=self.executor_name, source=self.snapshot_source
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
                        "device": str(snapshot_path.resolve()),
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
        return self.docker_images.alloy

    def get_alloy_volumes(self) -> dict[str, dict[str, str]]:
        return {
            str(self.alloy_config_file.resolve()): {
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
        if container.attrs is not None:
            container_ip = container.attrs["NetworkSettings"]["Networks"][network.name][
                "IPAddress"
            ]
            return f"http://{container_ip}:{ALLOY_PYROSCOPE_PORT}"
        else:
            raise ValueError("Container attributes are not available")

    def get_alloy_command(self) -> list[str]:
        return ["run", "/etc/alloy/config.alloy"]

    ### Grafana K6
    def get_k6_container_name(self) -> str:
        return self.get_container_name("k6")

    def get_k6_container_image(self) -> str:
        return self.docker_images.k6

    def get_k6_volumes(self) -> dict[str, dict[str, str]]:
        return {
            str(self.payloads_file.resolve()): {
                "bind": self._k6_container_payloads_file,
                "mode": "rw",
            },
            str(self.fcus_file.resolve()): {
                "bind": self._k6_container_fcus_file,
                "mode": "rw",
            },
            str(self.jwt_secret_file.resolve()): {
                "bind": self._k6_container_jwt_secret_file,
                "mode": "rw",
            },
            str(self.outputs_dir.resolve()): {
                "bind": self._k6_container_work_dir,
                "mode": "rw",
            },
        }

    def get_k6_environment(self) -> dict[str, str]:
        environment = {}
        if self.exports and self.exports.prometheus_rw:
            environment["K6_PROMETHEUS_RW_TREND_STATS"] = (
                "min,max,avg,med,p(90),p(95),p(99)"
            )
            environment["K6_PROMETHEUS_RW_SERVER_URL"] = (
                self.exports.prometheus_rw.endpoint
            )
            if self.exports.prometheus_rw.basic_auth:
                environment["K6_PROMETHEUS_RW_USERNAME"] = (
                    self.exports.prometheus_rw.basic_auth.username
                )
                environment["K6_PROMETHEUS_RW_PASSWORD"] = (
                    self.exports.prometheus_rw.basic_auth.password
                )
        return environment

    def get_k6_command(
        self,
        execution_client_engine_url: str,
        collect_per_payload_metrics: bool,
        enable_logging: bool,
        per_payload_metrics_logs: bool,
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
            f"--env=EXPB_PAYLOADS_DELAY={self.k6_payloads_delay}",
            f"--env=EXPB_PAYLOADS_WARMUP_DELAY={self.k6_payloads_warmup_delay}",
            f"--env=EXPB_PAYLOADS_SKIP={self.k6_payloads_skip}",
            f"--env=EXPB_PAYLOADS_WARMUP={self.k6_payloads_warmup}",
            f"--env=EXPB_ENGINE_ENDPOINT={execution_client_engine_url}",
            f"--env=EXPB_PER_PAYLOAD_METRICS={int(collect_per_payload_metrics)}",
            f"--env=EXPB_ENABLE_LOGGING={int(enable_logging)}",
            f"--env=EXPB_PER_PAYLOAD_METRICS_LOGS={int(per_payload_metrics_logs)}",
            f"--env=EXPB_WARMUP_WAIT={self.k6_warmup_wait}",
        ]
        if self.exports is not None and self.exports.prometheus_rw is not None:
            command.append("--out=experimental-prometheus-rw")
            for tag in self.exports.prometheus_rw.tags:
                command.append(f"--tag={tag}")
        else:
            k6_results_jsonl_file = f"{self._k6_container_work_dir}/k6-results.jsonl"
            command.append(f"--out=json={k6_results_jsonl_file}")
        return command
