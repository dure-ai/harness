import hashlib
from datetime import timedelta

from harness.baselines.simple import AllStoreEngine, NoneEngine
from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Result
from harness.agent import Choice
from harness.replay import feedback_events, run_scenario
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
    expected = "e" + hashlib.sha256(b"sc:p1:action").hexdigest()[:12]
    assert run.generated_ids[("p1", "action")] == expected


def test_generated_id_collision_gets_suffix():
    taken = "e" + hashlib.sha256(b"sc:p1:action").hexdigest()[:12]
    s = _scenario()
    s.events.append(ev(taken, T("10-01 11:00"), text="unrelated"))
    s.events.append(ev(f"{taken}-2", T("10-01 12:00"), text="unrelated too"))
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=CFG)
    assert run.generated_ids[("p1", "action")] == f"{taken}-3"
    ids = [le.event.id for le in run.generated]
    assert len(ids) == len(set(ids))


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
