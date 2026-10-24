import re
from datetime import timedelta
from pathlib import Path

from harness.baselines.simple import AllStoreEngine, NoneEngine
from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Result
from harness.agent import Choice
from harness.replay import feedback_events, run_scenario
from harness.scenario.load import load_scenario
from harness.scenario.model import Feedback, FeedbackTemplate
from tests.factories import T, ev, fact, probe, scenario

CFG = EvalConfig()


def _tmpl(text, **kw):
    return FeedbackTemplate(text=text, **kw)


def _feedback():
    return Feedback(
        action={
            "f_old": _tmpl("applied alpha deploy fix", fact="f_act_old"),
            "f_new": _tmpl("applied beta deploy fix", fact="f_act_new"),
        },
        abstain=_tmpl("gave up on the deploy question", fact="f_abstain"),
        on_false=_tmpl("deploy failed again", signal="test", outcome="failure", fact="f_fail"),
        on_true=_tmpl("deploy passed", signal="test", outcome="success", fact="f_ok"),
    )


def _scenario(mode="feedback", extra_probes=()):
    return scenario(
        family="echo",
        mode=mode,
        facts={
            "f_old": fact(valid_until=T("10-05 00:00")),
            "f_new": fact(valid_from=T("10-05 00:00")),
        },
        events=[ev("e_old", T("10-01 10:00"), fact="f_old", text="deploy host alpha")],
        probes=[
            probe("p1", "block", T("10-10 10:00"), ["f_old", "f_new"], query="alpha host",
                  feedback=_feedback()),
            *extra_probes,
        ],
    )


def gen(run, probe_id, slot):
    """The generated event for (probe, slot), looked up through the harness-side mapping."""
    event_id = run.generated_ids[(probe_id, slot)]
    return next(le for le in run.generated if le.event.id == event_id)


def test_action_event_follows_choice():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    assert run.error is None
    action = gen(run, "p1", "action")
    assert action.fact == "f_act_old"
    assert action.event.t == T("10-10 10:05")
    assert action.event.user == "u2"
    assert action.event.session == "s9"
    assert action.event.repo == "r"
    assert action.event.team == "a"
    assert action.event.text == "applied alpha deploy fix"
    assert action.secret_markers == ()


def test_abstain_template_used():
    run = run_scenario(NoneEngine(), _scenario(), run_id="r", cfg=CFG)
    assert [le.event.id for le in run.generated] == [run.generated_ids[("p1", "action")]]
    assert list(run.generated_ids) == [("p1", "action")]
    assert run.generated[0].event.text == "gave up on the deploy question"
    assert run.generated[0].fact == "f_abstain"


def test_on_false_outcome_after_one_day():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    outcome = gen(run, "p1", "outcome")
    assert outcome.event.t == T("10-10 10:00") + timedelta(days=1)
    assert outcome.event.signal == "test"
    assert outcome.event.outcome == "failure"
    assert outcome.fact == "f_fail"


def test_on_true_outcome_immediate_and_template_delay():
    s = _scenario()
    s.probes[0].feedback.on_true = _tmpl("deploy passed", outcome="success", delay=timedelta(hours=2))
    s.probes[0].feedback.action["f_new"] = _tmpl("beta", delay=timedelta(minutes=1))
    s.probes[0] = s.probes[0].model_copy(update={"query": "beta host"})
    s.events.append(ev("e_new", T("10-06 10:00"), fact="f_new", text="deploy host beta"))
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert gen(run, "p1", "outcome").event.outcome == "success"
    assert gen(run, "p1", "outcome").event.t == T("10-10 12:00")
    assert gen(run, "p1", "action").event.t == T("10-10 10:01")


def test_explicit_template_delay_is_clamped_to_action_time():
    s = _scenario()
    s.probes[0].feedback.on_true = _tmpl("deploy passed", outcome="success", delay=timedelta(0))
    s.probes[0].feedback.action["f_new"] = _tmpl("beta", delay=timedelta(minutes=1))
    s.probes[0] = s.probes[0].model_copy(update={"query": "beta host"})
    s.events.append(ev("e_new", T("10-06 10:00"), fact="f_new", text="deploy host beta"))
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert gen(run, "p1", "outcome").event.t == T("10-10 10:01")


class _OrderRecorder(AllStoreEngine):
    def __init__(self):
        super().__init__()
        self.log = []

    def record(self, event):
        self.log.append((event.t, event.text))
        super().record(event)


def _true_follow_scenario(*, with_action=True, on_true_delay=None):
    fb = Feedback(
        action={"f_new": _tmpl("ACTION did it", fact="f_act")} if with_action else {},
        on_true=_tmpl("OUTCOME passed", signal="test", outcome="success", delay=on_true_delay),
        on_false=_tmpl("OUTCOME failed", signal="test", outcome="failure"),
    )
    return scenario(
        family="echo",
        mode="feedback",
        facts={"f_new": fact(valid_from=T("10-05 00:00"))},
        events=[ev("e_new", T("10-06 10:00"), fact="f_new", text="deploy host beta")],
        probes=[probe("p1", "promote", T("10-10 10:00"), ["f_new"], query="beta host", feedback=fb)],
    )


def test_outcome_is_never_recorded_before_its_action():
    engine = _OrderRecorder()
    run = run_scenario(engine, _true_follow_scenario(), run_id="r", cfg=CFG)
    assert run.error is None
    assert gen(run, "p1", "outcome").event.t == gen(run, "p1", "action").event.t == T("10-10 10:05")
    texts = [text for _, text in engine.log]
    assert texts.index("ACTION did it") < texts.index("OUTCOME passed")
    times = [t for t, _ in engine.log]
    assert times == sorted(times)


def test_same_time_outcome_follows_action_in_insertion_order():
    s = _true_follow_scenario(on_true_delay=timedelta(minutes=5))  # exactly the action time
    engine = _OrderRecorder()
    run = run_scenario(engine, s, run_id="r", cfg=CFG)
    assert gen(run, "p1", "outcome").event.t == gen(run, "p1", "action").event.t
    texts = [text for _, text in engine.log]
    assert texts[-2:] == ["ACTION did it", "OUTCOME passed"]
    # the action is built first, so the timeline's insertion order keeps it ahead
    events = feedback_events(
        s.probes[0], Choice(index=0, basis=frozenset({"f_new"})), s, CFG, judged=["e_new"]
    )
    assert [slot for slot, _ in events] == ["action", "outcome"]


def test_failure_outcome_time_is_unchanged():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    assert gen(run, "p1", "action").event.t == T("10-10 10:05")
    assert gen(run, "p1", "outcome").event.t == T("10-11 10:00")


def test_outcome_counts_from_probe_time_when_no_action_record():
    s = _true_follow_scenario(with_action=False)
    engine = _OrderRecorder()
    run = run_scenario(engine, s, run_id="r", cfg=CFG)
    assert ("p1", "action") not in run.generated_ids
    assert gen(run, "p1", "outcome").event.t == T("10-10 10:00")
    s = _true_follow_scenario(with_action=False, on_true_delay=timedelta(hours=2))
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert gen(run, "p1", "outcome").event.t == T("10-10 12:00")


def test_missing_templates_generate_nothing():
    s = _scenario()
    s.probes[0].feedback = Feedback()
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert run.generated == []


class TwoSourceEngine(BaseEngine):
    def info(self):
        return EngineInfo(name="two", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        pass

    def search(self, query):
        return [Result(text="merged", sources=["e_new", "e_old"])]


def test_wrong_fact_preferred_for_action_template():
    s = scenario(
        family="echo",
        mode="feedback",
        facts={
            "f_a_new": fact(valid_from=T("10-05 00:00")),
            "f_b_old": fact(valid_until=T("10-05 00:00")),
        },
        events=[
            ev("e_old", T("10-01 10:00"), fact="f_b_old"),
            ev("e_new", T("10-06 10:00"), fact="f_a_new"),
        ],
        probes=[
            probe(
                "p1", "block", T("10-10 10:00"), ["f_a_new", "f_b_old"],
                feedback=Feedback(
                    action={"f_a_new": _tmpl("followed new"), "f_b_old": _tmpl("followed old")},
                ),
            )
        ],
    )
    run = run_scenario(TwoSourceEngine(), s, run_id="r", cfg=CFG)
    assert [le.event.text for le in run.generated] == ["followed old"]


class RecordingEngine(BaseEngine):
    def __init__(self):
        self.calls: list[tuple] = []

    def info(self):
        return EngineInfo(name="rec", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        self.calls.append(("record", event.id))

    def search(self, query):
        self.calls.append(("search", query.text))
        return [Result(text="deploy host alpha", sources=["e_old"])]


def test_generated_event_reaches_engine():
    p2 = probe("p2", "block", T("10-10 12:00"), ["f_old", "f_new"], query="second query")
    eng = RecordingEngine()
    run = run_scenario(eng, _scenario(extra_probes=[p2]), run_id="r", cfg=CFG)
    assert run.error is None
    action = ("record", run.generated_ids[("p1", "action")])
    outcome = ("record", run.generated_ids[("p1", "outcome")])
    assert action in eng.calls
    assert eng.calls.index(action) < eng.calls.index(("search", "second query"))
    assert eng.calls.index(("search", "alpha host")) < eng.calls.index(action)
    # on_false lands a day later, i.e. after p2 (10-10 12:00)
    assert eng.calls.index(("search", "second query")) < eng.calls.index(outcome)


def test_no_marker_of_echo_in_generated():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    for le in run.generated:
        assert le.event.meta == {}
    assert gen(run, "p1", "action").event.text == "applied alpha deploy fix"
    assert gen(run, "p1", "outcome").event.text == "deploy failed again"


def test_read_only_never_generates():
    run = run_scenario(AllStoreEngine(), _scenario(mode="read_only"), run_id="r", cfg=CFG)
    assert run.generated == []


def test_probe_without_feedback_generates_nothing():
    s = _scenario()
    s.probes[0] = s.probes[0].model_copy(update={"feedback": None})
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert run.generated == []


def test_generated_ids_reveal_neither_harness_nor_probe():
    p2 = probe("p2", "block", T("10-10 12:00"), ["f_old", "f_new"], query="second query")
    s = _scenario(extra_probes=[p2])
    eng = RecordingEngine()
    run = run_scenario(eng, s, run_id="r", cfg=CFG)
    recorded = [eid for call, eid in eng.calls if call == "record"]
    generated = set(run.generated_ids.values())
    assert generated and generated <= set(recorded)
    for eid in recorded:
        assert "#" not in eid
        assert not any(p.id in eid for p in s.probes)
    # one scenario event (e_old), so numbering starts at e2, in the order the records are made
    assert run.generated_ids == {("p1", "action"): "e2", ("p1", "outcome"): "e3"}


def test_generated_id_skips_ids_in_use():
    s = _scenario()
    s.events.append(ev("e3", T("10-01 11:00"), text="unrelated"))
    s.events.append(ev("e4", T("10-01 12:00"), text="unrelated too"))
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    # 3 scenario events -> starts at e4, which is taken; the outcome then starts at e5 (taken now)
    assert run.generated_ids == {("p1", "action"): "e5", ("p1", "outcome"): "e6"}
    ids = [le.event.id for le in run.generated]
    assert len(ids) == len(set(ids))


SHAPE_CLASSIFIERS = [
    r"^e\d{1,3}$",
    r"^e[0-9a-f]{12}(-\d+)?$",  # the old hashed ids (review finding D13)
    r"^e\d+$",
    r"-\d+$",
    r"^e\d{2,}$",
    r"^[a-z]+\d+$",
]


def _separable(pattern: str, generated: set[str], authored: set[str]) -> bool:
    """Does matching `pattern` split generated ids from authored ones (either way round)?"""
    def hit(i):
        return re.search(pattern, i) is not None
    return (all(map(hit, generated)) and not any(map(hit, authored))) or (
        not any(map(hit, generated)) and all(map(hit, authored))
    )


class _SeenIds(AllStoreEngine):
    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    def record(self, event):
        self.seen.append(event.id)
        super().record(event)


def test_generated_ids_look_like_scenario_ids_and_never_collide():
    s = load_scenario(Path("scenarios/examples/echo_stale_owner.yaml"))
    eng = _SeenIds()
    run = run_scenario(eng, s, run_id="r", cfg=CFG)
    generated = set(run.generated_ids.values())
    authored = {e.id for e in s.events}
    assert generated and not generated & authored
    assert set(eng.seen) == generated | authored
    shape = {re.sub(r"\d+", "#", i) for i in generated}
    assert shape <= {re.sub(r"\d+", "#", i) for i in authored}
    for pattern in SHAPE_CLASSIFIERS:
        assert not _separable(pattern, generated, authored), pattern

    # Authors may use any id, including ones the numbering would reach.
    odd = _scenario(extra_probes=[
        probe("p2", "block", T("10-10 12:00"), ["f_old", "f_new"], query="alpha host", feedback=_feedback()),
    ])
    odd.events += [ev("tc_o1", T("10-01 11:00"), text="x"), ev("e_new", T("10-01 12:00"), text="y"),
                   ev("e5", T("10-01 13:00"), text="z")]
    run = run_scenario(AllStoreEngine(), odd, run_id="r", cfg=CFG)
    ids = list(run.generated_ids.values())
    assert len(ids) == len(set(ids)) == 4
    assert not set(ids) & {e.id for e in odd.events}
    assert all(re.fullmatch(r"e\d+", i) for i in ids)
    assert ids == ["e6", "e7", "e8", "e9"]  # 4 events: start at e5 (taken), then count on


def test_generated_ids_are_independent_of_probe_ids():
    p2 = probe("p2", "block", T("10-10 12:00"), ["f_old", "f_new"], query="alpha host",
               feedback=_feedback())
    first = run_scenario(AllStoreEngine(), _scenario(extra_probes=[p2]), run_id="r", cfg=CFG)
    s = _scenario(extra_probes=[p2.model_copy(update={"id": "p_promote_zz"})])
    s.probes[0] = s.probes[0].model_copy(update={"id": "p_block_q9"})
    s = s.model_copy(update={"id": "another-scenario"})
    second = run_scenario(AllStoreEngine(), s, run_id="other", cfg=CFG)
    assert len(first.generated) == 4
    assert [le.event.id for le in first.generated] == [le.event.id for le in second.generated]
    assert second.generated_ids[("p_block_q9", "action")] == first.generated_ids[("p1", "action")]


class _Latest(BaseEngine):
    """Returns the latest recorded event the asker may see, citing it."""

    def info(self):
        return EngineInfo(name="latest", version="0")

    def reset(self, namespace):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def search(self, query):
        seen = [e for e in self.events if e.t <= query.t]
        return [Result(text=e.text, sources=[e.id]) for e in seen[-1:]]


def test_generated_event_refs_and_scoring_survive_the_id_change():
    p2 = probe("p2", "promote", T("10-12 10:00"), ["f_fail"], query="did the deploy fail")
    s = _scenario(extra_probes=[p2])
    s.facts["f_fail"] = fact()
    s.facts["f_act_old"] = fact()
    run = run_scenario(_Latest(), s, run_id="r", cfg=CFG)
    assert run.error is None
    assert run.generated_ids == {("p1", "action"): "e2", ("p1", "outcome"): "e3"}
    outcome = gen(run, "p1", "outcome")
    assert outcome.event.id == "e3" and outcome.fact == "f_fail"
    by_id = {r.probe_id: r for r in run.probes}
    assert outcome.event.refs == tuple(by_id["p1"].results[by_id["p1"].choice.index].sources) == ("e_old",)
    # the later probe follows the generated outcome record; scoring maps it to its hidden fact
    assert by_id["p2"].results[0].sources == ["e3"]
    assert by_id["p2"].bases == [frozenset({"f_fail"})]
    assert by_id["p2"].outcome == "TP"


def test_outcome_record_refs_the_followed_result_sources():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    assert gen(run, "p1", "outcome").event.refs == ("e_old",)


def test_action_record_has_no_refs():
    run = run_scenario(AllStoreEngine(), _scenario(), run_id="r", cfg=CFG)
    assert gen(run, "p1", "action").event.refs == ()


def test_empty_basis_counts_as_abstain_like_judge():
    s = _scenario()
    slots = feedback_events(s.probes[0], Choice(0, frozenset()), s, CFG)
    assert [slot for slot, _ in slots] == ["action"]
    assert slots[0][1].event.text == "gave up on the deploy question"


# --- the action record is picked inside the probe's `about` facts (PR #2 review, item 3) ---


def _outside_about_scenario():
    """About f_old/f_new; a_style (true) and a_false (false at the probe) sit outside it."""
    fb = Feedback(
        action={
            "f_old": _tmpl("applied alpha deploy fix", fact="f_act_old"),
            "f_new": _tmpl("applied beta deploy fix", fact="f_act_new"),
        },
        on_false=_tmpl("deploy failed again", signal="test", outcome="failure"),
        on_true=_tmpl("deploy passed", signal="test", outcome="success"),
    )
    return scenario(
        family="echo",
        mode="feedback",
        facts={
            "a_style": fact(),
            "a_false": fact(valid_until=T("10-05 00:00")),
            "f_old": fact(valid_until=T("10-05 00:00")),
            "f_new": fact(valid_from=T("10-05 00:00")),
        },
        events=[ev("e_new", T("10-06 10:00"), fact="f_new")],
        probes=[probe("pb1", "block", T("10-10 10:00"), ["f_old", "f_new"], feedback=fb)],
    )


def _texts(s, basis):
    events = feedback_events(s.probes[0], Choice(0, frozenset(basis)), s, CFG, judged=["e_new"])
    return [(slot, le.event.text) for slot, le in events], events


def test_action_template_is_picked_inside_about_when_basis_has_outside_facts():
    s = _outside_about_scenario()
    texts, events = _texts(s, {"a_style", "f_new"})  # the review's repro
    assert texts == [("action", "applied beta deploy fix"), ("outcome", "deploy passed")]
    # the outcome record is aligned to the action record's time
    assert events[1][1].event.t == events[0][1].event.t == T("10-10 10:05")


def test_wrong_fact_outside_about_does_not_hide_the_action_record():
    s = _outside_about_scenario()
    texts, _ = _texts(s, {"a_false", "f_new"})
    # a_false is false but not in about: the action follows the about fact; the basis is still
    # wrong as a whole, so the outcome is the failure record
    assert texts == [("action", "applied beta deploy fix"), ("outcome", "deploy failed again")]


def test_wrong_about_fact_is_still_preferred_inside_about():
    s = _outside_about_scenario()
    texts, _ = _texts(s, {"a_style", "f_old", "f_new"})
    assert texts[0] == ("action", "applied alpha deploy fix")  # f_old is wrong, f_new is not
    # basis wholly inside about: unchanged behaviour
    texts, _ = _texts(s, {"f_old", "f_new"})
    assert texts[0] == ("action", "applied alpha deploy fix")
    texts, _ = _texts(s, {"f_new"})
    assert texts[0] == ("action", "applied beta deploy fix")


def test_feedback_events_with_outside_facts_are_deterministic():
    s = _outside_about_scenario()
    ref = None
    for basis in ({"a_style", "f_new"}, {"f_new", "a_style"}, frozenset(["a_style", "f_new"])):
        events = feedback_events(s.probes[0], Choice(0, frozenset(basis)), s, CFG, judged=["e_new"])
        shape = [(slot, le.event.id, le.event.t, le.event.text, le.fact) for slot, le in events]
        assert ref is None or shape == ref
        ref = shape
    assert [slot for slot, *_ in ref] == ["action", "outcome"]


class _OutsideFactEngine(BaseEngine):
    def info(self):
        return EngineInfo(name="outside", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        pass

    def search(self, query):
        return [Result(text="merged", sources=["e_style", "e_new"])]


def test_run_records_the_action_when_the_followed_result_states_a_fact_outside_about():
    s = _outside_about_scenario()
    s.events.insert(0, ev("e_style", T("10-01 10:00"), fact="a_style"))
    run = run_scenario(_OutsideFactEngine(), s, run_id="r", cfg=CFG)
    assert run.error is None
    assert set(run.generated_ids) == {("pb1", "action"), ("pb1", "outcome")}
    assert gen(run, "pb1", "action").event.text == "applied beta deploy fix"
    assert gen(run, "pb1", "outcome").event.t == gen(run, "pb1", "action").event.t
