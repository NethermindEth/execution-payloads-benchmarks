import os
import json
import time
import docker
import shutil
import secrets
import requests as r
import subprocess
import docker.errors

from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from docker.models.containers import Container

from expb.logging import Logger
from expb.configs.networks import Network
from expb.configs.clients import (
    Client,
    CLIENTS_DATA_DIR,
    CLIENTS_JWT_SECRET_FILE,
    CLIENT_ENGINE_PORT,
)
from expb.payloads.utils.jwt import JWTProvider
from expb.payloads.compressor.compressor_utils import RPCError, engine_request


class Compressor:
    def __init__(
        self,
        network: Network,
        compression_factor: int,
        target_gas_limit: int,
        nethermind_snapshot_dir: Path,
        nethermind_docker_image: str,
        input_payloads_file: Path,
        output_payloads_dir: Path,
        logger: Logger,
    ) -> None:
        # Docker client
        self._docker_client = docker.from_env()

        # General config
        self._network = network
        self._compression_factor = compression_factor
        self._target_gas_limit = target_gas_limit

        # Outputs files and directories
        self._input_payloads_file = input_payloads_file
        if not self._input_payloads_file.exists():
            raise ValueError("Input payloads file does not exist")

        self._output_payloads_file = output_payloads_dir / "payloads.jsonl"
        if self._output_payloads_file.exists():
            raise ValueError(
                f"Output payloads file already exists: {self._output_payloads_file}"
            )

        self._output_fcus_file = output_payloads_dir / "fcus.jsonl"
        if self._output_fcus_file.exists():
            raise ValueError(
                f"Output forkchoice file already exists: {self._output_payloads_file}"
            )

        # Nethermind docker
        self._nethermind_docker_name = "nethermind-hacked"
        self._nethermind_container_network_name = (
            f"{self._nethermind_docker_name}-network"
        )
        self._nethermind_docker_image = nethermind_docker_image

        # Nethermind snapshot directory
        self._nethermind_snapshot_dir = nethermind_snapshot_dir

        # Nethermind logs file
        self._nethermind_conatainer_logs_file = output_payloads_dir / "nethermind.log"

        # Overlay directories
        self._overlay_work_dir = output_payloads_dir / "work"
        self._overlay_upper_dir = output_payloads_dir / "upper"
        self._overlay_merged_dir = output_payloads_dir / "merged"

        # Jwt secret file
        self._jwt_secret_file = output_payloads_dir / "jwtsecret.hex"

        self._logger = logger

    def prepare_directories(self) -> None:
        # Create overlay required directories
        self._overlay_work_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self._overlay_upper_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self._overlay_merged_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        # run mount command
        device_name = "nethermind-snapshot"
        mount_command: str = " ".join(
            [
                "mount",
                "-t",
                "overlay",
                device_name,
                "-o",
                ",".join(
                    [
                        f"lowerdir={self._nethermind_snapshot_dir.resolve()}",
                        f"upperdir={self._overlay_upper_dir.resolve()}",
                        f"workdir={self._overlay_work_dir.resolve()}",
                    ]
                ),
                str(self._overlay_merged_dir.resolve()),
            ]
        )
        try:
            subprocess.run(mount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self._logger.error("Failed to mount nethermind snapshot overlay", error=e)
            raise e

    def remove_directories(self) -> None:
        umount_command = " ".join(["umount", str(self._overlay_merged_dir.resolve())])
        try:
            subprocess.run(umount_command, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            self._logger.error("Failed to umount overlay", error=e)
            raise e
        try:
            paths_to_remove = [
                self._overlay_upper_dir.resolve(),
                self._overlay_work_dir.resolve(),
                self._overlay_merged_dir.resolve(),
            ]
            for path in paths_to_remove:
                shutil.rmtree(path)
        except Exception as e:
            self._logger.error("Failed to cleanup work directory", error=e)
            raise e

    def pull_docker_images(self) -> None:
        self._logger.info("Pulling Nethermind docker image")
        self._docker_client.images.pull(self._nethermind_docker_image)
        self._logger.info("Nethermind docker image pulled successfully")

    def prepare_jwt_secret_file(self) -> JWTProvider:
        self._logger.info("Preparing JWT secret file")
        self._jwt_secret_file.touch(
            mode=0o666,
            exist_ok=True,
        )
        self._jwt_secret_file.write_text(secrets.token_bytes(32).hex())
        self._logger.info("JWT secret file prepared successfully")
        return JWTProvider(self._jwt_secret_file)

    def start_nethermind(
        self,
    ) -> str:
        container_network = self._docker_client.networks.create(
            name=self._nethermind_container_network_name,
        )
        container: Container = self._docker_client.containers.run(
            image=self._nethermind_docker_image,
            name=self._nethermind_docker_name,
            volumes={
                self._overlay_merged_dir.resolve(): {
                    "bind": CLIENTS_DATA_DIR,
                    "mode": "rw",
                },
                self._jwt_secret_file.resolve(): {
                    "bind": CLIENTS_JWT_SECRET_FILE,
                    "mode": "rw",
                },
            },
            ports={},  # No ports are exposed to the host
            environment={},
            command=Client.NETHERMIND.value.get_command(
                instance=self._nethermind_docker_name,
                network=self._network,
                extra_flags=[
                    f"--Blocks.TargetBlockGasLimit={self._target_gas_limit}",
                ],
            ),
            detach=True,
            network=self._nethermind_container_network_name,
            user=os.getuid(),
            group_add=os.getgid(),
        )

        container.reload()
        container_ip = container.attrs["NetworkSettings"]["Networks"][
            container_network.name
        ]["IPAddress"]
        return f"http://{container_ip}:{CLIENT_ENGINE_PORT}"

    def wait_for_client_json_rpc(
        self,
        jwt_provider: JWTProvider,
        execution_client_rpc_url: str,
    ) -> None:
        self._logger.info("Waiting for client json rpc to be available")
        time.sleep(30)
        jwt = jwt_provider.get_jwt(expiration_seconds=300)  # 5 minutes expiration
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt}",
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1,
        }
        s = r.Session()
        retries = Retry(
            total=16,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        s.mount("http://", HTTPAdapter(max_retries=retries))
        s.mount("https://", HTTPAdapter(max_retries=retries))
        response: r.Response = s.post(
            execution_client_rpc_url,
            json=payload,
            headers=headers,
        )
        if response.ok:
            self._logger.info(
                "Nethermind json rpc is available",
                latest_block=int(response.json()["result"], 16),
            )
        else:
            self._logger.error(
                "Nethermind json rpc is not available", status_code=response.status_code
            )
            raise Exception("Nethermind json rpc is not available")

    def cleanup_compression(self) -> None:
        self._logger.info("Cleaning up compression setup")
        try:
            nethermind_container = self._docker_client.containers.get(
                self._nethermind_docker_name
            )
            nethermind_container.stop()
            logs = nethermind_container.logs(
                stream=True,
                follow=False,
                stdout=True,
                stderr=True,
            )
            with open(self._nethermind_conatainer_logs_file, "wb") as f:
                for line in logs:
                    f.write(line)
            logs.close()
            nethermind_container.remove()
            self._logger.info("Nethermind container stopped and removed successfully")
        except docker.errors.NotFound:
            pass
        try:
            container_network = self._docker_client.networks.get(
                self._nethermind_container_network_name
            )
            container_network.remove()
            self._logger.info("Nethermind container network removed successfully")
        except docker.errors.NotFound:
            pass
        self.remove_directories()
        self._logger.info("Overlay directories cleaned up successfully")
        self._logger.info("Compression setup cleaned up successfully")

    def compress_payloads(self) -> None:
        try:
            self._logger.info("Preparing payloads compression setup")
            self.prepare_directories()
            jwt_provider = self.prepare_jwt_secret_file()
            self.pull_docker_images()
            nethermind_engine_url = self.start_nethermind()
            self.wait_for_client_json_rpc(jwt_provider, nethermind_engine_url)
            self._logger.info("Payloads compression setup prepared successfully")
            self.start_payloads_compression(
                jwt_provider,
                nethermind_engine_url,
            )
            self._logger.info("Payloads compression completed successfully")
        except Exception as e:
            self._logger.error("Failed to prepare payloads compression setup", error=e)
            raise e
        finally:
            self.cleanup_compression()

    def start_payloads_compression(
        self,
        jwt_provider: JWTProvider,
        nethermind_engine_url: str,
    ) -> None:
        self._logger.info("Starting payloads compression")
        with self._input_payloads_file.open("r") as f:
            payloads_to_compress = []
            current_block = -1
            prev_method = ""
            for line in f:
                payload_data = json.loads(line)
                # Get starting block number and proceed to increase gas limit
                if current_block < 0:
                    starting_block = int(payload_data["params"][0]["blockNumber"], 16)
                    method = payload_data["method"]
                    current_block = self.increase_gas_limit(
                        starting_block,
                        method,
                        jwt_provider,
                        nethermind_engine_url,
                    )
                # Check if there was a hard fork
                if prev_method and payload_data["method"] != prev_method:
                    self._compress_payloads(
                        jwt_provider,
                        current_block,
                        nethermind_engine_url,
                        payloads_to_compress,
                    )
                    current_block += 1
                    payloads_to_compress = []

                # Add payload to compress list
                payloads_to_compress.append(payload_data)
                prev_method = payload_data["method"]

                # Check if compression factor is reached
                if len(payloads_to_compress) % self._compression_factor == 0:
                    self._compress_payloads(
                        jwt_provider,
                        current_block,
                        nethermind_engine_url,
                        payloads_to_compress,
                    )
                    current_block += 1
                    payloads_to_compress = []

            # Check if there is any payload left
            if payloads_to_compress:
                self._compress_payloads(
                    jwt_provider,
                    current_block,
                    nethermind_engine_url,
                    payloads_to_compress,
                )

        self._logger.info("Done compressing payloads")

    def increase_gas_limit(
        self,
        starting_block: int,
        method: str,
        jwt_provider: JWTProvider,
        nethermind_engine_url: str,
    ) -> int:
        self._logger.info(
            "Increasing gas limit to target gas limit",
            target_gas_limit=self._target_gas_limit,
        )
        hacked_method = method.replace("new", "get") + "Hacked"
        current_gas_limit = 0
        current_block = starting_block
        while current_gas_limit < self._target_gas_limit:
            try:
                # Generate empty payload
                hacked_payload_result = engine_request(
                    nethermind_engine_url,
                    jwt_provider,
                    {
                        "method": hacked_method,
                        "params": [
                            [],
                        ],
                    },
                )
                # New empty block
                generated_execution_payload = hacked_payload_result["executionPayload"]

                empty_payload_request, empty_fcu_request = self.generate_requests(
                    current_block,
                    method,
                    generated_execution_payload,
                )

                # Send empty payload request
                engine_request(
                    nethermind_engine_url,
                    jwt_provider,
                    empty_payload_request,
                )
                # Send empty fcu request
                engine_request(
                    nethermind_engine_url,
                    jwt_provider,
                    empty_fcu_request,
                )

                # Get latest block number
                latest_block_result = engine_request(
                    nethermind_engine_url,
                    jwt_provider,
                    {
                        "method": "eth_getBlockByNumber",
                        "params": [
                            "latest",
                            False,
                        ],
                    },
                )

                # Get gas limit
                current_gas_limit = latest_block_result["gasLimit"]
                current_block += 1
                self._logger.info(
                    "Gas limit successfully increased",
                    current_gas_limit=current_gas_limit,
                    target_gas_limit=self._target_gas_limit,
                    current_block=current_block,
                )

                # Write requests to output files
                with self._output_payloads_file.open("a") as f:
                    f.write(json.dumps(empty_payload_request))
                    f.write("\n")
                with self._output_fcus_file.open("a") as f:
                    f.write(json.dumps(empty_fcu_request))
                    f.write("\n")

            except RPCError as e:
                self._logger.error(
                    "Failed to increase gas limit",
                    error=e.error,
                    status_code=e.status_code,
                )
                raise e
        self._logger.info("Gas limit increased successfully")

    def _compress_payloads(
        self,
        jwt_provider: JWTProvider,
        block_number: int,
        nethermind_engine_url: str,
        payloads: list[dict],
    ) -> None:
        self._logger.info(
            "Compressing a batch of payloads",
            block_number=block_number,
            payloads=[
                int(payload["params"][0]["blockNumber"], 16) for payload in payloads
            ],
        )
        txs = []
        for payload in payloads:
            for tx in payload["params"][0]["transactions"]:
                if not isinstance(tx, str) or tx.startswith("0x03"):
                    continue
                txs.append(tx)

        method: str = payloads[0]["method"]
        get_payload_method = method.replace("new", "get")
        hacked_get_payload_request = {
            "method": f"{get_payload_method}Hacked",
            "params": [
                txs,
            ],
        }
        try:
            result = engine_request(
                nethermind_engine_url,
                jwt_provider,
                hacked_get_payload_request,
            )
        except RPCError as e:
            self._logger.error(
                "Failed to get hacked payload",
                error=e.error,
                status_code=e.status_code,
            )
            raise e

        generated_execution_payload = result["executionPayload"]

        compressed_new_payload_req, compressed_fcu_req = self.generate_requests(
            block_number,
            method,
            generated_execution_payload,
        )
        with self._output_payloads_file.open("a") as f:
            f.write(json.dumps(compressed_new_payload_req))
            f.write("\n")
        with self._output_fcus_file.open("a") as f:
            f.write(json.dumps(compressed_fcu_req))
            f.write("\n")

        # Send compressed payload requests to prepare for next one
        try:
            result = engine_request(
                nethermind_engine_url,
                jwt_provider,
                compressed_new_payload_req,
            )
        except RPCError as e:
            self._logger.error(
                "Failed to send compressed new payload request",
                error=e.error,
                status_code=e.status_code,
            )
            raise e

        # Send compressed forkchoice updated request
        try:
            result = engine_request(
                nethermind_engine_url,
                jwt_provider,
                compressed_fcu_req,
            )
        except RPCError as e:
            self._logger.error(
                "Failed to send compressed forkchoice updated request",
                error=e.error,
                status_code=e.status_code,
            )
            raise e

    def get_fcu_method_from_payload(
        self,
        new_payload_method: str,
    ) -> str:
        if new_payload_method == "engine_newPayloadV1":
            return "engine_forkchoiceUpdatedV1"
        if new_payload_method == "engine_newPayloadV2":
            return "engine_forkchoiceUpdatedV2"
        if new_payload_method == "engine_newPayloadV3":
            return "engine_forkchoiceUpdatedV3"
        if new_payload_method == "engine_newPayloadV4":
            return "engine_forkchoiceUpdatedV3"
        self._logger.error("Invalid new payload method", method=new_payload_method)
        raise ValueError(f"Invalid new payload method: {new_payload_method}")

    def generate_requests(
        self,
        block_number: int,
        method: str,
        execution_payload: dict[str],
    ) -> tuple[dict[str], dict[str]]:
        params = []
        (
            payload,
            blobs_versioned_hashes,
            parent_beacon_block_root,
            execution_requests,
        ) = None, None, None, None
        if method == "engine_newPayloadV1":
            payload = execution_payload
        elif method == "engine_newPayloadV2":
            payload = execution_payload
        elif method == "engine_newPayloadV3":
            payload = execution_payload
            blobs_versioned_hashes = []
            parent_beacon_block_root = execution_payload["parentHash"]
        elif method == "engine_newPayloadV4":
            payload = execution_payload
            blobs_versioned_hashes = []
            parent_beacon_block_root = execution_payload["parentHash"]
            execution_requests = []
        else:
            raise ValueError(f"Unknown payload method: {method}")

        params.append(payload)
        if blobs_versioned_hashes is not None:
            params.append(blobs_versioned_hashes)
        if parent_beacon_block_root is not None:
            params.append(parent_beacon_block_root)
        if execution_requests is not None:
            params.append(execution_requests)

        payload_request = {
            "id": block_number,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        block_hash = payload.get("blockHash")
        fcu_method = self.get_fcu_method_from_payload(method)
        fcu_request = {
            "id": block_number,
            "jsonrpc": "2.0",
            "method": fcu_method,
            "params": [
                {
                    "headBlockHash": block_hash,
                    "safeBlockHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                    "finalizedBlockHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                }
            ],
        }

        return payload_request, fcu_request
