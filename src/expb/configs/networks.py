from enum import Enum

from web3.types import BlockData


class ForkConfig:
    def __init__(self, name: str, order: int):
        self.name = name
        self.order = order

    def __str__(self) -> str:
        return self.name

    def __lt__(self, other: "ForkConfig") -> bool:
        return self.order < other.order

    def __gt__(self, other: "ForkConfig") -> bool:
        return self.order > other.order

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ForkConfig):
            return False
        return self.name == other.name

    def __ne__(self, other: object) -> bool:
        if not isinstance(other, ForkConfig):
            return False
        return self.name != other.name


class Fork(Enum):
    PARIS = ForkConfig("paris", 0)
    SHANGHAI = ForkConfig("shanghai", 1)
    CANCUN = ForkConfig("cancun", 2)
    PRAGUE = ForkConfig("prague", 3)
    OSAKA = ForkConfig("osaka", 4)

    @classmethod
    def from_name(cls, name: str) -> "Fork":
        name_lower = name.lower()
        for fork in cls:
            fork_name_lower = fork.value.name.lower()
            if fork_name_lower == name_lower:
                return fork

        raise ValueError(f"Invalid fork name: {name}.")

    @classmethod
    def all_fork_names(cls) -> list[str]:
        return [fork.value.name for fork in cls]


class NetworkConfig:
    def __init__(
        self,
        name: str,
        forks_timestamps: dict[Fork, int],
    ):
        self.name = name
        self.forks_timestamps = forks_timestamps

    def get_fork_timestamp(self, fork: Fork) -> int:
        return self.forks_timestamps.get(fork, -1)

    def get_block_fork(self, block: BlockData) -> Fork:
        closest_fork = Fork.PARIS
        closest_fork_timestamp = self.get_fork_timestamp(closest_fork)
        for fork, timestamp in self.forks_timestamps.items():
            if block["timestamp"] >= timestamp and timestamp > closest_fork_timestamp:
                if fork.value > closest_fork.value:
                    closest_fork = fork
                    closest_fork_timestamp = timestamp
        return closest_fork

    def __str__(self) -> str:
        return self.name


class Network(Enum):
    MAINNET = NetworkConfig(
        name="mainnet",
        forks_timestamps={
            Fork.PARIS: 1663224179,
            Fork.SHANGHAI: 1681338455,
            Fork.CANCUN: 1710338135,
            Fork.PRAGUE: 1746612311,
            Fork.OSAKA: 1764798551,
        },
    )

    @classmethod
    def from_name(cls, name: str) -> "Network":
        name_lower = name.lower()
        for network in cls:
            network_name_lower = network.value.name.lower()
            if network_name_lower == name_lower:
                return network

        raise ValueError(f"Invalid network name: {name}.")

    @classmethod
    def all_network_names(cls) -> list[str]:
        return [network.value.name for network in cls]
