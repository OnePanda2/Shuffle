"""
Media library scanning.

Responsibility
--------------
Given a folder path, enumerate the media files inside it (recursively, including
subfolders), filtered by a configurable extension set. Nothing here knows about
slots, VLC, exclusion state, or the database — it is pure filesystem I/O, which
keeps it trivially testable and reusable.

Performance
-----------
* Scans are on-demand (folder set-up and the explicit "rescan" action), never on
  every ``Next`` press.
* Results are cached per folder path with the extension set they were produced
  with; the ``Next`` pipeline reads the cached list rather than re-walking disk.
* ``os.scandir`` is used (faster than ``os.listdir``/``glob`` on large trees) and
  inaccessible directories are skipped gracefully rather than raising.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Iterator

from .config import MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)


class MediaScanner:
    """Recursively enumerates media files under a root folder.

    The extension set is injected (defaulting to the global config set) so a
    caller can support new formats or restrict types without editing this class.
    """

    def __init__(self, extensions: Iterable[str] | None = None) -> None:
        # Normalise to lowercase, dot-prefixed for case-insensitive matching.
        exts = extensions if extensions is not None else MEDIA_EXTENSIONS
        self._extensions = frozenset(e.lower() for e in exts)

    @property
    def extensions(self) -> frozenset[str]:
        return self._extensions

    def is_media(self, path: str | os.PathLike) -> bool:
        """True if *path* has a supported media extension (name check only)."""
        return Path(path).suffix.lower() in self._extensions

    def scan(self, root: str | os.PathLike) -> list[str]:
        """Return absolute paths of all media files under *root*, recursively.

        Missing or inaccessible roots yield an empty list (logged), never an
        exception — the caller should stay alive if a folder was unplugged.
        """
        root_path = Path(root)
        if not root_path.is_dir():
            logger.warning("Scan skipped; not a directory: %s", root)
            return []
        return list(self._walk(str(root_path)))

    def _walk(self, root: str) -> Iterator[str]:
        """Iterative, error-tolerant recursive walk using os.scandir."""
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                if Path(entry.name).suffix.lower() in self._extensions:
                                    yield os.path.abspath(entry.path)
                        except OSError as exc:
                            # A single bad entry must not abort the whole scan.
                            logger.debug("Skipping unreadable entry in %s: %s",
                                         current, exc)
            except OSError as exc:
                logger.warning("Skipping unreadable directory %s: %s", current, exc)


class MediaLibrary:
    """Caches scan results per folder path for cheap repeated reads.

    The cache is keyed by folder path. ``rescan`` forces a fresh walk (used by
    the explicit "rescan folder" UI action and after a folder's path changes).
    """

    def __init__(self, scanner: MediaScanner | None = None) -> None:
        self._scanner = scanner or MediaScanner()
        self._cache: dict[str, list[str]] = {}

    def get_files(self, folder_path: str) -> list[str]:
        """Return cached media files for *folder_path*, scanning once if needed."""
        key = os.path.abspath(folder_path)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._scanner.scan(key)
            self._cache[key] = cached
            logger.info("Scanned %s: %d media files", key, len(cached))
        return cached

    def rescan(self, folder_path: str) -> list[str]:
        """Force a fresh scan of *folder_path*, replacing the cache entry."""
        key = os.path.abspath(folder_path)
        files = self._scanner.scan(key)
        self._cache[key] = files
        logger.info("Rescanned %s: %d media files", key, len(files))
        return files

    def invalidate(self, folder_path: str | None = None) -> None:
        """Drop cached results for one folder, or all folders if None."""
        if folder_path is None:
            self._cache.clear()
        else:
            self._cache.pop(os.path.abspath(folder_path), None)


def file_exists(path: str) -> bool:
    """Cheap existence check used when building the eligible pool.

    Wrapped so a transient OS error (e.g. a disconnected network drive) is
    treated as 'not present' rather than crashing selection.

    A cloud (http/https) URL cannot be verified without a network round-trip, so
    we trust the manifest and treat any URL as present. A dead URL simply fails to
    load, which the Next pipeline already handles gracefully.
    """
    if path.startswith(("http://", "https://")):
        return True
    try:
        return os.path.isfile(path)
    except OSError:
        return False
