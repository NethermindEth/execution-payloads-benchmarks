import os
import json
import asyncio

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3
from web3.types import BlockData, HexBytes, TxData

from expb.logging import Logger
from expb.networks import Network, Fork


class Generator:
    def __init__(
        self,
        network: Network,
        rpc_url: str,
        start_block: int,
        output_dir: Path,
        end_block: int | None = None,  # if None, will use the latest block
        threads: int = 10,
        workers: int = 30,
        logger=Logger(),
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.network = network.value
        self.start_block = start_block
        self.output_dir = output_dir
        self.threads = threads
        self.workers = workers
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
        else:
            raise ValueError(f"Unknown fork: {fork}")

    def compose_payload_v1(
        self,
        block: BlockData,
        transactions: list[str],
    ) -> dict:
        return {
            "parentHash": block["parentHash"].to_0x_hex(),
            "feeRecipient": block["miner"],
            "stateRoot": block["stateRoot"].to_0x_hex(),
            "receiptsRoot": block["receiptsRoot"].to_0x_hex(),
            "logsBloom": block["logsBloom"].to_0x_hex(),
            "prevRandao": block["mixHash"].to_0x_hex(),
            "blockNumber": hex(block["number"]),
            "gasLimit": hex(block["gasLimit"]),
            "gasUsed": hex(block["gasUsed"]),
            "timestamp": hex(block["timestamp"]),
            "extraData": block["extraData"].to_0x_hex(),
            "baseFeePerGas": hex(block["baseFeePerGas"]),
            "blockHash": block["hash"].to_0x_hex(),
            "transactions": transactions,
        }

    def compose_payload_v2(
        self,
        block: BlockData,
        transactions: list[str],
        withdrawals: list[str],
    ) -> dict:
        payload = self.compose_payload_v1(block, transactions)
        payload.update(
            {
                "withdrawals": withdrawals,
            }
        )
        return payload

    def compose_payload_v3(
        self,
        block: BlockData,
        transactions: list[str],
        withdrawals: list[str],
    ) -> dict:
        payload = self.compose_payload_v2(block, transactions, withdrawals)
        block["parentBeaconBlockRoot"]
        payload.update(
            {
                "blobGasUsed": (
                    hex(block["blobGasUsed"])
                    if hasattr(block, "blobGasUsed")
                    else "0x0"
                ),
                "excessBlobGas": (
                    hex(block["excessBlobGas"])
                    if hasattr(block, "excessBlobGas")
                    else "0x0"
                ),
            }
        )
        return payload

    def compose_payload_v4(
        self,
        block: BlockData,
        transactions: list[str],
        withdrawals: list[str],
    ) -> dict:
        payload = self.compose_payload_v3(block, transactions, withdrawals)
        # payload.update({})
        return payload

    async def get_raw_tx(
        self,
        tx_semaphore: asyncio.Semaphore,
        tx_hash: str,
    ) -> str:
        async with tx_semaphore:
            raw = self.w3.eth.get_raw_transaction(tx_hash)
        return self.w3.to_hex(raw)

    async def get_block_transactions(
        self,
        block: BlockData,
    ) -> list[str]:
        tasks = []
        tx_semaphore = asyncio.Semaphore(self.workers)
        tx_hashes = [
            tx.to_0x_hex() if isinstance(tx, HexBytes) else tx["hash"].to_0x_hex()
            for tx in block["transactions"]
        ]
        for tx_hash in tx_hashes:
            tx_task = self.get_raw_tx(tx_semaphore, tx_hash)
            tasks.append(tx_task)
        transactions = await asyncio.gather(*tasks)
        return transactions

    def get_block_withdrawals(self, block: BlockData) -> list[dict]:
        if hasattr(block, "withdrawals") and block["withdrawals"] is not None:
            return [
                {
                    "index": hex(wd["index"]),
                    "validatorIndex": hex(wd["validatorIndex"]),
                    "address": wd["address"],
                    "amount": hex(wd["amount"]),
                }
                for wd in block["withdrawals"]
            ]
        return []

    def get_blobs_versioned_hashes(self, block: BlockData) -> list[str]:
        blob_versioned_hashes: list[str] = []
        for tx in block["transactions"]:
            tx_data: TxData | None = None
            if isinstance(tx, HexBytes):
                tx_data = self.w3.eth.get_transaction(tx)
            else:
                tx_data = tx
            if hasattr(tx_data, "blobVersionedHashes"):
                for hash in tx_data["blobVersionedHashes"]:
                    blob_versioned_hashes.append(hash.to_0x_hex())
        return blob_versioned_hashes

    async def get_execution_requests(self, block: BlockData) -> list[dict]:
        # TODO: implement this!
        return []

    async def get_new_payload_request(self, block: BlockData) -> list:
        txs_task = self.get_block_transactions(block)
        version = self.get_payload_version(block)
        params = []
        (
            payload,
            blobs_versioned_hashes,
            parent_beacon_block_root,
            execution_requests,
        ) = None, None, None, None
        # get engine_newPayload params for each version
        transactions = await txs_task
        if version == 1:
            payload = self.compose_payload_v1(block, transactions)
        elif version == 2:
            withdrawals = self.get_block_withdrawals(block)
            payload = self.compose_payload_v2(block, transactions, withdrawals)
        elif version == 3:
            withdrawals = self.get_block_withdrawals(block)
            payload = self.compose_payload_v3(block, transactions, withdrawals)
            blobs_versioned_hashes = self.get_blobs_versioned_hashes(block)
            parent_beacon_block_root = block["parentBeaconBlockRoot"].to_0x_hex()
        elif version == 4:
            withdrawals = self.get_block_withdrawals(block)
            payload = self.compose_payload_v4(block, transactions, withdrawals)
            blobs_versioned_hashes = self.get_blobs_versioned_hashes(block)
            parent_beacon_block_root = block["parentBeaconBlockRoot"].to_0x_hex()
            execution_requests = await self.get_execution_requests(block)
        else:
            raise ValueError(f"Unknown payload version: {version}")
        params.append(payload)
        if blobs_versioned_hashes is not None:
            params.append(blobs_versioned_hashes)
        if parent_beacon_block_root is not None:
            params.append(parent_beacon_block_root)
        if execution_requests is not None:
            params.append(execution_requests)
        return {
            "id": 1,
            "jsonrpc": "2.0",
            "method": f"engine_newPayloadV{version}",
            "params": params,
        }

    async def get_fcu_request(self, block: BlockData) -> dict:
        return {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "engine_forkChoiceUpdated",
            "params": [
                {
                    "headBlockHash": block["hash"].to_0x_hex(),
                    "safeBlockHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                    "finalizedBlockHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                }
            ],
        }

    def generate_payload(
        self,
        block_number: int,
    ) -> None:
        self.log.info("generating payload", block_number=block_number)
        block = self.w3.eth.get_block(block_number)
        self.log.debug(
            "generating engine_newPayload request", block_number=block_number
        )
        engine_new_payload_request = asyncio.run(self.get_new_payload_request(block))
        self.log.debug(
            "generating engine_forkChoiceUpdated request", block_number=block_number
        )
        fcu_request = asyncio.run(self.get_fcu_request(block))
        enp_req_file_name = os.path.join(
            self.output_dir, f"payload_{block_number}.json"
        )
        fcu_req_file_name = os.path.join(
            self.output_dir, f"payload_{block_number}_fcu.json"
        )
        self.log.debug("writing engine_newPayload request", block_number=block_number)
        with open(enp_req_file_name, "w") as f:
            json.dump(engine_new_payload_request, f)
        self.log.debug(
            "writing engine_forkChoiceUpdated request", block_number=block_number
        )
        with open(fcu_req_file_name, "w") as f:
            json.dump(fcu_request, f)
        self.log.info("payload generated", block_number=block_number)

    def generate_payloads(self) -> None:
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            list(
                executor.map(
                    self.generate_payload, range(self.start_block, self.end_block + 1)
                )
            )
