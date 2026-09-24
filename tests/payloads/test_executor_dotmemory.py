import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from expb.payloads.executor.executor import Executor


class _FakeClient:
    def __init__(self, running: bool = True, exec_run=None):
        self.attrs = {"State": {"Running": running, "Status": "running" if running else "exited"}}
        self.exec_run = Mock(side_effect=exec_run)

    def reload(self):
        pass


def _dotmemory_executor(tmp_path: Path, client: _FakeClient) -> Executor:
    config = Mock(outputs_dir=tmp_path, test_id="run-1", docker_client=Mock())
    config.get_execution_client_container_name.return_value = "nethermind"
    config.docker_client.containers.get.return_value = client
    (tmp_path / "dottrace").mkdir()
    return Executor(config=config, logger=Mock())


def test_cleanup_snapshots_after_eventpipe_and_before_client_teardown(tmp_path: Path):
    config = Mock(executor_name="scenario", outputs_dir=tmp_path, docker_client=Mock())
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"

    executor = Executor(config=config, logger=Mock())
    executor._dotmemory_active = True
    lifecycle: list[str] = []

    def teardown(name: str, **kwargs):
        lifecycle.append(name)
        return []

    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", side_effect=teardown),
        patch.object(executor, "_finalize_perf"),
        patch.object(
            executor, "_stop_dotnet_trace_collector", side_effect=lambda: lifecycle.append("eventpipe")
        ),
        patch.object(
            executor,
            "_take_dotmemory_snapshot",
            side_effect=lambda timeout: lifecycle.append("dotmemory") or True,
        ),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == ["k6", "payload-server", "alloy", "eventpipe", "dotmemory", "nethermind"]
    assert executor._dotmemory_active is False


def test_snapshot_runs_console_in_client_and_waits_for_workspace(tmp_path: Path):
    def exec_run(command):
        (tmp_path / "dottrace" / "run-1.dmw").write_bytes(b"workspace")
        return SimpleNamespace(exit_code=0, output=b'##dotMemory["snapshot-saved"]')

    client = _FakeClient(exec_run=exec_run)
    executor = _dotmemory_executor(tmp_path, client)

    assert executor._take_dotmemory_snapshot(timeout=5) is True

    command = client.exec_run.call_args.args[0]
    assert command[:4] == ["/usr/bin/env", "-u", "DOTNET_DiagnosticPorts", "HOME=/tmp"]
    assert command[4] == "/opt/dotmemory/dotmemory"
    assert command[5:8] == ["get-snapshot", "nethermind", "--with-max-mem"]
    assert "--save-to-file=/dottrace-output/run-1.dmw" in command
    assert "snapshot-saved" in (tmp_path / "dottrace" / "dotmemory-get-snapshot.log").read_text()


def test_snapshot_skipped_when_client_already_exited(tmp_path: Path):
    client = _FakeClient(running=False)
    executor = _dotmemory_executor(tmp_path, client)

    assert executor._take_dotmemory_snapshot(timeout=5) is False
    client.exec_run.assert_not_called()


def test_snapshot_failure_is_reported_without_raising(tmp_path: Path):
    client = _FakeClient(exec_run=lambda command: SimpleNamespace(exit_code=3, output=b"attach failed"))
    executor = _dotmemory_executor(tmp_path, client)

    assert executor._take_dotmemory_snapshot(timeout=5) is False
    assert "attach failed" in (tmp_path / "dottrace" / "dotmemory-get-snapshot.log").read_text()


def test_snapshot_timeout_does_not_block_teardown(tmp_path: Path):
    def exec_run(command):
        time.sleep(2)
        return SimpleNamespace(exit_code=0, output=b"")

    executor = _dotmemory_executor(tmp_path, _FakeClient(exec_run=exec_run))

    started = time.monotonic()
    assert executor._take_dotmemory_snapshot(timeout=0.2) is False
    assert time.monotonic() - started < 1.5


def test_dotmemory_mode_reads_environment(monkeypatch):
    monkeypatch.setenv("EXPB_DOTMEMORY", " Final ")
    assert Executor._dotmemory_mode() == "final"
    monkeypatch.delenv("EXPB_DOTMEMORY")
    assert Executor._dotmemory_mode() == ""
