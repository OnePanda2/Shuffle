# Smart VLC Randomizer — Developer Documentation

A local, offline Windows companion for VLC that provides a genuinely uniform,
unbiased shuffle across a large media library and manages per-folder watch
history. **It is a selector/controller only — VLC is always the sole media
player. This app never decodes or renders media.**

---

## 1. Module responsibilities

Modules are ordered low-level → high-level; each depends only on those above it.

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Immutable defaults, filesystem paths, supported media extensions, VLC auto-detection. | — |
| `logging_setup.py` | Rotating file + console logging (idempotent). | — |
| `media_library.py` | Recursive, error-tolerant folder scanning; extension filtering; per-folder scan caching; explicit rescan. Pure filesystem I/O. | config |
| `state_store.py` | SQLite persistence + data model: folders, exclusion list, review list, app settings. Thread-safe; corruption-recovering. | config |
| `selection_engine.py` | **The core moat.** Eligible-pool construction + uniform random pick. Pure, I/O-free, statistically tested. | media_library |
| `vlc_controller.py` | Launch/control one VLC process over its local HTTP interface: load a file, read status, terminate. | config |
| `slot_manager.py` | Slot lifecycle (create/assign/close) + **the 8-step Next pipeline**. Orchestrates state + selection + VLC. | all above |
| `file_ops.py` | Send-to-Recycle-Bin deletion (recoverable). | — |
| `ui/theme.py` | Neo-brutalist theming: dark/light palettes, QSS builder, `ShadowBox` (hard offset shadow), and the `ThemeManager` (apply/toggle/persist). Presentation only. | — |
| `app.py` | Composition root: builds `AppContext`, wires everything, starts Qt. | all |
| `ui/` | PySide6 UI: main window, slot widgets, settings, review panel. | app context |

**Design intent:** the selection engine and state store have zero knowledge of
VLC or slots, so the shuffle logic and history logic are independently testable
and reusable. VLC control has zero knowledge of folders or selection. All
cross-module orchestration lives in `slot_manager` and `app`.

---

## 2. Key design decisions

- **Slot = one app-launched VLC window on its own loopback HTTP port.** Feasibility
  validated: 6 concurrent instances bind independent ports at ~31 MB idle RSS
  each (`MAX_SLOTS = 6`). Multiple slots may share a folder (shared history,
  independent windows).
- **Elapsed time is wall-clock since load (pause-inclusive).** `slot_manager`
  records a `time.monotonic()` value when a file is loaded; elapsed = now − that.
  This satisfies the spec's "track total elapsed time since load, pause-inclusive"
  requirement. VLC's HTTP `time` field (playback position) freezes during pause
  and is therefore *not* used for the watched/skipped decision — it's available
  only for liveness/inspection.
- **Cooldown is exactly N future presses.** The spec's literal step order (add to
  exclusion, *then* decrement all) would decrement the just-added file on the same
  press, giving N−1. That contradicts the spec's HARD CONSTRAINT defining N as
  "future Next presses before eligible again." A hard constraint outranks step
  order, so `press_next` runs the cooldown tick on pre-existing exclusions *first*,
  then adds the just-evaluated file with a full count of N. This is the only
  reinterpretation of the literal order; it is isolated to one method and
  documented there.
- **Initial load on folder-assign.** Assigning a folder immediately loads one
  random eligible file so the window isn't black. This performs no evaluation and
  no state mutation — the first *Next* press is what evaluates it. (Spec left
  first-file behaviour unspecified; this is a pure UX addition.)
- **Native-control detection (v1.1).** VLC's own Next/Previous button and
  end-of-media also trigger a fresh random pick. VLC's HTTP interface is
  poll-only (no event push), so `load()` builds a deliberate **two-item playlist**
  (the file + a queued duplicate of it) and the 1 Hz poll watches VLC's
  `currentplid`. Validated against real VLC: on a *single*-item playlist VLC's
  Next merely *restarts* the current file (no observable change), but with a
  trailing duplicate, Next and end-of-media both advance to a new `currentplid`
  the poller detects — while seeking and pausing keep it unchanged (no false
  triggers). This intentionally **reverses** the original spec's "do not detect
  VLC's native Next/close clicks" rule at the user's explicit request.
- **Persistence = SQLite** (WAL, foreign keys, process-wide lock). Atomic writes,
  no partial-write corruption across the two lists, cheap per-folder queries. A
  corrupt DB file is quarantined and recreated on open (history loss on corruption
  is acceptable per spec).
- **Uniform selection** uses `random.SystemRandom` (OS CSPRNG) and a single index
  draw over the exact live pool — no weighting, no order dependence. The RNG is
  injectable only so tests can seed for determinism.
- **Recycle Bin, not hard delete.** "Delete Permanently" routes through
  `send2trash`.
- **Two lists are separate tables with separate APIs** and are never merged. A
  file can appear on both.

---

## 3. Database schema

SQLite file at `%LOCALAPPDATA%\VLCRandomizer\vlc_randomizer.db` (WAL mode).

```
folders
  id              INTEGER PK
  name            TEXT
  path            TEXT UNIQUE
  shuffle_count   INTEGER   -- N: future Next presses before re-eligible
  skip_threshold  INTEGER   -- seconds
  exclude_skipped INTEGER   -- 0/1
  created_at      REAL

exclusion_list                         -- temporary per-file cooldown
  id              INTEGER PK
  folder_id       INTEGER FK -> folders(id) ON DELETE CASCADE
  file_path       TEXT
  remaining_count INTEGER               -- decrements 1 per Next in this folder
  added_at        REAL
  UNIQUE(folder_id, file_path)

review_list                            -- permanent holding area for skips
  id         INTEGER PK
  folder_id  INTEGER FK -> folders(id) ON DELETE CASCADE
  file_path  TEXT
  reason     TEXT
  added_at   REAL
  UNIQUE(folder_id, file_path)

app_settings                           -- global key/value (e.g. vlc_path)
  key   TEXT PK
  value TEXT

favorites                              -- the user's curated Favorites list
  id         INTEGER PK
  folder_id  INTEGER FK -> folders(id) ON DELETE SET NULL  -- origin genre
  file_path  TEXT UNIQUE                                   -- favorited once, globally
  added_at   REAL

schema_meta                            -- schema version for future migrations
  key TEXT PK, value TEXT
```

The **Favorites genre** is a single row in `folders` with `is_favorites = 1` and a
sentinel `path` (`<favorites>`) that is never scanned on disk; its file list comes
from the `favorites` table instead. It is created once on startup
(`StateStore.ensure_favorites_folder`) and cannot be removed from the UI. It carries
its own settings and its own independent history like any other folder.

`favorites.folder_id` is `ON DELETE SET NULL` (not cascade) so removing a genre keeps
its favorited files — they still play in the Favorites genre.

All history state is scoped by `folder_id`; deleting a folder cascades to *its*
rows only, guaranteeing per-folder isolation. `remaining_count` is a countdown,
never a queue length.

---

## 4. The 8-step Next pipeline (`SlotManager.press_next`)

Scoped to one slot; the crux of the project.

1. **Evaluate** the previously-loaded file using pause-inclusive wall-clock
   elapsed time since it loaded.
2. If `elapsed < skip_threshold` → **SKIPPED**: always add to the folder's review
   list; additionally add to the exclusion list (count N) *iff* the folder's
   "exclude skipped" toggle is on.
3. Else → **WATCHED**: add to the exclusion list (count N).
   *(Steps 2/3's list writes are applied after step 4/5's tick — see §2.)*
4. **Decrement** every pre-existing excluded file's count by 1 (this folder only).
5. **Release** any file whose count reached 0 (delete from exclusion list).
6. **Build the eligible pool**: folder files (cached scan; for the Favorites genre,
   the favorited paths instead) that exist on disk and are not currently excluded.
   The **just-played file is also excluded from this one pick** so a single Next
   always advances to a different file — a *skipped* file is not on the persistent
   cooldown, so without this it could be re-served immediately (the "press Next 2-3
   times" bug). If that leaves the pool empty (e.g. a single-file folder) the guard
   is dropped so a lone file can replay.
7. **Pick one** from that exact pool. Pure uniform by default; if the folder has the
   Personal Algorithm on, or any pool file is a **favorite**, a single weighted draw
   is used instead. Favorites get a flat weight bonus (priority) that applies even
   when the Personal Algorithm is off; every eligible file always keeps a real chance.
8. **Load** it into the *same* VLC window via the `in_play` HTTP command; reset
   the load timestamp. The window is never closed or recreated.

Empty pool, load failure, unassigned folder, and unknown slot are all returned as
typed `NextResult` statuses — the pipeline never throws for these.

---

## 5. Communication flow

```
                 ┌─────────────┐
   user clicks   │  SlotWidget │  (ui/)
   "Next"  ─────▶│             │
                 └──────┬──────┘
                        │ context.slots.press_next(slot_id)
                        ▼
                 ┌─────────────┐   get_folder / add_exclusion /
                 │ SlotManager │──▶ apply_cooldown_tick / add_to_review
                 │  (pipeline) │      ┌───────────────┐
                 │             │◀────▶│  StateStore    │──▶ SQLite (WAL)
                 │             │      └───────────────┘
                 │             │   get_files (cached)
                 │             │──▶ ┌───────────────┐
                 │             │◀───│ MediaLibrary   │──▶ filesystem scan
                 │             │    └───────────────┘
                 │             │   select(pool)
                 │             │──▶ ┌───────────────┐
                 │             │◀───│SelectionEngine │  (uniform pick)
                 │             │    └───────────────┘
                 │             │   vlc.load(file)  (HTTP in_play)
                 │             │──▶ ┌───────────────┐
                 └─────────────┘    │  VlcInstance  │──▶ 127.0.0.1:80XX ──▶ VLC window
                                    └───────────────┘
```

- The UI depends only on `AppContext` (the facade in `app.py`).
- A 1 Hz Qt timer refreshes each slot's elapsed-time/liveness display locally
  (via `SlotManager.elapsed_for` and `VlcInstance.is_process_alive`) — **no HTTP
  polling on the timer**, keeping the app lightweight.
- HTTP traffic to VLC is strictly `127.0.0.1` and works fully offline.

---

## 6. Testing

Run all non-UI tests (no VLC, no GUI required):

```
py -3.10 -m pytest tests/ -q
```

- `test_selection_engine.py` — pool construction + **statistical uniformity**
  (per-bucket 5σ band, order-independence, chi-square goodness-of-fit).
- `test_media_library.py` — recursive scan, extension filter, cache/rescan.
- `test_state_store.py` — folder CRUD, cooldown tick, list separation, per-folder
  isolation, persistence across reopen.
- `test_slot_pipeline.py` — full Next pipeline with a fake VLC + controllable
  clock: classification, exactly-N cooldown, shared-folder decrement, slot
  isolation, edge cases, and **native-control detection** (auto-advance on VLC's
  own Next / end-of-media, grace-window suppression, no false trigger while
  playing).

Manual/real-VLC checks live in the developer scratchpad (feasibility harness and
a real-VLC end-to-end driver) and are run on demand; they launch actual windows.

---

## 7. Extensibility notes

- **New media formats:** add extensions to `VIDEO_EXTENSIONS` / `IMAGE_EXTENSIONS`
  in `config.py`. No logic changes needed.
- **New per-folder settings:** add a column to `folders` (bump `SCHEMA_VERSION`),
  extend the `Folder` dataclass and `FolderEditDialog`.
- **Alternative selection strategy** (e.g. weighting) would be a new function in
  `selection_engine.py`; the pipeline calls a single `select(...)` seam.
- **Slot cap** is the `MAX_SLOTS` constant; ports derive from `HTTP_PORT_BASE`.
```
