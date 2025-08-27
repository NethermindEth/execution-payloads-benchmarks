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


class BesuConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="besu",
            default_image="ethpandaops/besu:performance",
            default_command=[
                f"--data-path={CLIENTS_DATA_DIR}",
                "--p2p-host=0.0.0.0",
                f"--p2p-port={CLIENT_P2P_PORT}",
                "--rpc-http-enabled",
                "--rpc-http-host=0.0.0.0",
                f"--rpc-http-port={CLIENT_RPC_PORT}",
                "--rpc-http-cors-origins='*'",
                "--host-allowlist='*'",
                f"--engine-jwt-secret={CLIENTS_JWT_SECRET_FILE}",
                f"--engine-rpc-port={CLIENT_ENGINE_PORT}",
                "--engine-host-allowlist='*'",
                "--metrics-enabled",
                "--metrics-host=0.0.0.0",
                f"--metrics-port={CLIENT_METRICS_PORT}",
                "--rpc-http-api=ADMIN,DEBUG,ETH,MINER,NET,TRACE,TXPOOL,WEB3",
                "--sync-mode=FULL",
                "--version-compatibility-protection=false",
            ],
            prometheus_metrics_path="/metrics",
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
                    "--network=mainnet",
                ]
            )
        return self.default_command + command + extra_flags
