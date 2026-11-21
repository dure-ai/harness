"""Bootstrap intervals that resample whole clusters (base scenarios), for stress-set variants."""

import math
import random
from collections.abc import Sequence

CLUSTER_ITERS = 1000
CLUSTER_SEED = 20261019  # the eval config's default bootstrap seed


def cluster_ci(
    clusters: Sequence[tuple[int, int]], iters: int = CLUSTER_ITERS, seed: int = CLUSTER_SEED
) -> tuple[float, float] | None:
    """95% bootstrap interval of a pooled rate, resampling clusters rather than single trials.

    Variants of one base scenario share its facts, evidence and questions, so their outcomes
    move together; treating them as independent trials would make the interval too narrow.

    Args:
        clusters: (hits, trials) of each cluster, summed over the variants it holds.
        iters: Number of resamples.
        seed: Random seed.

    Returns:
        (low, high), or None when there are no trials.
    """
    pairs = [p for p in clusters if p[1] > 0]
    if not pairs:
        return None
    rng = random.Random(seed)
    m = len(pairs)
    rates: list[float] = []
    for _ in range(iters):
        k = n = 0
        for _ in range(m):
            hits, trials = pairs[rng.randrange(m)]
            k += hits
            n += trials
        rates.append(k / n)
    rates.sort()
    return rates[math.floor(0.025 * (iters - 1))], rates[math.ceil(0.975 * (iters - 1))]
