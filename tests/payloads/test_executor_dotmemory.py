from pathlib import Path
from unittest.mock import Mock, patch

from expb.payloads.executor.executor import Executor

CONNECTED = b'##dotMemory["connected",{"pid":33,"name":"nethermind"}]\n'
SAVED = b'##dotMemory["snapshot-saved",{"pid":33,"ordinal":1}]\n'


class _FakeSocket:
    def __init__(self, on_send):
        self._sock = self
        self.sent = b""
        self._on_send = on_send
        self.closed = False

    def sendall(self, data):
        self.sent += data
        self._on_send()

    def close(self):
        self.closed = True


class _FakeWrapper:
    """The client container: the dotMemory wrapper whose stdout carries service messages."""

    def __init__(self, running=True, logs=CONNECTED, save_on_request=True):
        self.attrs = {"State": {"Running": running, "Status": "running" if running else "exited"}}
        self._logs = logs
        self._save_on_request = save_on_request
        self.socket = None

    def reload(self):
        pass

    def logs(self):
        return self._logs

    def attach_socket(self, params):
        assert params == {"stdin": 1, "stream": 1}

        def on_send():
            if self._save_on_request:
                self._logs += SAVED

        self.socket = _FakeSocket(on_send)
        return self.socket


def _executor(container) -> Executor:
    config = Mock(docker_client=Mock())
    config.get_execution_client_container_name.return_value = "nethermind"
    config.docker_client.containers.get.return_value = container
    return Executor(config=config, logger=Mock())


def test_cleanup_requests_snapshot_after_eventpipe_and_before_client_teardown(tmp_path: Path):
    config = Mock(executor_name="scenario", outputs_dir=tmp_path, docker_client=Mock())
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"

    executor = Executor(config=config, logger=Mock())
    executor._dotmemory_active = True
    lifecycle: list[str] = []

    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", side_effect=lambda name, **kw: lifecycle.append(name) or []),
        patch.object(executor, "_finalize_perf"),
        patch.object(executor, "_stop_dotnet_trace_collector", side_effect=lambda: lifecycle.append("eventpipe")),
        patch.object(
            executor,
            "_request_dotmemory_snapshot",
            side_effect=lambda timeout: lifecycle.append("dotmemory") or True,
        ),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == ["k6", "payload-server", "alloy", "eventpipe", "dotmemory", "nethermind"]
    assert executor._dotmemory_active is False


def test_snapshot_request_writes_service_message_and_waits_for_save():
    wrapper = _FakeWrapper()

    assert _executor(wrapper)._request_dotmemory_snapshot(timeout=5) is True
    assert wrapper.socket.sent == b'##dotMemory["get-snapshot", {pid:33}]\n'
    assert wrapper.socket.closed


def test_snapshot_request_needs_the_wrapper_to_report_the_client_pid():
    wrapper = _FakeWrapper(logs=b"Starting...\n")

    assert _executor(wrapper)._request_dotmemory_snapshot(timeout=5) is False
    assert wrapper.socket is None


def test_snapshot_request_skipped_when_client_already_exited():
    wrapper = _FakeWrapper(running=False)

    assert _executor(wrapper)._request_dotmemory_snapshot(timeout=5) is False
    assert wrapper.socket is None


def test_snapshot_request_times_out_without_confirmation():
    wrapper = _FakeWrapper(save_on_request=False)

    assert _executor(wrapper)._request_dotmemory_snapshot(timeout=0.1) is False


def test_existing_snapshots_do_not_count_as_the_requested_one():
    wrapper = _FakeWrapper(logs=CONNECTED + SAVED, save_on_request=False)

    assert _executor(wrapper)._request_dotmemory_snapshot(timeout=0.1) is False


def test_trigger_args_from_environment(monkeypatch):
    for name in ("EXPB_DOTMEMORY_PERIOD", "EXPB_DOTMEMORY_MAX_SNAPSHOTS", "EXPB_DOTMEMORY_DELAY", "EXPB_DOTMEMORY_COLLECT_ALLOC"):
        monkeypatch.delenv(name, raising=False)
    assert Executor._dotmemory_trigger_args() == []

    monkeypatch.setenv("EXPB_DOTMEMORY_PERIOD", "00:00:20")
    monkeypatch.setenv("EXPB_DOTMEMORY_DELAY", "00:00:30")
    monkeypatch.setenv("EXPB_DOTMEMORY_COLLECT_ALLOC", "1")
    assert Executor._dotmemory_trigger_args() == [
        "--trigger-timer=00:00:20",
        "--trigger-max-snapshots=3",
        "--trigger-delay=00:00:30",
        "--collect-alloc",
    ]


def test_client_pid_skips_the_dotmemory_wrapper():
    container = Mock()
    container.top.return_value = {
        "Processes": [
            ["10", "/opt/dotmemory/linux-x64/dotnet/dotnet /opt/dotmemory/dotMemory.exe start "
                   "--service-output /usr/bin/env -- /nethermind/nethermind --config mainnet"],
            ["33", "/nethermind/nethermind --config mainnet"],
        ]
    }

    assert Executor(config=Mock(), logger=Mock())._client_host_pid(container, timeout=1) == 33


def test_dotmemory_mode_reads_environment(monkeypatch):
    monkeypatch.setenv("EXPB_DOTMEMORY", " Final ")
    assert Executor._dotmemory_mode() == "final"
    monkeypatch.delenv("EXPB_DOTMEMORY")
    assert Executor._dotmemory_mode() == ""
