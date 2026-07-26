"""
Selection Engine — the core value of this project.

VLC's native shuffle repeatedly replays a handful of files and starves large
parts of a library. This engine instead performs a *genuinely uniform* random
pick over the exact set of currently-eligible files, so every eligible item has
identical probability of being chosen on any given ``Next`` press.

Design guarantees
-----------------
1. **Uniformity.** Selection uses ``random.SystemRandom`` (OS CSPRNG) and a
   single ``randrange(len(pool))`` index draw. There is no weighting, no
   dependence on list order, insertion order, or history — only pool membership.
2. **Purity.** The pick function is a pure function of (pool, rng). It performs
   no I/O and no persistence, which makes it exhaustively unit- and
   statistically testable (see tests/test_selection_engine.py).
3. **Correct pool at the moment of the press.** Eligibility is recomputed every
   call from live inputs: files that belong to the folder, still exist on disk,
   and are not currently on the exclusion list.

The RNG is injectable purely so tests can supply a seeded ``random.Random`` for
determinism; production always uses the system CSPRNG.
"""
from __future__ import annotations

import logging
import random
from typing import Callable, Iterable, Optional, Sequence

from .media_library import file_exists

logger = logging.getLogger(__name__)

# Module-level system RNG (OS entropy source). Not seedable — exactly what we
# want for unbiased production selection.
_SYSTEM_RNG = random.SystemRandom()


def build_eligible_pool(
    folder_files: Iterable[str],
    excluded: Iterable[str],
    exists_check: Callable[[str], bool] = file_exists,
) -> list[str]:
    """Construct the eligible pool for a single ``Next`` press.

    A file is eligible iff it belongs to the folder, is **not** on the exclusion
    list, and still exists on disk. De-duplicates while preserving discovery
    order (order does not affect the uniform pick; it only makes results stable
    and debuggable).

    Parameters
    ----------
    folder_files : the folder's known media files (from the media library cache).
    excluded     : paths currently on this folder's exclusion list.
    exists_check : existence predicate (injectable for tests).
    """
    excluded_set = set(excluded)
    pool: list[str] = []
    seen: set[str] = set()
    for path in folder_files:
        if path in seen or path in excluded_set:
            continue
        seen.add(path)
        if exists_check(path):
            pool.append(path)
    return pool


def pick_uniform(
    pool: Sequence[str],
    rng: Optional[random.Random] = None,
) -> Optional[str]:
    """Return one element chosen uniformly at random, or None if *pool* is empty.

    Uses a single index draw over the exact pool. Every element has probability
    ``1 / len(pool)``.
    """
    n = len(pool)
    if n == 0:
        return None
    r = rng or _SYSTEM_RNG
    return pool[r.randrange(n)]


def select(
    folder_files: Iterable[str],
    excluded: Iterable[str],
    exists_check: Callable[[str], bool] = file_exists,
    rng: Optional[random.Random] = None,
) -> Optional[str]:
    """Convenience: build the eligible pool then pick one item uniformly.

    Returns None when no eligible file exists (empty pool). The caller decides
    how to surface that to the user (e.g. leave the current file playing).
    """
    pool = build_eligible_pool(folder_files, excluded, exists_check)
    choice = pick_uniform(pool, rng)
    if choice is None:
        logger.info("Eligible pool is empty; no selection made.")
    return choice
