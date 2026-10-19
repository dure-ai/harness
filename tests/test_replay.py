from datetime import datetime

import pytest

from harness.baselines.oracle import OracleEngine
from harness.baselines.simple import AllStoreEngine
from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.replay import run_scenario
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


def test_reset_called_with_namespace():
    eng = Recorder()
    run_scenario(eng, _same_time(), run_id="run1", cfg=CFG)
    assert eng.calls[0] == ("reset", "run1:sc")


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
