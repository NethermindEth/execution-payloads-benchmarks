import yaml

from pathlib import Path

from expb.payloads import Executor, ExecutorConfig
from expb.configs.clients import Client
from expb.configs.networks import Network
from expb.logging import Logger
from expb.configs.exports import Exports
from expb.configs.defaults import (
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
        # Name of the scenario
        self.name = name
        # Name of the client to use
        client_name: str = config.get("client")
        self.client: Client = Client[client_name.upper()]
        # Image of the client to use
        self.client_image: str | None = config.get("image", None)
        # Skip number of payloads
        self.payloads_skip: int = config.get("skip", 0)
        if not isinstance(self.payloads_skip, int):
            raise ValueError(f"Skip number of payloads is invalid for scenario {name}")
        # Amount of payloads to run
        self.payloads_amount: int | None = config.get("amount", None)
        if self.payloads_amount is None or not isinstance(self.payloads_amount, int):
            raise ValueError(f"Amount of payloads is required for scenario {name}")
        # Payload to use as warmup(no metrics will be collected for those)
        self.payloads_warmup: int = config.get("warmup", 0)
        if not isinstance(self.payloads_warmup, int):
            raise ValueError(
                f"Warmup number of payloads is invalid for scenario {name}"
            )
        # Duration of the scenario
        self.duration: str = config.get("duration", "10m")
        if self.duration is None or not isinstance(self.duration, str):
            raise ValueError(f"Duration is invalid for scenario {name}")
        # Snapshot directory to use
        snapshot_dir: str | None = config.get("snapshot_dir", None)
        if snapshot_dir is None:
            raise ValueError(f"Snapshot directory is required for scenario {name}")
        self.snapshot_dir = Path(snapshot_dir)
        # Wait time for client startup in seconds
        self.startup_wait: int = config.get("startup_wait", 30)
        if not isinstance(self.startup_wait, int):
            raise ValueError(f"Startup wait time is invalid for scenario {name}")
        # Extra flags to pass to the client
        self.extra_flags: list[str] = config.get("extra_flags", [])
        if not isinstance(self.extra_flags, list):
            raise ValueError(f"Extra flags must be a list for scenario {name}")
        # Extra environment variables to pass to the client
        self.extra_env: dict[str, str] = config.get("extra_env", {})
        if not isinstance(self.extra_env, dict):
            raise ValueError(
                f"Extra environment variables must be a dict for scenario {name}"
            )
        # Extra volumes to mount into the docker container
        self.extra_volumes: dict[str, dict[str, str]] = {}
        extra_volumes: dict[str, dict[str, str]] = config.get("extra_volumes", {})
        if not isinstance(self.extra_volumes, dict):
            raise ValueError(f"Extra volumes must be a dict for scenario {name}")
        for volume_name, volume_config in extra_volumes.items():
            bind_path = volume_config.get("bind", None)
            if bind_path is None:
                raise ValueError(
                    f"Bind path is required for volume {volume_name} for scenario {name}"
                )
            source_path = volume_config.get("source", None)
            mode = volume_config.get("mode", "rw")
            self.extra_volumes[volume_name] = {
                "bind": bind_path,
                "mode": mode,
                # This is a custom field not used by the docker api but it's used by the executor
                "source": source_path,
            }
        # Extra commands to run in the docker container during the test execution
        self.extra_commands: list[str] = config.get("extra_commands", [])
        if not isinstance(self.extra_commands, list):
            raise ValueError(f"Extra commands must be a list for scenario {name}")


class Scenarios:
    def __init__(self, config_file: Path):
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        if not isinstance(config, dict):
            raise ValueError("Invalid config file")

        # Parse network configuration
        config_network: str = config.get("network", Network.MAINNET.name)
        self.network = Network[config_network.upper()]

        # Parse docker images configurations
        pull_images: bool = config.get("pull_images", False)
        self.pull_images = pull_images

        images: dict[str, str] = config.get("images", {})
        self.docker_images = images

        # Paths for the payloads jsonl file, fcus jsonl file, work directory, and outputs directory
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

        # Parse resources configurations
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

        # Parse scenarios configurations
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
            config=ExecutorConfig(
                scenario_name=scenario.name,
                network=self.network,
                execution_client=scenario.client,
                execution_client_image=scenario.client_image,
                execution_client_extra_flags=scenario.extra_flags,
                execution_client_extra_env=scenario.extra_env,
                execution_client_extra_volumes=scenario.extra_volumes,
                execution_client_extra_commands=scenario.extra_commands,
                startup_wait=scenario.startup_wait,
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
                docker_images=self.docker_images,
                k6_duration=scenario.duration,
                k6_payloads_amount=scenario.payloads_amount,
                k6_payloads_skip=scenario.payloads_skip,
                k6_payloads_warmup=scenario.payloads_warmup,
                exports=self.exports,
            ),
            logger=logger,
        )
        return executor
