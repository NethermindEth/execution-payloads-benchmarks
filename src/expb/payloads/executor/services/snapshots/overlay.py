import os
import shutil
import subprocess
from pathlib import Path

from expb.payloads.executor.services.snapshots.base import SnapshotService


class OverlaySnapshotService(SnapshotService):
    def __init__(
        self,
        overlay_work_dir: Path,
        overlay_upper_dir: Path,
        overlay_merged_dir: Path,
    ):
        super().__init__()
        self.overlay_work_dir = overlay_work_dir
        self.overlay_upper_dir = overlay_upper_dir
        self.overlay_merged_dir = overlay_merged_dir

    def _cleanup_stale_mount(self) -> None:
        """Remove any leftover overlay state before mounting.

        A run that is killed (e.g. CI cancellation) before its delete_snapshot
        teardown leaves the overlay still mounted and/or its ``volatile`` workdir
        in a dirty state, which makes the next mount fail with "bad superblock"
        (exit 32) — bricking the runner for subsequent runs. Unmount any stale
        mount (lazy as a fallback for a busy mount) and delete the scratch
        work/upper/merged dirs so create_snapshot is self-healing and idempotent.

        Only the volatile scratch dirs are touched; the read-only lowerdir
        snapshot is never referenced here, so snapshot data integrity is preserved.
        """
        merged = str(self.overlay_merged_dir.resolve())
        if os.path.ismount(merged):
            # List form (no shell) so the path is passed as a single argument.
            for cmd in (["umount", merged], ["umount", "-l", merged]):
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    break
                except subprocess.CalledProcessError:
                    continue
        for path in (
            self.overlay_merged_dir,
            self.overlay_upper_dir,
            self.overlay_work_dir,
        ):
            if path.exists() and not os.path.ismount(str(path.resolve())):
                try:
                    shutil.rmtree(path)
                except OSError:
                    pass

    def create_snapshot(self, name: str, source: str) -> Path:
        # Convert source to absolute path
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise ValueError(f"Snapshot source does not exist: {source_path}")

        # Heal any leftover overlay state from a previously killed run so the
        # mount below always starts from a clean slate (see _cleanup_stale_mount).
        self._cleanup_stale_mount()

        # Ensure the overlay directories exist
        self.overlay_merged_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self.overlay_upper_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )
        self.overlay_work_dir.mkdir(
            mode=0o777,
            parents=True,
            exist_ok=True,
        )

        # Mount overlay
        device_name = name
        mount_command = " ".join(
            [
                "mount",
                "-t",
                "overlay",
                device_name,
                "-o",
                ",".join(
                    [
                        f"lowerdir={source_path}",
                        f"upperdir={self.overlay_upper_dir.resolve()}",
                        f"workdir={self.overlay_work_dir.resolve()}",
                        "redirect_dir=on",
                        "metacopy=on",
                        "volatile",
                    ]
                ),
                str(self.overlay_merged_dir.resolve()),
            ]
        )
        subprocess.run(mount_command, check=True, shell=True)

        return self.overlay_merged_dir

    def get_snapshot(self, name: str, source: str) -> Path:
        # Verify the snapshot exists (mounted overlay)
        if not self.overlay_merged_dir.exists():
            raise ValueError(
                f"Snapshot not found. Call create_snapshot first. "
                f"Name: {name}, Source: {source}"
            )
        return self.overlay_merged_dir

    def delete_snapshot(self, name: str, source: str) -> None:
        umount_command = " ".join(
            [
                "umount",
                str(self.overlay_merged_dir.resolve()),
            ]
        )
        try:
            subprocess.run(umount_command, check=True, shell=True)
        except subprocess.CalledProcessError:
            # If umount fails, log but continue with cleanup
            # The directories might still need to be removed
            pass

        paths_to_remove = [
            self.overlay_upper_dir.resolve(),
            self.overlay_work_dir.resolve(),
            self.overlay_merged_dir.resolve(),
        ]
        for path in paths_to_remove:
            if path.exists():
                try:
                    shutil.rmtree(path)
                except Exception:
                    # Log but don't fail if cleanup fails
                    pass
