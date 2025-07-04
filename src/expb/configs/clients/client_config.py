from expb.configs.networks import Network

CLIENTS_DATA_DIR = "/execution-data"
CLIENTS_JWT_SECRET_FILE = "/jwtsecret.hex"

CLIENT_RPC_PORT = 8545
CLIENT_ENGINE_PORT = 8551
CLIENT_METRICS_PORT = 6060
CLIENT_P2P_PORT = 30303


class ClientConfig:
    def __init__(
        self,
        name: str,
        default_image: str,
        default_command: list[str] = [],
        default_env: dict[str, str] = {},
    ):
        self.name = name
        self.default_image = default_image
        self.default_command = default_command
        self.default_env = default_env

    def get_command(self, network: Network) -> list[str]:
        raise NotImplementedError("get_network_command is not implemented")

    def __str__(self) -> str:
        return self.name
