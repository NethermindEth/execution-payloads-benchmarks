#!/usr/bin/env python3
"""Generate fake payloads and FCUs for mock engine testing.

Usage:
    python3 generate_payloads.py --count 1000 --output-dir ./test-payloads
"""

import argparse
import json
from pathlib import Path


def generate_payload(index: int) -> dict:
    block_hash = f"0x{index:064x}"
    parent_hash = f"0x{max(0, index - 1):064x}"
    return {
        "jsonrpc": "2.0",
        "method": "engine_newPayloadV3",
        "params": [
            {
                "parentHash": parent_hash,
                "feeRecipient": "0x0000000000000000000000000000000000000000",
                "stateRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "receiptsRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "logsBloom": "0x" + "00" * 256,
                "prevRandao": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "blockNumber": hex(index + 1),
                "gasLimit": "0x1c9c380",
                "gasUsed": hex(21000 * (1 + index % 10)),
                "timestamp": hex(1700000000 + index * 12),
                "extraData": "0x",
                "baseFeePerGas": "0x3b9aca00",
                "blockHash": block_hash,
                "transactions": [],
                "withdrawals": [],
                "blobGasUsed": "0x0",
                "excessBlobGas": "0x0",
            },
            [],
            "0x0000000000000000000000000000000000000000000000000000000000000000",
        ],
        "id": index + 1,
    }


def generate_fcu(index: int) -> dict:
    block_hash = f"0x{index:064x}"
    return {
        "jsonrpc": "2.0",
        "method": "engine_forkchoiceUpdatedV3",
        "params": [
            {
                "headBlockHash": block_hash,
                "safeBlockHash": block_hash,
                "finalizedBlockHash": block_hash,
            },
            None,
        ],
        "id": index + 1,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate fake payloads for mock testing")
    parser.add_argument("--count", type=int, default=1000, help="Number of payloads (default: 1000)")
    parser.add_argument("--output-dir", type=str, default="./test-payloads", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payloads_file = output_dir / "payloads.jsonl"
    fcus_file = output_dir / "fcus.jsonl"

    with open(payloads_file, "w") as pf, open(fcus_file, "w") as ff:
        for i in range(args.count):
            pf.write(json.dumps(generate_payload(i)) + "\n")
            ff.write(json.dumps(generate_fcu(i)) + "\n")

    print(f"Generated {args.count} payloads: {payloads_file}")
    print(f"Generated {args.count} FCUs: {fcus_file}")


if __name__ == "__main__":
    main()
