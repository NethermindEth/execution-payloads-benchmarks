from expb.configs.clients import Client
from expb.configs.exports import Pyroscope


def add_pyroscope_config(
    client: Client,
    executor_name: str,
    test_id: str,
    pyroscope: Pyroscope,
    command: list[str],
    environment: dict[str, str],
) -> None:
    if client == Client.NETHERMIND:
        # .NET 8/9 diagnostics: allow profiling but disable IPC/debugger
        environment["DOTNET_EnableDiagnostics"] = "1"
        environment["DOTNET_EnableDiagnostics_IPC"] = "0"
        environment["DOTNET_EnableDiagnostics_Debugger"] = "0"
        environment["DOTNET_EnableDiagnostics_Profiler"] = "1"

        # Where to send data + naming
        environment["PYROSCOPE_SERVER_ADDRESS"] = pyroscope.endpoint
        environment["PYROSCOPE_APPLICATION_NAME"] = executor_name
        environment["PYROSCOPE_LOG_LEVEL"] = "debug"

        # Enable ALL profiling types
        environment["PYROSCOPE_PROFILING_ENABLED"] = "1"
        environment["PYROSCOPE_PROFILING_CPU_ENABLED"] = "true"
        environment["PYROSCOPE_PROFILING_WALLTIME_ENABLED"] = "true"
        environment["PYROSCOPE_PROFILING_ALLOCATION_ENABLED"] = "true"
        environment["PYROSCOPE_PROFILING_LOCK_ENABLED"] = "true"
        environment["PYROSCOPE_PROFILING_EXCEPTION_ENABLED"] = "true"
        environment["PYROSCOPE_PROFILING_HEAP_ENABLED"] = "true"

        # Optional: basic auth
        if pyroscope.basic_auth is not None:
            environment["PYROSCOPE_BASIC_AUTH_USER"] = pyroscope.basic_auth.username
            environment["PYROSCOPE_BASIC_AUTH_PASSWORD"] = pyroscope.basic_auth.password

        # Labels (key=value -> key:value; plus scenario/client)
        if pyroscope.tags:
            environment["PYROSCOPE_LABELS"] = ",".join(
                [t.replace("=", ":", 1) for t in pyroscope.tags]
                + [f"testid:{test_id}", f"client_type:{client.value.name}"]
            )
        else:
            environment["PYROSCOPE_LABELS"] = ",".join(
                [f"testid:{test_id}", f"client_type:{client.value.name}"]
            )
    else:
        pass
