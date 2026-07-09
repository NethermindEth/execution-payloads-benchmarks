"""Unit tests for the CL(JSON) -> Engine-API field mapping."""


def _sample_ep() -> dict:
    return {
        "parent_hash": "0x" + "aa" * 32,
        "fee_recipient": "0x" + "bb" * 20,
        "state_root": "0x" + "cc" * 32,
        "receipts_root": "0x" + "dd" * 32,
        "logs_bloom": "0x" + "00" * 256,
        "prev_randao": "0x" + "ee" * 32,
        "block_number": "12345",
        "gas_limit": "30000000",
        "gas_used": "21000",
        "timestamp": "1746612311",
        "extra_data": "0xabcdef",
        "base_fee_per_gas": "1000000000",
        "block_hash": "0x" + "ff" * 32,
        "transactions": ["0x02aabb", "0x03ccdd"],
        "withdrawals": [
            {
                "index": "10",
                "validator_index": "256",
                "address": "0x" + "12" * 20,
                "amount": "1000000000",
            }
        ],
        "blob_gas_used": "131072",
        "excess_blob_gas": "0",
    }


def test_compose_payload_v1_fields(generator):
    payload = generator.compose_payload(_sample_ep(), version=1)
    assert payload["parentHash"] == "0x" + "aa" * 32
    assert payload["feeRecipient"] == "0x" + "bb" * 20
    assert payload["prevRandao"] == "0x" + "ee" * 32
    assert payload["blockNumber"] == hex(12345)
    assert payload["gasLimit"] == hex(30000000)
    assert payload["gasUsed"] == hex(21000)
    assert payload["timestamp"] == hex(1746612311)
    assert payload["baseFeePerGas"] == hex(1000000000)
    assert payload["blockHash"] == "0x" + "ff" * 32
    assert payload["transactions"] == ["0x02aabb", "0x03ccdd"]
    # v1 must not carry later-fork fields
    assert "withdrawals" not in payload
    assert "blobGasUsed" not in payload


def test_compose_payload_v2_adds_withdrawals(generator):
    payload = generator.compose_payload(_sample_ep(), version=2)
    assert payload["withdrawals"] == [
        {
            "index": hex(10),
            "validatorIndex": hex(256),
            "address": "0x" + "12" * 20,
            "amount": hex(1000000000),
        }
    ]
    assert "blobGasUsed" not in payload


def test_compose_payload_v3_adds_blob_fields(generator):
    payload = generator.compose_payload(_sample_ep(), version=3)
    assert payload["blobGasUsed"] == hex(131072)
    assert payload["excessBlobGas"] == hex(0)


def test_versioned_hash_algorithm(generator):
    # 0x01 prefix followed by sha256(commitment)[1:]
    commitment = "0x" + "00" * 48
    import hashlib

    expected = "0x01" + hashlib.sha256(bytes(48)).digest()[1:].hex()
    assert generator.get_blobs_versioned_hashes([commitment]) == [expected]
