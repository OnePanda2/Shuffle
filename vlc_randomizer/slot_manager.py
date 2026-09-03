"""
Slot Manager — slot lifecycle and the 8-step "Next" pipeline (the crux).

A *slot* is one app-launched VLC window bound to one folder. Multiple slots may
share a folder; they own independent windows but read/write that folder's shared
history state. Every window is opened and closed only through this manager —
never by the user touching VLC's native controls.

The single most important method here is :meth:`SlotManager.press_next`, which
implements the locked 8-step sequence.

Ordering decision (documented deliberately)
------------------------------------------
The spec lists, per ``Next`` press: (2/3) add the just-evaluated file to the
exclusion list with count N, then (4) decrement *every* excluded file by 1, then
(5) release those at 0. Taken literally, the file added in this same press would
be decremented immediately to N-1.

That contradicts the spec's own HARD CONSTRAINT: "N = number of future Next
presses within that folder before the file becomes eligible again." A hard
constraint outranks step ordering, so we apply the cooldown tick to the
*pre-existing* exclusions first, then add the just-evaluated file with a full
count of N. Result: the file sits out exactly N subsequent presses. This is the
only place the literal step order is reinterpreted, it is isolated to one method,
and flipping it back would be a two-line change.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from . import preference_engine
from .config import AUTO_ADVANCE_GRACE_SECONDS, HTTP_PORT_BASE, MAX_SLOTS
from .media_library import MediaLibrary
from .selection_engine import build_eligible_pool, pick_uniform
from .state_store import StateStore
from .vlc_controller import VlcError, VlcInstance

logger = logging.getLogger(__name__)


class Classification(str, Enum):
    """How the previously-playing file was judged on a Next press."""
    WATCHED = "watched"
    SKIPPED = "skipped"
    NONE = "none"        # first press in a slot — nothing was playing


class NextStatus(str, Enum):
    """Outcome of a Next press."""
    OK = "ok"                 # a new file was selected and loaded
    EMPTY_POOL = "empty_pool" # no eligible file to play
    NO_FOLDER = "no_folder"   # slot has no folder assigned
    LOAD_FAILED = "load_failed"
    NO_SLOT = "no_slot"


@dataclass
class NextResult:
    """Structured result of a Next press, for the UI and for tests."""
    status: NextStatus
    selected_file: Optional[str] = None
    previous_file: Optional[str] = None
    classification: Classification = Classification.NONE
    released_files: list[str] = field(default_factory=list)
    elapsed_seconds: Optional[float] = None
    message: str = ""


@dataclass
class Slot:
    """Runtime state of one player slot."""
    slot_id: int
    port: int
    vlc: VlcInstance
    folder_id: Optional[int] = None
    current_file: Optional[str] = None
    load_monotonic: Optional[float] = None  # clock value when current_file loaded
    expected_plid: Optional[int] = None     # VLC playlist id of the file we loaded


# A factory that builds (but does not launch) a VlcInstance for a given port.
VlcFactory = Callable[[int], VlcInstance]


class SlotManager:
    """Owns all slots, their ports, and the Next pipeline.

    Dependencies are injected so the pipeline can be unit-tested with fake VLC
    instances and a controllable clock.
    """

    def __init__(
        self,
        state: StateStore,
        library: MediaLibrary,
        vlc_factory: VlcFactory,
        max_slots: int = MAX_SLOTS,
        port_base: int = HTTP_PORT_BASE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._state = state
        self._library = library
        self._vlc_factory = vlc_factory
        self._max_slots = max_slots
        self._port_base = port_base
        self._clock = clock
        self._slots: dict[int, Slot] = {}
        self._next_slot_id = 1

    # -- introspection -----------------------------------------------------

    @property
    def slots(self) -> list[Slot]:
        return list(self._slots.values())

    @property
    def active_count(self) -> int:
        return len(self._slots)

    @property
    def can_add_slot(self) -> bool:
        return len(self._slots) < self._max_slots

    def get_slot(self, slot_id: int) -> Optional[Slot]:
        return self._slots.get(slot_id)

    def elapsed_for(self, slot_id: int) -> Optional[float]:
        """Wall-clock elapsed time (pause-inclusive) since the current file loaded."""
        slot = self._slots.get(slot_id)
        if slot is None or slot.load_monotonic is None:
            return None
        return self._clock() - slot.load_monotonic

    # -- slot lifecycle ----------------------------------------------------

    def create_slot(self, folder_id: Optional[int] = None) -> Slot:
        """Launch a new VLC window as a slot, optionally assigning a folder.

        Raises RuntimeError if the slot cap is reached, VlcError if VLC fails
        to launch.
        """
        if not self.can_add_slot:
            raise RuntimeError(f"Slot limit reached ({self._max_slots})")

        port = self._allocate_port()
        vlc = self._vlc_factory(port)
        try:
            vlc.launch()
        except VlcError:
            logger.exception("Slot creation failed on port %d", port)
            raise

        slot = Slot(slot_id=self._next_slot_id, port=port, vlc=vlc)
        self._slots[slot.slot_id] = slot
        self._next_slot_id += 1
        logger.info("Created slot %d on port %d", slot.slot_id, port)

        if folder_id is not None:
            self.assign_folder(slot.slot_id, folder_id)
        return slot

    def assign_folder(self, slot_id: int, folder_id: int) -> Slot:
        """Assign (or re-point) a slot to a folder and load an initial file.

        Loading a first random pick immediately is a UX choice: the window shows
        media right away rather than a black screen. It performs no evaluation
        and mutates no history — the first *Next* press is what evaluates it.
        Re-pointing mid-session simply switches folders; the outgoing file is not
        evaluated, since a folder change is not a Next press.
        """
        slot = self._require_slot(slot_id)
        slot.folder_id = folder_id
        slot.current_file = None
        slot.load_monotonic = None
        folder = self._state.get_folder(folder_id)
        self._initial_load(slot, folder)
        return slot

    def close_slot(self, slot_id: int) -> None:
        """Close a slot's VLC window and free its port. App-managed only."""
        slot = self._slots.pop(slot_id, None)
        if slot is None:
            return
        slot.vlc.stop()
        logger.info("Closed slot %d (port %d)", slot_id, slot.port)

    def shutdown(self) -> None:
        """Close every slot (called on application exit)."""
        for slot_id in list(self._slots.keys()):
            self.close_slot(slot_id)

    # -- the Next pipeline (the crux) -------------------------------------

    def press_next(self, slot_id: int) -> NextResult:
        """Execute the locked 8-step Next sequence for one slot.

        See the module docstring for the ordering rationale on steps 2-5.
        """
        slot = self._slots.get(slot_id)
        if slot is None:
            return NextResult(NextStatus.NO_SLOT, message="Unknown slot")
        if slot.folder_id is None:
            return NextResult(NextStatus.NO_FOLDER,
                              message="Assign a folder to this slot first")

        # Re-read folder settings every press so live setting changes take effect.
        folder = self._state.get_folder(slot.folder_id)
        previous = slot.current_file
        elapsed: Optional[float] = None
        classification = Classification.NONE

        # STEP 1 — evaluate the previously-playing file by pause-inclusive
        # wall-clock elapsed time since it was loaded.
        if previous is not None and slot.load_monotonic is not None:
            elapsed = self._clock() - slot.load_monotonic
            if elapsed < folder.skip_threshold:
                classification = Classification.SKIPPED
            else:
                classification = Classification.WATCHED

        # +++ ADDED (Personal Algorithm) — AFFINITY TRACKING. Distinct from the
        # locked 8 steps. Runs UNCONDITIONALLY on every Next press for every folder,
        # regardless of folder.personal_algorithm (only the STEP-7 selection below
        # is gated by the toggle). Piggybacks on the step-1 classification and the
        # SAME `elapsed` value — no new timer, no new measurement.
        if classification is Classification.WATCHED:
            self._update_affinity(folder.id, previous,
                                  preference_engine.compute_watch_reward(elapsed))
        elif classification is Classification.SKIPPED:
            self._update_affinity(folder.id, previous,
                                  -preference_engine.compute_skip_penalty())
        # Classification.NONE => nothing was playing, so there is no file to update.

        # STEPS 4 & 5 — cooldown tick on this folder FIRST (see module docstring):
        # decrement all pre-existing exclusions, release any that reach 0.
        released = self._state.apply_cooldown_tick(folder.id)

        # STEPS 2 & 3 — record the just-evaluated file into the appropriate lists
        # with a full count of N (so it sits out exactly N future presses).
        if classification is Classification.SKIPPED:
            # Skipped files ALWAYS go to the review list...
            self._state.add_to_review(folder.id, previous, reason="skipped")
            # ...and only enter the cooldown if this folder opts in.
            if folder.exclude_skipped:
                self._state.add_exclusion(folder.id, previous, folder.shuffle_count)
        elif classification is Classification.WATCHED:
            self._state.add_exclusion(folder.id, previous, folder.shuffle_count)

        # STEP 6 — build the eligible pool: folder files, on disk, not excluded.
        files = self._folder_files(folder)
        excluded = self._state.get_excluded_paths(folder.id)

        # BUGFIX — never hand back the file that was just playing. Pressing Next
        # (or a native advance) must always move to a DIFFERENT file. A *watched*
        # file is already on the exclusion list by now, but a *skipped* file is
        # intentionally NOT (that stays gated by exclude_skipped), so without this
        # guard the draw could immediately re-pick the file you just skipped — the
        # "had to press Next 2-3 times before it changed" bug. We exclude it for
        # THIS pick only; nothing is persisted, so cooldown semantics are untouched.
        pick_excluded = excluded | {previous} if previous is not None else excluded

        # STEP 7 — pick one file. The Personal Algorithm toggle and favorites both
        # feed into this single selection seam (see _select_for_folder).
        choice = self._select_for_folder(folder, files, pick_excluded)
        if choice is None and previous is not None:
            # Excluding the just-played file emptied the pool (e.g. a single-file
            # folder, or everything else on cooldown): allow it back rather than
            # stalling, so a lone file can still replay.
            choice = self._select_for_folder(folder, files, excluded)
        if choice is None:
            # Nothing eligible: keep the window as-is but stop tracking the old
            # file so the next press does not re-evaluate it.
            slot.current_file = None
            slot.load_monotonic = None
            return NextResult(
                NextStatus.EMPTY_POOL, previous_file=previous,
                classification=classification, released_files=released,
                elapsed_seconds=elapsed,
                message="No eligible media to play (all excluded or missing).",
            )

        # +++ ADDED (Personal Algorithm) — REDISCOVERY RESET. Distinct from the
        # locked 8 steps. Runs UNCONDITIONALLY in BOTH branches above: being picked
        # by the algorithm resets a file's neglect clock for the WHOLE folder
        # (folder-scoped, so it affects every slot sharing this folder). The initial
        # load in assign_folder/_initial_load is NOT a selection and never reaches
        # here, so it correctly does not reset Rediscovery.
        self._state.mark_selected(folder.id, choice, time.time())

        # STEP 8 — load the pick into the SAME window (never recreate it).
        if not self._apply_load(slot, choice):
            return NextResult(
                NextStatus.LOAD_FAILED, previous_file=previous,
                classification=classification, released_files=released,
                elapsed_seconds=elapsed,
                message="Selected a file but VLC did not accept the load command.",
            )

        logger.info("Slot %d Next: %s -> %s (%s)", slot_id,
                    previous, choice, classification.value)
        return NextResult(
            NextStatus.OK, selected_file=choice, previous_file=previous,
            classification=classification, released_files=released,
            elapsed_seconds=elapsed,
        )

    # -- internals ---------------------------------------------------------

    def _update_affinity(self, folder_id: int, file_path: str, delta: float) -> None:
        """Apply an Affinity delta to a file and persist it (zero-floored).

        Part of the Personal Algorithm's unconditional tracking. Reads the current
        stored score, applies the delta through preference_engine's zero-floor
        rule, and writes it back.
        """
        current = self._state.get_preference(folder_id, file_path)
        new_score = preference_engine.apply_affinity_delta(current.affinity_score, delta)
        self._state.update_affinity(folder_id, file_path, new_score)

    def _folder_files(self, folder) -> list[str]:
        """Return the media files a folder plays from.

        A normal genre reads the cached recursive disk scan. The virtual Favorites
        genre has no folder on disk — its "files" are the favorited paths from the
        favorites table (existence-filtered downstream by the selection engine).
        """
        if folder.is_favorites:
            return list(self._state.get_favorite_paths())
        return self._library.get_files(folder.path)

    def _select_for_folder(self, folder, files, excluded) -> Optional[str]:
        """Pick one eligible file, applying favorite priority and, if enabled, the
        Personal Algorithm — otherwise a plain uniform draw.

        Selection paths:
        * Personal Algorithm ON  -> weighted by Affinity + Rediscovery + favorites.
        * Algorithm OFF, favorites present in the pool -> weighted by favorites only
          (favorited files get a bonus; everything else stays at BASE_WEIGHT).
        * Algorithm OFF, no favorites in the pool -> pure uniform (unchanged).
        """
        pool = build_eligible_pool(files, excluded)
        if not pool:
            return None
        favorites = self._state.get_favorite_paths()
        personalize = folder.personal_algorithm
        if personalize or (favorites & set(pool)):
            preferences = (
                self._state.get_preferences_for_folder(folder.id) if personalize else {}
            )
            return preference_engine.select_weighted(
                pool, preferences, now=time.time(),
                favorites=favorites, personalize=personalize,
            )
        return pick_uniform(pool)

    def _initial_load(self, slot: Slot, folder) -> Optional[str]:
        """Pick and load one random eligible file with no evaluation/mutation."""
        files = self._folder_files(folder)
        excluded = self._state.get_excluded_paths(folder.id)
        choice = self._select_for_folder(folder, files, excluded)
        if choice is None:
            slot.current_file = None
            slot.load_monotonic = None
            slot.expected_plid = None
            logger.info("Slot %d: no eligible file to load initially", slot.slot_id)
            return None
        self._apply_load(slot, choice)
        return choice

    def _apply_load(self, slot: Slot, choice: str) -> bool:
        """Load *choice* into the slot's window and record load time + playlist id.

        The playlist id (captured from VLC right after loading) is the baseline
        the poller compares against to detect a native Next / end-of-media.
        """
        if not slot.vlc.load(choice):
            return False
        slot.current_file = choice
        slot.load_monotonic = self._clock()
        status = slot.vlc.get_status()
        slot.expected_plid = status.plid if status is not None else None
        return True

    def poll_and_maybe_advance(self, slot_id: int) -> Optional[NextResult]:
        """Detect a native VLC Next / end-of-media and auto-advance to a new pick.

        Called on the UI poll timer. Returns a NextResult if it auto-advanced,
        else None. The signal is a change in VLC's current playlist id away from
        the file we loaded (or a stopped state) — validated against real VLC to be
        unambiguous versus seeking or pausing, which keep the id unchanged.
        """
        slot = self._slots.get(slot_id)
        if (slot is None or slot.folder_id is None
                or slot.current_file is None or slot.load_monotonic is None):
            return None
        # Ignore the brief transition right after our own load.
        if (self._clock() - slot.load_monotonic) < AUTO_ADVANCE_GRACE_SECONDS:
            return None

        status = slot.vlc.get_status()
        if status is None:
            return None  # unreachable; liveness is surfaced separately by the UI

        # Establish the baseline on the first settled poll. Right after a load,
        # real VLC may still report currentplid = -1 (media not opened yet), so a
        # None/negative baseline means "not established yet" — adopt the real id
        # once the item is actually playing, rather than mistaking it for a change.
        if slot.expected_plid is None or slot.expected_plid < 0:
            if (status.plid is not None and status.plid >= 0
                    and status.state != "stopped"):
                slot.expected_plid = status.plid
            return None

        stopped = status.state == "stopped"
        moved = status.plid is not None and status.plid != slot.expected_plid
        if not (stopped or moved):
            return None

        logger.info("Slot %d: detected native advance/end -> auto Next", slot_id)
        return self.press_next(slot_id)

    def _require_slot(self, slot_id: int) -> Slot:
        slot = self._slots.get(slot_id)
        if slot is None:
            raise KeyError(f"No slot with id {slot_id}")
        return slot

    def _allocate_port(self) -> int:
        """Return the lowest free port in the slot port range."""
        used = {s.port for s in self._slots.values()}
        for offset in range(self._max_slots):
            port = self._port_base + offset
            if port not in used:
                return port
        raise RuntimeError("No free port available for a new slot")
