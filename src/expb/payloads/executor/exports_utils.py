from expb.configs.clients import Client
from expb.configs.exports import Pyroscope


# Add pyroscope config to execution client container parameters
def add_pyroscope_config(
    client: Client,
    executor_name: str,
    scenario_name: str,
    pyroscope: Pyroscope,
    command: list[str],
    environment: dict[str, str],
) -> None:
    # Add pyroscope config for Nethermind client
    if client == Client.NETHERMIND:
        environment["DOTNET_EnableDiagnostics"] = "1"
        environment["DOTNET_EnableDiagnostics_IPC"] = "0"
        environment["DOTNET_EnableDiagnostics_Debugger"] = "0"
        environment["DOTNET_EnableDiagnostics_Profiler"] = "1"
        environment["PYROSCOPE_SERVER_ADDRESS"] = pyroscope.endpoint
        environment["PYROSCOPE_APPLICATION_NAME"] = executor_name
        environment["PYROSCOPE_PROFILING_ENABLED"] = "1"
        environment["CORECLR_ENABLE_PROFILING"] = "1"
        environment["CORECLR_PROFILER"] = "{BD1A650D-AC5D-4896-B64F-D6FA25D6B26A}"
        environment["CORECLR_PROFILER_PATH"] = "Pyroscope.Profiler.Native.so"
        environment["LD_PRELOAD"] = "Pyroscope.Linux.ApiWrapper.x64.so"
        if pyroscope.basic_auth is not None:
            environment["PYROSCOPE_BASIC_AUTH_USER"] = pyroscope.basic_auth.username
            environment["PYROSCOPE_BASIC_AUTH_PASSWORD"] = pyroscope.basic_auth.password
        if pyroscope.tags:
            environment["PYROSCOPE_LABELS"] = ",".join(
                [tag.replace("=", ":", 1) for tag in pyroscope.tags]
                + [f"testid={scenario_name}client_type={client.value.name}"]
            )
    else:
        # Ignore other clients
        pass
