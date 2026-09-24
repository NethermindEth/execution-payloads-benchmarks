import signal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import expb.payloads.executor.executor as executor_module
from expb.payloads.executor.executor import Executor


class _FakeContainer:
    name = "nethermind"

    def __init__(self, processes: list[list[str]], exit_on_reload: int):
        self.attrs = {"State": {"Running": True, "Status": "running"}}
        self._processes = processes
        self._exit_on_reload = exit_on_reload
        self._reload_count = 0

    def reload(self):
        self._reload_count += 1
        if self._reload_count >= self._exit_on_reload:
            self.attrs["State"] = {"Running": False, "Status": "exited"}

    def top(self, ps_args: str):
        assert ps_args == "-eo pid,args"
        return {"Processes": self._processes}


def _dottrace_executor() -> Executor:
    executor = Executor(config=Mock(), logger=Mock())
    executor._dottrace_active = True
    return executor


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


def test_dottrace_shutdown_signals_verified_client_and_waits_for_snapshot():
    executor = _dottrace_executor()
    container = _FakeContainer(
        processes=[["321", "/usr/bin/dotnet Nethermind.Runner.dll"]],
        exit_on_reload=2,
    )

    with (
        patch.object(
            executor_module.os, "pidfd_open", return_value=91, create=True
        ) as open_pidfd,
        patch.object(
            executor_module.signal,
            "pidfd_send_signal",
            create=True,
        ) as send_signal,
        patch.object(executor_module.os, "close") as close_pidfd,
    ):
        assert executor._request_dottrace_client_shutdown(container, timeout=1)

    open_pidfd.assert_called_once_with(321, 0)
    send_signal.assert_called_once_with(91, signal.SIGTERM)
    close_pidfd.assert_called_once_with(91)


@pytest.mark.parametrize(
    ("client_pid", "processes", "shutdown_succeeds"),
    [
        (None, [], True),
        (321, [], True),
        (321, [["321", "/usr/bin/python unrelated"]], False),
    ],
    ids=["child-not-found", "child-exited-before-signal", "pid-reused-by-other-process"],
)
def test_dottrace_shutdown_waits_or_falls_back_when_client_pid_is_unavailable(
    client_pid: int | None,
    processes: list[list[str]],
    shutdown_succeeds: bool,
):
    executor = _dottrace_executor()
    container = _FakeContainer(processes=processes, exit_on_reload=2)

    with (
        patch.object(executor, "_client_host_pid", return_value=client_pid),
        patch.object(executor_module.os, "pidfd_open", return_value=91, create=True),
        patch.object(
            executor_module.signal, "pidfd_send_signal", create=True
        ) as send_signal,
        patch.object(executor_module.os, "close") as close_pidfd,
    ):
        assert (
            executor._request_dottrace_client_shutdown(container, timeout=1)
            is shutdown_succeeds
        )

    send_signal.assert_not_called()
    if client_pid is not None:
        close_pidfd.assert_called_once_with(91)
    else:
        close_pidfd.assert_not_called()


def test_dottrace_shutdown_timeout_uses_existing_container_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    config.get_containers_network_name.return_value = "scenario-network"

    executor = Executor(config=config, logger=Mock())
    executor._dottrace_active = True
    container = _FakeContainer(processes=[], exit_on_reload=100)
    config.docker_client.containers.get.return_value = container
    config.docker_client.networks.get.return_value = Mock()
    monkeypatch.setenv("EXPB_STOP_TIMEOUT", "0")

    lifecycle: list[str] = []
    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_finalize_perf"),
        patch.object(executor, "_client_host_pid", return_value=None),
        patch.object(
            executor,
            "_teardown_container",
            side_effect=lambda name, **kwargs: lifecycle.append(name) or [],
        ) as teardown,
        patch.object(executor, "_stop_dotnet_trace_collector"),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == ["k6", "payload-server", "alloy", "nethermind"]
    assert teardown.call_args_list[-1].kwargs["stop_timeout"] == 0
    executor.log.warning.assert_called_once()
