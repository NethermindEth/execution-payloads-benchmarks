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


class RethConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="reth",
            default_image="ethpandaops/reth:performance",
            default_command=[
                "node",
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--log.file.directory={CLIENTS_DATA_DIR}/logs",
                f"--port={CLIENT_P2P_PORT}",
                "--http",
                "--http.addr=0.0.0.0",
                f"--http.port={CLIENT_RPC_PORT}",
                "--authrpc.addr=0.0.0.0",
                f"--authrpc.port={CLIENT_ENGINE_PORT}",
                f"--authrpc.jwtsecret={CLIENTS_JWT_SECRET_FILE}",
                f"--metrics=0.0.0.0:{CLIENT_METRICS_PORT}",
                "--http.api=trace,rpc,eth,net,debug,web3,admin",
                # Disable peering
                "--disable-discovery",
                "--max-inbound-peers=0",
                "--max-outbound-peers=0",
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
                    "--full",
                ]
            )
        return self.default_command + command + extra_flags
