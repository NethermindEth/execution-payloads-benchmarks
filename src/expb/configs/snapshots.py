from enum import Enum


class SnapshotBackend(Enum):
    OVERLAY = "overlay"
    ZFS = "zfs"
    COPY = "copy"

    @staticmethod
    def from_string(backend: str) -> "SnapshotBackend":
        backend = backend.lower()
        try:
            return SnapshotBackend(backend)
        except ValueError:
            raise ValueError(f"Invalid snapshot backend: {backend}")
