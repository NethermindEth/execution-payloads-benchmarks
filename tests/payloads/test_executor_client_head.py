import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from expb.payloads.executor.executor import Executor


@pytest.fixture
def executor(tmp_path: Path) -> Executor:
    payloads_file = tmp_path / "payloads.jsonl"
    payloads_file.write_text(
        "".join(
            json.dumps({"params": [{"blockNumber": hex(number)}]}) + "\n"
            for number in (100, 101, 102)
        ),
        encoding="utf-8",
    )
    return Executor(
        config=Mock(
            payloads_file=payloads_file,
            k6_payloads_skip=None,
            startup_block=None,
            startup_wait_for_head=False,
            startup_wait=0,
            json_rpc_wait_max_retries=6,
        ),
        logger=Mock(),
    )


@pytest.mark.parametrize("skip,expected", [(None, 99), (0, 99), (2, 101), (3, None)])
def test_first_payload_parent_respects_skip_and_end_of_file(executor, skip, expected):
    executor.config.k6_payloads_skip = skip
    assert executor.first_payload_parent_number() == expected


def test_first_payload_parent_handles_genesis(executor):
    executor.config.payloads_file.write_text('{"params":[{"blockNumber":"0x0"}]}\n')
    assert executor.first_payload_parent_number() == 0


def test_first_payload_parent_does_not_parse_skipped_rows(executor):
    executor.config.payloads_file.write_text('not-json\n{"params":[{"blockNumber":"0x64"}]}\n')
    executor.config.k6_payloads_skip = 1
    assert executor.first_payload_parent_number() == 99


@pytest.mark.parametrize("number", [None, True, "100", "-0x1", "0x", "invalid"])
def test_first_payload_parent_rejects_invalid_selected_block_numbers(executor, number):
    executor.config.payloads_file.write_text(json.dumps({"params": [{"blockNumber": number}]}))
    with pytest.raises(ValueError):
        executor.first_payload_parent_number()


@pytest.mark.parametrize(
    "startup_block,wait_for_head,skip,heads",
    [
        (None, True, None, [50, 98, 99, 100, 100]),
        (None, True, None, [100, 100]),
        (102, False, None, [99, 99, 100, 102]),
        (102, False, None, [102]),
        (50, False, None, [50, 99]),
        (None, True, None, [100, None, 100, 100]),
        (None, True, 3, [0]),
        (102, False, 3, [101, 102]),
        (0, False, None, [0, 99]),
    ],
    ids=[
        "automatic-parent-and-settle",
        "automatic-ready-confirmation",
        "checkpoint-pause",
        "checkpoint-no-confirmation",
        "checkpoint-parent-floor",
        "automatic-reset-after-error",
        "automatic-no-selected-payload",
        "checkpoint-no-selected-payload",
        "zero-checkpoint-parent-floor",
    ],
)
def test_client_head_readiness_polls_until_the_selected_target(
    executor, startup_block, wait_for_head, skip, heads
):
    executor.config.startup_block = startup_block
    executor.config.startup_wait_for_head = wait_for_head
    executor.config.k6_payloads_skip = skip
    assert_polling(executor, heads)


def test_default_readiness_does_not_require_or_read_the_first_payload(executor):
    executor.config.payloads_file.unlink()
    with patch.object(executor, "first_payload_parent_number") as parent:
        assert_polling(executor, [0])
    parent.assert_not_called()
    executor.log.info.assert_called_once_with(
        "Client json rpc is available", latest_block=0, attempts=1
    )


@pytest.mark.parametrize("heads", [[50, 99, 100], [98, 98, 98]])
def test_automatic_readiness_uses_one_attempt_budget(executor, heads):
    executor.config.startup_wait_for_head = True
    executor.config.json_rpc_wait_max_retries = 3
    with pytest.raises(Exception, match="Client head is not ready after 3 attempts"):
        assert_polling(executor, heads)


def test_client_readiness_distinguishes_rpc_availability_from_head_readiness(executor):
    executor.config.startup_block = 100
    assert_polling(executor, [99, 100])
    messages = [call.args[0] for call in executor.log.info.call_args_list]
    assert messages == [
        "Client json rpc is available", "Waiting for client head", "Client head is ready"
    ]
    assert executor.log.info.call_args_list[-1].kwargs["required_block"] == 100


@pytest.mark.parametrize("available", [False, True])
def test_readiness_failure_identifies_rpc_or_head_phase(executor, available):
    executor.config.startup_block = 100
    executor.config.json_rpc_wait_max_retries = 2
    heads = [99, None] if available else [None, None]
    message = (
        r"Client head is not ready.*last head: 99, required startup block: 100"
        if available
        else "Client json rpc is not ready"
    )
    with pytest.raises(Exception, match=message):
        assert_polling(executor, heads)


def assert_polling(executor, heads):
    responses = []
    for head in heads:
        response = Mock(ok=True)
        response.json.return_value = (
            {"result": hex(head)} if head is not None else {"error": {"code": -32000}}
        )
        responses.append(response)
    with (
        patch("expb.payloads.executor.executor.requests.post", side_effect=responses) as post,
        patch("expb.payloads.executor.executor.time.sleep") as sleep,
    ):
        try:
            executor.wait_for_client_json_rpc("http://client:8545")
        finally:
            assert post.call_count == len(heads)
            assert sleep.call_count == len(heads) - 1


@pytest.mark.parametrize("startup_block,head", [(None, 0), (0, 0), (100, 100), (100, 101)])
def test_client_readiness_accepts_an_available_snapshot_head(startup_block, head):
    executor = make_readiness_executor(startup_block)
    response = Mock(ok=True)
    response.json.return_value = {"result": hex(head)}

    with (
        patch("expb.payloads.executor.executor.requests.post", return_value=response) as post,
        patch("expb.payloads.executor.executor.time.sleep") as sleep,
    ):
        executor.wait_for_client_json_rpc("http://client:8545")

    post.assert_called_once()
    sleep.assert_not_called()


def test_client_readiness_waits_for_snapshot_replay_before_returning():
    executor = make_readiness_executor(attempts=3)
    response = Mock(ok=True)
    response.json.side_effect = [{"result": hex(head)} for head in (94, 99, 100)]

    with (
        patch("expb.payloads.executor.executor.requests.post", return_value=response) as post,
        patch("expb.payloads.executor.executor.time.sleep") as sleep,
    ):
        executor.wait_for_client_json_rpc("http://client:8545")

    assert post.call_count == 3
    assert sleep.call_count == 2


@pytest.mark.parametrize(
    "rpc_reply",
    [
        {"error": {"code": -32000, "message": "not ready"}},
        {"result": None},
        {"result": "not-a-quantity"},
        {"result": "100"},
        {"result": "-0x1"},
        {"result": True},
        {"result": "0x64", "error": {"code": -32000}},
        [],
    ],
)
def test_client_readiness_rejects_unsuccessful_json_rpc_results(rpc_reply):
    executor = make_readiness_executor()
    response = Mock(ok=True)
    response.json.return_value = rpc_reply

    with (
        patch("expb.payloads.executor.executor.requests.post", return_value=response) as post,
        patch("expb.payloads.executor.executor.time.sleep"),
        pytest.raises(Exception, match="required startup block: 100"),
    ):
        executor.wait_for_client_json_rpc("http://client:8545")

    assert post.call_count == 2


@pytest.mark.parametrize(
    "request_error",
    [requests.exceptions.Timeout(), requests.exceptions.ConnectionError(), ValueError()],
)
def test_client_readiness_retries_transient_rpc_failures_within_the_attempt_limit(request_error):
    executor = make_readiness_executor()
    response = Mock(ok=True)
    response.json.return_value = {"result": "0x64"}

    with (
        patch(
            "expb.payloads.executor.executor.requests.post",
            side_effect=[request_error, response],
        ) as post,
        patch("expb.payloads.executor.executor.time.sleep") as sleep,
    ):
        executor.wait_for_client_json_rpc("http://client:8545")

    assert post.call_count == 2
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("head,ok", [("0x63", True), ("0x64", False)])
def test_client_readiness_fails_when_snapshot_replay_never_reaches_the_target(head, ok):
    executor = make_readiness_executor()
    response = Mock(ok=ok)
    response.json.return_value = {"result": head}

    with (
        patch("expb.payloads.executor.executor.requests.post", return_value=response) as post,
        patch("expb.payloads.executor.executor.time.sleep"),
        pytest.raises(Exception, match="not ready after 2 attempts"),
    ):
        executor.wait_for_client_json_rpc("http://client:8545")

    assert post.call_count == 2


def make_readiness_executor(
    startup_block: int | None = 100, attempts: int = 2
) -> Executor:
    executor = Executor(
        config=Mock(
            startup_wait=0,
            json_rpc_wait_max_retries=attempts,
            startup_block=startup_block,
            startup_wait_for_head=False,
        ),
        logger=Mock(),
    )
    executor.first_payload_parent_number = Mock(return_value=None)
    return executor
