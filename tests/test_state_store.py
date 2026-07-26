"""Tests for persistence, per-folder isolation, and the two-list separation."""
import sqlite3

import pytest

from vlc_randomizer.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def two_folders(store):
    f1 = store.add_folder("Thriller", r"C:\media\Thriller")
    f2 = store.add_folder("Comedy", r"C:\media\Comedy")
    return store, f1, f2


# -- folder CRUD -----------------------------------------------------------

def test_add_and_get_folder_defaults(store):
    f = store.add_folder("Sci-Fi", r"C:\media\SciFi")
    assert f.shuffle_count == 10
    assert f.skip_threshold == 60
    assert f.exclude_skipped is False
    assert store.get_folder(f.id).name == "Sci-Fi"


def test_duplicate_path_rejected(store):
    store.add_folder("A", r"C:\media\X")
    with pytest.raises(ValueError):
        store.add_folder("B", r"C:\media\X")


def test_update_folder_settings(store):
    f = store.add_folder("A", r"C:\media\A")
    updated = store.update_folder(f.id, shuffle_count=5, skip_threshold=30,
                                  exclude_skipped=True)
    assert updated.shuffle_count == 5
    assert updated.skip_threshold == 30
    assert updated.exclude_skipped is True


def test_remove_folder_cascades(two_folders):
    store, f1, _ = two_folders
    store.add_exclusion(f1.id, "x.mp4", 10)
    store.add_to_review(f1.id, "x.mp4", "skipped")
    store.remove_folder(f1.id)
    assert store.get_exclusions(f1.id) == {}
    assert store.get_review_items(f1.id) == []


# -- exclusion list (cooldown) --------------------------------------------

def test_exclusion_add_and_query(two_folders):
    store, f1, _ = two_folders
    store.add_exclusion(f1.id, "a.mp4", 10)
    assert store.get_excluded_paths(f1.id) == {"a.mp4"}
    assert store.get_exclusions(f1.id) == {"a.mp4": 10}


def test_cooldown_tick_decrements_and_releases(two_folders):
    store, f1, _ = two_folders
    store.add_exclusion(f1.id, "a.mp4", 2)
    store.add_exclusion(f1.id, "b.mp4", 1)
    released = store.apply_cooldown_tick(f1.id)      # a->1, b->0 (released)
    assert released == ["b.mp4"]
    assert store.get_exclusions(f1.id) == {"a.mp4": 1}
    released2 = store.apply_cooldown_tick(f1.id)     # a->0 (released)
    assert released2 == ["a.mp4"]
    assert store.get_exclusions(f1.id) == {}


def test_readd_resets_count(two_folders):
    store, f1, _ = two_folders
    store.add_exclusion(f1.id, "a.mp4", 10)
    store.apply_cooldown_tick(f1.id)                 # -> 9
    store.add_exclusion(f1.id, "a.mp4", 10)          # re-watched, reset
    assert store.get_exclusions(f1.id)["a.mp4"] == 10


def test_folder_state_is_isolated(two_folders):
    store, f1, f2 = two_folders
    store.add_exclusion(f1.id, "shared.mp4", 5)
    store.add_exclusion(f2.id, "shared.mp4", 3)
    store.apply_cooldown_tick(f1.id)                 # only f1 decrements
    assert store.get_exclusions(f1.id) == {"shared.mp4": 4}
    assert store.get_exclusions(f2.id) == {"shared.mp4": 3}


# -- review list & separation ---------------------------------------------

def test_review_add_remove(two_folders):
    store, f1, _ = two_folders
    store.add_to_review(f1.id, "s.mp4", "skipped")
    items = store.get_review_items(f1.id)
    assert len(items) == 1 and items[0].file_path == "s.mp4"
    store.remove_from_review(f1.id, "s.mp4")
    assert store.get_review_items(f1.id) == []


def test_file_can_be_on_both_lists(two_folders):
    store, f1, _ = two_folders
    store.add_to_review(f1.id, "x.mp4", "skipped")
    store.add_exclusion(f1.id, "x.mp4", 10)
    assert "x.mp4" in store.get_excluded_paths(f1.id)
    assert store.get_review_items(f1.id)[0].file_path == "x.mp4"
    # Releasing the cooldown must NOT touch the review entry.
    store.apply_cooldown_tick(f1.id)
    for _ in range(12):
        store.apply_cooldown_tick(f1.id)
    assert store.get_excluded_paths(f1.id) == set()
    assert store.get_review_items(f1.id)[0].file_path == "x.mp4"  # still present


# -- settings & persistence ------------------------------------------------

def test_app_settings_roundtrip(store):
    assert store.get_setting("vlc_path") is None
    store.set_setting("vlc_path", r"C:\VLC\vlc.exe")
    assert store.get_setting("vlc_path") == r"C:\VLC\vlc.exe"


def test_state_persists_across_reopen(tmp_path):
    db = tmp_path / "persist.db"
    s1 = StateStore(db)
    f = s1.add_folder("A", r"C:\media\A")
    s1.add_exclusion(f.id, "a.mp4", 7)
    s1.close()
    s2 = StateStore(db)
    assert s2.get_folders()[0].name == "A"
    assert s2.get_exclusions(f.id) == {"a.mp4": 7}
    s2.close()


# -- Personal Algorithm: folders.personal_algorithm -----------------------

def test_personal_algorithm_default_and_update(store):
    f = store.add_folder("A", r"C:\media\A")
    assert f.personal_algorithm is False                  # default OFF
    updated = store.update_folder(f.id, personal_algorithm=True)
    assert updated.personal_algorithm is True
    assert store.get_folder(f.id).personal_algorithm is True


def test_migration_adds_personal_algorithm_column_to_existing_db(tmp_path):
    """Simulate a pre-v1.x database (folders table lacking the new column)."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE folders (
               id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
               path TEXT NOT NULL UNIQUE, shuffle_count INTEGER NOT NULL,
               skip_threshold INTEGER NOT NULL, exclude_skipped INTEGER NOT NULL,
               created_at REAL NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO folders (name, path, shuffle_count, skip_threshold, "
        "exclude_skipped, created_at) VALUES ('Old', 'C:/old', 10, 60, 0, 123.0)"
    )
    conn.commit()
    conn.close()

    store = StateStore(db)                                # runs the ALTER migration
    folders = store.get_folders()
    assert len(folders) == 1
    assert folders[0].name == "Old"                       # existing data intact
    assert folders[0].personal_algorithm is False         # new column, default OFF
    store.close()

    # Re-opening must be a safe no-op (duplicate-column ALTER swallowed).
    store2 = StateStore(db)
    assert store2.get_folders()[0].name == "Old"
    store2.close()


# -- Personal Algorithm: media_preferences --------------------------------

def test_get_preference_returns_neutral_defaults_without_inserting(two_folders):
    store, f1, _ = two_folders
    p = store.get_preference(f1.id, "x.mp4")
    assert p.affinity_score == 0.0 and p.last_selected_at is None
    assert store.get_preferences_for_folder(f1.id) == {}   # reading inserted nothing


def test_update_affinity_preserves_last_selected(two_folders):
    store, f1, _ = two_folders
    store.update_affinity(f1.id, "a.mp4", 2.5)
    p = store.get_preference(f1.id, "a.mp4")
    assert p.affinity_score == 2.5 and p.last_selected_at is None
    store.mark_selected(f1.id, "a.mp4", 1000.0)
    store.update_affinity(f1.id, "a.mp4", 4.0)             # must not clobber timestamp
    p = store.get_preference(f1.id, "a.mp4")
    assert p.affinity_score == 4.0 and p.last_selected_at == 1000.0


def test_mark_selected_preserves_affinity_and_defaults_new_rows(two_folders):
    store, f1, _ = two_folders
    store.update_affinity(f1.id, "a.mp4", 3.0)
    store.mark_selected(f1.id, "a.mp4", 500.0)             # must not clobber affinity
    p = store.get_preference(f1.id, "a.mp4")
    assert p.affinity_score == 3.0 and p.last_selected_at == 500.0
    store.mark_selected(f1.id, "brand_new.mp4", 900.0)     # new row -> affinity 0
    p2 = store.get_preference(f1.id, "brand_new.mp4")
    assert p2.affinity_score == 0.0 and p2.last_selected_at == 900.0


def test_preferences_batch_query_and_folder_isolation(two_folders):
    store, f1, f2 = two_folders
    store.update_affinity(f1.id, "a", 1.0)
    store.update_affinity(f1.id, "b", 2.0)
    store.update_affinity(f2.id, "a", 9.0)                 # same path, other folder
    d1 = store.get_preferences_for_folder(f1.id)
    assert set(d1) == {"a", "b"} and d1["a"].affinity_score == 1.0
    assert store.get_preferences_for_folder(f2.id)["a"].affinity_score == 9.0


def test_preferences_cascade_on_folder_delete(two_folders):
    store, f1, _ = two_folders
    store.update_affinity(f1.id, "a", 1.0)
    store.mark_selected(f1.id, "a", 10.0)
    store.remove_folder(f1.id)
    assert store.get_preferences_for_folder(f1.id) == {}


def test_preferences_persist_across_reopen(tmp_path):
    db = tmp_path / "prefs.db"
    s1 = StateStore(db)
    f = s1.add_folder("A", r"C:\media\A", personal_algorithm=True)
    s1.update_affinity(f.id, "a.mp4", 5.5)
    s1.mark_selected(f.id, "a.mp4", 4242.0)
    s1.close()
    s2 = StateStore(db)
    assert s2.get_folders()[0].personal_algorithm is True
    p = s2.get_preference(f.id, "a.mp4")
    assert p.affinity_score == 5.5 and p.last_selected_at == 4242.0
    s2.close()
