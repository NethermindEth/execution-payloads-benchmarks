"""
Execution lock mechanism to prevent concurrent benchmark runs.

This module provides a file-based lock mechanism using filelock library to ensure
only one benchmark execution runs at a time, preventing resource conflicts.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from filelock import FileLock, Timeout

from expb.logging import Logger


DEFAULT_LOCK_FILE = Path("/tmp/expb.lock")


class ExecutionLockError(Exception):
    """Raised when unable to acquire execution lock."""

    pass


@contextmanager
def acquire_execution_lock(
    lock_file: Path | None = None,
    enabled: bool = True,
    timeout: float = 0,
    logger: Logger | None = None,
) -> Generator[None, None, None]:
    """
    Context manager to acquire an execution lock.

    Args:
        lock_file: Path to the lock file. Defaults to /tmp/expb.lock
        enabled: Whether locking is enabled. If False, no locking occurs.
        timeout: Maximum time to wait for lock acquisition in seconds.
                 0 means fail immediately if lock is held. Default: 0
        logger: Optional logger for lock status messages

    Raises:
        ExecutionLockError: If lock cannot be acquired within timeout

    Example:
        with acquire_execution_lock(enabled=True):
            # Your benchmark execution code here
            pass
    """
    if not enabled:
        # Locking disabled, just yield without acquiring lock
        if logger:
            logger.debug("Execution lock disabled, proceeding without lock")
        yield
        return

    lock_path = lock_file or DEFAULT_LOCK_FILE

    # Ensure parent directory exists
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock = FileLock(str(lock_path), timeout=timeout)

    if logger:
        logger.debug(
            "Attempting to acquire execution lock",
            lock_file=str(lock_path),
            timeout=timeout,
        )

    try:
        with lock.acquire(timeout=timeout):
            if logger:
                logger.info(
                    "Execution lock acquired",
                    lock_file=str(lock_path),
                )
            yield
    except Timeout:
        error_msg = (
            f"Could not acquire execution lock at {lock_path}. "
            f"Another expb process may be running. "
            f"If you're sure no other process is running, delete the lock file manually."
        )
        if logger:
            logger.error(
                "Failed to acquire execution lock",
                lock_file=str(lock_path),
                timeout=timeout,
            )
        raise ExecutionLockError(error_msg) from None
    finally:
        if logger and enabled:
            logger.debug(
                "Execution lock released",
                lock_file=str(lock_path),
            )


def get_default_lock_file() -> Path:
    """
    Get the default lock file path based on platform.

    Returns:
        Path to default lock file
    """
    if sys.platform == "win32":
        # Windows: use temp directory
        return Path.home() / "AppData" / "Local" / "Temp" / "expb.lock"
    else:
        # Unix-like systems: use /tmp
        return Path("/tmp/expb.lock")
