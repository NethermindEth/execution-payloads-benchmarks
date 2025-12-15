import subprocess
from pathlib import Path

from expb.payloads.executor.services.snapshots.base import SnapshotService


class ZFSSnapshotService(SnapshotService):
    def __init__(self):
        super().__init__()

    def _extract_dataset_name(self, source: str) -> str:
        return source.split("@")[0]

    def _get_dataset_clone_name(self, dataset: str, name: str) -> str:
        return f"{dataset}_{name}"

    def create_snapshot(self, name: str, source: str) -> Path:
        # validate zfs source dataset snapshot exists
        validate_source_command = " ".join(
            [
                "zfs",
                "list",
                "-t",
                "snapshot",
                source,
            ]
        )
        try:
            subprocess.run(validate_source_command, check=True, shell=True)
        except subprocess.CalledProcessError:
            raise ValueError(f"Snapshot {source} is not valid")

        # create zfs dataset clone
        dataset_name = self._extract_dataset_name(source)
        dataset_clone_name = self._get_dataset_clone_name(dataset_name, name)
        create_copy_command = " ".join(
            [
                "zfs",
                "clone",
                source,
                dataset_clone_name,
            ]
        )
        try:
            subprocess.run(create_copy_command, check=True, shell=True)
        except subprocess.CalledProcessError:
            raise ValueError(
                f"Failed to create snapshot {dataset_clone_name} from {source}"
            )

        return self.get_snapshot(name, source)

    def get_snapshot(self, name: str, source: str) -> Path:
        dataset_name = self._extract_dataset_name(source)
        dataset_clone_name = self._get_dataset_clone_name(dataset_name, name)
        # get mountpoint for the created dataset clone
        get_mountpoint_command = " ".join(
            [
                "zfs",
                "get",
                "-H",
                "-o",
                "value",
                "mountpoint",
                dataset_clone_name,
            ]
        )
        try:
            mountpoint = (
                subprocess.check_output(get_mountpoint_command, shell=True)
                .decode()
                .strip()
            )
        except subprocess.CalledProcessError:
            raise ValueError(f"Failed to get mountpoint for {dataset_clone_name}")

        mountpoint_path = Path(mountpoint)
        if not mountpoint_path.exists():
            raise ValueError(f"Mountpoint {mountpoint_path} does not exist")

        return mountpoint_path

    def delete_snapshot(self, name: str, source: str) -> None:
        dataset_name = self._extract_dataset_name(source)
        dataset_clone_name = self._get_dataset_clone_name(dataset_name, name)
        delete_command = " ".join(
            [
                "zfs",
                "destroy",
                dataset_clone_name,
            ]
        )
        try:
            subprocess.run(delete_command, check=True, shell=True)
        except subprocess.CalledProcessError:
            raise ValueError(f"Failed to delete snapshot {dataset_clone_name}")
