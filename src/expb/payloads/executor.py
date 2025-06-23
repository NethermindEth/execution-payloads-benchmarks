import time
import shutil
import docker
import secrets
import requests
import subprocess

from pathlib import Path
from urllib3.util.retry import Retry
from docker.models.containers import Container

from expb.logging import Logger
from expb.configs.networks import Network
from expb.configs.clients import Client, CLIENTS_DATA_DIR, CLIENTS_JWT_SECRET_FILE
from expb.configs.clients import (
    CLIENT_RPC_PORT,
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
)
from expb.payloads.utils.networking import limit_container_bandwidth

KUTE_DEFAULT_IMAGE = "nethermindeth/kute:latest"


class Executor:
    def __init__(
        self,
        network: Network,
        execution_client: Client,
        payloads_dir: Path,
        work_dir: Path,
        snapshot_dir: Path,
        logs_dir: Path,
        docker_container_cpus: float = 4.0,
        docker_container_mem_limit: str = "32g",
        docker_container_download_speed: str = "50mbit",
        docker_container_upload_speed: str = "15mbit",
        execution_client_image: str | None = None,
        kute_image: str = KUTE_DEFAULT_IMAGE,
        json_rpc_wait_max_retries: int = 16,
        logger=Logger(),
    ):
        self.execution_client = execution_client
        self.executor_name = f"expb-el-{self.execution_client.value.name}"
        self.network = network
        self.execution_client_image = (
            execution_client_image or self.execution_client.value.default_image
        )
        self.kute_image = kute_image
        self.docker_container_cpus = docker_container_cpus
        self.docker_container_mem_limit = docker_container_mem_limit
        self.docker_container_download_speed = docker_container_download_speed
        self.docker_container_upload_speed = docker_container_upload_speed
        self.json_rpc_wait_max_retries = json_rpc_wait_max_retries

        self.docker_client = docker.from_env()

        self.payloads_dir = payloads_dir
        self.work_dir = work_dir
        self._overlay_work_dir = self.work_dir / "work"
        self._overlay_upper_dir = self.work_dir / "upper"
        self._overlay_merged_dir = self.work_dir / "merged"
        self._jwt_secret_file = self.work_dir / "jwtsecret.hex"
        self.snapshot_dir = snapshot_dir
        self.logs_dir = logs_dir

        self.log = logger

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
        mount_command = [
            "mount",
            "-t",
            "overlay",
            device_name,
            "-o",
            ",".join(
                [
                    f"lowerdir={self.snapshot_dir}",
                    f"upperdir={self._overlay_upper_dir}",
                    f"workdir={self._overlay_work_dir}",
                ]
            ),
            self._overlay_merged_dir,
        ]
        try:
            subprocess.run(mount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to mount overlay", error=e)
            raise e

    def remove_directories(self) -> None:
        umount_command = ["umount", self._overlay_merged_dir]
        try:
            subprocess.run(umount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to umount overlay", error=e)
            raise e
        try:
            shutil.rmtree(self.work_dir.absolute())
        except Exception as e:
            self.log.error("failed to cleanup work directory", error=e)
            raise e

    def prepare_jwt_secret_file(self) -> None:
        self._jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self._jwt_secret_file.write_text(secrets.token_urlsafe(32))

    def start_execution_client(
        self,
        container_network: Network | None = None,
    ) -> Container:
        container_command = self.execution_client.value.get_command(self.network)
        container = self.docker_client.containers.run(
            image=self.execution_client_image,
            name=self.executor_name,
            volumes={
                self._overlay_merged_dir: {
                    "bind": CLIENTS_DATA_DIR,
                    "mode": "rw",
                },
                self._jwt_secret_file: {
                    "bind": CLIENTS_JWT_SECRET_FILE,
                    "mode": "rw",
                },
            },
            ports={
                f"{CLIENT_RPC_PORT}/tcp": f"{CLIENT_RPC_PORT}",
                f"{CLIENT_ENGINE_PORT}/tcp": f"{CLIENT_ENGINE_PORT}",
                f"{CLIENT_METRICS_PORT}/tcp": f"{CLIENT_METRICS_PORT}",
                f"{CLIENT_P2P_PORT}/tcp": f"{CLIENT_P2P_PORT}",
                f"{CLIENT_P2P_PORT}/udp": f"{CLIENT_P2P_PORT}",
            },
            command=container_command,
            network=container_network.name if container_network else None,
            detach=True,
            cpu_count=self.docker_container_cpus,  # Only works for windows
            nano_cpus=self.docker_container_cpus * 10**9,
            mem_limit=self.docker_container_mem_limit,
        )
        return container

    def wait_for_client_json_rpc(self) -> None:
        time.sleep(10)
        json_rpc_url = f"http://localhost:{CLIENT_RPC_PORT}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1,
        }
        response: requests.Response = requests.post(
            json_rpc_url,
            json=payload,
            headers=headers,
            retries=Retry(
                total=self.json_rpc_wait_max_retries,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
            ),
        )
        if response.ok:
            self.log.info(
                "client json rpc is available",
                latest_block=response.json()["result"],
            )
        else:
            self.log.error(
                "client json rpc is not available", status_code=response.status_code
            )
            raise Exception("client json rpc is not available")

    def run_kute(
        self,
        container_network: Network | None = None,
    ) -> Container:
        container = self.docker_client.containers.run(
            image=self.kute_image,
            name=f"{self.executor_name}-kute",
            volumes={
                self.payloads_dir: {
                    "bind": "/payloads",
                    "mode": "rw",
                },
                self._jwt_secret_file: {
                    "bind": CLIENTS_JWT_SECRET_FILE,
                    "mode": "rw",
                },
            },
            command=[
                "--input",
                "/payloads",
                "--secret",
                CLIENTS_JWT_SECRET_FILE,
                "-p",
            ],
            network=container_network.name if container_network else None,
            detach=False,
        )
        return container

    def execute_scenarios(self) -> None:
        self.log.info("preparing scenario", execution_client=self.execution_client)
        self.prepare_directories()
        self.prepare_jwt_secret_file()

        self.log.info("creating docker network")
        containers_network = self.docker_client.networks.create(
            name=self.executor_name,
            driver="bridge",
        )

        self.log.info(
            "starting execution client",
            execution_client=self.execution_client,
            execution_client_image=self.execution_client_image,
            docker_container_cpus=self.docker_container_cpus,
            docker_container_mem_limit=self.docker_container_mem_limit,
        )
        execution_client_container = self.start_execution_client(
            container_network=containers_network,
        )

        self.log.info(
            "limiting container bandwidth",
            execution_client=self.execution_client,
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
            self.log.error("failed to limit container bandwidth", error=e)
            raise e

        self.log.info("waiting for client json rpc to be available")
        try:
            self.wait_for_client_json_rpc()
        except Exception as e:
            self.log.error("failed to wait for client json rpc", error=e)
            raise e

        self.log.info(
            "running kute",
            kute_docker_image=self.kute_image,
        )
        kute_container = self.run_kute(container_network=containers_network)

        self.log.info(
            "payloads execution completed", execution_client=self.execution_client
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        logs_file = self.logs_dir / f"{self.executor_name}-{timestamp}.log"
        execution_client_logs = execution_client_container.logs()
        with open(logs_file, "w") as f:
            f.write(execution_client_logs)

        self.log.info("cleaning up")
        kute_container.stop()
        kute_container.remove()
        execution_client_container.stop()
        execution_client_container.remove()
        containers_network.remove()
        self.remove_directories()
        self.log.info("cleanup completed")
