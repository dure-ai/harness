import pytest

from harness.stats import Rate, bootstrap_diff, rate, wilson


def test_wilson_known_value():
    lo, hi = wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3


def test_wilson_zero_n():
    assert wilson(0, 0) is None


def test_wilson_extremes_stay_in_unit_interval():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.0 < hi < 0.4
    lo, hi = wilson(10, 10)
    assert hi == pytest.approx(1.0) and 0.6 < lo < 1.0


def test_rate_value_and_ci():
    r = rate(1, 4)
    assert r == Rate(k=1, n=4, value=0.25, ci=wilson(1, 4))


def test_rate_empty():
    assert rate(0, 0) == Rate(k=0, n=0, value=None, ci=None)


def test_bootstrap_zero_for_identical():
    a = [(1, 2), (0, 3), (2, 2)]
    assert bootstrap_diff(a, a, 1000, 1) == (0.0, 0.0)


def test_bootstrap_deterministic_and_seed_sensitive():
    a = [(1, 2), (0, 3), (2, 2), (1, 1), (0, 4)]
    b = [(0, 2), (1, 3), (1, 2), (0, 1), (2, 4)]
    assert bootstrap_diff(a, b, 500, 7) == bootstrap_diff(a, b, 500, 7)
    lo, hi = bootstrap_diff(a, b, 500, 7)
    assert lo <= hi
    assert bootstrap_diff(a, b, 500, 8) != (lo, hi)


def test_bootstrap_separates_clearly_different_rates():
    a = [(0, 5)] * 10
    b = [(5, 5)] * 10
    assert bootstrap_diff(a, b, 200, 1) == (-1.0, -1.0)


def test_bootstrap_zero_denominator_sample():
    assert bootstrap_diff([(0, 0)], [(0, 0)], 10, 1) == (0.0, 0.0)


def test_bootstrap_quantile_indices():
    # one scenario each: every resample gives the same diff
    assert bootstrap_diff([(1, 2)], [(0, 2)], 100, 3) == (0.5, 0.5)


@pytest.mark.parametrize("a,b", [([], []), ([(1, 2)], [(1, 2), (0, 1)])])
def test_bootstrap_rejects_bad_input(a, b):
    with pytest.raises(ValueError):
        bootstrap_diff(a, b, 10, 1)


def _reference_bootstrap_diff(a, b, iters, seed):
    """The original definition: each resample sums both lists' numerators and denominators."""
    import math
    import random

    def pooled(pairs, indices):
        k = sum(pairs[i][0] for i in indices)
        n = sum(pairs[i][1] for i in indices)
        return k / n if n else 0.0

    rng = random.Random(seed)
    diffs = []
    for _ in range(iters):
        indices = [rng.randrange(len(a)) for _ in range(len(a))]
        diffs.append(pooled(a, indices) - pooled(b, indices))
    diffs.sort()
    return diffs[math.floor(0.025 * (iters - 1))], diffs[math.ceil(0.975 * (iters - 1))]


@pytest.mark.parametrize("seed", range(8))
def test_bootstrap_diff_is_exactly_the_reference(seed):
    import random

    from harness.stats import bootstrap_diff

    rng = random.Random(seed)
    n = rng.randint(1, 40)
    a = [(rng.randint(0, 3), rng.choice([0, 1, 2, 4])) for _ in range(n)]  # zero denominators too
    b = [(rng.randint(0, 3), rng.choice([0, 1, 2, 4])) for _ in range(n)]
    a = [(min(k, d), d) for k, d in a]
    b = [(min(k, d), d) for k, d in b]
    iters = rng.choice([1, 7, 500])
    assert bootstrap_diff(a, b, iters, seed) == _reference_bootstrap_diff(a, b, iters, seed)


def test_cluster_ci_resamples_clusters():
    from harness.stats import cluster_ci

    assert cluster_ci([], seed=1) is None and cluster_ci([(0, 0), (1, 1), (1, 1)], seed=1) is None
    assert cluster_ci([(3, 3), (3, 3), (2, 2)], seed=1) == (1.0, 1.0)
    low, high = cluster_ci([(5, 5), (0, 5), (5, 5), (0, 5)], seed=1)
    assert 0.0 <= low < 0.5 < high <= 1.0
    # One cluster counted many times is still one cluster: 50 trials per base give the same
    # interval as 2, as wide as the spread between bases (not the 400 trials' binomial width).
    rates = [1.0, 0.0, 0.5, 1.0, 1.0, 0.0, 1.0, 0.5]
    many = cluster_ci([(int(50 * r), 50) for r in rates], seed=1)
    few = cluster_ci([(int(2 * r), 2) for r in rates], seed=1)
    assert many == few
    assert many is not None and many[1] - many[0] > 0.4
    assert cluster_ci([(5, 5), (0, 5), (3, 5)], seed=1) == cluster_ci([(5, 5), (0, 5), (3, 5)], seed=1)
