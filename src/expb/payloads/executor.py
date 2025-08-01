import os
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
from expb.configs.defaults import (
    KUTE_DEFAULT_IMAGE,
    PAYLOADS_DEFAULT_DIR,
    WORK_DEFAULT_DIR,
    LOGS_DEFAULT_DIR,
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
        payloads_dir: Path = PAYLOADS_DEFAULT_DIR,
        work_dir: Path = WORK_DEFAULT_DIR,
        logs_dir: Path = LOGS_DEFAULT_DIR,
        docker_container_cpus: int = DOCKER_CONTAINER_DEFAULT_CPUS,
        docker_container_mem_limit: str = DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
        docker_container_download_speed: str = DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED,
        docker_container_upload_speed: str = DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED,
        execution_client_image: str | None = None,
        kute_image: str = KUTE_DEFAULT_IMAGE,
        kute_filter: str | None = None,
        json_rpc_wait_max_retries: int = 16,
        pull_images: bool = False,
        limit_bandwidth: bool = False,
        prom_pushgateway_endpoint: str | None = None,
        prom_pushgateway_auth_username: str | None = None,
        prom_pushgateway_auth_password: str | None = None,
        prom_pushgateway_tags: list[str] = [],
        logger=Logger(),
    ):
        self.execution_client = execution_client
        self.scenario_name = scenario_name
        self.executor_name = f"expb-executor-{scenario_name}"
        self.network = network
        self.execution_client_image = (
            execution_client_image or self.execution_client.value.default_image
        )
        self.kute_image = kute_image
        self.kute_filter = kute_filter
        self.docker_container_cpus = docker_container_cpus
        self.docker_container_mem_limit = docker_container_mem_limit
        self.docker_container_download_speed = docker_container_download_speed
        self.docker_container_upload_speed = docker_container_upload_speed
        self.json_rpc_wait_max_retries = json_rpc_wait_max_retries
        self.limit_bandwidth = limit_bandwidth

        self.docker_client = docker.from_env()
        self.pull_images = pull_images

        self.payloads_dir = payloads_dir
        self.work_dir = work_dir
        self._overlay_work_dir = self.work_dir / "work"
        self._overlay_upper_dir = self.work_dir / "upper"
        self._overlay_merged_dir = self.work_dir / "merged"
        self._jwt_secret_file = self.work_dir / "jwtsecret.hex"
        self.snapshot_dir = snapshot_dir
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.logs_dir = logs_dir / f"{self.executor_name}-{timestamp}"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.prom_pushgateway_endpoint = prom_pushgateway_endpoint
        self.prom_pushgateway_auth_username = prom_pushgateway_auth_username
        self.prom_pushgateway_auth_password = prom_pushgateway_auth_password
        self.prom_pushgateway_tags = prom_pushgateway_tags

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

    def prepare_jwt_secret_file(self) -> None:
        self._jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self._jwt_secret_file.write_text(secrets.token_bytes(32).hex())

    def pull_docker_images(self) -> None:
        self.log.info("updating docker images")
        self.docker_client.images.pull(self.execution_client_image)
        self.docker_client.images.pull(self.kute_image)
        self.log.info("docker images updated")

    def start_execution_client(
        self,
        container_network: Network | None = None,
    ) -> Container:
        container_command = self.execution_client.value.get_command(self.network)
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
            command=container_command,
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
                "client json rpc is available",
                latest_block=int(response.json()["result"], 16),
            )
        else:
            self.log.error(
                "client json rpc is not available", status_code=response.status_code
            )
            raise Exception("client json rpc is not available")

    def run_kute(
        self,
        execution_client_container: Container,
        container_network: Network | None = None,
    ) -> Container:
        execution_client_container.reload()
        execution_client_ip = execution_client_container.attrs["NetworkSettings"][
            "Networks"
        ][container_network.name]["IPAddress"]
        engine_url = f"http://{execution_client_ip}:{CLIENT_ENGINE_PORT}"

        kute_filter_command = []
        if self.kute_filter:
            kute_filter_command.append("-f")
            kute_filter_command.append(self.kute_filter)

        kute_export_command = []
        if self.prom_pushgateway_endpoint:
            kute_export_command.append("--gateway")
            kute_export_command.append(self.prom_pushgateway_endpoint)
            if self.prom_pushgateway_auth_username:
                kute_export_command.append("--gateway-user")
                kute_export_command.append(self.prom_pushgateway_auth_username)
            if self.prom_pushgateway_auth_password:
                kute_export_command.append("--gateway-pass")
                kute_export_command.append(self.prom_pushgateway_auth_password)

        kute_tags_command = [
            "--labels",
            ",".join(
                [
                    f"scenario={self.scenario_name}",
                    f"instance={self.scenario_name}",
                    f"client={self.execution_client.value.name.lower()}",
                    f"network={self.network.value.name.lower()}",
                    f"image={self.execution_client_image}",
                ]
                + self.prom_pushgateway_tags
            ),
        ]

        kute_command = (
            [
                "--address",
                engine_url,
                "--input",
                "/payloads",
                "--secret",
                CLIENTS_JWT_SECRET_FILE,
                "--output",
                "Json",
            ]
            + kute_tags_command
            + kute_filter_command
            + kute_export_command
        )

        container = self.docker_client.containers.run(
            image=self.kute_image,
            name=f"{self.executor_name}-kute",
            volumes={
                self.payloads_dir.absolute(): {
                    "bind": "/payloads",
                    "mode": "rw",
                },
                self._jwt_secret_file.absolute(): {
                    "bind": CLIENTS_JWT_SECRET_FILE,
                    "mode": "rw",
                },
            },
            command=kute_command,
            network=container_network.name if container_network else None,
            detach=False,
            user=os.getuid(),
            group_add=[os.getgid()],
        )
        return container

    def cleanup_scenario(self) -> None:
        self.log.info("cleaning up scenario", scenario=self.executor_name)
        # Clean kute container
        try:
            kute_container = self.docker_client.containers.get(
                f"{self.executor_name}-kute"
            )
            kute_container.stop()
            logs_file = self.logs_dir / "kute.log"
            self.log.info("saving kute logs", logs_file=logs_file)
            logs_stream = kute_container.logs(
                stream=True,
                follow=False,
                stdout=True,
                stderr=True,
            )
            with open(logs_file, "wb") as f:
                for line in logs_stream:
                    f.write(line)
            logs_stream.close()
            kute_container.remove()
        except docker.errors.NotFound:
            pass

        # Clean execution client container
        try:
            execution_client_container = self.docker_client.containers.get(
                f"{self.executor_name}-{self.execution_client.value.name.lower()}"
            )
            execution_client_container.stop()
            logs_file = (
                self.logs_dir / f"{self.execution_client.value.name.lower()}.log"
            )
            self.log.info("saving execution client logs", logs_file=logs_file)
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
        self.log.info("cleanup completed")

    def execute_scenario(self) -> None:
        try:
            self.log.info(
                "preparing scenario",
                scenario=self.executor_name,
                execution_client=self.execution_client.value.name.lower(),
            )
            self.prepare_directories()
            self.prepare_jwt_secret_file()
            if self.pull_images:
                self.pull_docker_images()

            self.log.info("creating docker network")
            containers_network = self.docker_client.networks.create(
                name=f"{self.executor_name}-network",
                driver="bridge",
            )

            self.log.info(
                "starting execution client",
                execution_client=self.execution_client.value.name.lower(),
                execution_client_image=self.execution_client_image,
                docker_container_cpus=self.docker_container_cpus,
                docker_container_mem_limit=self.docker_container_mem_limit,
            )
            execution_client_container = self.start_execution_client(
                container_network=containers_network,
            )

            if self.limit_bandwidth:
                self.log.info(
                    "limiting container bandwidth",
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
            _ = self.run_kute(
                execution_client_container=execution_client_container,
                container_network=containers_network,
            )

            self.log.info(
                "payloads execution completed",
                execution_client=self.execution_client.value.name.lower(),
            )
        except Exception as e:
            self.log.error("failed to execute scenario", error=e)
            raise e
        finally:
            self.cleanup_scenario()
