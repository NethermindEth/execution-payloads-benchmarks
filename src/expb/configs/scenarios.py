from pathlib import Path

from pydantic import (
    BaseModel,
    Field,
    FilePath,
    NewPath,
    field_serializer,
    field_validator,
    model_validator,
)

from expb.clients import Client
from expb.configs.defaults import (
    ALLOY_DEFAULT_IMAGE,
    DOCKER_CONTAINER_DEFAULT_CPUS,
    DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED,
    DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
    DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED,
    K6_DEFAULT_IMAGE,
    OUTPUTS_DEFAULT_DIR,
    WORK_DEFAULT_DIR,
)
from expb.configs.exports import Exports
from expb.configs.networks import Network
from expb.configs.snapshots import SnapshotBackend


class ScenarioExtraVolume(BaseModel):
    bind: str = Field(
        description="Path to the volume bind inside the execution client docker container.",
        min_length=1,
    )
    source: NewPath | None = Field(
        description="Path to the volume source on the host.",
        default=None,
    )
    mode: str = Field(
        description="Mode of the volume.",
        default="rw",
    )


class Scenario(BaseModel):
    # General
    name: str | None = Field(
        description="Name of the scenario.",
        default=None,
    )
    client: Client = Field(
        description="Execution client.",
    )
    payloads_file: FilePath = Field(
        description="Path to the payloads requests.",
        alias="payloads",
    )
    fcus_file: FilePath = Field(
        description="Path to the forkchoice updated requests.",
        alias="fcus",
    )
    network: Network = Field(
        description="Ethereum network to use for the scenario.",
        default=Network.MAINNET,
    )
    client_image: str | None = Field(
        description="Execution client image.",
        alias="image",
        default=None,
    )
    # Payloads configuration
    payloads_skip: int | None = Field(
        description="Number of payloads to skip.",
        alias="skip",
        default=0,
        ge=0,
    )
    payloads_amount: int = Field(
        description="Number of payloads to execute.",
        alias="amount",
        default=1,
        ge=1,
    )
    payloads_warmup: int | None = Field(
        description="Number of payloads to execute as warmup(no metrics will be collected for those).",
        alias="warmup",
        default=None,
        ge=0,
    )
    payloads_delay: float = Field(
        description="Delay between payloads requests in seconds.",
        alias="delay",
        default=0.0,
        ge=0.0,
    )
    payloads_warmup_delay: float | None = Field(
        description="Delay between warmup payloads requests in seconds.",
        alias="warmup_delay",
        default=None,
        ge=0.0,
    )
    # Bench execution configuration
    duration: str = Field(
        description="Duration of the scenario.",
        default="10m",
    )
    warmup_duration: str = Field(
        description="Duration of the scenario warmup (k6 setup duration).",
        default="10m",
    )
    startup_wait: int = Field(
        description="Wait time for client startup in seconds.",
        default=30,
        ge=0,
    )
    warmup_wait: int = Field(
        description="Wait time between warmup and payloads requests in seconds.",
        default=0,
        ge=0,
    )
    # Snapshot
    snapshot_source: str = Field(
        description="Snapshot source for the selected client and network (either a path or zfs snapshot name).",
    )
    snapshot_backend: SnapshotBackend = Field(
        description="Snapshot backend to use.",
        default=SnapshotBackend.OVERLAY,
    )
    snapshot_path: Path | None = Field(
        description="Path to the snapshot directory for copy backend (overrides work_dir).",
        default=None,
    )
    # Execution client configuration
    extra_flags: list[str] = Field(
        description="Extra flags to pass to the execution client.",
        default=[],
    )
    extra_env: dict[str, str] = Field(
        description="Extra environment variables to pass to the execution client.",
        default={},
    )
    extra_volumes: dict[str, ScenarioExtraVolume] = Field(
        description="Extra volumes to mount into the execution client docker container.",
        default={},
    )
    extra_commands: list[str] = Field(
        description="Extra commands to run in the execution client docker container during the test execution.",
        default=[],
    )

    @field_validator("client", mode="before")
    @classmethod
    def validate_client(cls, v) -> Client:
        if isinstance(v, str):
            return Client.from_name(v)
        elif isinstance(v, Client):
            return v
        else:
            raise ValueError(f"Invalid client: {v}")

    @field_serializer("client")
    def serialize_client(self, v: Client) -> str:
        return v.value.name

    @field_validator("network", mode="before")
    @classmethod
    def validate_network(cls, v) -> Network:
        if isinstance(v, str):
            return Network.from_name(v)
        elif isinstance(v, Network):
            return v
        else:
            raise ValueError(f"Invalid network: {v}")

    @field_serializer("network")
    def serialize_network(self, v: Network) -> str:
        return v.value.name

    @model_validator(mode="after")
    def validate_payloads_delays(self):
        if self.payloads_warmup_delay is None:
            self.payloads_warmup_delay = self.payloads_delay
        return self


class ScenariosPaths(BaseModel):
    work: Path = Field(
        description="Path to the work directory.",
        default=WORK_DEFAULT_DIR,
    )
    outputs: Path = Field(
        description="Path to the outputs directory.",
        default=OUTPUTS_DEFAULT_DIR,
    )


class ScenariosResources(BaseModel):
    cpu: int = Field(
        description="Number of CPUs to use for the scenario.",
        default=DOCKER_CONTAINER_DEFAULT_CPUS,
    )
    mem: str = Field(
        description="Memory limit for the scenario.",
        default=DOCKER_CONTAINER_DEFAULT_MEM_LIMIT,
    )
    download_speed: str = Field(
        description="Download speed for the scenario.",
        default=DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED,
    )
    upload_speed: str = Field(
        description="Upload speed for the scenario.",
        default=DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED,
    )


class ScenariosImages(BaseModel):
    k6: str = Field(
        description="Image to use for the k6 container.",
        default=K6_DEFAULT_IMAGE,
    )
    alloy: str = Field(
        description="Image to use for the alloy container.",
        default=ALLOY_DEFAULT_IMAGE,
    )


class Scenarios(BaseModel):
    # General
    pull_images: bool = Field(
        description="Pull the docker images before execution.",
        default=False,
    )
    docker_images: ScenariosImages = Field(
        description="Images configuration for the scenarios.",
        alias="images",
        default=ScenariosImages(),
    )
    paths: ScenariosPaths = Field(
        description="Paths configuration for the scenarios.",
    )
    # Exports
    exports: Exports | None = Field(
        description="Exports configuration for the scenarios.",
        default=None,
    )
    # Resources
    resources: ScenariosResources | None = Field(
        description="Resources configuration for the scenarios.",
        default=None,
    )
    # Scenarios
    scenarios_configs: dict[str, Scenario] = Field(
        description="Scenarios configurations.",
        alias="scenarios",
        default={},
    )

    @model_validator(mode="after")
    def validate_scenarios(self):
        if len(self.scenarios_configs) == 0:
            raise ValueError("Scenarios configuration cannot be empty")

        # Set scenario name to the scenario config key
        for scenario_name, scenario_config in self.scenarios_configs.items():
            scenario_config.name = scenario_name
        return self
