"""Statistics primitives: Wilson interval, rates, bootstrap of a pooled-rate difference."""

import math
import random
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
    return diffs[math.floor(0.025 * (iters - 1))], diffs[math.ceil(0.975 * (iters - 1))]
