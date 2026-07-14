"""Validate EIP-7685 execution-requests encoding and blob-versioned-hash derivation
against real mainnet data whose expected values come from the EL block header
(independent of the encoder under test)."""
import pytest

from tests.conftest import compute_requests_hash, load_fixture

FIXTURES = ["block_with_deposit", "block_with_withdrawal"]


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_execution_requests_match_header_requests_hash(generator, fixture_name):
    fx = load_fixture(fixture_name)
    encoded = generator.get_execution_requests(fx["execution_requests"])
    assert compute_requests_hash(encoded).lower() == fx["requests_hash"].lower()


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_blob_versioned_hashes_match_el(generator, fixture_name):
    fx = load_fixture(fixture_name)
    derived = generator.get_blobs_versioned_hashes(fx["blob_kzg_commitments"])
    assert [h.lower() for h in derived] == [
        h.lower() for h in fx["expected_blob_versioned_hashes"]
    ]


def test_empty_execution_requests_encode_to_empty_list(generator):
    assert generator.get_execution_requests({}) == []
    assert generator.get_execution_requests(
        {"deposits": [], "withdrawals": [], "consolidations": []}
    ) == []


def test_requests_are_ordered_by_type_and_prefixed(generator):
    """A block carrying every request type must emit them ordered 0x00,0x01,0x02
    and exclude empty types."""
    execution_requests = {
        "withdrawals": [
            {
                "source_address": "0x" + "11" * 20,
                "validator_pubkey": "0x" + "22" * 48,
                "amount": "1",
            }
        ],
        "deposits": [
            {
                "pubkey": "0x" + "33" * 48,
                "withdrawal_credentials": "0x" + "44" * 32,
                "amount": "32000000000",
                "signature": "0x" + "55" * 96,
                "index": "7",
            }
        ],
        "consolidations": [],
    }
    encoded = generator.get_execution_requests(execution_requests)
    assert len(encoded) == 2
    assert encoded[0].startswith("0x00")  # deposit first
    assert encoded[1].startswith("0x01")  # withdrawal second
    # deposit request body is fixed-size 192 bytes (+1 type byte) => 386 hex chars + "0x"
    assert len(encoded[0]) == 2 + 2 + 192 * 2
    # withdrawal request body is fixed-size 76 bytes (+1 type byte)
    assert len(encoded[1]) == 2 + 2 + 76 * 2
