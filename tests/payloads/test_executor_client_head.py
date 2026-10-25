import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from expb.payloads.executor.executor import Executor


def create_executor(tmp_path: Path, skip: int | None = None) -> Executor:
    payloads_file = tmp_path / "payloads.jsonl"
    payloads_file.write_text(
        "".join(
            json.dumps(
                {
                    "method": "engine_newPayloadV4",
                    "params": [{"blockNumber": hex(number)}],
                }
            )
            + "\n"
            for number in (100, 101, 102)
        )
    )
    config = Mock(
        payloads_file=payloads_file,
        k6_payloads_skip=skip,
        json_rpc_wait_max_retries=10,
    )
    return Executor(config=config, logger=Mock())


def rpc_responses(heads: list[int]) -> list[Mock]:
    responses = []
    for head in heads:
        response = Mock()
        response.json.return_value = {"result": hex(head)}
        responses.append(response)
    return responses


@pytest.mark.parametrize(("skip", "expected"), [(None, 99), (2, 101), (3, None)])
def test_first_payload_parent_number(tmp_path: Path, skip: int | None, expected: int | None):
    assert create_executor(tmp_path, skip).first_payload_parent_number() == expected


def test_wait_for_client_head_waits_until_head_reaches_parent_and_settles(tmp_path: Path):
    executor = create_executor(tmp_path)
    post = Mock(side_effect=rpc_responses([50, 98, 99, 100, 100]))
    with (
        patch("expb.payloads.executor.executor.requests.post", post),
        patch("expb.payloads.executor.executor.time.sleep"),
    ):
        executor.wait_for_client_head("http://client:8545", parent_number=99)

    assert post.call_count == 5


def test_wait_for_client_head_fails_when_head_stays_behind(tmp_path: Path):
    executor = create_executor(tmp_path)
    post = Mock(side_effect=rpc_responses([50] * 10))
    with (
        patch("expb.payloads.executor.executor.requests.post", post),
        patch("expb.payloads.executor.executor.time.sleep"),
        pytest.raises(Exception, match="did not reach block 99"),
    ):
        executor.wait_for_client_head("http://client:8545", parent_number=99)
