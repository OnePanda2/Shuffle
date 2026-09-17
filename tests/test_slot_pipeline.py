"""
Integration tests for the 8-step Next pipeline in SlotManager, using a fake VLC
instance and a controllable clock so behaviour is deterministic.

These are the most important behavioural tests in the project: they lock in
watched/skipped classification, the exactly-N cooldown semantics, the strict
separation of the exclusion and review lists, per-folder state sharing, and
correct routing of loads to the correct window.
"""
from types import SimpleNamespace

import pytest

from vlc_randomizer.cloud_library import CloudLibrary
from vlc_randomizer.media_library import MediaLibrary
from vlc_randomizer.slot_manager import Classification, NextStatus, SlotManager
from vlc_randomizer.state_store import StateStore


class FakeVlc:
    """Stand-in for VlcInstance: records loaded files, never touches a process.

    Tracks a fake playlist id (``plid``) and state so the native-control
    detection (poll_and_maybe_advance) can be exercised deterministically.
    """

    def __init__(self, port):
        self.port = port
        self.loaded = []          # history of files loaded into this window
        self.queued = []          # history of files enqueued behind the current one
        self.launched = False
        self.stopped = False
        self.plid = 0             # increments on each load (like VLC assigns)
        self.state = "stopped"

    def launch(self, *a, **k):
        self.launched = True

    def load(self, file_path, queue_path=None):
        self.loaded.append(file_path)
        if queue_path is not None:
            self.queued.append(queue_path)
        self.plid += 1
        self.state = "playing"
        return True

    def enqueue(self, file_path):
        self.queued.append(file_path)
        return True

    def get_status(self):
        return SimpleNamespace(state=self.state, plid=self.plid,
                               time=0, length=0, filename=None)

    def stop(self):
        self.stopped = True

    def is_process_alive(self):
        return self.launched and not self.stopped

    @property
    def current(self):
        return self.loaded[-1] if self.loaded else None

    # -- helpers to simulate the user acting inside the VLC window ----------
    def simulate_native_next(self):
        """User pressed VLC's own Next (or media ended): VLC advances to the next
        playlist item, so currentplid changes and it keeps playing."""
        self.plid += 1
        self.state = "playing"

    # Media ending advances to the queued item exactly like a native Next.
    simulate_end = simulate_native_next

    def simulate_stop(self):
        """No next item to advance to: VLC halts (the duplicate-fallback case)."""
        self.state = "stopped"


class Clock:
    """Manually-advanced monotonic clock."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def env(tmp_path):
    """A state store, a real media folder, and a SlotManager wired to fakes."""
    # Build a folder with several media files.
    media_dir = tmp_path / "Thriller"
    media_dir.mkdir()
    files = []
    for i in range(6):
        p = media_dir / f"movie_{i}.mp4"
        p.write_bytes(b"x")
        files.append(str(p.resolve()))

    state = StateStore(tmp_path / "state.db")
    library = MediaLibrary()
    clock = Clock()
    fakes = {}

    def factory(port):
        fakes[port] = FakeVlc(port)
        return fakes[port]

    manager = SlotManager(state, library, factory, clock=clock)
    yield state, library, manager, clock, fakes, media_dir, files
    state.close()


# -- classification --------------------------------------------------------

def test_watched_goes_to_exclusion_only(env):
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))  # N=10, thr=60, toggle off
    slot = manager.create_slot(folder.id)
    watched = slot.current_file
    assert watched is not None                       # initial load happened

    clock.advance(120)                               # 120s >= 60 -> watched
    result = manager.press_next(slot.slot_id)

    assert result.classification is Classification.WATCHED
    assert watched in state.get_excluded_paths(folder.id)
    assert state.get_exclusions(folder.id)[watched] == 10
    assert state.get_review_items(folder.id) == []   # NOT on review list


def test_skipped_toggle_off_goes_to_review_only(env):
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot(folder.id)
    skipped = slot.current_file

    clock.advance(5)                                 # 5s < 60 -> skipped
    result = manager.press_next(slot.slot_id)

    assert result.classification is Classification.SKIPPED
    review = [r.file_path for r in state.get_review_items(folder.id)]
    assert skipped in review                          # ALWAYS on review list
    assert skipped not in state.get_excluded_paths(folder.id)  # toggle off -> not excluded


def test_skipped_toggle_on_goes_to_both_lists(env):
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir), exclude_skipped=True)
    slot = manager.create_slot(folder.id)
    skipped = slot.current_file

    clock.advance(5)
    manager.press_next(slot.slot_id)

    review = [r.file_path for r in state.get_review_items(folder.id)]
    assert skipped in review
    assert skipped in state.get_excluded_paths(folder.id)  # on BOTH


# -- exactly-N cooldown semantics -----------------------------------------

def test_cooldown_lasts_exactly_n_future_presses(env):
    """A watched file must sit out exactly N future presses, then be eligible."""
    state, _, manager, clock, _, media_dir, files = env
    N = 3
    folder = state.add_folder("Thriller", str(media_dir), shuffle_count=N)
    slot = manager.create_slot(folder.id)

    # Force a known watched file X, then drive presses deterministically.
    X = files[0]
    slot.current_file = X
    slot.load_monotonic = clock() - 120     # elapsed 120s -> watched on next press

    r0 = manager.press_next(slot.slot_id)     # press 0: X watched, excluded=N
    assert r0.classification is Classification.WATCHED
    assert state.get_exclusions(folder.id)[X] == N        # count == 3 (not 2)

    # Subsequent presses are quick (skipped, toggle off) so only X stays excluded.
    clock.advance(1); manager.press_next(slot.slot_id)    # press 1: X 3->2
    assert state.get_exclusions(folder.id).get(X) == 2
    clock.advance(1); manager.press_next(slot.slot_id)    # press 2: X 2->1
    assert state.get_exclusions(folder.id).get(X) == 1
    clock.advance(1); r3 = manager.press_next(slot.slot_id)  # press 3: X 1->0 released
    assert X not in state.get_excluded_paths(folder.id)
    assert X in r3.released_files                          # eligible again on 3rd press


# -- per-folder sharing across slots --------------------------------------

def test_shared_folder_cooldown_decrements_across_slots(env):
    state, _, manager, clock, _, media_dir, files = env
    folder = state.add_folder("Thriller", str(media_dir), shuffle_count=10)
    slot_a = manager.create_slot(folder.id)
    slot_b = manager.create_slot(folder.id)      # second slot, SAME folder

    # Exclude a known file via slot A watching it.
    X = files[0]
    slot_a.current_file = X
    slot_a.load_monotonic = clock() - 120
    manager.press_next(slot_a.slot_id)           # X excluded at 10
    assert state.get_exclusions(folder.id)[X] == 10

    # A Next on slot B (same folder) must decrement the shared cooldown.
    clock.advance(1)
    manager.press_next(slot_b.slot_id)
    assert state.get_exclusions(folder.id)[X] == 9


def test_slots_load_into_their_own_windows(env):
    state, _, manager, clock, fakes, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot_a = manager.create_slot(folder.id)
    slot_b = manager.create_slot(folder.id)
    fake_a = fakes[slot_a.port]
    fake_b = fakes[slot_b.port]

    before_b = len(fake_b.loaded)
    clock.advance(120)
    manager.press_next(slot_a.slot_id)           # act on slot A only

    assert fake_a.current == slot_a.current_file # A advanced
    assert len(fake_b.loaded) == before_b        # B untouched


# -- edge cases ------------------------------------------------------------

def test_empty_pool_when_all_excluded(env):
    state, _, manager, clock, _, media_dir, files = env
    folder = state.add_folder("Thriller", str(media_dir), shuffle_count=100)
    slot = manager.create_slot(folder.id)
    # Exclude every file with a long cooldown.
    for f in files:
        state.add_exclusion(folder.id, f, 100)
    slot.current_file = None            # nothing to evaluate
    slot.load_monotonic = None
    result = manager.press_next(slot.slot_id)
    assert result.status is NextStatus.EMPTY_POOL


def test_no_folder_assigned(env):
    state, _, manager, _, _, media_dir, _ = env
    state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot()        # no folder
    result = manager.press_next(slot.slot_id)
    assert result.status is NextStatus.NO_FOLDER


def test_reassign_folder_switches_source(env):
    state, _, manager, clock, _, tmp_media, _ = env
    f1 = state.add_folder("Thriller", str(tmp_media))
    # second folder
    other = tmp_media.parent / "Comedy"
    other.mkdir()
    (other / "c0.mp4").write_bytes(b"x")
    f2 = state.add_folder("Comedy", str(other))

    slot = manager.create_slot(f1.id)
    manager.assign_folder(slot.slot_id, f2.id)
    assert slot.folder_id == f2.id
    assert slot.current_file.endswith("c0.mp4")   # now sourcing from Comedy


def test_close_slot_stops_vlc_and_frees_port(env):
    state, _, manager, _, fakes, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot(folder.id)
    port = slot.port
    fake = fakes[port]
    manager.close_slot(slot.slot_id)
    assert fake.stopped
    assert manager.get_slot(slot.slot_id) is None
    # Port should be reusable by a new slot.
    new_slot = manager.create_slot(folder.id)
    assert new_slot.port == port


# -- native-control detection (VLC's own Next / end-of-media) --------------

def test_native_next_jumps_instantly_to_queued_pick(env):
    """VLC's own Next must adopt the already-playing queued pick (no reload)."""
    state, _, manager, clock, fakes, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot(folder.id)
    fake = fakes[slot.port]

    orig = slot.current_file
    pending = slot.pending_file
    assert pending is not None and pending != orig   # a real next pick was queued
    loads_before = len(fake.loaded)

    # Within the grace window, even a playlist change must NOT advance.
    fake.simulate_native_next()
    assert manager.poll_and_maybe_advance(slot.slot_id) is None
    assert slot.current_file == orig

    # Past the grace window: adopt the queued file — instant, with NO new load.
    clock.advance(2)
    result = manager.poll_and_maybe_advance(slot.slot_id)
    assert result is not None and result.status is NextStatus.OK
    assert slot.current_file == pending              # jumped straight to the queued pick
    assert result.previous_file == orig
    assert len(fake.loaded) == loads_before          # nothing was reloaded
    assert slot.pending_file is not None             # next pick queued for the next Next
    assert slot.pending_file != slot.current_file


def test_native_next_records_history_like_a_press(env):
    """Adopting the queued pick still evaluates the file that just ended."""
    state, _, manager, clock, fakes, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))  # thr=60, toggle off
    slot = manager.create_slot(folder.id)
    orig = slot.current_file

    clock.advance(120)                               # watched
    fake = fakes[slot.port]
    fake.simulate_native_next()
    result = manager.poll_and_maybe_advance(slot.slot_id)

    assert result.classification is Classification.WATCHED
    assert orig in state.get_excluded_paths(folder.id)   # cooldown recorded


def test_poll_no_trigger_while_playing_unchanged(env):
    """Seeking/pausing keep the playlist id — the poller must not advance."""
    state, _, manager, clock, fakes, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot(folder.id)
    fake = fakes[slot.port]
    current_before = slot.current_file

    clock.advance(30)                              # plenty past grace, id unchanged
    assert manager.poll_and_maybe_advance(slot.slot_id) is None
    assert slot.current_file == current_before


# -- Personal Algorithm integration ---------------------------------------

def test_tracking_runs_even_when_algorithm_off(env):
    """Affinity + Rediscovery are written on every Next press, toggle OFF."""
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))  # personal_algorithm OFF
    slot = manager.create_slot(folder.id)
    watched = slot.current_file

    clock.advance(400)                             # >= a reward tier and >= threshold -> WATCHED
    manager.press_next(slot.slot_id)

    # Watched file gained Affinity; newly-picked file had its Rediscovery reset.
    assert state.get_preference(folder.id, watched).affinity_score > 0
    picked = slot.current_file
    assert state.get_preference(folder.id, picked).last_selected_at is not None


def test_initial_load_does_not_touch_preferences(env):
    """assign_folder/_initial_load must not create or mutate any preference row."""
    state, _, manager, _, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    manager.create_slot(folder.id)                 # triggers the initial load
    assert state.get_preferences_for_folder(folder.id) == {}


def test_skip_penalty_reduces_affinity_floored_at_zero(env):
    state, _, manager, clock, _, media_dir, files = env
    folder = state.add_folder("Thriller", str(media_dir), skip_threshold=60)
    slot = manager.create_slot(folder.id)

    # First, watch the current file long enough to earn affinity.
    X = slot.current_file
    clock.advance(700)
    manager.press_next(slot.slot_id)
    earned = state.get_preference(folder.id, X).affinity_score
    assert earned > 0

    # Now force X back as current and skip it: affinity must drop but stay >= 0.
    slot.current_file = X
    slot.load_monotonic = clock() - 5              # elapsed 5s < 60 -> SKIPPED
    manager.press_next(slot.slot_id)
    after = state.get_preference(folder.id, X).affinity_score
    assert 0 <= after < earned


def test_algorithm_on_selects_and_still_tracks(env):
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir), personal_algorithm=True)
    slot = manager.create_slot(folder.id)
    clock.advance(5)
    result = manager.press_next(slot.slot_id)
    assert result.status is NextStatus.OK
    assert slot.current_file is not None
    assert state.get_preference(folder.id, slot.current_file).last_selected_at is not None


def test_switching_on_midsession_preserves_prior_history(env):
    """The key behaviour: history accrued while OFF is used immediately when ON."""
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))  # OFF
    slot = manager.create_slot(folder.id)

    for _ in range(3):                             # accumulate affinity while OFF
        clock.advance(700)
        manager.press_next(slot.slot_id)
    before = state.get_preferences_for_folder(folder.id)
    total_before = sum(p.affinity_score for p in before.values())
    assert total_before > 0

    state.update_folder(folder.id, personal_algorithm=True)   # flip ON

    after = state.get_preferences_for_folder(folder.id)
    assert sum(p.affinity_score for p in after.values()) == total_before  # no reset/backfill

    clock.advance(700)                             # a weighted press now works fine
    result = manager.press_next(slot.slot_id)
    assert result.status in (NextStatus.OK, NextStatus.EMPTY_POOL)


# -- skip-repeat bug fix ---------------------------------------------------

def test_next_never_returns_the_just_skipped_file(env):
    """Regression: a single Next after a skip must move to a DIFFERENT file.

    Skipped files (toggle off) are not put on the persistent cooldown, so the
    draw used to be able to re-pick the file just skipped. Next must never hand
    back the currently-playing file.
    """
    state, _, manager, clock, _, media_dir, files = env
    folder = state.add_folder("Thriller", str(media_dir))  # toggle off, default
    slot = manager.create_slot(folder.id)

    X = files[0]
    slot.current_file = X
    slot.load_monotonic = clock() - 5              # 5s < 60 -> SKIPPED
    result = manager.press_next(slot.slot_id)

    assert result.classification is Classification.SKIPPED
    assert result.status is NextStatus.OK
    assert result.selected_file != X               # not the file we just skipped
    assert slot.current_file != X


def test_repeated_next_never_repeats_consecutively(env):
    """Across many quick skips, no two consecutive picks are the same file."""
    state, _, manager, clock, _, media_dir, _ = env
    folder = state.add_folder("Thriller", str(media_dir))
    slot = manager.create_slot(folder.id)

    prev = slot.current_file
    for _ in range(25):
        clock.advance(2)                           # quick -> skipped, nothing persists
        result = manager.press_next(slot.slot_id)
        assert result.status is NextStatus.OK
        assert result.selected_file != prev        # always advances to a new file
        prev = result.selected_file


def test_single_file_folder_can_still_replay(env):
    """The no-repeat guard must not stall a folder that has only one file."""
    state, library, manager, clock, _, media_dir, _ = env
    lone = media_dir.parent / "Solo"
    lone.mkdir()
    only = str((lone / "only.mp4").resolve())
    (lone / "only.mp4").write_bytes(b"x")
    folder = state.add_folder("Solo", str(lone))
    slot = manager.create_slot(folder.id)
    assert slot.current_file == only

    clock.advance(2)
    result = manager.press_next(slot.slot_id)      # nothing else to pick
    assert result.status is NextStatus.OK
    assert result.selected_file == only            # falls back to the lone file


# -- Favorites genre -------------------------------------------------------

def test_favorites_genre_plays_only_favorites(env):
    """A slot on the virtual Favorites genre plays only favorited files."""
    state, _, manager, clock, _, media_dir, files = env
    genre = state.add_folder("Thriller", str(media_dir))
    fav_folder = state.ensure_favorites_folder()

    # Favorite three of the six files.
    favs = set(files[:3])
    for p in favs:
        state.add_favorite(p, genre.id)

    slot = manager.create_slot(fav_folder.id)
    assert slot.current_file in favs               # initial load is a favorite

    for _ in range(15):
        clock.advance(2)
        result = manager.press_next(slot.slot_id)
        if result.status is NextStatus.OK:
            assert result.selected_file in favs    # only ever plays favorites


def test_favorites_genre_empty_when_nothing_favorited(env):
    state, _, manager, _, _, media_dir, _ = env
    state.add_folder("Thriller", str(media_dir))
    fav_folder = state.ensure_favorites_folder()
    slot = manager.create_slot(fav_folder.id)
    # No favorites yet -> nothing to load.
    assert slot.current_file is None
    result = manager.press_next(slot.slot_id)
    assert result.status is NextStatus.EMPTY_POOL


def test_favorite_in_normal_genre_gets_picked(env):
    """A favorited file keeps a (boosted) presence when its own genre plays."""
    state, _, manager, clock, _, media_dir, files = env
    genre = state.add_folder("Thriller", str(media_dir))
    state.add_favorite(files[0], genre.id)         # one favorite in a 6-file genre
    slot = manager.create_slot(genre.id)

    seen = set()
    for _ in range(60):
        clock.advance(2)                           # quick skips: nothing persists
        result = manager.press_next(slot.slot_id)
        if result.selected_file:
            seen.add(result.selected_file)
    # The favorite (weight 3 vs 1) is overwhelmingly likely to appear; and the
    # genre still surfaces other files too (no starvation).
    assert files[0] in seen
    assert len(seen) > 1


# -- cloud genres (Internet Archive) --------------------------------------

def _cloud_env(tmp_path, item_files):
    """A SlotManager wired to a fake cloud library (no network)."""
    state = StateStore(tmp_path / "cloud.db")
    library = MediaLibrary()
    clock = Clock()
    fakes = {}

    def factory(port):
        fakes[port] = FakeVlc(port)
        return fakes[port]

    meta = {ident: {"files": [{"name": n} for n in names]}
            for ident, names in item_files.items()}
    cloud = CloudLibrary(fetcher=lambda ident: meta.get(ident))
    manager = SlotManager(state, library, factory, cloud_library=cloud, clock=clock)
    return state, manager, clock


def test_cloud_folder_streams_archive_urls(tmp_path):
    state, manager, clock = _cloud_env(
        tmp_path, {"my_item": [f"movie_{i}.mp4" for i in range(4)]})
    folder = state.add_folder("Cloud", "my_item", source_type="cloud")
    slot = manager.create_slot(folder.id)

    prefix = "https://archive.org/download/my_item/"
    assert slot.current_file.startswith(prefix)   # initial load streams a URL
    for _ in range(10):
        clock.advance(2)
        result = manager.press_next(slot.slot_id)
        assert result.status is NextStatus.OK
        assert result.selected_file.startswith(prefix)
        assert result.selected_file != result.previous_file  # no-repeat holds for URLs
    state.close()


def test_favoriting_a_streamed_movie_and_playing_favorites(tmp_path):
    state, manager, clock = _cloud_env(tmp_path, {"my_item": ["a.mp4", "b.mp4"]})
    cloud_folder = state.add_folder("Cloud", "my_item", source_type="cloud")
    slot = manager.create_slot(cloud_folder.id)
    url = slot.current_file
    assert url.startswith("https://archive.org/download/my_item/")

    # Favorite the streamed movie, then play the Favorites genre.
    state.add_favorite(url, cloud_folder.id)
    fav_folder = state.ensure_favorites_folder()
    fav_slot = manager.create_slot(fav_folder.id)
    assert fav_slot.current_file == url            # Favorites plays the cloud URL
    state.close()
