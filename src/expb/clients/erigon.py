from expb.clients.client_config import (
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
    CLIENT_RPC_PORT,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_FILE,
    ClientConfig,
)
from expb.configs.networks import Network


class ErigonConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="erigon",
            default_image="erigontech/erigon:main-latest",
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
                # Disable peering
                "--nodiscover",
                "--maxpeers=0",
            ],
            prometheus_metrics_path="/debug/metrics/prometheus",
        )

    def get_command(
        self,
        instance: str,
        network: Network,
        extra_flags: list[str] = [],
    ) -> list[str]:
        command = []
        if network == Network.MAINNET:
            command.extend(
                [
                    "--chain=mainnet",
                ]
            )
        return self.default_command + command + extra_flags
