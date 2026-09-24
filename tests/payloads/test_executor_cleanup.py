import ctypes
import errno
import os
import selectors
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import expb.payloads.executor.executor as executor_module
from expb.payloads.executor.executor import Executor


class _FakeContainer:
    name = "nethermind"

    def __init__(self, processes: list[list[str]], exit_on_reload: int):
        self.attrs = {"State": {"Running": True, "Status": "running"}}
        self._processes = processes
        self._exit_on_reload = exit_on_reload
        self._reload_count = 0

    def reload(self):
        self._reload_count += 1
        if self._reload_count >= self._exit_on_reload:
            self.attrs["State"] = {"Running": False, "Status": "exited"}

    def top(self, ps_args: str):
        assert ps_args == "-eo pid,args"
        return {"Processes": self._processes}


def _dottrace_executor() -> Executor:
    executor = Executor(config=Mock(), logger=Mock())
    executor._dottrace_active = True
    return executor


def test_cleanup_stops_rpc_consumers_before_client_and_preserves_metrics(tmp_path: Path):
    config = Mock(
        executor_name="scenario",
        outputs_dir=tmp_path,
        docker_client=Mock(),
    )
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"
    config.docker_client.networks.get.return_value = Mock()

    executor = Executor(config=config, logger=Mock())
    lifecycle: list[str] = []

    def teardown(name: str, **kwargs):
        lifecycle.append(name)
        if name == "k6":
            kwargs["line_callback"](
                'EXPB_PER_PAYLOAD_METRIC idx=7 gas_used=123 processing_ms=45.6\n'
            )
        return []

    def stop_trace():
        lifecycle.append("eventpipe")

    def finalize_perf(_container):
        lifecycle.append("perf")

    teardown_mock = Mock(side_effect=teardown)
    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_teardown_container", teardown_mock),
        patch.object(executor, "_finalize_perf", side_effect=finalize_perf),
        patch.object(executor, "_stop_dotnet_trace_collector", side_effect=stop_trace),
        patch.object(executor, "_print_per_payload_metrics_table") as print_table,
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario(
            print_logs_to_console=True,
            print_per_payload_metrics_table=True,
        )

    assert lifecycle == [
        "perf",
        "k6",
        "payload-server",
        "alloy",
        "eventpipe",
        "nethermind",
    ]
    teardown_calls = teardown_mock.call_args_list
    assert teardown_calls[0].kwargs["log_file"] == tmp_path / "k6.log"
    assert teardown_calls[0].kwargs["line_callback"] is not None
    assert teardown_calls[1].kwargs["log_file"] == tmp_path / "payload-server.log"
    print_table.assert_called_once_with([(7, "123", "45.6")])


def test_dottrace_shutdown_signals_verified_client_and_waits_for_snapshot():
    executor = _dottrace_executor()
    container = _FakeContainer(
        processes=[["321", "/usr/bin/dotnet Nethermind.Runner.dll"]],
        exit_on_reload=2,
    )

    with (
        patch.object(
            executor_module.os, "pidfd_open", return_value=91, create=True
        ) as open_pidfd,
        patch.object(
            executor_module.signal,
            "pidfd_send_signal",
            create=True,
        ) as send_signal,
        patch.object(executor_module.os, "close") as close_pidfd,
    ):
        assert executor._request_dottrace_client_shutdown(container, timeout=1)

    open_pidfd.assert_called_once_with(321, 0)
    send_signal.assert_called_once_with(91, signal.SIGTERM)
    close_pidfd.assert_called_once_with(91)


def test_pidfd_syscall_compatibility_calls_typed_open_and_send(monkeypatch):
    monkeypatch.setattr(executor_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(
        executor_module.signal, "pidfd_send_signal", None, raising=False
    )
    monkeypatch.setattr(executor_module.sys, "platform", "linux")
    monkeypatch.setattr(executor_module.platform, "machine", lambda: "x86_64")
    calls = []
    libc = Mock()

    def syscall(*arguments):
        calls.append(arguments)
        return 91 if arguments[0].value == 434 else 0

    libc.syscall.side_effect = syscall
    monkeypatch.setattr(executor_module.ctypes, "CDLL", Mock(return_value=libc))

    pidfd_open, pidfd_send_signal = executor_module._pidfd_signaling_functions()
    assert pidfd_open(321, 0) == 91
    pidfd_send_signal(91, signal.SIGTERM)

    def value(argument):
        return getattr(argument, "value", argument)

    assert [[value(argument) for argument in call] for call in calls] == [
        [434, 321, 0],
        [424, 91, signal.SIGTERM, None, 0],
    ]
    assert libc.syscall.restype is ctypes.c_long


@pytest.mark.parametrize(
    ("error_number", "error_type"),
    [(errno.ENOSYS, OSError), (errno.ESRCH, ProcessLookupError)],
    ids=["unsupported-kernel", "process-exited"],
)
def test_pidfd_syscall_compatibility_preserves_errno(
    monkeypatch, error_number: int, error_type: type[OSError]
):
    monkeypatch.setattr(executor_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(
        executor_module.signal, "pidfd_send_signal", None, raising=False
    )
    monkeypatch.setattr(executor_module.sys, "platform", "linux")
    monkeypatch.setattr(executor_module.platform, "machine", lambda: "aarch64")
    libc = Mock()

    def syscall(*_arguments):
        ctypes.set_errno(error_number)
        return -1

    libc.syscall.side_effect = syscall
    monkeypatch.setattr(executor_module.ctypes, "CDLL", Mock(return_value=libc))

    pidfd_open, _ = executor_module._pidfd_signaling_functions()
    with pytest.raises(error_type) as raised:
        pidfd_open(321, 0)
    assert raised.value.errno == error_number


@pytest.mark.parametrize(
    ("runtime_platform", "machine", "pointer_size"),
    [
        ("linux", "riscv64", 8),
        ("linux", "x86_64", 4),
        ("win32", "x86_64", 8),
    ],
    ids=["unknown-architecture", "x32-abi", "non-linux"],
)
def test_pidfd_syscall_compatibility_rejects_unsupported_abi(
    monkeypatch, runtime_platform: str, machine: str, pointer_size: int
):
    monkeypatch.setattr(executor_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(
        executor_module.signal, "pidfd_send_signal", None, raising=False
    )
    monkeypatch.setattr(executor_module.sys, "platform", runtime_platform)
    monkeypatch.setattr(executor_module.platform, "machine", lambda: machine)
    original_sizeof = ctypes.sizeof
    monkeypatch.setattr(
        executor_module.ctypes,
        "sizeof",
        lambda value: (
            pointer_size if value is ctypes.c_void_p else original_sizeof(value)
        ),
    )
    libc = Mock()
    monkeypatch.setattr(executor_module.ctypes, "CDLL", libc)

    assert executor_module._pidfd_signaling_functions() is None
    libc.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux pidfd syscalls")
def test_pidfd_syscall_compatibility_signals_owned_child_without_python_wrappers():
    if (
        executor_module.platform.machine().lower() not in ("x86_64", "aarch64")
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        pytest.skip("pidfd syscall compatibility supports native 64-bit x86_64/aarch64")

    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, sys, time; "
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
            "print('ready', flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "child did not start in time"
            assert child.stdout.readline().strip() == "ready"

        with (
            patch.object(executor_module.os, "pidfd_open", None, create=True),
            patch.object(
                executor_module.signal, "pidfd_send_signal", None, create=True
            ),
        ):
            pidfd_functions = executor_module._pidfd_signaling_functions()
            assert pidfd_functions is not None
            pidfd_open, pidfd_send_signal = pidfd_functions
            pidfd = pidfd_open(child.pid, 0)
            try:
                pidfd_send_signal(pidfd, signal.SIGTERM)
            finally:
                os.close(pidfd)

        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize(
    ("client_pid", "processes", "shutdown_succeeds"),
    [
        (None, [], True),
        (321, [], True),
        (321, [["321", "/usr/bin/python unrelated"]], False),
    ],
    ids=["child-not-found", "child-exited-before-signal", "pid-reused-by-other-process"],
)
def test_dottrace_shutdown_waits_or_falls_back_when_client_pid_is_unavailable(
    client_pid: int | None,
    processes: list[list[str]],
    shutdown_succeeds: bool,
):
    executor = _dottrace_executor()
    container = _FakeContainer(processes=processes, exit_on_reload=2)

    with (
        patch.object(executor, "_client_host_pid", return_value=client_pid),
        patch.object(executor_module.os, "pidfd_open", return_value=91, create=True),
        patch.object(
            executor_module.signal, "pidfd_send_signal", create=True
        ) as send_signal,
        patch.object(executor_module.os, "close") as close_pidfd,
    ):
        assert (
            executor._request_dottrace_client_shutdown(container, timeout=1)
            is shutdown_succeeds
        )

    send_signal.assert_not_called()
    if client_pid is not None:
        close_pidfd.assert_called_once_with(91)
    else:
        close_pidfd.assert_not_called()


@pytest.mark.parametrize(
    ("client_pid", "pidfd_open"),
    [(321, None), (321, PermissionError("not allowed")), (1, 91)],
    ids=["unsupported-architecture", "pidfd-permission-denied", "unsafe-pid"],
)
def test_dottrace_shutdown_falls_back_when_child_cannot_be_signaled(
    client_pid: int,
    pidfd_open: int | Exception | None,
):
    executor = _dottrace_executor()
    container = _FakeContainer(
        processes=[[str(client_pid), "/usr/bin/dotnet Nethermind.Runner.dll"]],
        exit_on_reload=100,
    )
    pidfd_effect = (
        {"side_effect": pidfd_open}
        if isinstance(pidfd_open, Exception)
        else {"return_value": pidfd_open}
    )
    pidfd_open_patch = (
        patch.object(executor_module.os, "pidfd_open", None, create=True)
        if pidfd_open is None
        else patch.object(
            executor_module.os, "pidfd_open", create=True, **pidfd_effect
        )
    )

    with (
        patch.object(executor, "_client_host_pid", return_value=client_pid),
        pidfd_open_patch as open_pidfd,
        patch.object(
            executor_module.signal,
            "pidfd_send_signal",
            create=True,
        ) as send_signal,
        patch.object(executor_module.sys, "platform", "linux"),
        patch.object(
            executor_module.platform,
            "machine",
            return_value="riscv64" if pidfd_open is None else "x86_64",
        ),
    ):
        assert not executor._request_dottrace_client_shutdown(container, timeout=1)

    if pidfd_open is None:
        assert open_pidfd is None
    elif client_pid <= 1:
        open_pidfd.assert_not_called()
    elif isinstance(pidfd_open, Exception):
        open_pidfd.assert_called_once_with(321, 0)
    send_signal.assert_not_called()


@pytest.mark.parametrize("container_gone", [False, True], ids=["exited", "missing"])
def test_dottrace_shutdown_skips_signal_for_exited_or_missing_container(
    container_gone: bool,
):
    executor = _dottrace_executor()
    container = _FakeContainer(processes=[], exit_on_reload=1)
    with (
        patch.object(executor, "_client_host_pid") as client_pid,
        patch.object(executor_module.os, "pidfd_open", create=True) as open_pidfd,
    ):
        if container_gone:
            with patch.object(
                container,
                "reload",
                side_effect=executor_module.docker.errors.NotFound("gone"),
            ):
                assert executor._request_dottrace_client_shutdown(container, timeout=1)
        else:
            assert executor._request_dottrace_client_shutdown(container, timeout=1)
        client_pid.assert_not_called()
        open_pidfd.assert_not_called()


def test_cleanup_waits_for_dottrace_before_tearing_down_client(tmp_path: Path, monkeypatch):
    config = Mock(
        executor_name="scenario",
        outputs_dir=tmp_path,
        docker_client=Mock(),
    )
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"
    config.get_containers_network_name.return_value = "scenario-network"
    config.docker_client.networks.get.return_value = Mock()

    executor = Executor(config=config, logger=Mock())
    executor._dottrace_active = True
    container = _FakeContainer(
        processes=[["321", "/usr/bin/dotnet Nethermind.Runner.dll"]],
        exit_on_reload=2,
    )
    config.docker_client.containers.get.return_value = container
    monkeypatch.setenv("EXPB_STOP_TIMEOUT", "1")

    lifecycle: list[str] = []
    wait_for_exit = executor._wait_for_container_exit

    def wait_for_wrapper(container, timeout):
        lifecycle.append("wrapper-wait")
        return wait_for_exit(container, timeout)

    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(
            executor, "_finalize_perf", side_effect=lambda _container: lifecycle.append("perf")
        ),
        patch.object(
            executor,
            "_teardown_container",
            side_effect=lambda name, **kwargs: lifecycle.append(name) or [],
        ),
        patch.object(
            executor,
            "_stop_dotnet_trace_collector",
            side_effect=lambda: lifecycle.append("eventpipe"),
        ),
        patch.object(
            executor,
            "_wait_for_container_exit",
            side_effect=wait_for_wrapper,
        ),
        patch.object(executor_module.os, "pidfd_open", return_value=91, create=True),
        patch.object(
            executor_module.signal,
            "pidfd_send_signal",
            side_effect=lambda *_args: lifecycle.append("client-sigterm"),
            create=True,
        ),
        patch.object(executor_module.os, "close"),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == [
        "perf",
        "k6",
        "payload-server",
        "alloy",
        "eventpipe",
        "client-sigterm",
        "wrapper-wait",
        "nethermind",
    ]


def test_dottrace_shutdown_timeout_uses_existing_container_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = Mock(
        executor_name="scenario",
        outputs_dir=tmp_path,
        docker_client=Mock(),
    )
    config.get_k6_container_name.return_value = "k6"
    config.get_payload_server_container_name.return_value = "payload-server"
    config.get_alloy_container_name.return_value = "alloy"
    config.get_execution_client_container_name.return_value = "nethermind"
    config.get_execution_client_name.return_value = "nethermind"
    config.get_containers_network_name.return_value = "scenario-network"

    executor = Executor(config=config, logger=Mock())
    executor._dottrace_active = True
    container = _FakeContainer(processes=[], exit_on_reload=100)
    config.docker_client.containers.get.return_value = container
    config.docker_client.networks.get.return_value = Mock()
    monkeypatch.setenv("EXPB_STOP_TIMEOUT", "0")

    lifecycle: list[str] = []
    with (
        patch.object(executor, "stop_extra_commands"),
        patch.object(executor, "_finalize_perf"),
        patch.object(executor, "_client_host_pid", return_value=None),
        patch.object(
            executor,
            "_teardown_container",
            side_effect=lambda name, **kwargs: lifecycle.append(name) or [],
        ) as teardown,
        patch.object(executor, "_stop_dotnet_trace_collector"),
        patch.object(executor, "remove_directories"),
    ):
        executor.cleanup_scenario()

    assert lifecycle == ["k6", "payload-server", "alloy", "nethermind"]
    assert teardown.call_args_list[-1].kwargs["stop_timeout"] == 0
    executor.log.warning.assert_called_once()
