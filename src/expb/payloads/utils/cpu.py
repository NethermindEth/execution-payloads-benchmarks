from __future__ import annotations

import glob
import os
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
        elif self._max_frequency_khz is not None:
            self._apply_freq_cap()
        else:
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
        for fpath in freq_paths:
            original = _read_sys(fpath)
            if original is not None:
                self._original_max_freqs[fpath] = original
            if not _write_sys(fpath, cap):
                if self.log:
                    self.log.warning(
                        "Failed to set scaling_max_freq (permission denied?)",
                        path=fpath,
                    )
                return
        if self.log:
            self.log.info(
                "Turbo boost disabled via frequency cap",
                max_freq_khz=self._max_frequency_khz,
                cores=len(freq_paths),
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
