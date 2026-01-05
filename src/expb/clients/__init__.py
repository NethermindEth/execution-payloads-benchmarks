from enum import Enum

from expb.clients.besu import BesuConfig
from expb.clients.client_config import (
    CLIENT_ENGINE_PORT,
    CLIENT_METRICS_PORT,
    CLIENT_P2P_PORT,
    CLIENT_RPC_PORT,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_DIR,
    CLIENTS_JWT_SECRET_FILE,
    ClientConfig,
)
from expb.clients.erigon import ErigonConfig
from expb.clients.geth import GethConfig
from expb.clients.nethermind import NethermindConfig
from expb.clients.reth import RethConfig


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
