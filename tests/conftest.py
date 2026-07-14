import hashlib
import json
from pathlib import Path

import pytest

from expb.configs.networks import Network
from expb.payloads.generator import Generator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def compute_requests_hash(encoded_requests: list[str]) -> str:
    """EIP-7685: sha256(concat(sha256(request) for each non-empty request))."""
    m = hashlib.sha256()
    for req in encoded_requests:
        m.update(hashlib.sha256(bytes.fromhex(req[2:])).digest())
    return "0x" + m.hexdigest()


@pytest.fixture
def generator() -> Generator:
    """A Generator instance whose network config is set but whose EL/Beacon URLs
    are never contacted (only pure helper methods are exercised)."""
    gen = Generator.__new__(Generator)
    gen.network = Network.MAINNET.value
    return gen
