from enum import Enum

from expb.configs.clients.client_config import (
    ClientConfig,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_DIR,
    CLIENTS_JWT_SECRET_FILE,
    CLIENT_RPC_PORT,
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
)
from expb.configs.clients.nethermind import NethermindConfig
from expb.configs.clients.geth import GethConfig
from expb.configs.clients.reth import RethConfig
from expb.configs.clients.erigon import ErigonConfig
from expb.configs.clients.besu import BesuConfig


class Client(Enum):
    NETHERMIND = NethermindConfig()
    BESU = BesuConfig()
    RETH = RethConfig()
    GETH = GethConfig()
    ERIGON = ErigonConfig()


__all__ = [
    "Client",
    "ClientConfig",
    "CLIENTS_DATA_DIR",
    "CLIENTS_JWT_SECRET_DIR",
    "CLIENTS_JWT_SECRET_FILE",
    "CLIENT_RPC_PORT",
    "CLIENT_ENGINE_PORT",
    "CLIENT_METRICS_PORT",
    "CLIENT_P2P_PORT",
]
