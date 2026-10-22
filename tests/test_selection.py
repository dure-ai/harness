from dataclasses import replace

import pytest

from harness.agent import Choice
from harness.config import EvalConfig
from harness.contract import Result
from harness.metrics import FamilyRow, compute_metrics
from harness.replay import ProbeRecord, ScenarioRun
from harness.selection import (
    EngineScore,
    _canonical_ci,
    averaged,
    judge_engine,
    rank,
    relative_metrics,
)
from harness.stats import Delay, Rate, rate

CFG = EvalConfig()


def R(value, n=10):
    return Rate(0 if value is None else round(value * n), n if value is not None else 0, value, None)


def M(m1=0.3, m2=0.75, m3=0, *, promote_fn=0.0, shallow_fix=0.0, reach=0.5,
      retraction=(), context_chars_mean=100.0):
    empty = compute_metrics([], {})
    return replace(
        empty,
        m1=R(m1),
        m2=R(m2),
        m3=m3,
        promote_fn=R(promote_fn),
        shallow_fix=R(shallow_fix),
        reach=R(reach),
        retraction=[Delay("f", "s", s) for s in retraction],
        context_chars_mean=context_chars_mean,
    )


BASE = M(m1=0.6, m2=0.8)


def test_pass_when_half_of_baseline():
    v = judge_engine(M(m1=0.3, m2=0.75), BASE, CFG)
    assert (v.m1_ok, v.m2_ok, v.m3_ok, v.abs_cap_ok, v.passed) == (True, True, True, None, True)
    assert v.reasons == []


def test_fail_m1_above_half():
    v = judge_engine(M(m1=0.31, m2=0.8), BASE, CFG)
    assert v.m1_ok is False and not v.passed
    assert any("M1" in r for r in v.reasons)


def test_fail_m2_drop_over_10pp():
    v = judge_engine(M(m1=0.3, m2=0.69), BASE, CFG)
    assert v.m2_ok is False and not v.passed
    assert any("M2" in r for r in v.reasons)


def test_m2_drop_exactly_10pp_passes():
    assert judge_engine(M(m1=0.3, m2=0.7), BASE, CFG).m2_ok is True


def test_fail_any_leak():
    v = judge_engine(M(m3=1), BASE, CFG)
    assert v.m3_ok is False and not v.passed
    assert any("M3" in r for r in v.reasons)


def test_abs_cap_optional():
    cfg = EvalConfig(judgment={"m1_abs_cap": 0.1})
    v = judge_engine(M(m1=0.2, m2=0.8), BASE, cfg)
    assert v.abs_cap_ok is False and v.m1_ok is True and not v.passed
    assert judge_engine(M(m1=0.1, m2=0.8), BASE, cfg).abs_cap_ok is True


@pytest.mark.parametrize("engine,base", [
    (M(m1=None), BASE), (M(m2=None), BASE), (BASE, M(m1=None)), (BASE, M(m2=None)),
])
def test_missing_sample_is_not_passed(engine, base):
    v = judge_engine(engine, base, CFG)
    assert not v.passed
    assert "표본 없음" in v.reasons
    assert None in (v.m1_ok, v.m2_ok)


def test_averaged_uses_mean_value():
    a = replace(M(m1=0.2, m2=0.6), m1=Rate(2, 10, 0.2, (0.1, 0.3)))
    b = M(m1=0.4, m2=0.8)
    avg = averaged([a, b])
    assert avg.m1.value == pytest.approx(0.3)
    assert avg.m2.value == pytest.approx(0.7)
    assert (avg.m1.k, avg.m1.n, avg.m1.ci) == (2, 10, (0.1, 0.3))  # M1/M2 interval: first repeat


def test_averaged_none_and_empty():
    assert averaged([M(m1=0.2), M(m1=None)]).m1.value is None
    with pytest.raises(ValueError):
        averaged([])


def test_relative_metrics():
    out = relative_metrics(
        M(m1=0.3, promote_fn=0.1), M(promote_fn=0.4), M(m1=0.6), M(m1=0.0)
    )
    assert out["improvement_ratio"] == pytest.approx(0.5)
    assert out["repeat_mistake_reduction"] == pytest.approx(0.75)


def test_relative_metrics_degenerate():
    out = relative_metrics(M(m1=0.3, promote_fn=0.1), M(promote_fn=0.0), M(m1=0.0), M(m1=0.0))
    assert out == {"improvement_ratio": None, "repeat_mistake_reduction": None}
    out = relative_metrics(M(m1=None, promote_fn=None), M(), M(m1=0.6), M(m1=0.0))
    assert out == {"improvement_ratio": None, "repeat_mistake_reduction": None}


def promote_run(sid, outcomes, *, mode="read_only", error=None):
    probes = [
        ProbeRecord(
            probe_id=f"p{i}", kind="promote", results=[Result(text="x", sources=["e"])],
            choice=Choice(None, frozenset()), outcome=o, leaks=[], bases=[frozenset()],
            contaminated=False, reached=False, search_ms=1.0, context_chars=1,
        )
        for i, o in enumerate(outcomes)
    ]
    return ScenarioRun(sid, "truth_change", mode, probes, [], [], error)


def uniform(outcome, n=6):
    return [promote_run(f"s{i}", [outcome]) for i in range(n)]


def score(engine_id, metrics):
    return EngineScore(engine_id, metrics, judge_engine(metrics, BASE, CFG), [metrics])


def test_rank_tie_broken_by_m1():
    runs = {"a": uniform("TP"), "b": uniform("TP")}
    scores = [score("a", M(m1=0.2)), score("b", M(m1=0.1))]
    ranked = rank(scores, runs, CFG)
    assert [e.engine_id for e in ranked] == ["b", "a"]
    assert [e.position for e in ranked] == [1, 2]
    assert ranked[0].tied_with_previous is False and ranked[0].tiebreak is None
    assert ranked[0].diff_ci is None
    assert ranked[1].tied_with_previous is True and ranked[1].tiebreak == "m1"
    lo, hi = ranked[1].diff_ci
    assert lo <= 0 <= hi


def test_rank_tiebreak_chain_follows_order():
    runs = {k: uniform("TP") for k in "abcde"}
    base = dict(m1=0.2)
    cases = {
        "shallow_fix": (M(**base, shallow_fix=0.5), M(**base, shallow_fix=0.1)),
        "retraction": (M(**base, retraction=(300.0, None)), M(**base, retraction=(60.0, 90.0))),
        "reach": (M(**base, reach=0.2), M(**base, reach=0.6)),
        "context_chars": (M(**base, context_chars_mean=500.0), M(**base, context_chars_mean=50.0)),
        "equal": (M(**base), M(**base)),
    }
    for expected, (first, second) in cases.items():
        ranked = rank([score("a", first), score("b", second)], runs, CFG)
        assert ranked[1].tiebreak == expected, expected
        # "equal" keeps the current (engine_id) order; others put the better one first
        assert ranked[0].engine_id == ("a" if expected == "equal" else "b"), expected


def test_rank_none_values_compare_worst():
    runs = {k: uniform("TP") for k in "ab"}
    a = M(m1=0.2, retraction=())
    b = M(m1=0.2, retraction=(1e9,))
    ranked = rank([score("a", a), score("b", b)], runs, CFG)
    assert [e.engine_id for e in ranked] == ["b", "a"] and ranked[1].tiebreak == "retraction"


def test_rank_distinct_m2_not_tied_and_ordered_by_m2():
    runs = {"a": uniform("TP"), "b": uniform("FN")}
    scores = [score("b", M(m1=0.1, m2=0.72)), score("a", M(m1=0.3, m2=0.8))]
    ranked = rank(scores, runs, CFG)
    assert [e.engine_id for e in ranked] == ["a", "b"]
    assert ranked[1].tied_with_previous is False and ranked[1].tiebreak is None
    lo, hi = ranked[1].diff_ci
    assert lo > 0


def test_rank_only_passed_engines():
    runs = {"a": uniform("TP"), "b": uniform("TP")}
    scores = [score("a", M(m1=0.2)), score("b", M(m1=0.1, m3=1))]
    assert [e.engine_id for e in rank(scores, runs, CFG)] == ["a"]
    assert rank([], runs, CFG) == []


def test_rank_uses_common_scenarios_only_and_empty_intersection_is_not_tied():
    runs = {
        "a": [promote_run("s1", ["TP"]), promote_run("s2", ["TP"]), promote_run("e", ["TP"], error="x")],
        "b": [promote_run("s3", ["FN"]), promote_run("s4", ["FN"])],
    }
    scores = [score("a", M(m1=0.2, m2=0.8)), score("b", M(m1=0.1, m2=0.75))]
    ranked = rank(scores, runs, CFG)
    assert [e.engine_id for e in ranked] == ["a", "b"]
    assert ranked[1].tied_with_previous is False and ranked[1].diff_ci is None


def test_rank_ignores_feedback_runs_in_intersection():
    runs = {
        "a": [promote_run("s1", ["TP"]), promote_run("s2", ["TP"], mode="feedback")],
        "b": [promote_run("s1", ["TP"]), promote_run("s2", ["FN"], mode="feedback")],
    }
    ranked = rank([score("a", M(m1=0.2)), score("b", M(m1=0.1))], runs, CFG)
    assert ranked[1].tied_with_previous is True and ranked[1].tiebreak == "m1"


def test_rank_three_way_tie_is_sorted_and_labels_consistent():
    runs = {k: uniform("TP") for k in "abc"}
    scores = [
        score("a", M(m1=0.30, m2=0.80)),
        score("b", M(m1=0.25, m2=0.78)),
        score("c", M(m1=0.10, m2=0.76)),
    ]
    ranked = rank(scores, runs, CFG)
    assert [e.engine_id for e in ranked] == ["c", "b", "a"]
    assert [e.tied_with_previous for e in ranked] == [False, True, True]
    assert [e.tiebreak for e in ranked] == [None, "m1", "m1"]
    m1 = {s.engine_id: s.metrics.m1.value for s in scores}
    for prev, cur in zip(ranked, ranked[1:]):
        assert m1[prev.engine_id] < m1[cur.engine_id]


def test_tiebreak_tolerance_falls_through_to_next_criterion():
    runs = {k: uniform("TP") for k in "ab"}
    first, second = M(m1=0.2, shallow_fix=0.5), M(m1=0.2 + 1e-12, shallow_fix=0.1)
    assert first.m1.value != second.m1.value
    ranked = rank([score("a", first), score("b", second)], runs, CFG)
    assert [e.engine_id for e in ranked] == ["b", "a"]
    assert ranked[1].tiebreak == "shallow_fix"


def test_averaged_m3_is_worst_repeat_and_fails_gate():
    repeats = [M(m3=0), M(m3=2), M(m3=0)]
    assert averaged(repeats).m3 == 2
    assert not judge_engine(averaged(repeats), BASE, CFG).m3_ok


def test_engine_score_from_repeats():
    one = M(m1=0.2, m2=0.8)
    s = EngineScore.from_repeats("e", [one], BASE, CFG)
    assert s.metrics is one and s.repeats == [one] and s.verdict.passed

    repeats = [M(m1=0.2, m2=0.8), M(m1=0.4, m2=0.8, m3=1)]
    s = EngineScore.from_repeats("e", repeats, BASE, CFG)
    assert s.metrics.m1.value == pytest.approx(0.3)
    assert s.metrics.m3 == 1
    assert s.verdict == judge_engine(s.metrics, BASE, CFG) and not s.verdict.passed
    assert s.repeats == repeats


def test_errored_scenarios_fail_verdict():
    m = replace(M(m1=0.2, m2=0.8), errors=["s1", "s2"])
    v = judge_engine(m, BASE, CFG)
    assert (v.m1_ok, v.m2_ok, v.m3_ok) == (True, True, True)
    assert v.passed is False
    assert "판정 불가: 오류 시나리오 2개" in v.reasons


def test_averaged_errors_union_over_repeats():
    repeats = [M(), replace(M(), errors=["s2"]), replace(M(), errors=["s1", "s2"])]
    assert averaged(repeats).errors == ["s1", "s2"]
    s = EngineScore.from_repeats("e", repeats, BASE, CFG)
    assert not s.verdict.passed and "판정 불가: 오류 시나리오 2개" in s.verdict.reasons


def test_engine_score_flip():
    one = M()
    assert EngineScore.from_repeats("e", [one], BASE, CFG).flip is None
    flip = Rate(1, 4, 0.25, None)
    assert EngineScore.from_repeats("e", [one, one], BASE, CFG, flip=flip).flip == flip


# ---- A4: bootstrap sample and repeat aggregation ------------------------------------------


def test_canonical_ci_ignores_scenarios_without_promote_probes():
    # s2 has no promote probe for either engine; as a (0, 0) pair it used to let a resample
    # draw only s2 and report a 0 difference, which widened the interval to (0.0, 1.0).
    runs = {
        "a": [promote_run("s1", ["TP"]), promote_run("s2", [])],
        "b": [promote_run("s1", ["FN"]), promote_run("s2", [])],
    }
    assert _canonical_ci("a", "b", runs, CFG) == (1.0, 1.0)
    ranked = rank([score("a", M(m1=0.2, m2=0.8)), score("b", M(m1=0.1, m2=0.75))], runs, CFG)
    assert [e.engine_id for e in ranked] == ["a", "b"]
    assert ranked[1].tied_with_previous is False and ranked[1].diff_ci == (1.0, 1.0)  # previous minus current


def test_canonical_ci_needs_promote_probes_in_both_engines():
    runs = {
        "a": [promote_run("s1", ["TP"]), promote_run("s2", ["TP"])],
        "b": [promote_run("s1", ["FN"]), promote_run("s2", [])],
    }
    assert _canonical_ci("a", "b", runs, CFG) == (1.0, 1.0)  # s2 is out: b has no promote there
    only_empty = {"a": [promote_run("s1", [])], "b": [promote_run("s1", [])]}
    assert _canonical_ci("a", "b", only_empty, CFG) is None


def K(k, n):
    return rate(k, n)


def test_averaged_tiebreak_metrics_use_all_repeats():
    # reach per repeat: A 0/3, 3/3, 3/3 and B 2/3 x 3 -> pooled 6/9 for both. Keeping repeat 1's
    # reach made B win on "reach"; with every repeat the chain falls through to context_chars.
    base = M(m1=0.2, m2=0.8)
    a_repeats = [replace(base, reach=K(k, 3), context_chars_mean=50.0) for k in (0, 3, 3)]
    b_repeats = [replace(base, reach=K(2, 3), context_chars_mean=100.0) for _ in range(3)]
    runs = {k: uniform("TP") for k in "AB"}
    scores = [
        EngineScore.from_repeats(name, repeats, BASE, CFG)
        for name, repeats in (("A", a_repeats), ("B", b_repeats))
    ]
    assert scores[0].metrics.reach.value == pytest.approx(scores[1].metrics.reach.value)
    ranked = rank(scores, runs, CFG)
    assert [e.engine_id for e in ranked] == ["A", "B"]
    assert ranked[1].tied_with_previous is True and ranked[1].tiebreak == "context_chars"


def test_averaged_rate_metrics_pool_counts_not_first_repeat():
    names = ("promote_fn", "promote_fp", "shallow_fix", "reach", "contamination", "sourceless")
    first = replace(M(), **{n: K(0, 4) for n in names})
    second = replace(M(), **{n: K(3, 4) for n in names})
    avg = averaged([first, second])
    for n in names:
        got = getattr(avg, n)
        assert (got.k, got.n) == (3, 8) and got.value == pytest.approx(0.375)
        assert got.ci is not None


def test_averaged_family_tables_pool_repeats():
    def row(m1, m2, m3=0):
        return FamilyRow(m1=m1, m2=m2, m3=m3)

    one = replace(
        M(),
        by_family={"x": row(K(1, 2), K(2, 2)), "y": row(K(0, 2), K(1, 2))},
        feedback_by_family={"echo": row(K(0, 2), K(2, 2))},
        by_author={"human": {"m1": K(1, 2), "m2": K(2, 2)}},
        worst_family=("x", 0.5),
    )
    two = replace(
        M(),
        by_family={"x": row(K(0, 2), K(1, 2), 1), "y": row(K(2, 2), K(1, 2))},
        feedback_by_family={"echo": row(K(2, 2), K(0, 2))},
        by_author={"human": {"m1": K(0, 2), "m2": K(0, 2)}, "llm": {"m1": K(1, 1), "m2": K(0, 0)}},
        worst_family=("y", 1.0),
    )
    avg = averaged([one, two])
    assert (avg.by_family["x"]["m1"].k, avg.by_family["x"]["m1"].n) == (1, 4)
    assert (avg.by_family["y"]["m1"].k, avg.by_family["y"]["m1"].n) == (2, 4)
    assert (avg.by_family["x"]["m2"].k, avg.by_family["x"]["m2"].n) == (3, 4)
    assert avg.by_family["x"]["m3"] == 1  # worst repeat, like the headline M3
    assert avg.worst_family == ("y", 0.5)  # recomputed from the pooled table, not repeat 1's
    assert (avg.feedback_by_family["echo"]["m1"].k, avg.feedback_by_family["echo"]["m1"].n) == (2, 4)
    assert (avg.by_author["human"]["m1"].k, avg.by_author["human"]["m1"].n) == (1, 4)
    assert (avg.by_author["llm"]["m1"].k, avg.by_author["llm"]["m1"].n) == (1, 1)  # only in repeat 2
    assert avg.by_author["llm"]["m2"].value is None


def test_averaged_concatenates_delays_and_averages_context_and_latency():
    one = replace(M(retraction=(10.0,), context_chars_mean=100.0), record_ms_p50=1.0, search_ms_p95=4.0)
    two = replace(M(retraction=(30.0, None), context_chars_mean=200.0), record_ms_p50=3.0,
                  search_ms_p95=None)
    avg = averaged([one, two])
    assert [d.seconds for d in avg.retraction] == [10.0, 30.0, None]
    assert avg.context_chars_mean == pytest.approx(150.0)
    assert avg.record_ms_p50 == pytest.approx(2.0)
    assert avg.search_ms_p95 == pytest.approx(4.0)  # a repeat without a sample does not erase the others
    assert avg.search_ms_p50 is None  # nobody had one


def test_averaged_repeat_mistake_reduction_uses_mean_fn():
    # promote_fn per repeat 3/3, 0/3, 0/3: the mean (1/3) gives 0.667, repeat 1 alone gave 0.0.
    repeats = [replace(M(), promote_fn=K(k, 3)) for k in (3, 0, 0)]
    out = relative_metrics(averaged(repeats), M(promote_fn=1.0), M(m1=0.6), M(m1=0.0))
    assert out["repeat_mistake_reduction"] == pytest.approx(1 - (3 / 9) / 1.0)


def test_averaged_handles_a_repeat_with_zero_denominator():
    empty = K(0, 0)
    one = replace(M(), reach=empty, promote_fp=empty, by_family={"x": FamilyRow(m1=empty, m2=empty, m3=0)})
    two = replace(M(), reach=K(1, 2), promote_fp=empty,
                  by_family={"x": FamilyRow(m1=K(1, 2), m2=empty, m3=0)})
    avg = averaged([one, two])
    assert (avg.reach.k, avg.reach.n, avg.reach.value) == (1, 2, 0.5)
    assert avg.promote_fp.value is None and avg.promote_fp.ci is None  # every denominator 0
    assert avg.by_family["x"]["m2"].value is None
    assert avg.by_family["x"]["m1"].value == pytest.approx(0.5)
    assert avg.worst_family == ("x", 0.5)
    none_only = averaged([one, one])
    assert none_only.worst_family is None and none_only.reach.value is None
    for r in (avg.reach, avg.promote_fp, avg.by_family["x"]["m1"]):
        assert r.value is None or 0.0 <= r.value <= 1.0
