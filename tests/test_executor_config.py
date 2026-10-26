import json
from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock

import pytest
from click import unstyle
from typer.testing import CliRunner

from expb.clients import Client
from expb.configs.scenarios import Scenario, ScenariosPaths
from expb.payloads.executor import executor_config as executor_config_module
from expb.payloads.executor.executor_config import ExecutorConfig
from expb.payloads.executor.services.snapshots import SnapshotService


class StubSnapshotService(SnapshotService):
    def __init__(self, snapshot_path: Path):
        self.snapshot_path = snapshot_path

    def get_snapshot(self, name: str, source: str) -> Path:
        return self.snapshot_path


def make_scenario(tmp_path: Path, **overrides) -> Scenario:
    payloads = tmp_path / "payloads.jsonl"
    fcus = tmp_path / "fcus.jsonl"
    payloads.touch()
    fcus.touch()
    values = {
        "client": Client.GETH,
        "payloads": payloads,
        "fcus": fcus,
        "snapshot_source": "snapshot",
    }
    values.update(overrides)
    return Scenario.model_validate(values)


@pytest.mark.parametrize(
    "mount_path",
    ["/execution-data", "/execution-data/geth"],
)
def test_snapshot_mount_path_allows_supported_client_layouts(
    tmp_path: Path, mount_path: str
) -> None:
    scenario = make_scenario(tmp_path, snapshot_mount_path=mount_path)

    assert scenario.snapshot_mount_path == mount_path


def test_snapshot_mount_path_defaults_to_client_data_directory(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)

    assert scenario.snapshot_mount_path == "/execution-data"


def test_snapshot_mount_path_rejects_arbitrary_container_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_scenario(tmp_path, snapshot_mount_path="/tmp/snapshot")


@pytest.mark.parametrize("startup_block", [None, 0, 25490000])
@pytest.mark.parametrize("wait_for_head", [False, True])
def test_startup_block_preserves_the_configured_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, startup_block: int | None,
    wait_for_head: bool,
) -> None:
    monkeypatch.setattr(executor_config_module.docker, "from_env", lambda: object())
    monkeypatch.setattr(executor_config_module.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(executor_config_module.os, "getgid", lambda: 0, raising=False)
    scenario = make_scenario(
        tmp_path, startup_block=startup_block, startup_wait_for_head=wait_for_head
    )
    config = ExecutorConfig(
        scenario=scenario,
        snapshot_service=StubSnapshotService(tmp_path),
        paths=ScenariosPaths(work=tmp_path / "work", outputs=tmp_path / "outputs"),
    )

    assert config.startup_block == startup_block
    assert config.startup_wait_for_head is wait_for_head


def test_head_wait_is_opt_in(tmp_path: Path) -> None:
    assert make_scenario(tmp_path).startup_wait_for_head is False


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false"])
def test_head_wait_rejects_non_boolean_values(tmp_path: Path, value) -> None:
    with pytest.raises(ValueError):
        make_scenario(tmp_path, startup_wait_for_head=value)


@pytest.mark.parametrize("checkpoint", [-1, True, False, "100", "0x64", "many", 1.5])
def test_startup_block_rejects_invalid_checkpoints(tmp_path: Path, checkpoint) -> None:
    with pytest.raises(ValueError):
        make_scenario(tmp_path, startup_block=checkpoint)


@pytest.mark.parametrize("command", ["execute_scenario", "execute_scenarios"])
@pytest.mark.parametrize("checkpoint", [None, "0", "105", "-1", "true", "1.5"])
def test_cli_startup_block_validates_and_overrides_the_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, checkpoint: str | None
) -> None:
    module = import_module(f"expb.{command}")
    scenario = make_scenario(tmp_path, client="nethermind", startup_block=100)
    config_file = tmp_path / "expb.yaml"
    config_file.write_text(json.dumps({
        "paths": {"work": str(tmp_path / "work"), "outputs": str(tmp_path / "outputs")},
        "scenarios": {"ready": {
            "client": "nethermind",
            "payloads": str(scenario.payloads_file),
            "fcus": str(scenario.fcus_file),
            "snapshot_source": scenario.snapshot_source,
            "startup_block": scenario.startup_block,
        }},
    }))
    factory = Mock(return_value=Mock())
    monkeypatch.setattr(module.Executor, "from_scenarios", factory)
    monkeypatch.setattr(module, "acquire_execution_lock", lambda **kwargs: nullcontext())
    arguments = ["--config-file", str(config_file)]
    if command == "execute_scenario":
        arguments.extend(["--scenario-name", "ready"])
    if checkpoint is not None:
        arguments.extend(["--startup-block", checkpoint])

    result = CliRunner().invoke(module.app, arguments)

    if checkpoint in ("-1", "true", "1.5"):
        assert result.exit_code != 0
        assert "startup-block" in unstyle(result.output)
        factory.assert_not_called()
    else:
        assert result.exit_code == 0, result.output
        configured = factory.call_args.args[0].scenarios_configs["ready"]
        assert configured.startup_block == (100 if checkpoint is None else int(checkpoint))


@pytest.mark.parametrize("checkpoint", [None, "0", "105"])
@pytest.mark.parametrize("filtered", [False, True])
def test_multi_scenario_checkpoint_override_requires_one_selected_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checkpoint: str | None, filtered: bool
) -> None:
    module = import_module("expb.execute_scenarios")
    scenario = make_scenario(tmp_path, client="nethermind")
    config_file = tmp_path / "expb.yaml"
    config_file.write_text(json.dumps({
        "paths": {"work": str(tmp_path / "work"), "outputs": str(tmp_path / "outputs")},
        "scenarios": {
            name: {
                "client": "nethermind",
                "payloads": str(scenario.payloads_file),
                "fcus": str(scenario.fcus_file),
                "snapshot_source": name,
                "startup_block": block,
            }
            for name, block in (("first", 100), ("second", 200))
        },
    }))
    factory = Mock(return_value=Mock())
    monkeypatch.setattr(module.Executor, "from_scenarios", factory)
    monkeypatch.setattr(module, "acquire_execution_lock", lambda **kwargs: nullcontext())
    arguments = ["--config-file", str(config_file)]
    if checkpoint is not None:
        arguments.extend(["--startup-block", checkpoint])
    if filtered:
        arguments.extend(["--filter", "^first$"])

    result = CliRunner().invoke(module.app, arguments)

    if checkpoint is not None and not filtered:
        assert result.exit_code != 0
        assert "select one scenario" in unstyle(result.output)
        factory.assert_not_called()
    else:
        assert result.exit_code == 0, result.output
        assert factory.call_count == (1 if filtered else 2)
        configured = factory.call_args.args[0].scenarios_configs
        assert configured["first"].startup_block == (100 if checkpoint is None else int(checkpoint))
        assert configured["second"].startup_block == 200


def test_alloy_scrapes_the_client_alias_regardless_of_container_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executor_config_module.docker, "from_env", lambda: object())
    monkeypatch.setattr(executor_config_module.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(executor_config_module.os, "getgid", lambda: 0, raising=False)
    scenario = make_scenario(tmp_path, name="nethermind-multi-" + "a" * 40 + "-run1")
    config = ExecutorConfig(
        scenario=scenario,
        snapshot_service=StubSnapshotService(tmp_path),
        paths=ScenariosPaths(work=tmp_path / "work", outputs=tmp_path / "outputs"),
    )

    assert len(config.get_execution_client_container_name()) > 63
    assert config.get_execution_metrics_address() == "execution-client:6060"


def test_executor_mounts_snapshot_at_configured_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executor_config_module.docker, "from_env", lambda: object())
    monkeypatch.setattr(executor_config_module.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(executor_config_module.os, "getgid", lambda: 0, raising=False)

    snapshot_path = tmp_path / "snapshot"
    snapshot_path.mkdir()
    scenario = make_scenario(tmp_path, name="geth-fusaka", snapshot_mount_path="/execution-data/geth")
    config = ExecutorConfig(
        scenario=scenario,
        snapshot_service=StubSnapshotService(snapshot_path),
        paths=ScenariosPaths(
            work=tmp_path / "work",
            outputs=tmp_path / "outputs",
        ),
    )

    volumes = config.get_execution_client_volumes()
    snapshot_volume = next(
        volume
        for volume in volumes
        if volume["config"]["name"].endswith("-overlay-merged")
    )

    assert snapshot_volume["bind"] == "/execution-data/geth"
    assert snapshot_volume["config"]["driver_opts"]["o"].startswith("bind,rw")
    assert snapshot_volume["config"]["driver_opts"]["device"] == str(
        snapshot_path.resolve()
    )
    assert all(volume["bind"] != "/execution-data" for volume in volumes)
