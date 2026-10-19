import pytest

from harness.baselines.oracle import OracleEngine
from harness.contract import Event
from tests.factories import T, ev, fact, probe, scenario


def make_scenario(**ev2_kw):
    facts = {
        "f_old": fact(text="use vendor v1", valid_until=T("10-09 00:00")),
        "f_new": fact(text="use vendor v2", valid_from=T("10-09 00:00")),
    }
    events = [
        ev("e1", T("10-01 10:00"), fact="f_old", text="vendor module v1 works"),
        ev("e2", T("10-10 10:00"), fact="f_new", text="vendor module v2 works", **ev2_kw),
    ]
    probes = [probe("p_block", "block", T("10-14 10:00"), ["f_old", "f_new"], query="vendor module")]
    return scenario(facts, events, probes)


def record_all(eng, sc):
    eng.reset("ns")
    for le in sc.labeled_events():
        eng.record(le.event)


def query(sc):
    return sc.query_for(sc.probes[0], 5)


def test_info():
    info = OracleEngine(make_scenario()).info()
    assert (info.name, info.version) == ("oracle", "0.1")
    assert info.sources and info.scopes and not info.virtual_clock


def test_oracle_returns_only_currently_true():
    sc = make_scenario()
    eng = OracleEngine(sc)
    record_all(eng, sc)
    res = eng.search(query(sc))
    assert [r.sources for r in res] == [["e2"]]
    assert res[0].text == "vendor module v2 works"


def test_oracle_respects_visibility():
    sc = make_scenario(scope="personal", user="u3")  # u3 is team b; probe user u2 is team a
    eng = OracleEngine(sc)
    record_all(eng, sc)
    assert eng.search(query(sc)) == []


def test_oracle_ignores_unknown_event_ids():
    sc = make_scenario()
    eng = OracleEngine(sc)
    record_all(eng, sc)
    eng.record(Event(
        id="fb1", t=T("10-11 10:00"), user="u1", session="s", repo="r", scope="team",
        team="a", signal="self", kind="agent_record", outcome="unknown", text="feedback",
    ))
    assert [r.sources for r in eng.search(query(sc))] == [["e2"]]


def test_oracle_unknown_query_returns_empty():
    sc = make_scenario()
    eng = OracleEngine(sc)
    record_all(eng, sc)
    q = query(sc).model_copy(update={"text": "something else"})
    assert eng.search(q) == []


def test_oracle_ignores_events_after_query_time():
    sc = make_scenario()
    late = sc.model_copy(update={"events": [sc.events[0], sc.events[1].model_copy(update={"t": T("10-15 10:00")})]})
    eng = OracleEngine(late)
    record_all(eng, late)
    assert eng.search(query(late)) == []


def test_oracle_most_recent_wins_and_ties_by_recording_order():
    facts = {"f": fact(text="x")}
    events = [
        ev("a", T("10-01 10:00"), fact="f"),
        ev("b", T("10-02 10:00"), fact="f"),
        ev("c", T("10-02 10:00"), fact="f"),
    ]
    sc = scenario(facts, events, [probe("p", "block", T("10-14 10:00"), ["f"], query="q")])
    eng = OracleEngine(sc)
    record_all(eng, sc)
    assert [r.sources for r in eng.search(query(sc))] == [["c"]]


def test_oracle_k_limit_and_about_order():
    facts = {f"f{i}": fact(text=f"t{i}") for i in range(3)}
    events = [ev(f"e{i}", T("10-01 10:00"), fact=f"f{i}") for i in range(3)]
    sc = scenario(facts, events, [probe("p", "block", T("10-14 10:00"), ["f2", "f0", "f1"], query="q")])
    eng = OracleEngine(sc)
    record_all(eng, sc)
    q = query(sc).model_copy(update={"k": 2})
    assert [r.sources for r in eng.search(q)] == [["e2"], ["e0"]]


def test_oracle_first_probe_wins_on_duplicate_key():
    facts = {"f1": fact(text="a"), "f2": fact(text="b")}
    events = [ev("e1", T("10-01 10:00"), fact="f1"), ev("e2", T("10-01 10:00"), fact="f2")]
    probes = [
        probe("p1", "block", T("10-14 10:00"), ["f1"], query="same"),
        probe("p2", "block", T("10-14 10:00"), ["f2"], query="same"),
    ]
    sc = scenario(facts, events, probes)
    eng = OracleEngine(sc)
    record_all(eng, sc)
    assert [r.sources for r in eng.search(sc.query_for(sc.probes[1], 5))] == [["e1"]]


def test_oracle_requires_reset():
    sc = make_scenario()
    eng = OracleEngine(sc)
    with pytest.raises(RuntimeError):
        eng.record(sc.labeled_events()[0].event)
    with pytest.raises(RuntimeError):
        eng.search(query(sc))


def test_oracle_reset_clears_namespace():
    sc = make_scenario()
    eng = OracleEngine(sc)
    record_all(eng, sc)
    eng.reset("ns")
    assert eng.search(query(sc)) == []
