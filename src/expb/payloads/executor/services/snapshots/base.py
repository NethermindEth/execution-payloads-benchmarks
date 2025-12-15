from pathlib import Path


class SnapshotService:
    def __init__(self):
        pass

    # creates a copy of the source with the given name and returns a path to it
    def create_snapshot(self, name: str, source: str) -> Path:
        raise NotImplementedError("Not implemented")

    # returns a path to the snapshot with the given name and source
    def get_snapshot(self, name: str, source: str) -> Path:
        raise NotImplementedError("Not implemented")

    # deletes the snapshot with the given name and source
    def delete_snapshot(self, name: str, source: str) -> None:
        raise NotImplementedError("Not implemented")
