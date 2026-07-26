"""
Tests for the Selection Engine — the core moat.

These include a statistical uniformity test: over many draws from a fixed pool,
every item must be selected and observed frequencies must sit within a tight
tolerance band of the uniform expectation. Bounds are deliberately generous
(5 sigma per bucket) so the test essentially never flakes yet would still catch
any real bias (weighting, order dependence, off-by-one pool errors).
"""
import math
import random

import pytest

from vlc_randomizer.selection_engine import (
    build_eligible_pool,
    pick_uniform,
    select,
)


def _always_exists(_):  # test double for the on-disk existence check
    return True


# -- pool construction -----------------------------------------------------

def test_pool_excludes_excluded_files():
    files = ["a", "b", "c", "d"]
    pool = build_eligible_pool(files, excluded={"b", "d"}, exists_check=_always_exists)
    assert set(pool) == {"a", "c"}


def test_pool_drops_missing_files():
    files = ["present1", "missing", "present2"]
    exists = lambda p: p.startswith("present")
    pool = build_eligible_pool(files, excluded=set(), exists_check=exists)
    assert set(pool) == {"present1", "present2"}


def test_pool_dedupes_preserving_order():
    files = ["a", "b", "a", "c", "b"]
    pool = build_eligible_pool(files, excluded=set(), exists_check=_always_exists)
    assert pool == ["a", "b", "c"]


def test_empty_pool_returns_none():
    assert pick_uniform([]) is None
    assert select([], excluded=set(), exists_check=_always_exists) is None


def test_all_excluded_returns_none():
    files = ["a", "b"]
    assert select(files, excluded={"a", "b"}, exists_check=_always_exists) is None


# -- pick mechanics --------------------------------------------------------

def test_pick_is_member_of_pool():
    pool = ["x", "y", "z"]
    for _ in range(200):
        assert pick_uniform(pool) in pool


def test_seeded_pick_is_deterministic():
    pool = list("abcdefgh")
    r1 = random.Random(1234)
    r2 = random.Random(1234)
    seq1 = [pick_uniform(pool, r1) for _ in range(50)]
    seq2 = [pick_uniform(pool, r2) for _ in range(50)]
    assert seq1 == seq2  # same seed -> same sequence (pure function of rng)


# -- statistical uniformity ------------------------------------------------

def test_uniform_distribution_system_rng():
    """Every item selected, and each frequency within 5 sigma of uniform."""
    pool = [f"file_{i}" for i in range(20)]
    draws = 40_000
    k = len(pool)
    counts = {p: 0 for p in pool}
    for _ in range(draws):
        counts[pick_uniform(pool)] += 1

    expected = draws / k
    # Binomial standard deviation for each bucket.
    sigma = math.sqrt(draws * (1 / k) * (1 - 1 / k))

    assert all(c > 0 for c in counts.values()), "some item never selected"
    for path, c in counts.items():
        assert abs(c - expected) <= 5 * sigma, (
            f"{path}: count {c} deviates >5σ from expected {expected:.1f}"
        )


def test_uniformity_independent_of_pool_order():
    """Shuffled input order must not change the (uniform) selection profile."""
    base = [f"f{i}" for i in range(10)]
    draws = 20_000

    def profile(pool):
        counts = {p: 0 for p in pool}
        for _ in range(draws):
            counts[pick_uniform(pool)] += 1
        return counts

    counts_sorted = profile(list(base))
    shuffled = list(base)
    random.Random(99).shuffle(shuffled)
    counts_shuffled = profile(shuffled)

    expected = draws / len(base)
    sigma = math.sqrt(draws * (1 / len(base)) * (1 - 1 / len(base)))
    for p in base:
        assert abs(counts_sorted[p] - expected) <= 5 * sigma
        assert abs(counts_shuffled[p] - expected) <= 5 * sigma


def test_chi_square_within_bounds():
    """Chi-square goodness-of-fit stays below a loose critical value."""
    pool = [f"item{i}" for i in range(10)]  # df = 9
    draws = 30_000
    counts = {p: 0 for p in pool}
    for _ in range(draws):
        counts[pick_uniform(pool)] += 1
    expected = draws / len(pool)
    chi_sq = sum((c - expected) ** 2 / expected for c in counts.values())
    # Critical chi-square for df=9 at p=0.999 is ~27.9; use 40 as a very loose
    # ceiling so the test is robust against normal randomness but catches bias.
    assert chi_sq < 40, f"chi-square {chi_sq:.2f} too high — distribution not uniform"
