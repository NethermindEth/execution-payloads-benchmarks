from unittest.mock import Mock

import pytest

from expb.payloads.executor.executor import Executor
from expb.payloads.executor.executor_config import EXECUTION_CLIENT_ALIAS


def make_executor(container_name: str) -> Executor:
    config = Mock(resources=None, docker_client=Mock())
    config.get_execution_client_volumes.return_value = []
    config.get_execution_client_container_name.return_value = container_name
    config.get_execution_client_aliases.return_value = [EXECUTION_CLIENT_ALIAS]
    return Executor(config=config, logger=Mock())


def test_client_joins_the_scenario_network_under_its_alias():
    executor = make_executor("expb-executor-nethermind")
    network = Mock()
    network.name = "expb-network"

    executor.start_execution_client(container_network=network)

    docker_client = executor.config.docker_client
    docker_client.api.create_endpoint_config.assert_called_once_with(
        aliases=[EXECUTION_CLIENT_ALIAS]
    )
    run_kwargs = docker_client.containers.run.call_args.kwargs
    assert run_kwargs["network"] == "expb-network"
    assert run_kwargs["networking_config"] == {
        "expb-network": docker_client.api.create_endpoint_config.return_value
    }
    assert run_kwargs["detach"] is True
    docker_client.containers.create.assert_not_called()


def test_client_without_network_has_no_endpoint_config():
    executor = make_executor("expb-executor-nethermind")

    executor.start_execution_client(container_network=None)

    run_kwargs = executor.config.docker_client.containers.run.call_args.kwargs
    assert run_kwargs["network"] is None
    assert "networking_config" not in run_kwargs


@pytest.mark.parametrize("length,warns", [(63, False), (64, True)])
def test_overlong_container_name_logs_a_warning(length: int, warns: bool):
    executor = make_executor("x" * length)
    network = Mock()
    network.name = "expb-network"

    executor.start_execution_client(container_network=network)

    assert executor.log.warning.called is warns
