"""
Cloud media source — list streamable movie URLs from Internet Archive items.

Mirrors ``media_library`` but for the network: given one or more Internet Archive
item identifiers, it returns direct download URLs for the media files inside them,
which VLC can stream (they support HTTP range requests). Nothing here knows about
slots, selection, or the database — it is pure metadata I/O, cached per identifier
exactly like the on-disk scan is cached, so the Next pipeline never re-fetches.

The network fetch is injectable so the whole module is unit-testable without ever
touching the network.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Optional

from .config import (
    CLOUD_REQUEST_TIMEOUT,
    IA_DOWNLOAD_URL,
    IA_METADATA_URL,
    VIDEO_EXTENSIONS,
)

logger = logging.getLogger(__name__)

# identifier -> parsed metadata dict (or None on any failure).
Fetcher = Callable[[str], Optional[dict]]


def _default_fetch(identifier: str) -> Optional[dict]:
    """Fetch and parse an Internet Archive item's metadata JSON.

    Returns None on any network/parse failure (logged), never raises, so a flaky
    connection degrades to an empty pool rather than crashing selection.
    """
    url = IA_METADATA_URL.format(identifier=urllib.parse.quote(identifier, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "shuffl-cloud/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=CLOUD_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        logger.warning("Internet Archive metadata fetch failed for %s: %s",
                       identifier, exc)
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Internet Archive returned unparseable metadata for %s: %s",
                       identifier, exc)
        return None


def _is_media_name(name: str, extensions: frozenset[str]) -> bool:
    dot = name.rfind(".")
    return dot != -1 and name[dot:].lower() in extensions


def item_media_urls(
    identifier: str,
    fetcher: Fetcher = _default_fetch,
    extensions: Iterable[str] | None = None,
) -> list[str]:
    """Return streamable download URLs for every media file in one IA item.

    Empty list if the identifier is blank, the fetch fails, or the item holds no
    media of a supported type.
    """
    identifier = identifier.strip()
    if not identifier:
        return []
    # Video-only by default: Internet Archive auto-generates thumbnail images
    # (.jpg/.gif) as derivatives, and the app treats images as playable media, so
    # matching all media would drop posters/thumbnails into a movie genre.
    exts = frozenset(e.lower() for e in (extensions if extensions is not None
                                         else VIDEO_EXTENSIONS))
    data = fetcher(identifier)
    if not data:
        return []
    quoted_id = urllib.parse.quote(identifier, safe="")
    urls: list[str] = []
    for entry in data.get("files") or []:
        name = entry.get("name")
        if not name or not _is_media_name(name, exts):
            continue
        urls.append(IA_DOWNLOAD_URL.format(
            identifier=quoted_id,
            filename=urllib.parse.quote(name),
        ))
    return urls


class CloudLibrary:
    """Caches the media-URL list per Internet Archive identifier spec.

    A cloud genre's "path" is one or more identifiers (comma or newline separated);
    ``get_files`` returns the combined, de-duplicated URL list, scanning once and
    caching. ``rescan`` forces a fresh fetch (the explicit rescan UI action).
    """

    def __init__(self, fetcher: Fetcher = _default_fetch) -> None:
        self._fetch = fetcher
        self._cache: dict[str, list[str]] = {}

    @staticmethod
    def parse_identifiers(spec: str) -> list[str]:
        """Split a spec of identifiers on commas/newlines, trimming blanks."""
        parts = (spec or "").replace("\n", ",").split(",")
        return [p.strip() for p in parts if p.strip()]

    def get_files(self, spec: str) -> list[str]:
        key = (spec or "").strip()
        cached = self._cache.get(key)
        if cached is None:
            cached = self._scan(key)
            self._cache[key] = cached
            logger.info("Cloud scan %s: %d media URLs", key, len(cached))
        return cached

    def rescan(self, spec: str) -> list[str]:
        key = (spec or "").strip()
        files = self._scan(key)
        self._cache[key] = files
        logger.info("Cloud rescan %s: %d media URLs", key, len(files))
        return files

    def invalidate(self, spec: Optional[str] = None) -> None:
        if spec is None:
            self._cache.clear()
        else:
            self._cache.pop((spec or "").strip(), None)

    def _scan(self, spec: str) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for identifier in self.parse_identifiers(spec):
            for url in item_media_urls(identifier, self._fetch):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls
