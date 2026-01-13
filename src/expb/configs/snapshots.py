from enum import Enum


class SnapshotBackend(Enum):
    OVERLAY = "overlay"
    ZFS = "zfs"
    COPY = "copy"
