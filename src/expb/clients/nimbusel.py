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


class NimbusELConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="nimbusel",
            default_image="statusim/nimbus-eth1:master",
            default_command=[
                f"--data-dir={CLIENTS_DATA_DIR}",
                "--listen-address=0.0.0.0",
                f"--tcp-port={CLIENT_P2P_PORT}",
                f"--udp-port={CLIENT_P2P_PORT}",
                "--rpc",
                "--http-address=0.0.0.0",
                f"--http-port={CLIENT_RPC_PORT}",
                "--ws",
                "--engine-api",
                "--engine-api-address=0.0.0.0",
                f"--engine-api-port={CLIENT_ENGINE_PORT}",
                f"--jwt-secret={CLIENTS_JWT_SECRET_FILE}",
                "--metrics",
                "--metrics-address=0.0.0.0",
                f"--metrics-port={CLIENT_METRICS_PORT}",
                # Disable peering
                "--discovery=None",
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
