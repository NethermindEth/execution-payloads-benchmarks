import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # Hatchling passes "editable" instead of the real version for editable installs.
        # Fall back to reading the version directly from the metadata.
        if version == "editable":
            version = self.metadata.version

        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            commit = "unknown"

        version_file = Path(self.root) / "src" / "expb" / "_version.py"
        version_file.write_text(f'__version__ = "{version}"\n__commit__ = "{commit}"\n')
