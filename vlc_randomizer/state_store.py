"""
State Store — SQLite persistence and the in-memory data model.

Owns four independent concerns, one table each:

* ``folders``        registered genre folders + their per-folder settings.
* ``exclusion_list`` temporary per-file cooldown (Remaining Shuffle Count).
* ``review_list``    permanent holding area for skipped files (manual action).
* ``app_settings``   global key/value settings (e.g. VLC executable path).

Hard rules enforced structurally
--------------------------------
* The exclusion list and the review list are **separate tables** with separate
  APIs. They are never merged or conflated. A file may exist in both.
* All state is scoped by ``folder_id``; no query ever lets one folder's state
  affect another's. Deleting a folder cascades to *its* rows only.
* ``remaining_count`` is a countdown of future ``Next`` presses, not a queue.

Reliability
-----------
* WAL journalling + a process-wide lock make writes atomic and safe for the
  UI thread plus background polling.
* A corrupted database file is detected on open, moved aside, and recreated so
  the app still starts (history loss on corruption is acceptable per spec).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import (
    DEFAULT_EXCLUDE_SKIPPED,
    DEFAULT_PERSONAL_ALGORITHM,
    DEFAULT_SHUFFLE_COUNT,
    DEFAULT_SKIP_THRESHOLD,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    path               TEXT    NOT NULL UNIQUE,
    shuffle_count      INTEGER NOT NULL,
    skip_threshold     INTEGER NOT NULL,
    exclude_skipped    INTEGER NOT NULL,           -- 0/1 boolean
    created_at         REAL    NOT NULL,
    personal_algorithm INTEGER NOT NULL DEFAULT 0, -- 0/1 boolean; weighted selection on
    is_favorites       INTEGER NOT NULL DEFAULT 0  -- 0/1; the one virtual Favorites genre
);

CREATE TABLE IF NOT EXISTS exclusion_list (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id       INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    file_path       TEXT    NOT NULL,
    remaining_count INTEGER NOT NULL,
    added_at        REAL    NOT NULL,
    UNIQUE(folder_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_excl_folder ON exclusion_list(folder_id);

CREATE TABLE IF NOT EXISTS review_list (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id  INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    file_path  TEXT    NOT NULL,
    reason     TEXT    NOT NULL,
    added_at   REAL    NOT NULL,
    UNIQUE(folder_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_review_folder ON review_list(folder_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Personal Algorithm: the app's learned understanding of each media file.
-- One row per (folder_id, file_path). affinity_score is cumulative (no ceiling,
-- floored at 0). last_selected_at is NULL until the file is first selected; the
-- Rediscovery score is derived from it on the fly and never stored here.
CREATE TABLE IF NOT EXISTS media_preferences (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id        INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    file_path        TEXT    NOT NULL,
    affinity_score   REAL    NOT NULL DEFAULT 0,
    last_selected_at REAL,              -- NULL = never selected (neutral start)
    UNIQUE(folder_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_media_prefs_folder ON media_preferences(folder_id);

-- Favorites: the user's curated list. One row per favorited file (UNIQUE by path,
-- so a movie is favorited once, globally). folder_id records the origin genre the
-- file was favorited from and is ON DELETE SET NULL so removing a genre keeps its
-- favorites (they still play in the Favorites genre; the file on disk is untouched).
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id  INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    file_path  TEXT    NOT NULL UNIQUE,
    added_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_favorites_folder ON favorites(folder_id);
"""

# The single virtual Favorites genre. Its path is a sentinel that is never scanned
# on disk; its file list comes from the favorites table instead.
FAVORITES_FOLDER_NAME = "❤ Favorites"
FAVORITES_FOLDER_PATH = "<favorites>"


@dataclass
class Folder:
    """A registered genre folder and its independent settings."""
    id: int
    name: str
    path: str
    shuffle_count: int          # N — future Next-presses before re-eligible
    skip_threshold: int         # seconds
    exclude_skipped: bool
    created_at: float
    personal_algorithm: bool = False  # weighted (vs pure uniform) selection
    is_favorites: bool = False        # True only for the virtual Favorites genre


@dataclass
class ReviewItem:
    """One entry on a folder's Review/Delete list."""
    file_path: str
    reason: str
    added_at: float


@dataclass
class MediaPreference:
    """The app's learned understanding of one media file (Personal Algorithm)."""
    file_path: str
    affinity_score: float
    last_selected_at: Optional[float]  # None = never selected


class StateStore:
    """Thread-safe SQLite-backed repository for all persistent state."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._init_schema()

    # -- connection / lifecycle -------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open the DB, recreating it if the file is unreadable/corrupt."""
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            # Touch the DB to force corruption to surface now, not mid-session.
            conn.execute("PRAGMA quick_check;")
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.DatabaseError as exc:
            logger.error("Database unreadable (%s); recreating: %s",
                         self._db_path, exc)
            self._quarantine_corrupt_db()
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.row_factory = sqlite3.Row
            return conn

    def _quarantine_corrupt_db(self) -> None:
        """Move a corrupt DB file aside so a fresh one can be created."""
        src = Path(self._db_path)
        if src.exists():
            backup = src.with_suffix(src.suffix + f".corrupt.{int(time.time())}")
            try:
                src.rename(backup)
                logger.warning("Corrupt database moved to %s", backup)
            except OSError as exc:
                logger.error("Could not move corrupt DB aside: %s", exc)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_folders_personal_algorithm()
            self._migrate_folders_is_favorites()
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def _migrate_folders_personal_algorithm(self) -> None:
        """Additively add the personal_algorithm column to the folders table.

        CREATE TABLE IF NOT EXISTS is a no-op on an existing v1.x folders table, so
        the new column would NOT appear for existing users just by editing the
        CREATE string. This ALTER adds it; on databases that already have the
        column (fresh DBs, or a second launch) SQLite raises OperationalError with
        'duplicate column name', which we treat as a safe no-op.
        """
        try:
            self._conn.execute(
                "ALTER TABLE folders ADD COLUMN "
                "personal_algorithm INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise  # only swallow the 'already exists' case; surface anything else

    def _migrate_folders_is_favorites(self) -> None:
        """Additively add the is_favorites column to an existing folders table.

        Same rationale/pattern as _migrate_folders_personal_algorithm: the CREATE
        IF NOT EXISTS never adds a column to a pre-existing table, so this ALTER
        backfills it for older databases; the 'duplicate column' error on a DB that
        already has it is a safe no-op.
        """
        try:
            self._conn.execute(
                "ALTER TABLE folders ADD COLUMN "
                "is_favorites INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- folders -----------------------------------------------------------

    def add_folder(
        self,
        name: str,
        path: str,
        shuffle_count: int = DEFAULT_SHUFFLE_COUNT,
        skip_threshold: int = DEFAULT_SKIP_THRESHOLD,
        exclude_skipped: bool = DEFAULT_EXCLUDE_SKIPPED,
        personal_algorithm: bool = DEFAULT_PERSONAL_ALGORITHM,
    ) -> Folder:
        """Register a new folder. Raises ValueError if the path already exists."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO folders
                       (name, path, shuffle_count, skip_threshold,
                        exclude_skipped, created_at, personal_algorithm)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, path, shuffle_count, skip_threshold,
                     int(exclude_skipped), time.time(), int(personal_algorithm)),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Folder path already registered: {path}") from exc
            return self.get_folder(cur.lastrowid)  # type: ignore[arg-type]

    def update_folder(
        self,
        folder_id: int,
        *,
        name: Optional[str] = None,
        path: Optional[str] = None,
        shuffle_count: Optional[int] = None,
        skip_threshold: Optional[int] = None,
        exclude_skipped: Optional[bool] = None,
        personal_algorithm: Optional[bool] = None,
    ) -> Folder:
        """Patch any subset of a folder's fields."""
        fields: list[str] = []
        values: list[object] = []
        if name is not None:
            fields.append("name = ?"); values.append(name)
        if path is not None:
            fields.append("path = ?"); values.append(path)
        if shuffle_count is not None:
            fields.append("shuffle_count = ?"); values.append(shuffle_count)
        if skip_threshold is not None:
            fields.append("skip_threshold = ?"); values.append(skip_threshold)
        if exclude_skipped is not None:
            fields.append("exclude_skipped = ?"); values.append(int(exclude_skipped))
        if personal_algorithm is not None:
            fields.append("personal_algorithm = ?"); values.append(int(personal_algorithm))
        if not fields:
            return self.get_folder(folder_id)
        values.append(folder_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE folders SET {', '.join(fields)} WHERE id = ?", values
            )
            self._conn.commit()
        return self.get_folder(folder_id)

    def remove_folder(self, folder_id: int) -> None:
        """Delete a folder and (via cascade) all of its list state."""
        with self._lock:
            self._conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            self._conn.commit()

    def get_folder(self, folder_id: int) -> Folder:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folders WHERE id = ?", (folder_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No folder with id {folder_id}")
        return self._row_to_folder(row)

    def get_folders(self) -> list[Folder]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM folders ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_folder(r) for r in rows]

    @staticmethod
    def _row_to_folder(row: sqlite3.Row) -> Folder:
        return Folder(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            shuffle_count=row["shuffle_count"],
            skip_threshold=row["skip_threshold"],
            exclude_skipped=bool(row["exclude_skipped"]),
            created_at=row["created_at"],
            personal_algorithm=bool(row["personal_algorithm"]),
            is_favorites=bool(row["is_favorites"]),
        )

    # -- favorites folder (the one virtual genre) -------------------------

    def ensure_favorites_folder(self) -> Folder:
        """Return the virtual Favorites genre, creating it once if absent.

        Idempotent: a second launch finds the existing row and returns it. The
        row is a normal folders record (so it carries its own settings and its own
        independent history) flagged with is_favorites=1 and a sentinel path that
        is never scanned on disk.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folders WHERE is_favorites = 1 LIMIT 1"
            ).fetchone()
            if row is not None:
                return self._row_to_folder(row)
            cur = self._conn.execute(
                """INSERT INTO folders
                   (name, path, shuffle_count, skip_threshold, exclude_skipped,
                    created_at, personal_algorithm, is_favorites)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 1)""",
                (FAVORITES_FOLDER_NAME, FAVORITES_FOLDER_PATH, DEFAULT_SHUFFLE_COUNT,
                 DEFAULT_SKIP_THRESHOLD, int(DEFAULT_EXCLUDE_SKIPPED), time.time()),
            )
            self._conn.commit()
            return self.get_folder(cur.lastrowid)  # type: ignore[arg-type]

    # -- favorites list ---------------------------------------------------

    def add_favorite(self, file_path: str, folder_id: Optional[int] = None) -> None:
        """Mark a file as a favorite (idempotent; records its origin genre)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO favorites (folder_id, file_path, added_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(file_path)
                   DO UPDATE SET folder_id = excluded.folder_id""",
                (folder_id, file_path, time.time()),
            )
            self._conn.commit()

    def remove_favorite(self, file_path: str) -> None:
        """Un-favorite a file (the file on disk is untouched)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM favorites WHERE file_path = ?", (file_path,)
            )
            self._conn.commit()

    def toggle_favorite(self, file_path: str, folder_id: Optional[int] = None) -> bool:
        """Flip a file's favorite state. Returns the NEW state (True = now favorite)."""
        if self.is_favorite(file_path):
            self.remove_favorite(file_path)
            return False
        self.add_favorite(file_path, folder_id)
        return True

    def is_favorite(self, file_path: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM favorites WHERE file_path = ?", (file_path,)
            ).fetchone()
        return row is not None

    def get_favorite_paths(self) -> set[str]:
        """Return the set of all favorited file paths (across every genre)."""
        with self._lock:
            rows = self._conn.execute("SELECT file_path FROM favorites").fetchall()
        return {r["file_path"] for r in rows}

    # -- exclusion list (temporary cooldown) ------------------------------

    def add_exclusion(self, folder_id: int, file_path: str, count: int) -> None:
        """Add/reset a file's cooldown with Remaining Shuffle Count = *count*.

        If the file is already excluded, its count is reset to *count* (a fresh
        watch restarts the cooldown).
        """
        with self._lock:
            self._conn.execute(
                """INSERT INTO exclusion_list
                   (folder_id, file_path, remaining_count, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(folder_id, file_path)
                   DO UPDATE SET remaining_count = excluded.remaining_count,
                                 added_at = excluded.added_at""",
                (folder_id, file_path, count, time.time()),
            )
            self._conn.commit()

    def get_excluded_paths(self, folder_id: int) -> set[str]:
        """Return the set of currently-excluded file paths for a folder."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path FROM exclusion_list WHERE folder_id = ?",
                (folder_id,),
            ).fetchall()
        return {r["file_path"] for r in rows}

    def get_exclusions(self, folder_id: int) -> dict[str, int]:
        """Return {file_path: remaining_count} for a folder (for inspection/UI)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT file_path, remaining_count FROM exclusion_list
                   WHERE folder_id = ?""",
                (folder_id,),
            ).fetchall()
        return {r["file_path"]: r["remaining_count"] for r in rows}

    def apply_cooldown_tick(self, folder_id: int) -> list[str]:
        """Decrement every excluded file's count by 1, then release those at 0.

        This is steps 4 & 5 of the Next pipeline, executed atomically for a
        single folder. Returns the list of released (now-eligible) file paths.

        NOTE ON ORDERING: the pipeline calls this *before* adding the
        just-evaluated file to the exclusion list, so the freshly-excluded file
        is not decremented on the same press. That makes its cooldown exactly N
        future presses, honouring the hard-constraint definition of N. See
        SlotManager.press_next for the full sequence and rationale.
        """
        with self._lock:
            self._conn.execute(
                """UPDATE exclusion_list SET remaining_count = remaining_count - 1
                   WHERE folder_id = ?""",
                (folder_id,),
            )
            released_rows = self._conn.execute(
                """SELECT file_path FROM exclusion_list
                   WHERE folder_id = ? AND remaining_count <= 0""",
                (folder_id,),
            ).fetchall()
            self._conn.execute(
                """DELETE FROM exclusion_list
                   WHERE folder_id = ? AND remaining_count <= 0""",
                (folder_id,),
            )
            self._conn.commit()
        return [r["file_path"] for r in released_rows]

    def clear_exclusions(self, folder_id: int) -> None:
        """Remove all cooldown entries for a folder (used when settings change)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM exclusion_list WHERE folder_id = ?", (folder_id,)
            )
            self._conn.commit()

    # -- review / delete list (permanent, skipped files) ------------------

    def add_to_review(self, folder_id: int, file_path: str,
                      reason: str = "skipped") -> None:
        """Add a skipped file to the review list (idempotent per file)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO review_list (folder_id, file_path, reason, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(folder_id, file_path)
                   DO UPDATE SET reason = excluded.reason,
                                 added_at = excluded.added_at""",
                (folder_id, file_path, reason, time.time()),
            )
            self._conn.commit()

    def get_review_items(self, folder_id: int) -> list[ReviewItem]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT file_path, reason, added_at FROM review_list
                   WHERE folder_id = ? ORDER BY added_at DESC""",
                (folder_id,),
            ).fetchall()
        return [ReviewItem(r["file_path"], r["reason"], r["added_at"]) for r in rows]

    def remove_from_review(self, folder_id: int, file_path: str) -> None:
        """Remove a review-list entry (the file on disk is untouched here)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM review_list WHERE folder_id = ? AND file_path = ?",
                (folder_id, file_path),
            )
            self._conn.commit()

    # -- media preferences (Personal Algorithm: affinity + last-selected) --

    def get_preference(self, folder_id: int, file_path: str) -> MediaPreference:
        """Return a file's learned preference, or neutral defaults if no row yet.

        Does NOT insert a row just for reading (neutral start = affinity 0,
        last_selected_at None).
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT affinity_score, last_selected_at FROM media_preferences
                   WHERE folder_id = ? AND file_path = ?""",
                (folder_id, file_path),
            ).fetchone()
        if row is None:
            return MediaPreference(file_path, 0.0, None)
        return MediaPreference(file_path, row["affinity_score"], row["last_selected_at"])

    def get_preferences_for_folder(self, folder_id: int) -> dict[str, MediaPreference]:
        """Return {file_path: MediaPreference} for a folder in ONE batch query.

        Used when building weights for a whole eligible pool, to avoid N queries
        per Next press. Files with no row simply won't appear (callers default
        them to neutral).
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT file_path, affinity_score, last_selected_at
                   FROM media_preferences WHERE folder_id = ?""",
                (folder_id,),
            ).fetchall()
        return {
            r["file_path"]: MediaPreference(
                r["file_path"], r["affinity_score"], r["last_selected_at"]
            )
            for r in rows
        }

    def update_affinity(self, folder_id: int, file_path: str, new_score: float) -> None:
        """UPSERT the affinity score, preserving any existing last_selected_at."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO media_preferences
                   (folder_id, file_path, affinity_score)
                   VALUES (?, ?, ?)
                   ON CONFLICT(folder_id, file_path)
                   DO UPDATE SET affinity_score = excluded.affinity_score""",
                (folder_id, file_path, new_score),
            )
            self._conn.commit()

    def mark_selected(self, folder_id: int, file_path: str, timestamp: float) -> None:
        """UPSERT last_selected_at, preserving any existing affinity_score.

        A brand-new row defaults affinity_score to 0 (the neutral start).
        """
        with self._lock:
            self._conn.execute(
                """INSERT INTO media_preferences
                   (folder_id, file_path, last_selected_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(folder_id, file_path)
                   DO UPDATE SET last_selected_at = excluded.last_selected_at""",
                (folder_id, file_path, timestamp),
            )
            self._conn.commit()

    # -- global app settings ----------------------------------------------

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default
