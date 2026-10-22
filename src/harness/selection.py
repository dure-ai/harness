"""Judgment against the all_store baseline and ranking of passing engines."""

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, replace

from harness.config import EvalConfig
from harness.metrics import FamilyRow, Metrics, per_scenario_counts
from harness.replay import ScenarioRun
from harness.stats import Rate, bootstrap_diff, rate

EPS = 1e-9
NO_SAMPLE = "표본 없음"
TIEBREAKS = ("m1", "shallow_fix", "retraction", "reach", "context_chars")


@dataclass
class Verdict:
    """Pass/fail against the pre-registered criteria.

    Attributes:
        m1_ok: M1 is within the limit; None when there is no block sample.
        m2_ok: M2 is above the floor; None when there is no promote sample.
        m3_ok: Nothing leaked.
        abs_cap_ok: M1 is under the absolute cap; None when no cap is set.
        passed: Every criterion holds and no scenario errored.
        reasons: Why it did not pass (empty when it passed).
    """

    m1_ok: bool | None
    m2_ok: bool | None
    m3_ok: bool
    abs_cap_ok: bool | None
    passed: bool
    reasons: list[str]


@dataclass
class EngineScore:
    """An engine's metrics and verdict.

    Attributes:
        engine_id: Engine name (blinded later in the report).
        metrics: The run's metrics, averaged over repeats when there are several.
        verdict: Judgment of `metrics` against all_store.
        repeats: Per-repeat metrics.
        flip: Re-run flip rate; None for an engine run once.
    """

    engine_id: str
    metrics: Metrics
    verdict: Verdict
    repeats: list[Metrics]
    flip: Rate | None = None

    @classmethod
    def from_repeats(
        cls,
        engine_id: str,
        repeats: list[Metrics],
        base: Metrics,
        cfg: EvalConfig,
        flip: Rate | None = None,
    ) -> "EngineScore":
        """Average the repeats (see `averaged`) and judge the result against `base`."""
        metrics = averaged(repeats) if len(repeats) > 1 else repeats[0]
        return cls(engine_id, metrics, judge_engine(metrics, base, cfg), repeats, flip)


@dataclass
class RankEntry:
    """One position in the ranking.

    Attributes:
        engine_id: The engine.
        position: 1-based position.
        tied_with_previous: The M2 difference from the previous entry is not significant.
        tiebreak: The tiebreak that separated it from the previous entry, or None.
        diff_ci: Bootstrap interval of the promote-TP difference from the previous entry.
    """

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
    if m.errors:  # an errored scenario would otherwise drop silently out of M1/M2
        reasons.append(f"판정 불가: 오류 시나리오 {len(m.errors)}개")
    passed = m1_ok is True and m2_ok is True and m3_ok and abs_cap_ok is not False and not m.errors
    return Verdict(m1_ok, m2_ok, m3_ok, abs_cap_ok, passed, reasons)


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if len(present) != len(values):
        return None
    return sum(present) / len(present)


def _mean_present(values: list[float | None]) -> float | None:
    """Mean of the repeats that have a value; None only when none of them does."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _pool(rates: list[Rate]) -> Rate:
    """rate(sum of k, sum of n); the value is None when every denominator is 0."""
    return rate(sum(r.k for r in rates), sum(r.n for r in rates))


def _pool_rows(tables: list[dict[str, FamilyRow]]) -> dict[str, FamilyRow]:
    """Pool each family's M1/M2 across repeats; M3 is the worst repeat's, as for the headline."""
    out: dict[str, FamilyRow] = {}
    for family in sorted({f for t in tables for f in t}):
        rows = [t[family] for t in tables if family in t]
        out[family] = FamilyRow(
            m1=_pool([r["m1"] for r in rows]),
            m2=_pool([r["m2"] for r in rows]),
            m3=max(r["m3"] for r in rows),
        )
    return out


def _pool_authors(tables: list[dict[str, dict[str, Rate]]]) -> dict[str, dict[str, Rate]]:
    out: dict[str, dict[str, Rate]] = {}
    for author in sorted({a for t in tables for a in t}):
        rows = [t[author] for t in tables if author in t]
        out[author] = {key: _pool([r[key] for r in rows]) for key in ("m1", "m2")}
    return out


def _worst_family(by_family: dict[str, FamilyRow]) -> tuple[str, float] | None:
    values = {f: v for f, t in by_family.items() if (v := t["m1"].value) is not None}
    if not values:
        return None
    family = min(values, key=lambda f: (-values[f], f))
    return family, values[family]


def averaged(repeats: list[Metrics]) -> Metrics:
    """Combine repeats of one engine into one `Metrics` (design 8.3).

    Only the M1/M2 intervals (and the ranking bootstrap, which uses the first repeat's runs)
    are based on the first repeat. Everything else covers every repeat:

    - M1/M2 `value` is the mean over repeats; M3 is the worst repeat's; `errors` is the union.
    - Rate-like metrics (promote FN/FP, shallow fix, reach, contamination, sourceless) and
      every cell of the family and author tables are pooled as rate(sum k, sum n). Their
      intervals come from the pooled counts and are for reference. `worst_family` is
      recomputed from the pooled family table. A rate whose pooled denominator is 0 has
      value None.
    - `retraction` and `reflection` are concatenated; `context_chars_mean` and the latency
      percentiles are averaged over the repeats that have a value.

    Raises:
        ValueError: If `repeats` is empty.
    """
    if not repeats:
        raise ValueError("averaged() needs at least one repeat")
    first = repeats[0]
    by_family = _pool_rows([r.by_family for r in repeats])
    return replace(
        first,
        m1=replace(first.m1, value=_mean([r.m1.value for r in repeats])),
        m2=replace(first.m2, value=_mean([r.m2.value for r in repeats])),
        m3=max(r.m3 for r in repeats),  # a leak in any repeat fails the gate
        promote_fn=_pool([r.promote_fn for r in repeats]),
        promote_fp=_pool([r.promote_fp for r in repeats]),
        by_family=by_family,
        feedback_by_family=_pool_rows([r.feedback_by_family for r in repeats]),
        by_author=_pool_authors([r.by_author for r in repeats]),
        shallow_fix=_pool([r.shallow_fix for r in repeats]),
        reach=_pool([r.reach for r in repeats]),
        contamination=_pool([r.contamination for r in repeats]),
        sourceless=_pool([r.sourceless for r in repeats]),
        retraction=[d for r in repeats for d in r.retraction],
        reflection=[d for r in repeats for d in r.reflection],
        worst_family=_worst_family(by_family),
        errors=sorted({e for r in repeats for e in r.errors}),  # so does an error in any repeat
        context_chars_mean=_mean_present([r.context_chars_mean for r in repeats]),
        record_ms_p50=_mean_present([r.record_ms_p50 for r in repeats]),
        record_ms_p95=_mean_present([r.record_ms_p95 for r in repeats]),
        search_ms_p50=_mean_present([r.search_ms_p50 for r in repeats]),
        search_ms_p95=_mean_present([r.search_ms_p95 for r in repeats]),
    )


def relative_metrics(
    m: Metrics, none: Metrics, all_store: Metrics, oracle: Metrics
) -> dict[str, float | None]:
    """Observation numbers relative to the none / all_store / oracle baselines."""
    improvement = None
    base_m1, oracle_m1, m1 = all_store.m1.value, oracle.m1.value, m.m1.value
    if base_m1 is not None and oracle_m1 is not None and m1 is not None:
        denom = base_m1 - oracle_m1
        if not math.isclose(denom, 0.0, abs_tol=EPS):
            improvement = (base_m1 - m1) / denom
    reduction = None
    if m.promote_fn.value is not None and none.promote_fn.value:
        reduction = 1 - m.promote_fn.value / none.promote_fn.value
    return {"improvement_ratio": improvement, "repeat_mistake_reduction": reduction}


def _promote_tp_by_scenario(runs: list[ScenarioRun]) -> dict[str, tuple[int, int]]:
    ids = sorted(r.scenario_id for r in runs if r.error is None and r.mode == "read_only")
    return dict(zip(ids, per_scenario_counts(runs, "promote", "TP")))


def _canonical_ci(
    first: str, second: str, runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig
) -> tuple[float, float] | None:
    """Bootstrap CI of promote-TP rate(first) - rate(second) over their common scenarios.

    Only scenarios where both engines have at least one promote probe are resampled: a
    (0, 0) pair adds nothing to the pooled rates, but a resample made of such scenarios
    alone would count as a 0 difference and widen the interval toward a false tie.
    """
    a, b = _promote_tp_by_scenario(runs[first]), _promote_tp_by_scenario(runs[second])
    common = sorted(s for s in a.keys() & b.keys() if a[s][1] > 0 and b[s][1] > 0)
    if not common:
        return None
    return bootstrap_diff(
        [a[s] for s in common], [b[s] for s in common], cfg.stats.bootstrap_iters, cfg.stats.seed
    )


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


def _close(a: float, b: float) -> bool:
    return a == b or abs(a - b) <= EPS


def _tiebreak(a: Metrics, b: Metrics) -> tuple[int, str]:
    """(-1 if a is better, 1 if b is better, 0 if equal; criterion that decided)."""
    ka, kb = _tiebreak_keys(a), _tiebreak_keys(b)
    for name in TIEBREAKS:
        if not _close(ka[name], kb[name]):
            return (-1 if ka[name] < kb[name] else 1), name
    return 0, "equal"


class _Pairs:
    """Pairwise comparison of passed engines, with the bootstrap CI computed once per pair."""

    def __init__(self, runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig) -> None:
        self._runs, self._cfg = runs, cfg
        self._cache: dict[tuple[str, str], tuple[float, float] | None] = {}

    def ci(self, a: str, b: str) -> tuple[float, float] | None:
        """Return the CI of rate(a) - rate(b).

        It is derived from one canonical (id-ordered) computation, so the answer does not
        depend on argument order.
        """
        lo_id, hi_id = sorted((a, b))
        key = (lo_id, hi_id)
        if key not in self._cache:
            self._cache[key] = _canonical_ci(lo_id, hi_id, self._runs, self._cfg)
        ci = self._cache[key]
        if ci is None or a == lo_id:
            return ci
        return -ci[1], -ci[0]

    def tied(self, a: str, b: str) -> bool:
        ci = self.ci(a, b)
        return ci is not None and ci[0] <= 0 <= ci[1]

    def compare(self, a: EngineScore, b: EngineScore) -> tuple[int, str | None]:
        """(-1 if a goes first, 1 if b goes first, 0 keep order; tiebreak label or None)."""
        if not self.tied(a.engine_id, b.engine_id):
            ma, mb = _passed_m2(a), _passed_m2(b)
            if _close(ma, mb):
                return 0, None
            return (-1 if ma > mb else 1), None
        return _tiebreak(a.metrics, b.metrics)


def _passed_m2(score: EngineScore) -> float:
    """M2 of a passing engine; passing requires an M2 sample, so the value is never None."""
    value = score.metrics.m2.value
    if value is None:
        raise ValueError(f"{score.engine_id}: ranked engine has no M2 sample")
    return value


def rank(
    scores: list[EngineScore], runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig
) -> list[RankEntry]:
    """Rank passing engines by M2.

    Engines whose promote-TP difference is indistinguishable (95% bootstrap interval contains
    0) are ordered by the tiebreak chain `TIEBREAKS`. This is a stable insertion sort with a
    pairwise comparator, so each entry's label describes the criterion that actually separates
    it from the entry right before it.

    Args:
        scores: Candidate engines; those that did not pass are left out.
        runs: Engine name to scenario runs, for the bootstrap.
        cfg: Evaluation config (bootstrap settings).

    Returns:
        Ranked entries, best first.
    """
    pairs = _Pairs(runs, cfg)
    # Engines with no M2 sample cannot pass, so every M2 value here is a float.
    order: list[EngineScore] = []
    for score in sorted(
        (s for s in scores if s.verdict.passed), key=lambda s: (-_passed_m2(s), s.engine_id)
    ):
        i = len(order)
        while i > 0 and pairs.compare(score, order[i - 1])[0] < 0:
            i -= 1
        order.insert(i, score)

    entries = []
    for i, score in enumerate(order):
        if i == 0:
            entries.append(RankEntry(score.engine_id, 1, False, None, None))
            continue
        prev = order[i - 1]
        tied = pairs.tied(prev.engine_id, score.engine_id)
        tiebreak = _tiebreak(prev.metrics, score.metrics)[1] if tied else None
        entries.append(
            RankEntry(score.engine_id, i + 1, tied, tiebreak, pairs.ci(prev.engine_id, score.engine_id))
        )
    return entries
