import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from web3 import Web3
from web3.types import BlockData

from expb.configs.networks import Fork, Network
from expb.logging import Logger


class Generator:
    def __init__(
        self,
        network: Network,
        rpc_url: str,
        beacon_url: str,
        start_block: int,
        output_dir: Path,
        end_block: int | None = None,  # if None, will use the latest block
        join_payloads: bool = True,
        threads: int = 10,
        logger=Logger(),
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.beacon_url = beacon_url.rstrip("/")
        self.beacon = requests.Session()
        self.network = network.value
        self.start_block = start_block
        self.output_dir = output_dir
        self.join_payloads = join_payloads
        self.threads = threads
        self.log = logger
        if end_block is None:
            self.end_block = self.w3.eth.get_block("latest")["number"]
        else:
            self.end_block = end_block

    def get_payload_version(self, block: BlockData) -> int:
        fork = self.network.get_block_fork(block)
        if fork == Fork.PARIS:
            return 1
        elif fork == Fork.SHANGHAI:
            return 2
        elif fork == Fork.CANCUN:
            return 3
        elif fork == Fork.PRAGUE:
            return 4
        elif fork == Fork.OSAKA:
            return 4
        else:
            raise ValueError(f"Unknown fork: {fork}")

    def get_fcu_version(self, block: BlockData) -> int:
        fork = self.network.get_block_fork(block)
        if fork == Fork.PARIS:
            return 1
        elif fork == Fork.SHANGHAI:
            return 2
        elif fork == Fork.CANCUN:
            return 3
        elif fork == Fork.PRAGUE:
            return 3
        elif fork == Fork.OSAKA:
            return 3
        else:
            raise ValueError(f"Unknown fork: {fork}")

    def get_beacon_block(self, slot: int) -> dict:
        resp = self.beacon.get(
            f"{self.beacon_url}/eth/v2/beacon/blocks/{slot}",
            timeout=120,
        )
        if resp.status_code == 404:
            raise ValueError(f"No beacon block found for slot {slot}")
        resp.raise_for_status()
        return resp.json()["data"]["message"]

    @staticmethod
    def _to_hex(dec_str: str | int) -> str:
        return hex(int(dec_str))

    @staticmethod
    def _to_bytes(hex_str: str) -> bytes:
        return bytes.fromhex(hex_str[2:] if hex_str.startswith("0x") else hex_str)

    @staticmethod
    def _u64_le(dec_str: str | int) -> bytes:
        return int(dec_str).to_bytes(8, "little")

    def map_withdrawals(self, withdrawals: list[dict]) -> list[dict]:
        return [
            {
                "index": self._to_hex(wd["index"]),
                "validatorIndex": self._to_hex(wd["validator_index"]),
                "address": wd["address"],
                "amount": self._to_hex(wd["amount"]),
            }
            for wd in withdrawals
        ]

    def compose_payload(self, ep: dict, version: int) -> dict:
        payload = {
            "parentHash": ep["parent_hash"],
            "feeRecipient": ep["fee_recipient"],
            "stateRoot": ep["state_root"],
            "receiptsRoot": ep["receipts_root"],
            "logsBloom": ep["logs_bloom"],
            "prevRandao": ep["prev_randao"],
            "blockNumber": self._to_hex(ep["block_number"]),
            "gasLimit": self._to_hex(ep["gas_limit"]),
            "gasUsed": self._to_hex(ep["gas_used"]),
            "timestamp": self._to_hex(ep["timestamp"]),
            "extraData": ep["extra_data"],
            "baseFeePerGas": self._to_hex(ep["base_fee_per_gas"]),
            "blockHash": ep["block_hash"],
            "transactions": ep["transactions"],
        }
        if version >= 2:
            payload["withdrawals"] = self.map_withdrawals(ep.get("withdrawals", []))
        if version >= 3:
            payload["blobGasUsed"] = self._to_hex(ep["blob_gas_used"])
            payload["excessBlobGas"] = self._to_hex(ep["excess_blob_gas"])
        return payload

    def get_blobs_versioned_hashes(self, commitments: list[str]) -> list[str]:
        versioned_hashes = []
        for commitment in commitments:
            digest = hashlib.sha256(self._to_bytes(commitment)).digest()
            versioned_hashes.append("0x01" + digest[1:].hex())
        return versioned_hashes

    def encode_deposit_request(self, d: dict) -> bytes:
        return (
            self._to_bytes(d["pubkey"])
            + self._to_bytes(d["withdrawal_credentials"])
            + self._u64_le(d["amount"])
            + self._to_bytes(d["signature"])
            + self._u64_le(d["index"])
        )

    def encode_withdrawal_request(self, w: dict) -> bytes:
        return (
            self._to_bytes(w["source_address"])
            + self._to_bytes(w["validator_pubkey"])
            + self._u64_le(w["amount"])
        )

    def encode_consolidation_request(self, c: dict) -> bytes:
        return (
            self._to_bytes(c["source_address"])
            + self._to_bytes(c["source_pubkey"])
            + self._to_bytes(c["target_pubkey"])
        )

    def get_execution_requests(self, execution_requests: dict) -> list[str]:
        """EIP-7685 encoding: list of ``request_type ++ request_data`` byte arrays,
        ordered ascending by type, with empty types excluded. Each request type is a
        fixed-size SSZ container, so ``request_data`` is the concatenation of its
        elements."""
        requests_list: list[str] = []
        deposits = execution_requests.get("deposits", [])
        withdrawals = execution_requests.get("withdrawals", [])
        consolidations = execution_requests.get("consolidations", [])
        if deposits:
            data = b"".join(self.encode_deposit_request(d) for d in deposits)
            requests_list.append("0x00" + data.hex())
        if withdrawals:
            data = b"".join(self.encode_withdrawal_request(w) for w in withdrawals)
            requests_list.append("0x01" + data.hex())
        if consolidations:
            data = b"".join(self.encode_consolidation_request(c) for c in consolidations)
            requests_list.append("0x02" + data.hex())
        return requests_list

    def get_new_payload_request(
        self,
        block_number: int,
        message: dict,
        version: int,
    ) -> dict:
        body = message["body"]
        ep = body["execution_payload"]
        payload = self.compose_payload(ep, version)
        params: list = [payload]
        if version >= 3:
            params.append(
                self.get_blobs_versioned_hashes(body.get("blob_kzg_commitments", []))
            )
            params.append(message["parent_root"])
        if version >= 4:
            params.append(
                self.get_execution_requests(body.get("execution_requests", {}))
            )
        return {
            "id": block_number,
            "jsonrpc": "2.0",
            "method": f"engine_newPayloadV{version}",
            "params": params,
        }

    def get_fcu_request(
        self,
        block_number: int,
        ep: dict,
        version: int,
    ) -> dict:
        return {
            "id": block_number,
            "jsonrpc": "2.0",
            "method": f"engine_forkchoiceUpdatedV{version}",
            "params": [
                {
                    "headBlockHash": ep["block_hash"],
                    "safeBlockHash": ep["parent_hash"],
                    "finalizedBlockHash": ep["parent_hash"],
                }
            ],
        }

    def generate_payload(
        self,
        block_number: int,
    ) -> None:
        self.log.info("Generating payload", block_number=block_number)
        block = self.w3.eth.get_block(block_number)
        slot = self.network.slot_from_timestamp(block["timestamp"])
        self.log.debug(
            "Fetching beacon block", block_number=block_number, slot=slot
        )
        message = self.get_beacon_block(slot)
        ep = message["body"]["execution_payload"]
        if int(ep["block_number"]) != block_number:
            self.log.error(
                "Beacon block number mismatch, skipping",
                block_number=block_number,
                slot=slot,
                beacon_block_number=int(ep["block_number"]),
            )
            return

        payload_version = self.get_payload_version(block)
        fcu_version = self.get_fcu_version(block)
        self.log.debug(
            "Generating engine_newPayload request", block_number=block_number
        )
        engine_new_payload_request = self.get_new_payload_request(
            block_number, message, payload_version
        )
        self.log.debug(
            "Generating engine_forkChoiceUpdated request", block_number=block_number
        )
        fcu_request = self.get_fcu_request(block_number, ep, fcu_version)

        enp_req_file_name = os.path.join(
            self.output_dir, f"payload_{block_number}.json"
        )
        fcu_req_file_name = os.path.join(
            self.output_dir, f"payload_{block_number}_fcu.json"
        )
        self.log.debug("Writing engine_newPayload request", block_number=block_number)
        with open(enp_req_file_name, "w") as f:
            json.dump(engine_new_payload_request, f)
        self.log.debug(
            "Writing engine_forkChoiceUpdated request", block_number=block_number
        )
        with open(fcu_req_file_name, "w") as f:
            json.dump(fcu_request, f)
        self.log.info("Payload generated", block_number=block_number)

    def join_payloads_files(self) -> None:
        payloads_file = self.output_dir / "payloads.jsonl"
        fcus_file = self.output_dir / "fcus.jsonl"
        self.log.info(
            "Joining payloads files",
            payloads_file=payloads_file,
            fcus_file=fcus_file,
        )

        payloads_filepaths = [
            p
            for p in self.output_dir.glob("*.json")
            if re.match(r"^payload_\d+\.json$", p.name)
        ]
        fcu_filepaths: list[Path] = [
            p.parent / f"payload_{p.name.split('_')[1].split('.')[0]}_fcu.json"
            for p in payloads_filepaths
        ]

        pairs = sorted(
            zip(payloads_filepaths, fcu_filepaths),
            key=lambda p: int(p[0].name.split("_")[1].split(".")[0]),
        )

        with payloads_file.open("w") as f_payloads, fcus_file.open("w") as f_fcus:
            for payload_filepath, fcu_filepath in pairs:
                with payload_filepath.open("r") as f:
                    payload = f.readline().strip()
                with fcu_filepath.open("r") as f:
                    fcu = f.readline().strip()
                f_payloads.write(payload + "\n")
                f_fcus.write(fcu + "\n")

        self.log.info(
            "Cleaning output directory",
            output_dir=self.output_dir,
        )
        for file in payloads_filepaths:
            file.unlink()
        for file in fcu_filepaths:
            file.unlink()

    def generate_payloads(self) -> None:
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            list(
                executor.map(
                    self.generate_payload, range(self.start_block, self.end_block + 1)
                )
            )

        if self.join_payloads:
            self.join_payloads_files()
