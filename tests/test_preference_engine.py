"""
Tests for the Personal Algorithm's pure preference engine.

Values in config are placeholders, so these tests assert on STRUCTURE and
PROPERTIES (ordering, floors, convexity, dominance-without-exclusion) derived
from the config constants — never on hardcoded magic numbers — so retuning the
placeholders never breaks them.
"""
import random

import pytest

from vlc_randomizer import config
from vlc_randomizer.preference_engine import (
    apply_affinity_delta,
    compute_contributions,
    compute_rediscovery_score,
    compute_skip_penalty,
    compute_watch_reward,
    compute_weight,
    select_weighted,
)
from vlc_randomizer.state_store import MediaPreference


# -- affinity reward tiers -------------------------------------------------

def test_reward_zero_below_lowest_tier():
    lowest_min = min(m for m, _ in config.AFFINITY_WATCH_REWARD_TIERS)
    assert compute_watch_reward(lowest_min - 1) == 0.0
    assert compute_watch_reward(0) == 0.0


def test_reward_matches_highest_qualifying_tier_at_boundaries():
    for min_elapsed, amount in config.AFFINITY_WATCH_REWARD_TIERS:
        assert compute_watch_reward(min_elapsed) == amount           # exactly at boundary
        assert compute_watch_reward(min_elapsed + 0.5) == amount     # just above


def test_reward_saturates_at_top_tier():
    top_min, top_amount = max(config.AFFINITY_WATCH_REWARD_TIERS, key=lambda t: t[0])
    assert compute_watch_reward(top_min + 100000) == top_amount


def test_reward_is_non_decreasing_in_watch_time():
    samples = [0, 100, 300, 500, 600, 1200, 2400, 5000]
    rewards = [compute_watch_reward(s) for s in samples]
    assert all(rewards[i] <= rewards[i + 1] for i in range(len(rewards) - 1))


def test_skip_penalty_smaller_than_smallest_reward():
    smallest_reward = min(a for _, a in config.AFFINITY_WATCH_REWARD_TIERS)
    assert compute_skip_penalty() < smallest_reward  # locked invariant


# -- affinity floor + cumulative growth -----------------------------------

def test_affinity_never_goes_below_zero():
    assert apply_affinity_delta(0.0, -compute_skip_penalty()) == 0.0
    assert apply_affinity_delta(0.1, -1000.0) == 0.0
    score = 0.0
    for _ in range(20):                       # repeated skips on a neutral file
        score = apply_affinity_delta(score, -compute_skip_penalty())
    assert score == 0.0


def test_affinity_is_cumulative_without_ceiling():
    score, inc = 0.0, 2.5
    for _ in range(100):
        score = apply_affinity_delta(score, inc)
    assert score == pytest.approx(250.0)      # strictly linear, no saturation


def test_penalty_smaller_than_reward_so_favorites_survive_skips():
    # A watch reward, then many skips: must still take MANY skips to erase it.
    reward = compute_watch_reward(max(m for m, _ in config.AFFINITY_WATCH_REWARD_TIERS))
    score = apply_affinity_delta(0.0, reward)
    skips_to_zero = 0
    while score > 0 and skips_to_zero < 10000:
        score = apply_affinity_delta(score, -compute_skip_penalty())
        skips_to_zero += 1
    assert skips_to_zero > 1                   # not a one-or-two-skip collapse


# -- rediscovery -----------------------------------------------------------

def test_rediscovery_neutral_when_never_selected():
    assert compute_rediscovery_score(None, 1_000_000.0) == 0.0


def test_rediscovery_zero_or_neutral_at_or_before_selection():
    now = 1_000_000.0
    assert compute_rediscovery_score(now, now) == 0.0          # just selected
    assert compute_rediscovery_score(now + 5000, now) == 0.0   # future -> guarded to 0


def test_rediscovery_monotonic_and_convex():
    now = 2_000_000_000.0
    day = 86400.0
    # Monotonic increasing with neglect.
    ages = [1, 7, 30, 90, 180, 365]
    scores = [compute_rediscovery_score(now - a * day, now) for a in ages]
    assert all(scores[i] < scores[i + 1] for i in range(len(scores) - 1))
    # Convex (accelerating): second differences positive on equal spacing.
    xs = [10, 20, 30, 40, 50]
    ys = [compute_rediscovery_score(now - x * day, now) for x in xs]
    first_diffs = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    assert all(first_diffs[i] < first_diffs[i + 1] for i in range(len(first_diffs) - 1))
    # "Barely moves at 1 day, much larger by 6 months."
    assert compute_rediscovery_score(now - 1 * day, now) * 50 < \
           compute_rediscovery_score(now - 180 * day, now)


# -- weight formula --------------------------------------------------------

def test_weight_includes_base_even_with_zero_contributions():
    w = compute_weight([("affinity", 0.0), ("rediscovery", 0.0)])
    assert w == config.BASE_WEIGHT
    assert w > 0


def test_weight_is_additive():
    w = compute_weight([("affinity", 5.0), ("rediscovery", 2.0)])
    assert w == config.BASE_WEIGHT + 7.0


def test_contributions_are_named_and_extensible():
    contribs = compute_contributions(4.0, 3.0)
    names = [n for n, _ in contribs]
    assert "affinity" in names and "rediscovery" in names
    assert isinstance(contribs, list)  # list so future signals just append


# -- weighted selection ----------------------------------------------------

def test_select_weighted_empty_pool_returns_none():
    assert select_weighted([], {}, now=1000.0) is None


def test_select_weighted_favours_high_affinity_but_never_starves():
    """Extreme Affinity dominates, yet every eligible file keeps a real chance."""
    pool = [f"f{i}" for i in range(6)]
    prefs = {"f0": MediaPreference("f0", 1000.0, None)}   # f0 hugely preferred
    rng = random.Random(20240524)
    draws = 30_000
    counts = {p: 0 for p in pool}
    for _ in range(draws):
        counts[select_weighted(pool, prefs, now=1000.0, rng=rng)] += 1

    others = sum(counts[p] for p in pool if p != "f0")
    assert counts["f0"] > others * 5            # disproportionately picked
    assert counts["f0"] < draws                 # but NOT deterministic
    assert all(counts[p] > 0 for p in pool)     # no eligible file has zero probability


def test_select_weighted_uniform_when_no_preferences():
    """With no stored preferences, weights are all BASE_WEIGHT (roughly uniform)."""
    pool = [f"f{i}" for i in range(5)]
    rng = random.Random(7)
    counts = {p: 0 for p in pool}
    for _ in range(20_000):
        counts[select_weighted(pool, {}, now=1000.0, rng=rng)] += 1
    # Every file within a loose band of the uniform expectation (4000).
    assert all(2800 < c < 5200 for c in counts.values())
