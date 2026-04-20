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


class NethermindConfig(ClientConfig):
    def __init__(self):
        super().__init__(
            name="nethermind",
            default_image="nethermindeth/nethermind:master",
            default_command=[
                f"--datadir={CLIENTS_DATA_DIR}",
                f"--Network.P2PPort={CLIENT_P2P_PORT}",
                f"--Network.DiscoveryPort={CLIENT_P2P_PORT}",
                "--JsonRpc.Enabled=true",
                "--JsonRpc.Host=0.0.0.0",
                f"--JsonRpc.Port={CLIENT_RPC_PORT}",
                "--Init.WebSocketsEnabled=true",
                f"--JsonRpc.WebSocketsPort={CLIENT_RPC_WS_PORT}",
                f"--JsonRpc.JwtSecretFile={CLIENTS_JWT_SECRET_FILE}",
                "--JsonRpc.EngineHost=0.0.0.0",
                f"--JsonRpc.EnginePort={CLIENT_ENGINE_PORT}",
                "--JsonRpc.EnabledModules=Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Debug,Admin",
                "--Metrics.Enabled=true",
                f"--Metrics.ExposePort={CLIENT_METRICS_PORT}",
                "--Metrics.ExposeHost=0.0.0.0",
                # Required for SSE data feed (/data/events)
                "--HealthChecks.Enabled=true",
                "--Init.LogRules=Consensus.Processing.ProcessingStats:Debug",
                # Disable peering
                "--Init.DiscoveryEnabled=false",
                "--Network.MaxActivePeers=0",
                # Suppress forced GC between blocks for stable benchmarks
                "--Merge.SweepMemory=NoGC",
                "--Merge.CompactMemory=No",
                "--Merge.CollectionsPerDecommit=-1",
            ],
            prometheus_metrics_path="/metrics",
            sse_data_feed_path="/data/events",
            default_env={
                "DOTNET_TieredCompilation": "0",
                "DOTNET_GCLatencyLevel": "0",
            },
            entrypoint="/nethermind/nethermind",
        )

    def get_command(
        self,
        instance: str,
        network: Network,
        extra_flags: list[str] = [],
    ) -> list[str]:
        command = [
            f"--Metrics.NodeName={instance}",
        ]
        if network == Network.MAINNET:
            command.extend(
                [
                    "--config=mainnet",
                    "--Init.BaseDbPath=mainnet",
                ]
            )
        return self.default_command + command + extra_flags
