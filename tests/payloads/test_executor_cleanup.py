from pathlib import Path
from unittest.mock import Mock, patch

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
