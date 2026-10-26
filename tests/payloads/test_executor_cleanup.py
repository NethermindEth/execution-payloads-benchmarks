from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from expb.payloads.executor.executor import Executor


def test_cleanup_stops_rpc_consumers_before_client_and_preserves_metrics(tmp_path: Path):
    config = Mock(
        executor_name="scenario",
        outputs_dir=tmp_path,
        docker_client=Mock(),
    )
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"
    config.docker_client.networks.get.return_value = Mock()

    executor = Executor(config=config, logger=Mock())
    lifecycle: list[str] = []

    def teardown(name: str, **kwargs):
        lifecycle.append(name)
        if name == "k6":
            kwargs["line_callback"](
                'EXPB_PER_PAYLOAD_METRIC idx=7 gas_used=123 processing_ms=45.6\n'
            )
        return []

    def stop_trace():
        lifecycle.append("eventpipe")

    def finalize_perf(_container):
        lifecycle.append("perf")

    teardown_mock = Mock(side_effect=teardown)
    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", teardown_mock),
        patch.object(executor, "_finalize_perf", side_effect=finalize_perf),
        patch.object(executor, "_stop_dotnet_trace_collector", side_effect=stop_trace),
        patch.object(executor, "_print_per_payload_metrics_table") as print_table,
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario(
            print_logs_to_console=True,
            print_per_payload_metrics_table=True,
        )

    assert lifecycle == [
        "perf",
        "k6",
        "payload-server",
        "alloy",
        "eventpipe",
        "nethermind",
    ]
    teardown_calls = teardown_mock.call_args_list
    assert teardown_calls[0].kwargs["log_file"] == tmp_path / "k6.log"
    assert teardown_calls[0].kwargs["line_callback"] is not None
    assert teardown_calls[1].kwargs["log_file"] == tmp_path / "payload-server.log"
    print_table.assert_called_once_with([(7, "123", "45.6")])


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
        ),
        logger=Mock(),
    )
    executor.first_payload_parent_number = Mock(return_value=None)
    return executor
