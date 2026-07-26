"""
Application configuration: immutable defaults, filesystem paths, and VLC
executable auto-detection.

Design notes
------------
* Values here are *defaults and constants*. Anything the user can change at
  runtime (the VLC path override, per-folder settings) is persisted in the
  state store, never hardcoded here. This keeps future configurability cheap.
* The media extension sets are centralised here so adding a new supported
  format is a one-line change with no logic edits elsewhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "VLCRandomizer"

# --- Slot / VLC networking ------------------------------------------------

# Validated in the Step-1 feasibility harness: 6 concurrent instances bind
# independent local HTTP ports with ~31 MB idle RSS each. Kept as a constant so
# the cap can be tuned in one place.
MAX_SLOTS = 6

# Base local HTTP control port. Slot i uses HTTP_PORT_BASE + i. 127.0.0.1 only.
HTTP_PORT_BASE = 8070

# VLC's HTTP interface requires a password. This is a *local-only* control
# channel (loopback), never exposed off-machine.
HTTP_PASSWORD = "vlc_randomizer_local"

HTTP_HOST = "127.0.0.1"

# --- Per-folder setting defaults -----------------------------------------

DEFAULT_SHUFFLE_COUNT = 10       # N: future Next-presses before a file is eligible again
DEFAULT_SKIP_THRESHOLD = 60      # seconds; below this an item is treated as "skipped"
DEFAULT_EXCLUDE_SKIPPED = False  # whether skipped items also enter the exclusion cooldown
DEFAULT_PERSONAL_ALGORITHM = False  # whether a folder uses weighted (vs pure uniform) selection

# --- Personal Algorithm (Affinity + Rediscovery) — ALL VALUES BELOW ARE PLACEHOLDERS.
# The project owner expects to retune these after real-world use. Formulas in
# preference_engine.py must reference these constants only — never a literal number.

# Affinity reward tiers: list of (min_elapsed_seconds, reward_amount) pairs, ordered
# ascending. On a WATCHED classification, the reward is the value of the highest tier
# whose min_elapsed_seconds the elapsed watch time meets or exceeds.
AFFINITY_WATCH_REWARD_TIERS = [
    (300,  0.5),   # >= 5 min watched  -> +0.5 (placeholder)
    (600,  1.0),   # >= 10 min watched -> +1.0 (placeholder)
    (1200, 2.0),   # >= 20 min watched -> +2.0 (placeholder)
    (2400, 3.0),   # >= 40 min watched -> +3.0, largest tier (placeholder)
]

# Flat Affinity subtraction on every SKIPPED classification. Must stay smaller than
# the smallest meaningful watch reward tier above, per the locked spec.
AFFINITY_SKIP_PENALTY = 0.3  # placeholder

# Multiplier applied to the raw, unmodified stored Affinity Score to produce its
# weight contribution. No normalization/log-scaling/dampening is ever applied elsewhere.
AFFINITY_WEIGHT_MULTIPLIER = 1.0  # placeholder

# Rediscovery score = (days_since_last_selected ** REDISCOVERY_GROWTH_EXPONENT) *
# REDISCOVERY_SCALE. Exponent > 1 makes this a convex/accelerating curve: barely
# moves after 1 day, grows substantially by 6 months, per the locked spec.
REDISCOVERY_GROWTH_EXPONENT = 1.6  # placeholder
REDISCOVERY_SCALE = 0.05  # placeholder

# Multiplier applied LINEARLY to the already-non-linear Rediscovery score above to
# produce its weight contribution. No second non-linear transform here by design.
REDISCOVERY_WEIGHT_MULTIPLIER = 1.0  # placeholder

# Flat constant added to every eligible file's weight regardless of its Affinity or
# Rediscovery values. This is what structurally guarantees every eligible file always
# has a non-zero, non-deterministic selection probability. Do not remove or zero this.
BASE_WEIGHT = 1.0  # placeholder

# --- Native-control detection --------------------------------------------
# When the user presses VLC's own Next/Previous button or a file ends, the app
# detects it (by a change in VLC's current playlist id) and auto-advances to a
# fresh random pick. This grace window after each load prevents the app's own
# load transition from being mistaken for a user action.
AUTO_ADVANCE_GRACE_SECONDS = 1.2

# UI status-poll interval (ms). Also drives native-control detection.
POLL_INTERVAL_MS = 1000

# Keep still images on screen until the user advances, instead of VLC's default
# ~10s auto-timeout (which would otherwise trigger an auto-advance).
IMAGE_DURATION_SECONDS = 3600

# --- Supported media extensions ------------------------------------------
# Lowercase, leading dot. Images are treated as watchable media (VLC displays
# them like a still/slideshow), exactly as a drag-and-dropped image would be.

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".m2v", ".3gp", ".3g2", ".ts", ".m2ts", ".mts",
    ".ogv", ".vob", ".divx", ".f4v", ".rm", ".rmvb", ".asf",
})

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif",
    ".jfif", ".heic", ".heif",
})

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

# --- Common VLC install locations for auto-detection ----------------------

_VLC_CANDIDATES = (
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
)


def default_data_dir() -> Path:
    """Return the per-user data directory (created on demand).

    Uses %LOCALAPPDATA% on Windows, falling back to the user home directory so
    the app still works if the environment variable is unset.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_vlc_path() -> str | None:
    """Best-effort auto-detection of vlc.exe. Returns None if not found.

    The user can always override this via global settings, so detection failing
    is non-fatal.
    """
    for candidate in _VLC_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    # PATH fallback.
    from shutil import which
    found = which("vlc")
    return found


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem locations for this run."""
    data_dir: Path
    db_path: Path
    log_path: Path

    @classmethod
    def create(cls, data_dir: Path | None = None) -> "AppPaths":
        data_dir = data_dir or default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_dir,
            db_path=data_dir / "vlc_randomizer.db",
            log_path=logs / "app.log",
        )
