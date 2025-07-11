import yaml

from pathlib import Path

from expb.payloads import Executor
from expb.configs.clients import Client
from expb.configs.networks import Network
from expb.logging import Logger
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


class Scenario:
    def __init__(
        self,
        name: str,
        config: dict[str],
    ) -> None:
        self.name = name
        client_name: str = config.get("client")
        self.client: Client = Client[client_name.upper()]
        self.client_image: str | None = config.get("image", None)
        self.kute_filter: str | None = config.get("kute_filter", None)
        snapshot_dir: str | None = config.get("snapshot_dir", None)
        if snapshot_dir is None:
            raise ValueError(f"Snapshot directory is required for scenario {name}")
        self.snapshot_dir = Path(snapshot_dir)


class Scenarios:
    def __init__(self, config_file: Path):
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        if not isinstance(config, dict):
            raise ValueError("Invalid config file")

        config_network: str = config.get("network", Network.MAINNET.name)
        self.network = Network[config_network.upper()]

        pull_images: bool = config.get("pull_images", False)
        self.pull_images = pull_images

        kute_image: str = config.get("kute_image", KUTE_DEFAULT_IMAGE)
        self.kute_image = kute_image

        directories: dict[str, str] = config.get("directories", {})

        payloads_dir: str = directories.get("payloads", PAYLOADS_DEFAULT_DIR)
        self.payloads_dir = Path(payloads_dir)

        work_dir: str = directories.get("work", WORK_DEFAULT_DIR)
        self.work_dir = Path(work_dir)

        logs_dir: str = directories.get("logs", LOGS_DEFAULT_DIR)
        self.logs_dir = Path(logs_dir)

        export: dict[str] = config.get("export", {})
        if export:
            prometheus_pushgateway: dict[str] | None = export.get(
                "prometheus_pushgateway", None
            )
            if isinstance(prometheus_pushgateway, dict) and prometheus_pushgateway:
                self.prom_pushgateway_endpoint = prometheus_pushgateway.get(
                    "endpoint", None
                )
                prom_pushgateway_basic_auth: dict[str, str] | None = (
                    prometheus_pushgateway.get("basic_auth", None)
                )
                if prom_pushgateway_basic_auth:
                    self.prom_pushgateway_auth_username = (
                        prom_pushgateway_basic_auth.get("username", None)
                    )
                    self.prom_pushgateway_auth_password = (
                        prom_pushgateway_basic_auth.get("password", None)
                    )
                else:
                    self.prom_pushgateway_auth_username = None
                    self.prom_pushgateway_auth_password = None

                self.prom_pushgateway_tags: list[str] = prometheus_pushgateway.get(
                    "tags", []
                )
        else:
            self.prom_pushgateway_endpoint = None
            self.prom_pushgateway_auth_username = None
            self.prom_pushgateway_auth_password = None
            self.prom_pushgateway_tags = []

        resources: dict[str, str] = config.get("resources", {})

        docker_container_cpus: int = resources.get("cpu", DOCKER_CONTAINER_DEFAULT_CPUS)
        self.docker_container_cpus = docker_container_cpus

        docker_container_mem_limit: str = resources.get(
            "mem", DOCKER_CONTAINER_DEFAULT_MEM_LIMIT
        )
        self.docker_container_mem_limit = docker_container_mem_limit

        docker_container_download_speed: str = resources.get(
            "download_speed", DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED
        )
        self.docker_container_download_speed = docker_container_download_speed

        docker_container_upload_speed: str = resources.get(
            "upload_speed", DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED
        )
        self.docker_container_upload_speed = docker_container_upload_speed

        scenarios_configs: dict[str, dict[str]] = config.get("scenarios", {})
        if not isinstance(scenarios_configs, dict):
            raise ValueError("Invalid scenarios")

        self.scenarios: dict[str, Scenario] = {}
        for scenario_name, scenario_config in scenarios_configs.items():
            scenario = Scenario(
                name=scenario_name,
                config=scenario_config,
            )
            self.scenarios[scenario_name] = scenario

    def get_scenario_executor(
        self,
        scenario: Scenario,
        logger: Logger = Logger(),
    ) -> Executor:
        executor = Executor(
            scenario_name=scenario.name,
            network=self.network,
            execution_client=scenario.client,
            execution_client_image=scenario.client_image,
            payloads_dir=self.payloads_dir,
            work_dir=self.work_dir,
            snapshot_dir=scenario.snapshot_dir,
            docker_container_cpus=self.docker_container_cpus,
            docker_container_download_speed=self.docker_container_download_speed,
            docker_container_upload_speed=self.docker_container_upload_speed,
            docker_container_mem_limit=self.docker_container_mem_limit,
            logs_dir=self.logs_dir,
            pull_images=self.pull_images,
            kute_image=self.kute_image,
            kute_filter=scenario.kute_filter,
            prom_pushgateway_endpoint=self.prom_pushgateway_endpoint,
            prom_pushgateway_auth_username=self.prom_pushgateway_auth_username,
            prom_pushgateway_auth_password=self.prom_pushgateway_auth_password,
            prom_pushgateway_tags=self.prom_pushgateway_tags,
            logger=logger,
        )
        return executor
