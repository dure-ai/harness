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
