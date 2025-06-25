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


class NethermindConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="nethermind",
            default_image="ethpandaops/nethermind:performance",
            default_command=[
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--Network.P2PPort={CLIENT_P2P_PORT}",
                f"--Network.DiscoveryPort={CLIENT_P2P_PORT}",
                "--JsonRpc.Enabled=true",
                "--JsonRpc.Host=0.0.0.0",
                f"--JsonRpc.Port={CLIENT_RPC_PORT}",
                "--Init.WebSocketsEnabled=true",
                f"--JsonRpc.WebSocketsPort={CLIENT_RPC_PORT}",
                f"--JsonRpc.JwtSecretFile={CLIENTS_JWT_SECRET_FILE}",
                "--JsonRpc.EngineHost=0.0.0.0",
                f"--JsonRpc.EnginePort={CLIENT_ENGINE_PORT}",
                "--JsonRpc.EnabledModules=Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Debug,Admin",
                "--Metrics.Enabled=true",
                "--Metrics.NodeName=expb-el-nethermind",
                f"--Metrics.ExposePort={CLIENT_METRICS_PORT}",
                "--Metrics.ExposeHost=0.0.0.0",
            ],
        )

    def get_command(self, network: Network) -> list[str]:
        if network == Network.MAINNET:
            return self.default_command + [
                "--config=mainnet",
                "--Init.BaseDbPath=mainnet",
            ]
