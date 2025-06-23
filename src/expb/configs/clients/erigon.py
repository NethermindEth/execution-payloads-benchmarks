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


class ErigonConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="erigon",
            default_image="ethpandaops/erigon:performance",
            default_command=[
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--port={CLIENT_P2P_PORT}",
                "--http",
                "--http.addr=0.0.0.0",
                f"--http.port={CLIENT_RPC_PORT}",
                f"--torrent.port={CLIENT_P2P_PORT}",
                f"--authrpc.jwtsecret={CLIENTS_JWT_SECRET_FILE}",
                "--authrpc.addr=0.0.0.0",
                f"--authrpc.port={CLIENT_ENGINE_PORT}",
                "--authrpc.vhosts=*",
                "--metrics",
                "--metrics.addr=0.0.0.0",
                f"--metrics.port={CLIENT_METRICS_PORT}",
                "--http.api=eth,erigon,engine,web3,net,debug,trace,txpool,admin",
                "--http.vhosts=*",
                "--ws",
                "--prune.mode=full",
                "--externalcl",
            ],
        )

    def get_command(self, network: Network) -> list[str]:
        if network == Network.MAINNET:
            return self.default_command + [
                "--chain=mainnet",
            ]
        else:
            raise ValueError(f"Network {network} not supported")
