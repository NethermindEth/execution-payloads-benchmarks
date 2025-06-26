from expb.configs.clients.client_config import (
    ClientConfig,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_FILE,
    CLIENT_RPC_PORT,
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
)
from expb.configs.networks import Network


class GethConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="geth",
            default_image="ethpandaops/geth:performance",
            default_command=[
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--port={CLIENT_P2P_PORT}",
                "--http",
                "--http.addr=0.0.0.0",
                f"--http.port={CLIENT_RPC_PORT}",
                "--http.vhosts=*",
                "--http.api=eth,net,web3,debug,admin",
                "--authrpc.addr=0.0.0.0",
                f"--authrpc.port={CLIENT_ENGINE_PORT}",
                "--authrpc.vhosts=*",
                f"--authrpc.jwtsecret={CLIENTS_JWT_SECRET_FILE}",
                "--metrics",
                f"--metrics.port={CLIENT_METRICS_PORT}",
                "--metrics.addr=0.0.0.0",
                "--discovery.v5",
                "--ws",
                "--ws.addr=0.0.0.0",
                f"--ws.port={CLIENT_RPC_PORT}",
                "--ws.api=eth,web3,net,debug,admin",
            ],
        )

    def get_command(self, network: Network) -> list[str]:
        if network == Network.MAINNET:
            return self.default_command + [
                "--mainnet",
                "--syncmode=full",
            ]
