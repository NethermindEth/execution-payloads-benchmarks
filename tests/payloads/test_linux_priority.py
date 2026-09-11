from unittest.mock import Mock

import pytest

from expb.payloads.executor.executor import Executor


def make_config(client: str = "nethermind") -> Mock:
    config = Mock()
    config.get_execution_client_name.return_value = client
    config.get_execution_client_command.return_value = ["--config=mainnet"]
    config.get_execution_client_env.return_value = {"EXISTING": "value"}
    config.get_execution_client_volumes.return_value = []
    config.get_execution_client_ports.return_value = {}
    config.get_execution_client_container_name.return_value = "execution-client"
    config.execution_client_image = "example/client:latest"
    config.docker_user = None
    config.docker_group_add = []
    config.execution_client_security_opt = []
    config.resources = None
    config.docker_client.containers.run.return_value = Mock()
    return config


@pytest.mark.parametrize("mode", ["observe", "nice"])
def test_priority_mode_forwards_mode_and_sys_nice_capability(monkeypatch, mode):
    monkeypatch.setenv("EXPB_NETHERMIND_PRIORITY_MODE", mode)
    config = make_config()
    executor = Executor(config=config, logger=Mock())

    executor.start_execution_client()

    kwargs = config.docker_client.containers.run.call_args.kwargs
    assert kwargs["environment"]["NETHERMIND_EXPB_PRIORITY_MODE"] == mode
    assert kwargs["cap_add"] == ["SYS_NICE"]


def test_priority_mode_off_preserves_default_container_arguments(monkeypatch):
    monkeypatch.delenv("EXPB_NETHERMIND_PRIORITY_MODE", raising=False)
    config = make_config()
    executor = Executor(config=config, logger=Mock())

    executor.start_execution_client()

    kwargs = config.docker_client.containers.run.call_args.kwargs
    assert "NETHERMIND_EXPB_PRIORITY_MODE" not in kwargs["environment"]
    assert "cap_add" not in kwargs


def test_priority_mode_off_is_an_explicit_noop(monkeypatch):
    monkeypatch.setenv("EXPB_NETHERMIND_PRIORITY_MODE", "off")
    config = make_config()
    executor = Executor(config=config, logger=Mock())

    executor.start_execution_client()

    kwargs = config.docker_client.containers.run.call_args.kwargs
    assert "NETHERMIND_EXPB_PRIORITY_MODE" not in kwargs["environment"]
    assert "cap_add" not in kwargs


def test_priority_mode_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("EXPB_NETHERMIND_PRIORITY_MODE", "turbo")

    with pytest.raises(ValueError, match="must be one of"):
        Executor(config=make_config(), logger=Mock())


def test_priority_mode_rejects_non_nethermind_client(monkeypatch):
    monkeypatch.setenv("EXPB_NETHERMIND_PRIORITY_MODE", "observe")

    with pytest.raises(ValueError, match="only supported for the nethermind client"):
        Executor(config=make_config("reth"), logger=Mock())
