import hashlib
import hmac
import re
from datetime import datetime

import pytest

from harness.baselines.oracle import OracleEngine
from harness.baselines.simple import AllStoreEngine
from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.replay import DEFAULT_BLIND_KEY, blind_key, run_scenario
from tests.factories import T, ev, fact, probe, scenario

CFG = EvalConfig()


def _truth_change():
    return scenario(
        facts={
            "f_old": fact(valid_until=T("10-05 00:00")),
            "f_new": fact(valid_from=T("10-05 00:00")),
        },
        events=[
            ev("e_old", T("10-01 10:00"), fact="f_old", text="deploy host alpha deploy host alpha"),
            ev("e_new", T("10-05 10:00"), fact="f_new", text="deploy host beta"),
        ],
        probes=[
            probe("p_block", "block", T("10-10 10:00"), ["f_old", "f_new"], query="alpha host"),
            probe("p_promote", "promote", T("10-10 11:00"), ["f_new"], query="beta host"),
        ],
    )


def test_all_store_fp_on_truth_change():
    run = run_scenario(AllStoreEngine(), _truth_change(), run_id="run1", cfg=CFG)
    assert run.error is None
    by_id = {r.probe_id: r for r in run.probes}
    block = by_id["p_block"]
    assert block.outcome == "FP"
    assert block.contaminated
    assert block.bases[0] == frozenset({"f_old"})
    assert block.context_chars == sum(len(r.text) for r in block.results)
    assert len(run.record_ms) == 2
    assert block.search_ms >= 0


def test_oracle_blocks_and_promotes():
    s = _truth_change()
    run = run_scenario(OracleEngine(s), s, run_id="run1", cfg=CFG)
    by_id = {r.probe_id: r for r in run.probes}
    assert by_id["p_block"].outcome == "BLOCKED"
    assert not by_id["p_block"].contaminated
    assert by_id["p_block"].reached
    assert by_id["p_promote"].outcome == "TP"
    assert by_id["p_promote"].reached


class Recorder(BaseEngine):
    def __init__(self, virtual_clock=False, n_results=0, fail_on=None):
        self.virtual_clock = virtual_clock
        self.n_results = n_results
        self.fail_on = fail_on
        self.calls: list[tuple] = []
        self.received: list = []

    def info(self):
        return EngineInfo(name="rec", version="0", virtual_clock=self.virtual_clock)

    def reset(self, namespace):
        self.calls.append(("reset", namespace))

    def record(self, event):
        self.received.append(event)
        self.calls.append(("record", event.id))

    def search(self, query):
        self.received.append(query)
        self.calls.append(("search", query.text))
        if self.fail_on == "search":
            raise RuntimeError("boom")
        return [Result(text=f"r{i}", sources=[]) for i in range(self.n_results)]

    def tick(self, t: datetime):
        self.calls.append(("tick", t))

    def flush(self):
        self.calls.append(("flush",))


def _same_time():
    return scenario(
        facts={"f": fact()},
        events=[ev("e1", T("10-01 10:00"), fact="f")],
        probes=[probe("p", "promote", T("10-01 10:00"), ["f"])],
    )


def test_events_before_probe_same_time():
    eng = Recorder()
    run_scenario(eng, _same_time(), run_id="run1", cfg=CFG)
    kinds = [c[0] for c in eng.calls]
    assert kinds == ["reset", "record", "flush", "search"]


def _keyed(key: bytes, scenario_id: str) -> str:
    return hmac.new(key, f"ns:{scenario_id}".encode(), hashlib.sha256).hexdigest()[:16]


def test_reset_called_with_namespace():
    eng = Recorder()
    run_scenario(eng, _same_time(), run_id="run1", cfg=CFG)
    assert eng.calls[0] == ("reset", f"run1:{_keyed(DEFAULT_BLIND_KEY, 'sc')}")


def test_blind_key_is_derived_from_the_seed():
    assert blind_key(7) == hashlib.sha256(b"t5-blind:7").digest()
    assert blind_key(7) != blind_key(8)
    eng = Recorder()
    run_scenario(eng, _same_time(), run_id="run1", cfg=CFG, blind_key=blind_key(7))
    assert eng.calls[0] == ("reset", f"run1:{_keyed(blind_key(7), 'sc')}")


def _named(sid: str):
    return scenario(
        id=sid,
        family="echo",
        facts={"f": fact()},
        events=[ev("e1", T("10-01 10:00"), fact="f")],
        probes=[probe("p_block_1", "promote", T("10-01 10:00"), ["f"])],
    )


def test_namespace_does_not_contain_scenario_id():
    sid = "echo-shop-api-4f2a1c"
    eng = Recorder()
    run_scenario(eng, _named(sid), run_id="run1", cfg=CFG, blind_key=blind_key(3))
    namespace = eng.calls[0][1]
    assert re.fullmatch(r"run1:[0-9a-f]{16}", namespace)
    rest = namespace.removeprefix("run1:")
    for hidden in (sid, "echo", "shop", "4f2a1c", "p_block_1", "block"):
        assert hidden not in rest


def _hash_guesses(candidate: str, run_id: str) -> set[str]:
    """Unkeyed transforms an engine could try on a guessed scenario id."""
    out = {candidate}
    for text in (candidate, f"ns:{candidate}", f"{run_id}:{candidate}", f"t5-blind:{candidate}"):
        for algo in ("sha256", "sha1", "md5", "blake2b", "sha512"):
            out.add(hashlib.new(algo, text.encode()).hexdigest()[:16])
        for key in (b"", DEFAULT_BLIND_KEY, blind_key(0)):  # keys an engine can know or guess
            out.add(hmac.new(key, f"ns:{text}".encode(), hashlib.sha256).hexdigest()[:16])
    return out


def test_namespace_cannot_be_recovered_by_hashing_scenario_ids():
    # Reproduces review finding D1: an unkeyed hash of a guessable id is a dictionary lookup.
    sid = "echo-shop-api-00002a"
    candidates = [f"{fam}-shop-api-{i:06x}" for fam in ("echo", "truth_change") for i in range(64)]
    assert sid in candidates
    eng = Recorder()
    run_scenario(eng, _named(sid), run_id="run1", cfg=CFG, blind_key=blind_key(918273645))
    rest = eng.calls[0][1].removeprefix("run1:")
    assert not any(rest in _hash_guesses(c, "run1") for c in candidates)
    other = Recorder()
    run_scenario(other, _named(sid), run_id="run1", cfg=CFG, blind_key=blind_key(918273646))
    assert other.calls[0][1] != eng.calls[0][1]  # the run's key, not the id alone, decides it


def test_tick_only_when_virtual_clock():
    off = Recorder(virtual_clock=False)
    run_scenario(off, _same_time(), run_id="r", cfg=CFG)
    assert not any(c[0] == "tick" for c in off.calls)

    on = Recorder(virtual_clock=True)
    run_scenario(on, _same_time(), run_id="r", cfg=CFG)
    t = T("10-01 10:00")
    assert [c[0] for c in on.calls] == ["reset", "tick", "record", "tick", "flush", "search"]
    assert [c[1] for c in on.calls if c[0] == "tick"] == [t, t]


def test_results_truncated_to_k():
    s = _same_time()
    run = run_scenario(Recorder(n_results=10), s, run_id="r", cfg=EvalConfig(k=5))
    assert len(run.probes[0].results) == 5
    assert run.probes[0].context_chars == len("r0") * 5
    assert len(run.probes[0].bases) == 5

    s.probes[0] = s.probes[0].model_copy(update={"k": 2})
    run = run_scenario(Recorder(n_results=10), s, run_id="r", cfg=EvalConfig(k=5))
    assert len(run.probes[0].results) == 2


def test_engine_exception_marks_error():
    run = run_scenario(Recorder(fail_on="search"), _same_time(), run_id="r", cfg=CFG)
    assert run.error == "RuntimeError: boom"
    assert run.probes == []
    assert len(run.record_ms) == 1


def test_search_generator_raising_marks_error():
    class GenEngine(Recorder):
        def search(self, query):
            def gen():
                yield Result(text="ok")
                raise ValueError("mid-iteration")

            return gen()

    run = run_scenario(GenEngine(), _same_time(), run_id="r", cfg=CFG)
    assert run.error == "ValueError: mid-iteration"
    assert run.probes == []


def test_search_returning_none_marks_error():
    class NoneEngine(Recorder):
        def search(self, query):
            return None

    run = run_scenario(NoneEngine(), _same_time(), run_id="r", cfg=CFG)
    assert run.error.startswith("TypeError")


def test_harness_error_is_not_converted_to_engine_error():
    s = scenario(
        facts={"f": fact()},
        events=[],
        probes=[probe("p", "promote", T("10-02 10:00"), ["f"], user="nobody")],
    )
    with pytest.raises(KeyError):
        run_scenario(Recorder(), s, run_id="r", cfg=CFG)


def test_engine_never_sees_hidden_fields():
    s = scenario(
        facts={"f": fact()},
        events=[ev("e1", T("10-01 10:00"), fact="f", secret_markers=["sekret"])],
        probes=[probe("p", "promote", T("10-02 10:00"), ["f"])],
    )
    eng = Recorder()
    run_scenario(eng, s, run_id="r", cfg=CFG)
    assert [type(o) for o in eng.received] == [Event, Query]
    forbidden = {"fact", "secret_markers", "about", "feedback"}
    for obj in eng.received:
        keys = set(obj.model_dump())
        assert not keys & forbidden
        if isinstance(obj, Query):
            assert "kind" not in keys  # Event.kind is a real contract field; only the probe kind is hidden


class Returns(BaseEngine):
    def __init__(self, items):
        self.items = items

    def info(self):
        return EngineInfo(name="returns", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        pass

    def search(self, query):
        return self.items


def test_search_dicts_validated_into_results():
    run = run_scenario(Returns([{"text": "a", "sources": ["e1"]}, Result(text="b")]), _same_time(),
                       run_id="r", cfg=CFG)
    assert run.error is None
    assert run.probes[0].results == [Result(text="a", sources=["e1"]), Result(text="b")]


@pytest.mark.parametrize("items", [[1], [{"sources": ["e1"]}], None])
def test_malformed_search_return_marks_error(items):
    run = run_scenario(Returns(items), _same_time(), run_id="r", cfg=CFG)
    assert run.error is not None and run.probes == []


class Cites(BaseEngine):
    """Stores nothing and cites fixed event ids, recorded or not."""

    def __init__(self, sources):
        self.sources = list(sources)

    def info(self):
        return EngineInfo(name="cites", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        pass

    def search(self, query):
        return [Result(text="whatever", sources=self.sources)]


def _future_sources():
    # Reproduces review finding C1: e2 and e3 are recorded only after both probes.
    return scenario(
        facts={"f_a": fact(markers=["m"]), "f_bad": fact(markers=["x"], valid=False)},
        events=[
            ev("e1", T("10-01 10:00"), fact="f_a"),
            ev("e2", T("10-20 10:00"), fact="f_a"),
            ev("e3", T("10-20 10:00"), fact="f_bad", user="u3"),
        ],
        probes=[
            probe("pb", "block", T("10-05 10:00"), ["f_a", "f_bad"]),
            probe("pp", "promote", T("10-06 10:00"), ["f_a"]),
        ],
    )


def test_unrecorded_source_id_is_not_evidence():
    s = _future_sources()
    run = run_scenario(Cites(["e2", "e3"]), s, run_id="r", cfg=CFG)
    assert run.error is None
    by_id = {r.probe_id: r for r in run.probes}
    assert by_id["pb"].outcome == "BLOCKED"
    assert by_id["pp"].outcome == "FN"
    assert all(r.bases == [frozenset()] for r in run.probes)
    assert not any(r.contaminated or r.reached for r in run.probes)
    # e3 is another team's event, but it was not recorded yet: nothing could have leaked.
    assert all(r.leaks == [] for r in run.probes)

    recorded = run_scenario(Cites(["e1"]), s, run_id="r", cfg=CFG)
    assert {r.probe_id: r.outcome for r in recorded.probes} == {"pb": "BLOCKED", "pp": "TP"}


def test_generated_event_is_evidence_only_after_it_is_recorded():
    from harness.replay import generated_event_id
    from harness.scenario.model import Feedback, FeedbackTemplate

    s = scenario(
        family="echo",
        mode="feedback",
        facts={"f_a": fact(), "f_gave_up": fact()},
        events=[ev("e1", T("10-01 10:00"), fact="f_a")],
        probes=[
            probe("p1", "block", T("10-10 10:00"), ["f_a"],
                  feedback=Feedback(abstain=FeedbackTemplate(text="gave up", fact="f_gave_up"))),
            probe("p_early", "promote", T("10-10 10:01"), ["f_gave_up"]),  # action at 10:05
            probe("p_late", "promote", T("10-10 10:06"), ["f_gave_up"]),
        ],
    )
    gid = generated_event_id({"e1"})
    assert gid == "e2"
    run = run_scenario(Cites([gid]), s, run_id="r", cfg=CFG)
    assert run.error is None
    assert run.generated_ids[("p1", "action")] == gid
    by_id = {r.probe_id: r for r in run.probes}
    assert by_id["p_early"].outcome == "FN"
    assert by_id["p_late"].outcome == "TP"


class Mutator(BaseEngine):
    """Keeps every Result it returned and rewrites it at the next `record` call."""

    def __init__(self):
        self.kept: list[Result] = []
        self.events: list[Event] = []

    def info(self):
        return EngineInfo(name="mut", version="0")

    def reset(self, namespace):
        pass

    def record(self, event):
        self.events.append(event)
        for r in self.kept:
            r.text = "MUTATED-AFTER-SCORING"
            r.sources = []

    def search(self, query):
        out = [Result(text=e.text, sources=[e.id]) for e in self.events[-1:]]
        self.kept.extend(out)
        return out


def test_stored_result_does_not_change_when_engine_mutates_it():
    s = scenario(
        facts={"f": fact(markers=["m"])},
        events=[ev("e1", T("10-01 10:00"), fact="f", text="alpha m"),
                ev("e2", T("10-12 10:00"), text="later noise")],
        probes=[probe("pp", "promote", T("10-10 10:00"), ["f"], query="alpha")],
    )
    eng = Mutator()
    run = run_scenario(eng, s, run_id="r", cfg=CFG)
    assert run.error is None
    stored = run.probes[0].results[0]
    assert eng.kept[0].text == "MUTATED-AFTER-SCORING"
    assert stored is not eng.kept[0]
    assert (stored.text, stored.sources) == ("alpha m", ["e1"])


def test_explicit_probe_k_is_honoured_in_replay():
    s = scenario(
        facts={"f": fact()},
        events=[ev(f"e{i}", T(f"10-0{i} 10:00"), fact="f", text="alpha") for i in range(1, 5)],
        probes=[probe("pp", "promote", T("10-10 10:00"), ["f"], query="alpha", k=1)],
    )
    run = run_scenario(AllStoreEngine(), s, run_id="r", cfg=EvalConfig(k=3))
    assert len(run.probes[0].results) == 1
