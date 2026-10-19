from datetime import timedelta

from harness.agent import Choice
from harness.contract import Result
from harness.replay import ProbeRecord, ScenarioRun
from harness.scenario.model import Provenance
from harness.scoring import Leak
from harness.metrics import compute_metrics, flip_rate, per_scenario_counts
from harness.stats import Delay
from tests.factories import T, ev, fact, probe, scenario


def rec(probe_id, kind, outcome, *, basis=(), bases=None, leaks=0, contaminated=False,
        reached=False, search_ms=1.0, context_chars=10, results=None, index=0):
    basis = frozenset(basis)
    return ProbeRecord(
        probe_id=probe_id,
        kind=kind,
        results=results if results is not None else [Result(text="x", sources=["e"])],
        choice=Choice(index if basis else None, basis),
        outcome=outcome,
        leaks=[Leak(0, f"h{i}", "source") for i in range(leaks)],
        bases=bases if bases is not None else [basis],
        contaminated=contaminated,
        reached=reached,
        search_ms=search_ms,
        context_chars=context_chars,
    )


def run(sid, probes, *, family="truth_change", mode="read_only", error=None, record_ms=()):
    return ScenarioRun(sid, family, mode, list(probes), list(record_ms), [], error)


def sc(sid, *, family="truth_change", mode="read_only", author="human", facts=None, probes=None):
    facts = facts if facts is not None else {"f": fact()}
    probes = probes if probes is not None else [probe("p", "block", T("10-05 10:00"), ["f"])]
    return scenario(facts, [ev("e", T("10-01 10:00"), fact="f")], probes, id=sid,
                    family=family, mode=mode, provenance=Provenance(author=author))


def scs(*scenarios):
    return {s.id: s for s in scenarios}


def test_m1_m2_pooled():
    s1, s2 = sc("s1"), sc("s2")
    runs = [
        run("s1", [rec("p", "block", "FP"), rec("q", "block", "BLOCKED")]),
        run("s2", [rec("p", "promote", "TP")]),
    ]
    m = compute_metrics(runs, scs(s1, s2))
    assert (m.m1.k, m.m1.n, m.m1.value) == (1, 2, 0.5)
    assert (m.m2.k, m.m2.n, m.m2.value) == (1, 1, 1.0)
    assert m.errors == []
    assert m.by_family["truth_change"]["m1"].value == 0.5
    assert m.by_family["truth_change"]["m2"].value == 1.0
    assert m.by_family["truth_change"]["m3"] == 0


def test_promote_fp_and_fn_are_not_tp():
    s = sc("s1")
    r = run("s1", [rec("a", "promote", "PROMOTE_FP"), rec("b", "promote", "FN"),
                   rec("c", "promote", "TP"), rec("d", "leak", "LEAK_ONLY")])
    m = compute_metrics([r], scs(s))
    assert (m.m2.k, m.m2.n) == (1, 3)
    assert m.m1.n == 0 and m.m1.value is None


def test_feedback_runs_excluded_from_m1():
    s1 = sc("s1")
    s2 = sc("s2", family="echo", mode="feedback")
    runs = [
        run("s1", [rec("p", "block", "BLOCKED")]),
        run("s2", [rec("p", "block", "FP", leaks=1)], family="echo", mode="feedback"),
    ]
    m = compute_metrics(runs, scs(s1, s2))
    assert (m.m1.k, m.m1.n) == (0, 1)
    assert "echo" not in m.by_family
    assert m.feedback_by_family["echo"]["m1"].k == 1
    assert m.feedback_by_family["echo"]["m1"].n == 1
    assert m.feedback_by_family["echo"]["m2"].value is None
    assert m.feedback_by_family["echo"]["m3"] == 1
    assert m.worst_family == ("truth_change", 0.0)


def test_m3_counts_both_modes_and_leaking_results():
    s1 = sc("s1")
    s2 = sc("s2", family="echo", mode="feedback")
    # one result leaking through two reasons still counts as a single leaking result
    two_reasons = rec("p", "leak", "LEAK_ONLY", results=[Result(text="a", sources=[]), Result(text="b", sources=[])])
    two_reasons.leaks = [Leak(1, "h1", "source"), Leak(1, "h1", "secret_marker"), Leak(0, "h2", "source")]
    runs = [
        run("s1", [two_reasons]),
        run("s2", [rec("p", "block", "BLOCKED", leaks=1)], family="echo", mode="feedback"),
    ]
    m = compute_metrics(runs, scs(s1, s2))
    assert m.m3 == 3


def test_errored_runs_excluded():
    s1 = sc("s1")
    r = run("s1", [rec("p", "block", "FP", leaks=2)], error="boom", record_ms=[5.0])
    m = compute_metrics([r], scs(s1))
    assert m.m1.value is None and m.m2.value is None
    assert m.m3 == 0
    assert m.errors == ["s1"]
    assert m.by_family == {} and m.feedback_by_family == {}
    assert m.worst_family is None
    assert m.context_chars_mean is None
    assert m.record_ms_p50 is None and m.search_ms_p95 is None


def test_errors_sorted():
    ss = [sc("b"), sc("a")]
    runs = [run("b", [], error="x"), run("a", [], error="y")]
    assert compute_metrics(runs, scs(*ss)).errors == ["a", "b"]


def test_by_author_split():
    human = sc("h1", author="human")
    llm = sc("l1", author="llm")
    runs = [
        run("h1", [rec("p", "block", "BLOCKED"), rec("q", "promote", "TP")]),
        run("l1", [rec("p", "block", "FP"), rec("q", "promote", "FN")]),
    ]
    m = compute_metrics(runs, scs(human, llm))
    assert m.by_author["human"]["m1"].value == 0.0
    assert m.by_author["llm"]["m1"].value == 1.0
    assert m.by_author["human"]["m2"].value == 1.0
    assert m.by_author["llm"]["m2"].value == 0.0
    assert "generator" not in m.by_author


def test_worst_family_max_with_name_tiebreak():
    ss = [sc("a", family="coincidence"), sc("b", family="conditional"), sc("c", family="truth_change")]
    runs = [
        run("a", [rec("p", "block", "FP")], family="coincidence"),
        run("b", [rec("p", "block", "FP")], family="conditional"),
        run("c", [rec("p", "block", "BLOCKED")], family="truth_change"),
    ]
    m = compute_metrics(runs, scs(*ss))
    assert m.worst_family == ("coincidence", 1.0)


def test_worst_family_skips_undefined_m1():
    s = sc("a", family="conditional")
    m = compute_metrics([run("a", [rec("p", "promote", "TP")], family="conditional")], scs(s))
    assert m.worst_family is None


def test_shallow_fix():
    facts = {"root": fact(fix="root"), "wa": fact(fix="workaround"),
             "old_root": fact(fix="root", valid_until=T("10-03 10:00")), "plain": fact()}
    probes = [
        probe("p1", "block", T("10-05 10:00"), ["root", "wa"]),
        probe("p2", "promote", T("10-05 10:00"), ["root", "wa"]),
        probe("p3", "block", T("10-05 10:00"), ["old_root", "wa"]),  # root already false: not in denominator
        probe("p4", "block", T("10-05 10:00"), ["plain"]),
        probe("p5", "leak", T("10-05 10:00"), ["root"]),
    ]
    s = sc("s1", facts=facts, probes=probes)
    r = run("s1", [
        rec("p1", "block", "FP", basis={"wa"}),
        rec("p2", "promote", "TP", basis={"root"}),
        rec("p3", "block", "FP", basis={"wa"}),
        rec("p4", "block", "BLOCKED"),
        rec("p5", "leak", "LEAK_ONLY", basis={"wa"}),
    ])
    m = compute_metrics([r], scs(s))
    assert (m.shallow_fix.k, m.shallow_fix.n) == (1, 2)


def test_reach_and_contamination():
    s = sc("s1")
    r = run("s1", [
        rec("a", "promote", "TP", reached=True),
        rec("b", "promote", "FN"),
        rec("c", "block", "FP", contaminated=True),
        rec("d", "block", "BLOCKED"),
        rec("e", "leak", "LEAK_ONLY", reached=True, contaminated=True),
    ])
    m = compute_metrics([r], scs(s))
    assert (m.reach.k, m.reach.n) == (1, 2)
    assert (m.contamination.k, m.contamination.n) == (1, 4)


def test_sourceless_uses_all_modes_and_all_results():
    s1, s2 = sc("s1"), sc("s2", family="echo", mode="feedback")
    r1 = run("s1", [rec("a", "block", "BLOCKED", results=[Result(text="x", sources=[]), Result(text="y", sources=["e"])])])
    r2 = run("s2", [rec("a", "block", "BLOCKED", results=[Result(text="z", sources=[])])], family="echo", mode="feedback")
    m = compute_metrics([r1, r2], scs(s1, s2))
    assert (m.sourceless.k, m.sourceless.n) == (2, 3)


def test_retraction_delay():
    facts = {"f_old": fact(valid_until=T("10-09 10:00")), "f_new": fact(valid_from=T("10-09 10:00"))}
    probes = [
        probe("early", "block", T("10-08 10:00"), ["f_old"]),
        probe("p1", "block", T("10-10 10:00"), ["f_old"]),
        probe("p2", "block", T("10-12 10:00"), ["f_old"]),
        probe("p3", "block", T("10-13 10:00"), ["f_old"]),
    ]
    s = sc("s1", facts=facts, probes=probes)
    r = run("s1", [
        rec("early", "block", "BLOCKED", bases=[frozenset({"f_old"})]),
        rec("p1", "block", "FP", bases=[frozenset({"f_old"}), frozenset()]),
        rec("p2", "block", "BLOCKED", bases=[frozenset(), frozenset({"f_new"})]),
        rec("p3", "block", "BLOCKED", bases=[]),
    ])
    m = compute_metrics([r], scs(s))
    assert m.retraction == [Delay("f_old", "s1", 259200.0)]
    assert m.retraction[0].seconds == timedelta(days=3).total_seconds()


def test_retraction_never_retracted_and_no_probe():
    facts = {"a": fact(valid_until=T("10-09 10:00")), "b": fact(valid_until=T("10-20 10:00")),
             "c": fact()}
    probes = [
        probe("p1", "block", T("10-10 10:00"), ["a"]),
        probe("p2", "block", T("10-11 10:00"), ["a", "b", "c"]),
        probe("p3", "promote", T("10-12 10:00"), ["a"]),
        probe("p4", "leak", T("10-13 10:00"), ["a"]),
    ]
    s = sc("s1", facts=facts, probes=probes)
    still_a = [frozenset({"a"})]
    r = run("s1", [
        rec("p1", "block", "FP", bases=still_a),
        rec("p2", "block", "FP", bases=still_a),
        rec("p3", "promote", "PROMOTE_FP", bases=still_a),
        rec("p4", "leak", "LEAK_ONLY", bases=[]),  # leak probes are ignored
    ])
    m = compute_metrics([r], scs(s))
    assert m.retraction == [Delay("a", "s1", None)]  # b has no probe after its end: no entry; c is infinite


def test_retraction_at_exact_valid_until_counts():
    facts = {"f": fact(valid_until=T("10-09 10:00"))}
    probes = [probe("p1", "block", T("10-09 10:00"), ["f"])]
    s = sc("s1", facts=facts, probes=probes)
    r = run("s1", [rec("p1", "block", "BLOCKED", bases=[])])
    assert compute_metrics([r], scs(s)).retraction == [Delay("f", "s1", 0.0)]


def test_reflection_delay():
    facts = {"f_new": fact(valid_from=T("10-09 10:00")), "g": fact()}
    probes = [
        probe("before", "promote", T("10-08 10:00"), ["f_new"]),
        probe("p1", "promote", T("10-09 10:00"), ["f_new"]),
        probe("p2", "promote", T("10-11 10:00"), ["f_new"]),
    ]
    s = sc("s1", facts=facts, probes=probes)
    r = run("s1", [
        rec("before", "promote", "FN", bases=[frozenset({"f_new"})]),  # before V: ignored
        rec("p1", "promote", "FN", bases=[frozenset()]),
        rec("p2", "promote", "TP", bases=[frozenset(), frozenset({"f_new"})]),
    ])
    m = compute_metrics([r], scs(s))
    assert m.reflection == [Delay("f_new", "s1", 172800.0)]
    assert m.retraction == []


def test_reflection_never_reflected():
    facts = {"f": fact(valid_from=T("10-09 10:00"))}
    s = sc("s1", facts=facts, probes=[probe("p1", "promote", T("10-10 10:00"), ["f"])])
    r = run("s1", [rec("p1", "promote", "FN", bases=[])])
    assert compute_metrics([r], scs(s)).reflection == [Delay("f", "s1", None)]


def test_delays_exclude_feedback_runs_and_errors():
    facts = {"f": fact(valid_until=T("10-09 10:00"))}
    probes = [probe("p1", "block", T("10-10 10:00"), ["f"])]
    s1 = sc("s1", facts=facts, probes=probes, family="echo", mode="feedback")
    s2 = sc("s2", facts=facts, probes=probes)
    r1 = run("s1", [rec("p1", "block", "BLOCKED", bases=[])], family="echo", mode="feedback")
    r2 = run("s2", [rec("p1", "block", "BLOCKED", bases=[])], error="boom")
    assert compute_metrics([r1, r2], scs(s1, s2)).retraction == []


def test_performance_numbers():
    s1, s2 = sc("s1"), sc("s2", family="echo", mode="feedback")
    r1 = run("s1", [rec("a", "block", "BLOCKED", search_ms=1.0, context_chars=10),
                    rec("b", "block", "BLOCKED", search_ms=3.0, context_chars=30)],
             record_ms=[1.0, 2.0, 3.0, 4.0])
    r2 = run("s2", [rec("a", "block", "BLOCKED", search_ms=2.0, context_chars=20)],
             family="echo", mode="feedback", record_ms=[5.0])
    r3 = run("s1", [rec("a", "block", "BLOCKED", search_ms=999.0)], error="x", record_ms=[999.0])
    m = compute_metrics([r1, r2, r3], scs(s1, s2))
    assert m.context_chars_mean == 20.0
    # record_ms sorted: 1,2,3,4,5 -> p50 = ceil(2.5)=3rd, p95 = ceil(4.75)=5th
    assert (m.record_ms_p50, m.record_ms_p95) == (3.0, 5.0)
    # search_ms sorted: 1,2,3
    assert (m.search_ms_p50, m.search_ms_p95) == (2.0, 3.0)


def test_per_scenario_counts():
    s1, s2, s3 = sc("s2"), sc("s1"), sc("s3", family="echo", mode="feedback")
    runs = [
        run("s2", [rec("a", "block", "FP"), rec("b", "block", "BLOCKED")]),
        run("s1", [rec("a", "promote", "TP")]),
        run("s3", [rec("a", "block", "FP")], family="echo", mode="feedback"),
        run("s4", [rec("a", "block", "FP")], error="x"),
    ]
    assert per_scenario_counts(runs, "block", "FP") == [(0, 0), (1, 2)]
    assert per_scenario_counts(runs, "promote", "TP") == [(1, 1), (0, 0)]


def test_flip_rate():
    def results(*texts):
        return [Result(text=t, sources=["e"]) for t in texts]

    def rep(texts_by_probe, sid="s1", error=None):
        return run(sid, [rec(p, "block", "BLOCKED", results=results(*t)) for p, t in texts_by_probe.items()],
                   error=error)

    repeats = [
        [rep({"a": ["x"], "b": ["y", "z"]})],
        [rep({"a": ["x"], "b": ["y", "w"]})],
        [rep({"a": ["x"], "b": ["y", "z"]})],
    ]
    r = flip_rate(repeats)
    assert (r.k, r.n) == (1, 2)


def test_flip_rate_sources_matter_and_alignment():
    def one(sources, probe_id="a", error=None, sid="s1"):
        res = [Result(text="x", sources=sources)]
        return [run(sid, [rec(probe_id, "block", "BLOCKED", results=res)], error=error)]

    assert flip_rate([one(["e1"]), one(["e2"])]).k == 1
    # probe missing from one repeat is not aligned
    r = flip_rate([one(["e1"]), one(["e1"], probe_id="b")])
    assert (r.k, r.n) == (0, 0)
    # errored runs are not aligned
    r = flip_rate([one(["e1"]), one(["e2"], error="x")])
    assert (r.k, r.n) == (0, 0)
    assert flip_rate([]).value is None
