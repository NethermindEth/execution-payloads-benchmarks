from __future__ import annotations

import glob
import os
from pathlib import Path

import docker
from docker.client import DockerClient
from docker.errors import APIError, DockerException

from expb.logging import Logger

_HELPER_IMAGE = "alpine:latest"


def _read_sys(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except (OSError, PermissionError):
        return None


def _write_sys(path: str, value: str) -> bool:
    try:
        Path(path).write_text(value)
        return True
    except (OSError, PermissionError):
        return False


def _detect_turbo_path() -> tuple[str, str, str] | None:
    """Detect turbo boost sysfs path and the values to disable/enable it.

    Returns (path, disable_value, enable_value) or None.
    """
    # Intel pstate: 1 = no turbo, 0 = turbo enabled
    intel = "/sys/devices/system/cpu/intel_pstate/no_turbo"
    if os.path.exists(intel):
        return intel, "1", "0"

    # AMD / acpi-cpufreq: 0 = boost disabled, 1 = boost enabled
    amd = "/sys/devices/system/cpu/cpufreq/boost"
    if os.path.exists(amd):
        return amd, "0", "1"

    return None


def _get_governor_paths() -> list[str]:
    return sorted(glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"))


def _get_max_freq_paths() -> list[str]:
    return sorted(glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq"))


def _get_base_frequency() -> str | None:
    """Get the CPU base (non-turbo) frequency in kHz.

    Tries base_frequency sysfs first, then falls back to cpuinfo_min_freq
    (which on 'performance' governor systems often equals base clock).
    """
    base = _read_sys("/sys/devices/system/cpu/cpu0/cpufreq/base_frequency")
    if base is not None:
        return base
    return _read_sys("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq")


def _docker_run_cmd(client: DockerClient, cmd: str) -> str:
    """Run a shell command in a privileged container with host /sys mounted."""
    output: bytes = client.containers.run(
        image=_HELPER_IMAGE,
        command=["sh", "-c", cmd],
        privileged=True,
        volumes={"/sys": {"bind": "/sys", "mode": "rw"}},
        remove=True,
    )
    return output.decode().strip()


def _docker_detect_turbo_path(client: DockerClient) -> tuple[str, str, str] | None:
    output = _docker_run_cmd(
        client,
        "if [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ]; then echo intel;"
        " elif [ -f /sys/devices/system/cpu/cpufreq/boost ]; then echo amd;"
        " else echo none; fi",
    )
    if output == "intel":
        return "/sys/devices/system/cpu/intel_pstate/no_turbo", "1", "0"
    if output == "amd":
        return "/sys/devices/system/cpu/cpufreq/boost", "0", "1"
    return None


def _docker_read_sys(client: DockerClient, path: str) -> str | None:
    try:
        return _docker_run_cmd(client, f"cat {path}")
    except (APIError, DockerException):
        return None


def _docker_write_sys(client: DockerClient, path: str, value: str) -> bool:
    try:
        _docker_run_cmd(client, f"echo '{value}' > {path}")
        return True
    except (APIError, DockerException):
        return False


def _docker_get_governor_paths(client: DockerClient) -> list[str]:
    try:
        output = _docker_run_cmd(
            client,
            "ls -1 /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null",
        )
        if output:
            return sorted(output.splitlines())
    except (APIError, DockerException):
        pass
    return []


def _docker_get_max_freq_paths(client: DockerClient) -> list[str]:
    try:
        output = _docker_run_cmd(
            client,
            "ls -1 /sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq 2>/dev/null",
        )
        if output:
            return sorted(output.splitlines())
    except (APIError, DockerException):
        pass
    return []


def _docker_get_base_frequency(client: DockerClient) -> str | None:
    base = _docker_read_sys(
        client, "/sys/devices/system/cpu/cpu0/cpufreq/base_frequency"
    )
    if base is not None:
        return base
    return _docker_read_sys(
        client, "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq"
    )


class CpuStabilizer:
    """Disables turbo boost and sets the CPU governor to 'performance'.

    Use as a context manager to automatically restore original settings.
    When sysfs paths are not available locally (e.g. running inside a Docker
    container), falls back to running a privileged helper container that
    accesses the host's /sys filesystem.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        docker_client: DockerClient | None = None,
    ):
        self.log = logger
        self._docker_client = docker_client
        self._turbo_via_docker = False
        self._governors_via_docker = False
        self._freq_cap_via_docker = False
        self._original_turbo: str | None = None
        self._turbo_path: str | None = None
        self._turbo_enable_value: str | None = None
        self._original_governors: dict[str, str] = {}
        self._original_max_freqs: dict[str, str] = {}

    def _get_docker_client(self) -> DockerClient | None:
        if self._docker_client is not None:
            return self._docker_client
        try:
            self._docker_client = docker.from_env()
            return self._docker_client
        except DockerException:
            return None

    def apply(self) -> None:
        self._apply_turbo()
        self._apply_governors()

    def _apply_turbo(self) -> None:
        if self._apply_turbo_sysfs():
            return
        if self._apply_turbo_freq_cap():
            return
        if self.log:
            self.log.warning("No method available to disable turbo boost, skipping")

    def _apply_turbo_sysfs(self) -> bool:
        turbo_info = _detect_turbo_path()
        if turbo_info:
            path, disable_val, enable_val = turbo_info
            self._turbo_path = path
            self._turbo_enable_value = enable_val
            self._original_turbo = _read_sys(path)
            if _write_sys(path, disable_val):
                if self.log:
                    self.log.info(
                        "Turbo boost disabled",
                        path=path,
                        original=self._original_turbo,
                    )
            else:
                if self.log:
                    self.log.warning(
                        "Failed to disable turbo boost (permission denied?)",
                        path=path,
                    )
            return True

        client = self._get_docker_client()
        if client is not None:
            turbo_info = _docker_detect_turbo_path(client)
            if turbo_info:
                path, disable_val, enable_val = turbo_info
                self._turbo_path = path
                self._turbo_enable_value = enable_val
                self._turbo_via_docker = True
                self._original_turbo = _docker_read_sys(client, path)
                if _docker_write_sys(client, path, disable_val):
                    if self.log:
                        self.log.info(
                            "Turbo boost disabled via Docker",
                            path=path,
                            original=self._original_turbo,
                        )
                else:
                    if self.log:
                        self.log.warning(
                            "Failed to disable turbo boost via Docker",
                            path=path,
                        )
                return True

        return False

    def _apply_turbo_freq_cap(self) -> bool:
        """Disable turbo by capping scaling_max_freq to base frequency."""
        # Try local first
        base_freq = _get_base_frequency()
        freq_paths = _get_max_freq_paths()
        if base_freq and freq_paths:
            return self._cap_frequencies_local(base_freq, freq_paths)

        # Try via Docker
        client = self._get_docker_client()
        if client is not None:
            base_freq = _docker_get_base_frequency(client)
            freq_paths = _docker_get_max_freq_paths(client)
            if base_freq and freq_paths:
                return self._cap_frequencies_docker(client, base_freq, freq_paths)

        return False

    def _cap_frequencies_local(
        self, base_freq: str, freq_paths: list[str]
    ) -> bool:
        for fpath in freq_paths:
            original = _read_sys(fpath)
            if original is not None:
                self._original_max_freqs[fpath] = original
            _write_sys(fpath, base_freq)
        if self.log:
            self.log.info(
                "Turbo boost disabled via frequency cap",
                base_freq_khz=base_freq,
                cores=len(freq_paths),
            )
        return True

    def _cap_frequencies_docker(
        self, client: DockerClient, base_freq: str, freq_paths: list[str]
    ) -> bool:
        self._freq_cap_via_docker = True
        for fpath in freq_paths:
            original = _docker_read_sys(client, fpath)
            if original is not None:
                self._original_max_freqs[fpath] = original
            _docker_write_sys(client, fpath, base_freq)
        if self.log:
            self.log.info(
                "Turbo boost disabled via frequency cap (Docker)",
                base_freq_khz=base_freq,
                cores=len(freq_paths),
            )
        return True

    def _apply_governors(self) -> None:
        governor_paths = _get_governor_paths()
        if governor_paths:
            for gpath in governor_paths:
                original = _read_sys(gpath)
                if original is not None:
                    self._original_governors[gpath] = original
                _write_sys(gpath, "performance")
            if self.log:
                sample = _read_sys(governor_paths[0])
                self.log.info(
                    "CPU governor set",
                    governor=sample,
                    cores=len(governor_paths),
                )
            return

        client = self._get_docker_client()
        if client is not None:
            governor_paths = _docker_get_governor_paths(client)
            if governor_paths:
                self._governors_via_docker = True
                for gpath in governor_paths:
                    original = _docker_read_sys(client, gpath)
                    if original is not None:
                        self._original_governors[gpath] = original
                    _docker_write_sys(client, gpath, "performance")
                if self.log:
                    sample = _docker_read_sys(client, governor_paths[0])
                    self.log.info(
                        "CPU governor set via Docker",
                        governor=sample,
                        cores=len(governor_paths),
                    )
                return

        if self.log:
            self.log.warning("No CPU governor paths found, skipping")

    def restore(self) -> None:
        self._restore_turbo()
        self._restore_freq_cap()
        self._restore_governors()

    def _restore_turbo(self) -> None:
        if not self._turbo_path or self._original_turbo is None:
            return

        if self._turbo_via_docker:
            client = self._get_docker_client()
            if client is not None:
                if _docker_write_sys(client, self._turbo_path, self._original_turbo):
                    if self.log:
                        self.log.info(
                            "Turbo boost restored via Docker",
                            value=self._original_turbo,
                        )
            elif self.log:
                self.log.warning("Docker not available, turbo boost not restored")
        else:
            if _write_sys(self._turbo_path, self._original_turbo):
                if self.log:
                    self.log.info(
                        "Turbo boost restored",
                        value=self._original_turbo,
                    )

    def _restore_freq_cap(self) -> None:
        if not self._original_max_freqs:
            return

        if self._freq_cap_via_docker:
            client = self._get_docker_client()
            if client is not None:
                for fpath, original in self._original_max_freqs.items():
                    _docker_write_sys(client, fpath, original)
                if self.log:
                    self.log.info(
                        "Max frequencies restored via Docker",
                        cores=len(self._original_max_freqs),
                    )
            elif self.log:
                self.log.warning("Docker not available, max frequencies not restored")
        else:
            for fpath, original in self._original_max_freqs.items():
                _write_sys(fpath, original)
            if self.log:
                self.log.info(
                    "Max frequencies restored",
                    cores=len(self._original_max_freqs),
                )

    def _restore_governors(self) -> None:
        if not self._original_governors:
            return

        if self._governors_via_docker:
            client = self._get_docker_client()
            if client is not None:
                for gpath, original in self._original_governors.items():
                    _docker_write_sys(client, gpath, original)
                if self.log:
                    self.log.info(
                        "CPU governors restored via Docker",
                        cores=len(self._original_governors),
                    )
            elif self.log:
                self.log.warning("Docker not available, governors not restored")
        else:
            for gpath, original in self._original_governors.items():
                _write_sys(gpath, original)
            if self.log:
                self.log.info(
                    "CPU governors restored",
                    cores=len(self._original_governors),
                )

    def __enter__(self) -> CpuStabilizer:
        self.apply()
        return self

    def __exit__(self, *exc) -> None:
        self.restore()
