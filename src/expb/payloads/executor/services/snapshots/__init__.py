from expb.configs.scenarios import Scenario, Scenarios
from expb.configs.snapshots import SnapshotBackend
from expb.payloads.executor.services.snapshots.base import SnapshotService
from expb.payloads.executor.services.snapshots.copy import CopySnapshotService
from expb.payloads.executor.services.snapshots.overlay import OverlaySnapshotService
from expb.payloads.executor.services.snapshots.zfs import ZFSSnapshotService


def setup_snapshot_service(
    scenarios: Scenarios,
    scenario: Scenario,
) -> SnapshotService:
    if scenario.snapshot_backend == SnapshotBackend.OVERLAY:
        overlay_work_dir = scenarios.paths.work / "work"
        overlay_upper_dir = scenarios.paths.work / "upper"
        overlay_merged_dir = scenarios.paths.work / "merged"
        return OverlaySnapshotService(
            overlay_work_dir=overlay_work_dir,
            overlay_upper_dir=overlay_upper_dir,
            overlay_merged_dir=overlay_merged_dir,
        )
    elif scenario.snapshot_backend == SnapshotBackend.ZFS:
        return ZFSSnapshotService()
    elif scenario.snapshot_backend == SnapshotBackend.COPY:
        if scenario.snapshot_path is not None:
            copy_work_dir = scenario.snapshot_path
        else:
            copy_work_dir = scenarios.paths.work / "snapshot"
        return CopySnapshotService(work_dir=copy_work_dir)
    else:
        raise ValueError(f"Invalid snapshot backend: {scenario.snapshot_backend}")


__all__ = [
    "SnapshotService",
    "CopySnapshotService",
    "OverlaySnapshotService",
    "ZFSSnapshotService",
]
