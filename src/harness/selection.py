"""Judgment against the all_store baseline and ranking of passing engines."""

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, replace

from harness.config import EvalConfig
from harness.metrics import Metrics, per_scenario_counts
from harness.replay import ScenarioRun
from harness.stats import bootstrap_diff

EPS = 1e-9
NO_SAMPLE = "표본 없음"
TIEBREAKS = ("m1", "shallow_fix", "retraction", "reach", "context_chars")


@dataclass
class Verdict:
    m1_ok: bool | None
    m2_ok: bool | None
    m3_ok: bool
    abs_cap_ok: bool | None
    passed: bool
    reasons: list[str]


@dataclass
class EngineScore:
    engine_id: str
    metrics: Metrics
    verdict: Verdict
    repeats: list[Metrics]


@dataclass
class RankEntry:
    engine_id: str
    position: int
    tied_with_previous: bool
    tiebreak: str | None
    diff_ci: tuple[float, float] | None


def judge_engine(m: Metrics, base: Metrics, cfg: EvalConfig) -> Verdict:
    """Pre-registered criteria relative to the all_store baseline `base`."""
    j = cfg.judgment
    reasons: list[str] = []
    m1_ok = m2_ok = abs_cap_ok = None

    if m.m1.value is not None and base.m1.value is not None:
        limit = base.m1.value * j.m1_ratio
        m1_ok = m.m1.value <= limit + EPS
        if not m1_ok:
            reasons.append(f"M1 {m.m1.value:.3f} > {limit:.3f} (all_store {base.m1.value:.3f} x {j.m1_ratio})")
    if m.m2.value is not None and base.m2.value is not None:
        floor = base.m2.value - j.m2_drop
        m2_ok = m.m2.value >= floor - EPS
        if not m2_ok:
            reasons.append(f"M2 {m.m2.value:.3f} < {floor:.3f} (all_store {base.m2.value:.3f} - {j.m2_drop})")
    if j.m1_abs_cap is not None and m.m1.value is not None:
        abs_cap_ok = m.m1.value <= j.m1_abs_cap + EPS
        if not abs_cap_ok:
            reasons.append(f"M1 {m.m1.value:.3f} > absolute cap {j.m1_abs_cap:.3f}")
    m3_ok = m.m3 == 0
    if not m3_ok:
        reasons.append(f"M3 {m.m3} leaked results (must be 0)")

    if m1_ok is None or m2_ok is None:
        reasons.append(NO_SAMPLE)
    passed = m1_ok is True and m2_ok is True and m3_ok and abs_cap_ok is not False
    return Verdict(m1_ok, m2_ok, m3_ok, abs_cap_ok, passed, reasons)


def _mean(values: list[float | None]) -> float | None:
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def averaged(repeats: list[Metrics]) -> Metrics:
    """First repeat's metrics, with M1/M2 `value` replaced by the mean over repeats."""
    if not repeats:
        raise ValueError("averaged() needs at least one repeat")
    first = repeats[0]
    return replace(
        first,
        m1=replace(first.m1, value=_mean([r.m1.value for r in repeats])),
        m2=replace(first.m2, value=_mean([r.m2.value for r in repeats])),
    )


def relative_metrics(
    m: Metrics, none: Metrics, all_store: Metrics, oracle: Metrics
) -> dict[str, float | None]:
    """Observation numbers relative to the none / all_store / oracle baselines."""
    improvement = None
    if None not in (all_store.m1.value, oracle.m1.value, m.m1.value):
        denom = all_store.m1.value - oracle.m1.value
        if not math.isclose(denom, 0.0, abs_tol=EPS):
            improvement = (all_store.m1.value - m.m1.value) / denom
    reduction = None
    if m.promote_fn.value is not None and none.promote_fn.value:
        reduction = 1 - m.promote_fn.value / none.promote_fn.value
    return {"improvement_ratio": improvement, "repeat_mistake_reduction": reduction}


def _promote_tp_by_scenario(runs: list[ScenarioRun]) -> dict[str, tuple[int, int]]:
    ids = sorted(r.scenario_id for r in runs if r.error is None and r.mode == "read_only")
    return dict(zip(ids, per_scenario_counts(runs, "promote", "TP")))


def _diff_ci(
    first: str, second: str, runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig
) -> tuple[float, float] | None:
    """Bootstrap CI of promote-TP rate(first) - rate(second) over their common scenarios."""
    a, b = _promote_tp_by_scenario(runs[first]), _promote_tp_by_scenario(runs[second])
    common = sorted(a.keys() & b.keys())
    if not common:
        return None
    return bootstrap_diff(
        [a[s] for s in common], [b[s] for s in common], cfg.stats.bootstrap_iters, cfg.stats.seed
    )


def _contains_zero(ci: tuple[float, float] | None) -> bool:
    return ci is not None and ci[0] <= 0 <= ci[1]


def _low(value: float | None) -> float:
    return math.inf if value is None else value


def _median_retraction(m: Metrics) -> float:
    if not m.retraction:
        return math.inf
    return statistics.median(_low(d.seconds) for d in m.retraction)


def _tiebreak_keys(m: Metrics) -> dict[str, float]:
    """Smaller is better on every criterion; missing values are worst."""
    return {
        "m1": _low(m.m1.value),
        "shallow_fix": _low(m.shallow_fix.value),
        "retraction": _median_retraction(m),
        "reach": math.inf if m.reach.value is None else -m.reach.value,
        "context_chars": _low(m.context_chars_mean),
    }


def _chain(m: Metrics) -> tuple[float, ...]:
    keys = _tiebreak_keys(m)
    return tuple(keys[name] for name in TIEBREAKS)


def _first_difference(a: Metrics, b: Metrics) -> str:
    ka, kb = _tiebreak_keys(a), _tiebreak_keys(b)
    return next((name for name in TIEBREAKS if ka[name] != kb[name]), "equal")


def rank(
    scores: list[EngineScore], runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig
) -> list[RankEntry]:
    """Rank passing engines by M2; engines whose promote-TP difference is indistinguishable
    (95% bootstrap interval contains 0) are ordered by the tiebreak chain."""
    passed = [s for s in scores if s.verdict.passed]
    # Engines with no M2 sample cannot pass, so every value here is a float.
    order = sorted(passed, key=lambda s: (-s.metrics.m2.value, s.engine_id))

    for i in range(1, len(order)):
        prev, cur = order[i - 1], order[i]
        if _contains_zero(_diff_ci(prev.engine_id, cur.engine_id, runs, cfg)):
            if _chain(cur.metrics) < _chain(prev.metrics):
                order[i - 1], order[i] = cur, prev

    entries = []
    for i, score in enumerate(order):
        if i == 0:
            entries.append(RankEntry(score.engine_id, 1, False, None, None))
            continue
        prev = order[i - 1]
        ci = _diff_ci(prev.engine_id, score.engine_id, runs, cfg)
        tied = _contains_zero(ci)
        tiebreak = _first_difference(prev.metrics, score.metrics) if tied else None
        entries.append(RankEntry(score.engine_id, i + 1, tied, tiebreak, ci))
    return entries
