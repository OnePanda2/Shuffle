"""
Filesystem side-effects that are not media scanning: sending a file to the
Windows Recycle Bin.

"Delete Permanently" in the UI means *recoverable* deletion — the file goes to
the Recycle Bin, never an unrecoverable OS-level unlink. This is isolated here so
the deletion mechanism can be swapped or hardened without touching UI code.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def move_to_recycle_bin(path: str) -> bool:
    """Send *path* to the Windows Recycle Bin. Returns True on success.

    Never raises: a missing file or a permission error is logged and reported as
    failure so the caller can inform the user without crashing.
    """
    try:
        from send2trash import send2trash
    except ImportError:
        logger.error("send2trash is not installed; cannot recycle %s", path)
        return False

    try:
        send2trash(path)
        logger.info("Sent to Recycle Bin: %s", path)
        return True
    except Exception as exc:  # send2trash raises OSError subclasses on failure
        logger.error("Failed to recycle %s: %s", path, exc)
        return False
