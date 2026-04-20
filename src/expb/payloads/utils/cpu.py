from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

from expb.logging import Logger


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


class CpuStabilizer:
    """Disables turbo boost and sets the CPU governor to 'performance'.

    Use as a context manager to automatically restore original settings.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        max_frequency_khz: int | None = None,
    ):
        self.log = logger
        self._max_frequency_khz = max_frequency_khz
        self._original_turbo: str | None = None
        self._turbo_path: str | None = None
        self._turbo_enable_value: str | None = None
        self._original_governors: dict[str, str] = {}
        self._original_max_freqs: dict[str, str] = {}

    def apply(self) -> None:
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

        if self._max_frequency_khz is not None:
            self._apply_freq_cap()
        elif turbo_info is None:
            if self.log:
                self.log.warning(
                    "Turbo boost sysfs path not found and cpu_max_frequency_khz "
                    "not configured, turbo boost not disabled",
                )

        governor_paths = _get_governor_paths()
        for gpath in governor_paths:
            original = _read_sys(gpath)
            if original is not None:
                self._original_governors[gpath] = original
            _write_sys(gpath, "performance")
        if governor_paths:
            if self.log:
                sample = _read_sys(governor_paths[0])
                self.log.info(
                    "CPU governor set",
                    governor=sample,
                    cores=len(governor_paths),
                )
        else:
            if self.log:
                self.log.warning("No CPU governor paths found, skipping")

    def _apply_freq_cap(self) -> None:
        freq_paths = _get_max_freq_paths()
        if not freq_paths:
            if self.log:
                self.log.warning("No scaling_max_freq paths found, skipping frequency cap")
            return
        cap = str(self._max_frequency_khz)
        failed = 0
        for fpath in freq_paths:
            original = _read_sys(fpath)
            if original is not None:
                self._original_max_freqs[fpath] = original
            if not _write_sys(fpath, cap):
                failed += 1
        if failed:
            if self.log:
                self.log.warning(
                    "Failed to set scaling_max_freq on some cores (permission denied?)",
                    failed=failed,
                    total=len(freq_paths),
                )
        if self._original_max_freqs:
            if self.log:
                self.log.info(
                    "CPU frequency capped",
                    max_freq_khz=self._max_frequency_khz,
                    cores=len(freq_paths) - failed,
                )

    def restore(self) -> None:
        if self._turbo_path and self._original_turbo is not None:
            if _write_sys(self._turbo_path, self._original_turbo):
                if self.log:
                    self.log.info(
                        "Turbo boost restored",
                        value=self._original_turbo,
                    )

        for fpath, original in self._original_max_freqs.items():
            _write_sys(fpath, original)
        if self._original_max_freqs:
            if self.log:
                self.log.info(
                    "Max frequencies restored",
                    cores=len(self._original_max_freqs),
                )

        for gpath, original in self._original_governors.items():
            _write_sys(gpath, original)
        if self._original_governors:
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


NOISY_TIMERS = [
    "sysstat-collect.timer",
    "apt-daily.timer",
    "apt-daily-upgrade.timer",
    "fwupd-refresh.timer",
    "fstrim.timer",
]


class TimerStabilizer:
    """Stops systemd timers that cause I/O and CPU spikes during benchmarks.

    Restores (starts) them after the benchmark completes.
    """

    def __init__(self, logger: Logger | None = None):
        self.log = logger
        self._stopped_timers: list[str] = []

    def apply(self) -> None:
        try:
            result = subprocess.run(
                ["systemctl", "list-timers", "--no-pager", "--no-legend"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return
        except Exception:
            return

        active = [t for t in NOISY_TIMERS if t in result.stdout]
        if not active:
            if self.log:
                self.log.info("No noisy systemd timers active")
            return

        for timer in active:
            try:
                subprocess.run(
                    ["systemctl", "stop", timer],
                    capture_output=True,
                    timeout=10,
                )
                self._stopped_timers.append(timer)
            except Exception:
                if self.log:
                    self.log.warning("Failed to stop timer", timer=timer)

        if self._stopped_timers and self.log:
            self.log.info(
                "Systemd timers stopped",
                timers=self._stopped_timers,
            )

    def restore(self) -> None:
        if not self._stopped_timers:
            return

        for timer in self._stopped_timers:
            try:
                subprocess.run(
                    ["systemctl", "start", timer],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                if self.log:
                    self.log.warning("Failed to restart timer", timer=timer)

        if self.log:
            self.log.info(
                "Systemd timers restored",
                timers=self._stopped_timers,
            )
        self._stopped_timers = []

    def __enter__(self) -> TimerStabilizer:
        self.apply()
        return self

    def __exit__(self, *exc) -> None:
        self.restore()
