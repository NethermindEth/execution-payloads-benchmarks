from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from expb.payloads.executor.executor import Executor


class _FakeClient:
    """The running client container: exec_run is how the dump is requested."""

    def __init__(self, running=True, exit_code=0, dump: Path | None = None):
        self.attrs = {"State": {"Running": running, "Status": "running" if running else "exited"}}
        self.exec_calls: list[list[str]] = []
        self._exit_code = exit_code
        self._dump = dump

    def reload(self):
        pass

    def exec_run(self, cmd):
        self.exec_calls.append(cmd)
        if "collect" in cmd and self._exit_code == 0 and self._dump is not None:
            self._dump.write_bytes(b"core")
        return SimpleNamespace(exit_code=self._exit_code if "collect" in cmd else 0, output=b"Complete\n")


def _executor(tmp_path: Path, container=None) -> Executor:
    config = Mock(docker_client=Mock(), outputs_dir=tmp_path, test_id="run1")
    config.get_execution_client_container_name.return_value = "nethermind"
    config.execution_client.value.entrypoint = "/nethermind/nethermind"
    config.execution_client_image = "nethermindeth/nethermind:master"
    config.docker_client.containers.get.return_value = container
    return Executor(config=config, logger=Mock())


def test_cleanup_dumps_after_eventpipe_before_dotmemory_and_analyzes_after_teardown(tmp_path: Path):
    config = Mock(executor_name="scenario", outputs_dir=tmp_path, docker_client=Mock())
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"

    executor = Executor(config=config, logger=Mock())
    executor._dotnet_dump_active = True
    executor._dotmemory_active = True
    lifecycle: list[str] = []

    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", side_effect=lambda name, **kw: lifecycle.append(name) or []),
        patch.object(executor, "_finalize_perf"),
        patch.object(executor, "_stop_dotnet_trace_collector", side_effect=lambda: lifecycle.append("eventpipe")),
        patch.object(executor, "_collect_dotnet_dump", side_effect=lambda timeout: lifecycle.append("dump") or True),
        patch.object(executor, "_request_dotmemory_snapshot", side_effect=lambda timeout: lifecycle.append("dotmemory") or True),
        patch.object(executor, "_analyze_dotnet_dump", side_effect=lambda timeout: lifecycle.append("analyze")),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == ["k6", "payload-server", "alloy", "eventpipe", "dump", "dotmemory", "nethermind", "analyze"]
    assert executor._dotnet_dump_active is False


def test_cleanup_skips_analysis_when_the_dump_failed(tmp_path: Path):
    config = Mock(executor_name="scenario", outputs_dir=tmp_path, docker_client=Mock())
    executor = Executor(config=config, logger=Mock())
    executor._dotnet_dump_active = True

    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", return_value=[]),
        patch.object(executor, "_finalize_perf"),
        patch.object(executor, "_stop_dotnet_trace_collector"),
        patch.object(executor, "_collect_dotnet_dump", return_value=False),
        patch.object(executor, "_analyze_dotnet_dump") as analyze,
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    analyze.assert_not_called()


def test_collect_asks_the_client_image_tool_for_a_heap_dump_and_makes_it_readable(tmp_path: Path):
    (tmp_path / "dotnet-dump").mkdir()
    client = _FakeClient(dump=tmp_path / "dotnet-dump" / "run1.dmp")

    assert _executor(tmp_path, client)._collect_dotnet_dump(timeout=5) is True
    assert client.exec_calls == [
        [
            "/usr/bin/env", "DOTNET_ROLL_FORWARD=Major", "/opt/dotnet-dump/dotnet-dump",
            "collect", "--name", "nethermind", "--type", "Heap", "--output", "/expb-dotnet-dump/run1.dmp",
        ],
        ["chmod", "0644", "/expb-dotnet-dump/run1.dmp"],
    ]


def test_collect_reports_a_failed_dump(tmp_path: Path):
    assert _executor(tmp_path, _FakeClient(exit_code=1))._collect_dotnet_dump(timeout=5) is False


def test_collect_skipped_when_client_already_exited(tmp_path: Path):
    client = _FakeClient(running=False)

    assert _executor(tmp_path, client)._collect_dotnet_dump(timeout=5) is False
    assert client.exec_calls == []


def test_collect_gives_up_on_a_hung_exec(tmp_path: Path):
    client = _FakeClient()
    client.exec_run = lambda cmd: __import__("time").sleep(2)

    assert _executor(tmp_path, client)._collect_dotnet_dump(timeout=0.1) is False


def _analysis_containers(outputs: dict[str, bytes]):
    """containers.run for the throwaway analysis containers: one per report command."""
    runs: list[dict] = []

    def run(**kwargs):
        runs.append(kwargs)
        command = kwargs["entrypoint"][kwargs["entrypoint"].index("--command") + 1]
        container = Mock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = outputs.get(command, b"")
        return container

    return runs, run


def test_analysis_writes_one_report_per_command_and_drops_the_dump(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EXPB_DOTNET_DUMP_KEEP", raising=False)
    monkeypatch.setenv("EXPB_DOTNET_DUMP_COMMANDS", "dumpheap -stat; gcheapstat")
    dump = tmp_path / "dotnet-dump" / "run1.dmp"
    dump.parent.mkdir()
    dump.write_bytes(b"core")
    executor = _executor(tmp_path)
    runs, run = _analysis_containers({"dumpheap -stat": b"Statistics:\n", "gcheapstat": b"Heap0\n"})
    executor.config.docker_client.containers.run.side_effect = run

    with patch.object(executor, "_ensure_dotnet_dump_installed", return_value="/opt/dotnet-dump"):
        executor._analyze_dotnet_dump(timeout=5)

    reports = tmp_path / "dottrace" / "dotnet-dump"
    assert (reports / "dumpheap-stat.txt").read_bytes() == b"Statistics:\n"
    assert (reports / "gcheapstat.txt").read_bytes() == b"Heap0\n"
    assert not dump.exists()
    assert runs[0]["image"] == "nethermindeth/nethermind:master"
    assert runs[0]["network_mode"] == "none"
    assert runs[0]["entrypoint"][3:] == ["analyze", "/expb-dotnet-dump/run1.dmp", "--command", "dumpheap -stat", "--command", "exit"]
    assert f"{dump.parent}:/expb-dotnet-dump:ro" in runs[0]["volumes"]


def test_analysis_keeps_the_dump_with_the_reports_on_request(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EXPB_DOTNET_DUMP_KEEP", "1")
    monkeypatch.setenv("EXPB_DOTNET_DUMP_COMMANDS", "gcheapstat")
    dump = tmp_path / "dotnet-dump" / "run1.dmp"
    dump.parent.mkdir()
    dump.write_bytes(b"core")
    executor = _executor(tmp_path)
    _, run = _analysis_containers({})
    executor.config.docker_client.containers.run.side_effect = run

    with patch.object(executor, "_ensure_dotnet_dump_installed", return_value="/opt/dotnet-dump"):
        executor._analyze_dotnet_dump(timeout=5)

    assert (tmp_path / "dottrace" / "dotnet-dump" / "run1.dmp").read_bytes() == b"core"


def test_a_failing_report_does_not_stop_the_others(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EXPB_DOTNET_DUMP_KEEP", raising=False)
    monkeypatch.setenv("EXPB_DOTNET_DUMP_COMMANDS", "dumpheap -stat -live;sizestats")
    dump = tmp_path / "dotnet-dump" / "run1.dmp"
    dump.parent.mkdir()
    dump.write_bytes(b"core")
    executor = _executor(tmp_path)
    _, run = _analysis_containers({"sizestats": b"Size Statistics\n"})

    def flaky(**kwargs):
        if "dumpheap -stat -live" in kwargs["entrypoint"]:
            raise RuntimeError("analysis container failed")
        return run(**kwargs)

    executor.config.docker_client.containers.run.side_effect = flaky

    with patch.object(executor, "_ensure_dotnet_dump_installed", return_value="/opt/dotnet-dump"):
        executor._analyze_dotnet_dump(timeout=5)

    assert (tmp_path / "dottrace" / "dotnet-dump" / "sizestats.txt").exists()
    assert not (tmp_path / "dottrace" / "dotnet-dump" / "dumpheap-stat-live.txt").exists()


def test_default_reports_cover_generations_types_liveness_and_large_objects(monkeypatch):
    monkeypatch.delenv("EXPB_DOTNET_DUMP_COMMANDS", raising=False)
    assert dict(Executor._dotnet_dump_reports()) == {
        "gcheapstat": "gcheapstat",
        "eeheap-gc": "eeheap -gc",
        "dumpheap-stat": "dumpheap -stat",
        "dumpheap-stat-live": "dumpheap -stat -live",
        "dumpheap-stat-dead": "dumpheap -stat -dead",
        "dumpheap-stat-loh": "dumpheap -stat -min 85000",
        "sizestats": "sizestats",
    }


def test_dump_mode_reads_environment(monkeypatch):
    monkeypatch.setenv("EXPB_DOTNET_DUMP", " Final ")
    assert Executor._dotnet_dump_mode() == "final"
    monkeypatch.delenv("EXPB_DOTNET_DUMP")
    assert Executor._dotnet_dump_mode() == ""
