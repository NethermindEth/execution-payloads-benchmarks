from pathlib import Path

import pytest

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
