"""Statistics primitives: Wilson interval, rates, bootstrap of a pooled-rate difference."""

import math
import random
from dataclasses import dataclass


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials; None when n == 0."""
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
    k: int
    n: int
    value: float | None
    ci: tuple[float, float] | None


def rate(k: int, n: int) -> Rate:
    return Rate(k, n, k / n if n else None, wilson(k, n))


@dataclass(frozen=True)
class Delay:
    """Time until a fact stopped (retraction) or started (reflection) showing up in the basis.

    `seconds` is None when that never happened.
    """

    fact: str
    scenario_id: str
    seconds: float | None


def _pooled(pairs: list[tuple[int, int]], indices: list[int]) -> float:
    k = sum(pairs[i][0] for i in indices)
    n = sum(pairs[i][1] for i in indices)
    return k / n if n else 0.0


def bootstrap_diff(
    a: list[tuple[int, int]], b: list[tuple[int, int]], iters: int, seed: int
) -> tuple[float, float]:
    """95% bootstrap interval of pooled_rate(a) - pooled_rate(b), resampling scenarios.

    `a` and `b` hold (numerator, denominator) per scenario in the same scenario order.
    """
    if not a or len(a) != len(b):
        raise ValueError("a and b must be non-empty and the same length")
    rng = random.Random(seed)
    diffs = []
    for _ in range(iters):
        indices = [rng.randrange(len(a)) for _ in range(len(a))]
        diffs.append(_pooled(a, indices) - _pooled(b, indices))
    diffs.sort()
    return diffs[math.floor(0.025 * (iters - 1))], diffs[math.ceil(0.975 * (iters - 1))]
