import shutil
from pathlib import Path

from expb.payloads.executor.services.snapshots.base import SnapshotService


class CopySnapshotService(SnapshotService):
    def __init__(self, work_dir: Path):
        super().__init__()
        self.work_dir = work_dir

    def _get_snapshot_path(self, name: str) -> Path:
        """Get the path where the snapshot will be stored."""
        return self.work_dir / name

    def create_snapshot(self, name: str, source: str) -> Path:
        # Convert source to absolute path
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise ValueError(f"Snapshot source does not exist: {source_path}")

        # Get destination path
        dest_path = self._get_snapshot_path(name)

        # Remove destination if it already exists
        if dest_path.exists():
            shutil.rmtree(dest_path)

        # Ensure work directory exists
        self.work_dir.mkdir(mode=0o777, parents=True, exist_ok=True)

        # Copy the entire directory tree
        shutil.copytree(source_path, dest_path)

        return dest_path

    def get_snapshot(self, name: str, source: str) -> Path:
        snapshot_path = self._get_snapshot_path(name)
        if not snapshot_path.exists():
            raise ValueError(
                f"Snapshot not found. Call create_snapshot first. "
                f"Name: {name}, Source: {source}"
            )
        return snapshot_path

    def delete_snapshot(self, name: str, source: str) -> None:
        snapshot_path = self._get_snapshot_path(name)
        if snapshot_path.exists():
            shutil.rmtree(snapshot_path)
