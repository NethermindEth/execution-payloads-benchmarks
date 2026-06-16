from __future__ import annotations

import glob
import os
import subprocess
import time
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


def _parse_cpuset(cpuset: str) -> set[int]:
    """Parse a cpuset string like '2-7,10-15' into a set of CPU IDs."""
    cpus: set[int] = set()
    for part in cpuset.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.update(range(int(start), int(end) + 1))
        else:
            cpus.add(int(part))
    return cpus


def _get_cpu_topology() -> dict[int, list[int]]:
    """Read CPU topology. Returns {core_id: [cpu_id, ...]} mapping."""
    cores: dict[int, list[int]] = {}
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id")):
        cpu_id = int(path.split("/cpu")[2].split("/")[0])
        core_id_str = _read_sys(path)
        if core_id_str is not None:
            core_id = int(core_id_str)
            cores.setdefault(core_id, []).append(cpu_id)
    return cores


def detect_smt_siblings(cpuset: str, infra_cpuset: str | None = None) -> list[int]:
    """Find HT/SMT siblings that should be offlined for the given cpusets.

    Returns CPU IDs that share a physical core with any CPU in cpuset or
    infra_cpuset but are not themselves in either set.
    """
    used_cpus = _parse_cpuset(cpuset)
    if infra_cpuset:
        used_cpus |= _parse_cpuset(infra_cpuset)

    topology = _get_cpu_topology()
    to_offline: set[int] = set()
    for _core_id, cpu_ids in topology.items():
        if len(cpu_ids) < 2:
            continue
        pinned = [c for c in cpu_ids if c in used_cpus]
        if pinned:
            siblings = [c for c in cpu_ids if c not in used_cpus]
            to_offline.update(siblings)

    return sorted(to_offline)


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


class SmtStabilizer:
    """Offlines HT/SMT sibling CPUs during benchmarks for cache isolation.

    Auto-detects siblings from CPU topology when cpuset is provided.
    Falls back to explicit cpu list via offline_cpus override.
    Only CPUs that were online at apply time are offlined, and only
    those are brought back online on restore.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        cpuset: str | None = None,
        infra_cpuset: str | None = None,
        offline_cpus: list[int] | None = None,
    ):
        self.log = logger
        if offline_cpus:
            self._cpus_to_offline = offline_cpus
        elif cpuset:
            self._cpus_to_offline = detect_smt_siblings(cpuset, infra_cpuset)
        else:
            self._cpus_to_offline = []
        self._offlined_cpus: list[int] = []

    @staticmethod
    def _cpu_online_path(cpu_id: int) -> str:
        return f"/sys/devices/system/cpu/cpu{cpu_id}/online"

    def apply(self) -> None:
        if not self._cpus_to_offline:
            if self.log:
                self.log.info("No SMT siblings to offline (no cpuset configured or no HT detected)")
            return

        for cpu_id in self._cpus_to_offline:
            path = self._cpu_online_path(cpu_id)
            current = _read_sys(path)
            if current is None:
                continue
            if current == "0":
                continue
            if _write_sys(path, "0"):
                self._offlined_cpus.append(cpu_id)
            elif self.log:
                self.log.warning(
                    "Failed to offline CPU (permission denied?)",
                    cpu=cpu_id,
                )

        if self._offlined_cpus and self.log:
            self.log.info(
                "SMT siblings offlined for cache isolation",
                cpus=self._offlined_cpus,
            )

    def restore(self) -> None:
        if not self._offlined_cpus:
            return

        for cpu_id in self._offlined_cpus:
            path = self._cpu_online_path(cpu_id)
            if not _write_sys(path, "1"):
                if self.log:
                    self.log.warning("Failed to online CPU", cpu=cpu_id)

        if self.log:
            self.log.info(
                "SMT siblings restored",
                cpus=self._offlined_cpus,
            )
        self._offlined_cpus = []

    def __enter__(self) -> SmtStabilizer:
        self.apply()
        return self

    def __exit__(self, *exc) -> None:
        self.restore()


def _read_proc_stat_busy() -> tuple[int, int] | None:
    """Read aggregate CPU jiffies from /proc/stat.

    Returns (busy, total) where busy excludes idle and iowait. None on failure.
    """
    line = _read_sys("/proc/stat")
    if not line:
        return None
    first = line.splitlines()[0]
    parts = first.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        vals = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    # user nice system idle iowait irq softirq steal guest guest_nice
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    total = sum(vals)
    return total - idle, total


def measure_cpu_busy_pct(interval: float = 1.0) -> float | None:
    """Sample system-wide CPU busy percentage over ``interval`` seconds."""
    a = _read_proc_stat_busy()
    if a is None:
        return None
    time.sleep(interval)
    b = _read_proc_stat_busy()
    if b is None:
        return None
    dbusy = b[0] - a[0]
    dtotal = b[1] - a[1]
    if dtotal <= 0:
        return None
    return 100.0 * dbusy / dtotal


def wait_for_quiescence(
    logger: Logger | None = None,
    max_wait: float = 90.0,
    busy_threshold_pct: float = 8.0,
    stable_samples: int = 2,
    sample_interval: float = 1.0,
    min_settle: float = 5.0,
) -> None:
    """Block until the machine is quiet, so each measured run starts from an
    identical low-activity baseline.

    Polls system-wide CPU busy% in ``sample_interval`` windows and returns once
    busy% stays below ``busy_threshold_pct`` for ``stable_samples`` consecutive
    windows, or once ``max_wait`` seconds elapse. A fixed ``min_settle`` sleep is
    always applied first to let teardown of the previous run drain. Unlike the
    1-minute load average, this reacts within seconds, so it does not stall when
    the load EWMA lags behind actual activity.
    """
    if min_settle > 0:
        time.sleep(min_settle)
    deadline = time.monotonic() + max_wait
    consecutive = 0
    last_busy: float | None = None
    while time.monotonic() < deadline:
        busy = measure_cpu_busy_pct(sample_interval)
        if busy is None:
            break
        last_busy = busy
        if busy <= busy_threshold_pct:
            consecutive += 1
            if consecutive >= stable_samples:
                break
        else:
            consecutive = 0
    if logger:
        logger.info(
            "Quiescence wait complete",
            last_busy_pct=round(last_busy, 2) if last_busy is not None else None,
            threshold_pct=busy_threshold_pct,
            min_settle=min_settle,
        )


# vm sysctls controlling dirty-page writeback aggressiveness.
_DIRTY_SYSCTLS = (
    "vm.dirty_background_bytes",
    "vm.dirty_bytes",
    "vm.dirty_background_ratio",
    "vm.dirty_ratio",
    "vm.dirty_expire_centisecs",
)


def _sysctl_path(key: str) -> str:
    return "/proc/sys/" + key.replace(".", "/")


class IoStabilizer:
    """Defers dirty-page writeback for the duration of a benchmark run.

    The execution client writes hundreds of MB of state/SST data per run. With
    default ``vm.dirty_*`` settings the kernel writes those pages back mid-run, and
    the write-back competes for the single memory controller and shared L3 with the
    client's own (read-heavy) block processing. The exact timing of that write-back
    varies run-to-run, injecting a global per-run offset into processing times. By
    raising the dirty thresholds above the per-run write volume, write-back is
    deferred to the run boundary (the next ``sync`` in ``clean_system_cache``), so it
    no longer perturbs the measured window. Reversible: restores original sysctls.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        dirty_bytes: int = 32 * 1024 * 1024 * 1024,
        dirty_background_bytes: int = 16 * 1024 * 1024 * 1024,
        dirty_expire_centisecs: int = 60000,
    ):
        self.log = logger
        self._dirty_bytes = dirty_bytes
        self._dirty_background_bytes = dirty_background_bytes
        self._dirty_expire_centisecs = dirty_expire_centisecs
        self._original: dict[str, str] = {}

    def apply(self) -> None:
        for key in _DIRTY_SYSCTLS:
            original = _read_sys(_sysctl_path(key))
            if original is not None:
                self._original[key] = original
        # Setting *_bytes to non-zero disables the corresponding *_ratio (kernel
        # treats whichever was written last as authoritative), so write bytes.
        desired = {
            "vm.dirty_bytes": str(self._dirty_bytes),
            "vm.dirty_background_bytes": str(self._dirty_background_bytes),
            "vm.dirty_expire_centisecs": str(self._dirty_expire_centisecs),
        }
        applied = 0
        for key, value in desired.items():
            if _write_sys(_sysctl_path(key), value):
                applied += 1
        if self.log:
            if applied:
                self.log.info(
                    "Dirty-page write-back deferred",
                    dirty_bytes=self._dirty_bytes,
                    dirty_background_bytes=self._dirty_background_bytes,
                )
            else:
                self.log.warning(
                    "Failed to defer dirty-page write-back (permission denied?)",
                )

    def restore(self) -> None:
        for key, original in self._original.items():
            _write_sys(_sysctl_path(key), original)
        if self._original and self.log:
            self.log.info("Dirty-page write-back settings restored")

    def __enter__(self) -> IoStabilizer:
        self.apply()
        return self

    def __exit__(self, *exc) -> None:
        self.restore()
