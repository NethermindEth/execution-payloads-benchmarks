import yaml

from pathlib import Path

from expb.payloads import Executor
from expb.configs.clients import Client
from expb.configs.networks import Network
from expb.logging import Logger
from expb.configs.exports import Exports
from expb.configs.defaults import (
    K6_DEFAULT_IMAGE,
    PAYLOADS_DEFAULT_FILE,
    FCUS_DEFAULT_FILE,
    WORK_DEFAULT_DIR,
    OUTPUTS_DEFAULT_DIR,
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
        self.payloads_delay: float | None = config.get("delay", None)
        if self.payloads_delay is None:
            raise ValueError(f"Delay between payloads is required for scenario {name}")
        self.payloads_amount: int | None = config.get("amount", None)
        if self.payloads_amount is None:
            raise ValueError(f"Amount of payloads is required for scenario {name}")
        snapshot_dir: str | None = config.get("snapshot_dir", None)
        if snapshot_dir is None:
            raise ValueError(f"Snapshot directory is required for scenario {name}")
        self.snapshot_dir = Path(snapshot_dir)
        self.payloads_start: int | None = config.get("start", 1)


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

        k6_image: str = config.get("k6_image", K6_DEFAULT_IMAGE)
        self.k6_image = k6_image

        paths: dict[str, str] = config.get("paths", {})

        payloads_file: str = paths.get("payloads", PAYLOADS_DEFAULT_FILE)
        self.payloads_file = Path(payloads_file)

        fcus_file: str = paths.get("fcus", FCUS_DEFAULT_FILE)
        self.fcus_file = Path(fcus_file)

        work_dir: str = paths.get("work", WORK_DEFAULT_DIR)
        self.work_dir = Path(work_dir)

        outputs_dir: str = paths.get("outputs", OUTPUTS_DEFAULT_DIR)
        self.outputs_dir = Path(outputs_dir)

        # Parse export configurations
        exports: dict[str] = config.get("export", {})
        if exports and isinstance(exports, dict):
            self.exports = Exports(exports)

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
            payloads_file=self.payloads_file,
            fcus_file=self.fcus_file,
            work_dir=self.work_dir,
            snapshot_dir=scenario.snapshot_dir,
            docker_container_cpus=self.docker_container_cpus,
            docker_container_download_speed=self.docker_container_download_speed,
            docker_container_upload_speed=self.docker_container_upload_speed,
            docker_container_mem_limit=self.docker_container_mem_limit,
            outputs_dir=self.outputs_dir,
            pull_images=self.pull_images,
            k6_image=self.k6_image,
            k6_payloads_amount=scenario.payloads_amount,
            k6_payloads_delay=scenario.payloads_delay,
            k6_payloads_start=scenario.payloads_start,
            exports=self.exports,
            logger=logger,
        )
        return executor
