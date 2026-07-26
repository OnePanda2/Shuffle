"""
Preference Engine — the Personal Algorithm (Affinity + Rediscovery).

Pure and I/O-free, exactly like selection_engine.py: no database access, no VLC
access, every function a pure function of its explicit arguments plus config
constants, and an injectable RNG that defaults to random.SystemRandom in
production. This makes the whole weighting scheme exhaustively unit-testable.

Two signals combine into a per-file selection weight when a folder has the
Personal Algorithm turned ON:

* Affinity — long-term, cumulative "genuine enjoyment". No ceiling, no
  diminishing returns, floored at zero. Grows with watch duration, shrinks a
  little on each skip. Recency is deliberately NOT Affinity's concern.
* Rediscovery — "gravity, not magnetism". Measures neglect: 0 for a file just
  selected (or never selected), growing non-linearly the longer a file goes
  unselected. It is a pure function of time-since-last-selected, so it is never
  stored and never drifts.

Locked weight formula:
    Weight = BASE_WEIGHT + AffinityContribution + RediscoveryContribution
The flat BASE_WEIGHT is what structurally guarantees every eligible file always
keeps a non-zero, non-deterministic selection probability — no artificial cap on
Affinity is used or wanted.
"""
from __future__ import annotations

import random
from typing import Optional, Sequence

from .config import (
    AFFINITY_SKIP_PENALTY,
    AFFINITY_WATCH_REWARD_TIERS,
    AFFINITY_WEIGHT_MULTIPLIER,
    BASE_WEIGHT,
    REDISCOVERY_GROWTH_EXPONENT,
    REDISCOVERY_SCALE,
    REDISCOVERY_WEIGHT_MULTIPLIER,
)
from .state_store import MediaPreference

# Same production RNG discipline as selection_engine: OS CSPRNG, not seedable.
_SYSTEM_RNG = random.SystemRandom()

_SECONDS_PER_DAY = 86400.0


def compute_watch_reward(elapsed_seconds: float) -> float:
    """Return the Affinity reward for a WATCHED file of the given watch duration.

    Looks up AFFINITY_WATCH_REWARD_TIERS and returns the reward of the highest
    tier whose min-elapsed threshold is met, or 0.0 if the watch is below the
    lowest tier. Robust to tier ordering.
    """
    reward = 0.0
    best_min = -1.0
    for min_elapsed, amount in AFFINITY_WATCH_REWARD_TIERS:
        if elapsed_seconds >= min_elapsed and min_elapsed > best_min:
            best_min = min_elapsed
            reward = amount
    return reward


def compute_skip_penalty() -> float:
    """Return the flat Affinity penalty for a SKIPPED file.

    Kept as a function (not a bare constant reference) so a future version can
    make the penalty conditional without changing any call site.
    """
    return AFFINITY_SKIP_PENALTY


def apply_affinity_delta(current_score: float, delta: float) -> float:
    """Apply a change to an Affinity score, enforcing the hard zero floor.

    This is the ONLY place the zero-floor rule lives; both watch rewards (positive
    delta) and skip penalties (negative delta) must flow through here.
    """
    return max(0.0, current_score + delta)


def compute_rediscovery_score(last_selected_at: Optional[float], now: float) -> float:
    """Return the Rediscovery score from time-since-last-selected.

    Neutral (0.0) if never selected. Otherwise a convex, accelerating function of
    days elapsed: (days ** REDISCOVERY_GROWTH_EXPONENT) * REDISCOVERY_SCALE. The
    non-days<=0 guard keeps a fractional power from ever seeing a negative base
    (which Python would turn into a complex number) if a clock skew makes
    last_selected_at appear to be in the future.
    """
    if last_selected_at is None:
        return 0.0
    days = (now - last_selected_at) / _SECONDS_PER_DAY
    if days <= 0:
        return 0.0
    return (days ** REDISCOVERY_GROWTH_EXPONENT) * REDISCOVERY_SCALE


def compute_contributions(
    affinity_score: float, rediscovery_score: float
) -> list[tuple[str, float]]:
    """Return the named weight contributions from each signal.

    A list of (name, value) specifically so future signals (Like/Dislike, Play
    Count, Skip Streaks, ...) become one new appended entry later, without
    changing this function's signature or any caller.
    """
    return [
        ("affinity", affinity_score * AFFINITY_WEIGHT_MULTIPLIER),
        ("rediscovery", rediscovery_score * REDISCOVERY_WEIGHT_MULTIPLIER),
    ]


def compute_weight(contributions: list[tuple[str, float]]) -> float:
    """Combine the flat base weight with all named contributions (additive).

    The BASE_WEIGHT floor is what makes every weight strictly positive; no other
    safeguard is needed or wanted here.
    """
    return BASE_WEIGHT + sum(value for _, value in contributions)


def select_weighted(
    pool: Sequence[str],
    preferences: dict[str, MediaPreference],
    now: float,
    rng: Optional[random.Random] = None,
) -> Optional[str]:
    """Pick one file from *pool* by a single weighted random draw.

    For each file: look up its MediaPreference (neutral affinity 0 / never
    selected if missing from *preferences*), derive its Rediscovery score at
    *now*, compute its contributions, then its weight. Higher weight only raises
    probability — it never guarantees the pick, and no eligible file can reach a
    zero probability (BASE_WEIGHT floor). Returns None for an empty pool, matching
    selection_engine.select's contract.
    """
    if not pool:
        return None
    r = rng or _SYSTEM_RNG
    weights: list[float] = []
    for path in pool:
        pref = preferences.get(path)
        affinity = pref.affinity_score if pref is not None else 0.0
        last_selected = pref.last_selected_at if pref is not None else None
        rediscovery = compute_rediscovery_score(last_selected, now)
        weights.append(compute_weight(compute_contributions(affinity, rediscovery)))
    return r.choices(list(pool), weights=weights, k=1)[0]
