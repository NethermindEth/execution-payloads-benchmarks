from pathlib import Path
from unittest.mock import Mock

import pytest

from expb.configs.scenarios import Scenario, ScenariosPaths
from expb.payloads.executor.executor_config import ExecutorConfig


@pytest.fixture
def scenario_inputs(tmp_path: Path) -> tuple[Scenario, ScenariosPaths]:
    payloads_file = tmp_path / "payloads.jsonl"
    fcus_file = tmp_path / "fcus.jsonl"
    payloads_file.touch()
    fcus_file.touch()
    scenario = Scenario(
        name="skip-override",
        client="nethermind",
        payloads=payloads_file,
        fcus=fcus_file,
        snapshot_source="snapshot",
        skip=7,
        amount=100,
        warmup=3,
    )
    return scenario, ScenariosPaths(work=tmp_path / "work", outputs=tmp_path / "outputs")


def create_config(
    monkeypatch: pytest.MonkeyPatch,
    scenario_inputs: tuple[Scenario, ScenariosPaths],
) -> ExecutorConfig:
    scenario, paths = scenario_inputs
    monkeypatch.setattr("expb.payloads.executor.executor_config.docker.from_env", Mock())
    monkeypatch.setattr(
        "expb.payloads.executor.executor_config.os.getuid", lambda: 1000, raising=False
    )
    monkeypatch.setattr(
        "expb.payloads.executor.executor_config.os.getgid", lambda: 1000, raising=False
    )
    return ExecutorConfig(
        scenario=scenario,
        snapshot_service=Mock(),
        paths=paths,
    )


def test_skip_override_preserves_configured_skip_and_downstream_totals(
    monkeypatch: pytest.MonkeyPatch,
    scenario_inputs: tuple[Scenario, ScenariosPaths],
):
    monkeypatch.delenv("EXPB_SKIP_OVERRIDE", raising=False)

    config = create_config(monkeypatch, scenario_inputs)

    assert config.k6_payloads_skip == 7
    environment = config.get_payload_server_environment()
    assert environment["EXPB_SKIP"] == "7"
    assert environment["EXPB_TOTAL"] == "103"


@pytest.mark.parametrize("override", ["0", "11"])
def test_skip_override_is_used_by_payload_server(
    monkeypatch: pytest.MonkeyPatch,
    scenario_inputs: tuple[Scenario, ScenariosPaths],
    override: str,
):
    monkeypatch.setenv("EXPB_SKIP_OVERRIDE", override)

    config = create_config(monkeypatch, scenario_inputs)

    assert config.k6_payloads_skip == int(override)
    environment = config.get_payload_server_environment()
    assert environment["EXPB_SKIP"] == override
    assert environment["EXPB_TOTAL"] == "103"


@pytest.mark.parametrize("override", ["", "-1", "1.5", "many"])
def test_skip_override_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    scenario_inputs: tuple[Scenario, ScenariosPaths],
    override: str,
):
    monkeypatch.setenv("EXPB_SKIP_OVERRIDE", override)

    with pytest.raises(
        ValueError,
        match="EXPB_SKIP_OVERRIDE must be a nonnegative integer",
    ):
        create_config(monkeypatch, scenario_inputs)
