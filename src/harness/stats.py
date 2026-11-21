"""Statistics primitives: Wilson interval, rates, bootstrap of a pooled-rate difference."""

import functools
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials.

    Args:
        k: Successes.
        n: Trials.
        z: Normal quantile; the default gives a 95% interval.

    Returns:
        (low, high) clamped to [0, 1], or None when n is 0.
    """
    if n == 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


@dataclass(frozen=True)
class Rate:
    """A proportion with its sample size and interval.

    Attributes:
        k: Numerator.
        n: Denominator.
        value: k / n, or None when n is 0 (no sample).
        ci: Wilson 95% interval, or None when n is 0.
    """

    k: int
    n: int
    value: float | None
    ci: tuple[float, float] | None


def rate(k: int, n: int) -> Rate:
    """Build a `Rate` from a count; see `Rate`."""
    return Rate(k, n, k / n if n else None, wilson(k, n))


@dataclass(frozen=True)
class Delay:
    """Time until a fact stopped (retraction) or started (reflection) showing up in the basis.

    Attributes:
        fact: Fact id.
        scenario_id: Scenario the measurement comes from.
        seconds: Delay from the fact's validity edge, or None when it never happened.
    """

    fact: str
    scenario_id: str
    seconds: float | None


def bootstrap_diff(
    a: list[tuple[int, int]], b: list[tuple[int, int]], iters: int, seed: int
) -> tuple[float, float]:
    """95% bootstrap interval of pooled_rate(a) - pooled_rate(b), resampling scenarios.

    Both lists are resampled with the same indices, so the comparison is paired by scenario.

    Args:
        a: (numerator, denominator) per scenario for the first engine.
        b: The same for the second engine, in the same scenario order.
        iters: Number of resamples.
        seed: Random seed.

    Returns:
        (low, high) of the difference.

    Raises:
        ValueError: If the lists are empty or of different lengths.
    """
    if not a or len(a) != len(b):
        raise ValueError("a and b must be non-empty and the same length")
    rng = random.Random(seed)
    size = len(a)
    diffs: list[float] = []
    for _ in range(iters):
        # One pass per resample: the same draws, and integer sums, as summing each list apart.
        ka = na = kb = nb = 0
        for _ in range(size):
            i = rng.randrange(size)
            ka += a[i][0]
            na += a[i][1]
            kb += b[i][0]
            nb += b[i][1]
        diffs.append((ka / na if na else 0.0) - (kb / nb if nb else 0.0))
    diffs.sort()
    return _percentile_95(diffs)


def _percentile_95(ordered: list[float]) -> tuple[float, float]:
    """The 2.5th and 97.5th percentiles of sorted bootstrap values (nearest-rank, as above)."""
    last = len(ordered) - 1
    return ordered[math.floor(0.025 * last)], ordered[math.ceil(0.975 * last)]


CLUSTER_ITERS = 1000  # fewer than the config's bootstrap_iters: one interval per table cell
MIN_CLUSTERS = 3  # fewer clusters give an interval of one or a few points, which reads as certainty


@functools.lru_cache(maxsize=64)
def _resample_rows(clusters: int, iters: int, seed: int) -> tuple[tuple[int, ...], ...]:
    """The cluster indices of every resample, drawn in the order `cluster_ci` used to draw them."""
    rng = random.Random(seed)
    return tuple(tuple(rng.randrange(clusters) for _ in range(clusters)) for _ in range(iters))


def cluster_ci(
    clusters: Sequence[tuple[int, int]], seed: int, iters: int = CLUSTER_ITERS
) -> tuple[float, float] | None:
    """95% bootstrap interval of a pooled rate, resampling clusters (e.g. base scenarios).

    Every call with the same number of clusters, seed and iterations uses the same resamples,
    so intervals of several engines or table cells are comparable (and computed once).

    Args:
        clusters: (hits, trials) of each cluster; clusters without trials are left out.
        seed: Random seed.
        iters: Number of resamples.

    Returns:
        (low, high), or None with fewer than `MIN_CLUSTERS` clusters that have trials.
    """
    pairs = [p for p in clusters if p[1] > 0]
    if len(pairs) < MIN_CLUSTERS:
        return None
    hits = [k for k, _ in pairs]
    trials = [n for _, n in pairs]
    rates = sorted(
        sum(hits[i] for i in row) / sum(trials[i] for i in row) for row in _resample_rows(len(pairs), iters, seed)
    )
    return _percentile_95(rates)
