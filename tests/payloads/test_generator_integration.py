"""End-to-end fidelity test against a live mainnet archive (EL + Beacon).

Deselected by default; run with:
    uv run pytest -m integration
Endpoints can be overridden via EXPB_TEST_RPC_URL / EXPB_TEST_BEACON_URL.
"""
import os
from pathlib import Path

import pytest

from expb.configs.networks import Network
from expb.payloads.generator import Generator
from tests.conftest import compute_requests_hash

pytestmark = pytest.mark.integration

RPC_URL = os.environ.get("EXPB_TEST_RPC_URL", "http://38.154.254.162:8545")
BEACON_URL = os.environ.get("EXPB_TEST_BEACON_URL", "http://38.154.254.162:4000")

# Prague blocks validated previously: (block_number, note)
BLOCKS = [25488796, 25488822, 25488855]


@pytest.fixture(scope="module")
def gen(tmp_path_factory) -> Generator:
    return Generator(
        network=Network.MAINNET,
        rpc_url=RPC_URL,
        beacon_url=BEACON_URL,
        start_block=BLOCKS[0],
        end_block=BLOCKS[0],
        output_dir=Path(tmp_path_factory.mktemp("payloads")),
    )


@pytest.mark.parametrize("block_number", BLOCKS)
def test_generated_new_payload_matches_canonical_block(gen, block_number):
    block = gen.w3.eth.get_block(block_number)
    el_block = gen.w3.eth.get_block(block_number, full_transactions=True)
    slot = gen.network.slot_from_timestamp(block["timestamp"])
    message = gen.get_beacon_block(slot)
    ep = message["body"]["execution_payload"]
    assert int(ep["block_number"]) == block_number

    version = gen.get_payload_version(block)
    req = gen.get_new_payload_request(block_number, message, version)
    payload = req["params"][0]

    # block hash + tx count == canonical block
    assert payload["blockHash"].lower() == block["hash"].to_0x_hex().lower()
    assert len(payload["transactions"]) == len(el_block["transactions"])

    if version >= 3:
        blob_hashes, parent_beacon_root = req["params"][1], req["params"][2]
        assert parent_beacon_root.lower() == block["parentBeaconBlockRoot"].to_0x_hex().lower()
        el_blob_hashes = [
            vh.to_0x_hex().lower()
            for tx in el_block["transactions"]
            for vh in (tx.get("blobVersionedHashes") or [])
        ]
        assert [h.lower() for h in blob_hashes] == el_blob_hashes

    if version >= 4:
        execution_requests = req["params"][3]
        computed = compute_requests_hash(execution_requests)
        assert computed.lower() == block["requestsHash"].to_0x_hex().lower()


@pytest.mark.parametrize("block_number", BLOCKS)
def test_generated_fcu_uses_parent_hash(gen, block_number):
    block = gen.w3.eth.get_block(block_number)
    slot = gen.network.slot_from_timestamp(block["timestamp"])
    message = gen.get_beacon_block(slot)
    ep = message["body"]["execution_payload"]
    fcu = gen.get_fcu_request(block_number, ep, gen.get_fcu_version(block))
    params = fcu["params"][0]
    assert params["headBlockHash"].lower() == block["hash"].to_0x_hex().lower()
    assert params["safeBlockHash"].lower() == block["parentHash"].to_0x_hex().lower()
    assert params["finalizedBlockHash"] == params["safeBlockHash"]
