from expb.clients.client_config import (
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
    CLIENT_RPC_PORT,
    CLIENT_RPC_WS_PORT,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_FILE,
    ClientConfig,
)
from expb.configs.networks import Network


class EthrexConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="ethrex",
            default_image="ghcr.io/lambdaclass/ethrex:main",
            default_command=[
                "--syncmode=full",
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--p2p.port={CLIENT_P2P_PORT}",
                f"--discovery.port={CLIENT_P2P_PORT}",
                "--http.addr=0.0.0.0",
                f"--http.port={CLIENT_RPC_PORT}",
                "--ws.enabled",
                "--ws.addr=0.0.0.0",
                f"--ws.port={CLIENT_RPC_WS_PORT}",
                "--authrpc.addr=0.0.0.0",
                f"--authrpc.port={CLIENT_ENGINE_PORT}",
                f"--authrpc.jwtsecret={CLIENTS_JWT_SECRET_FILE}",
                "--metrics",
                "--metrics.addr=0.0.0.0",
                f"--metrics.port={CLIENT_METRICS_PORT}",
                # Disable peering
                "--p2p.disabled",
            ],
            prometheus_metrics_path="/metrics",
            default_env={},
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
