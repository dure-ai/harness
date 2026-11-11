import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cortex_mem.engines.baseline import PromoteAllEngine

from harness.config import load_config
from harness.contract import BaseEngine, Event, Query
from harness.core_bridge import CoreBridge, to_core_event
from harness.engines import make_engine
from harness.replay import run_scenario
from harness.scenario.load import load_set

T0 = datetime(2026, 10, 1, tzinfo=UTC)
PROMOTE_ALL = "cortex_mem.engines.baseline:PromoteAllEngine"
NO_MEMORY = "cortex_mem.engines.baseline:NoMemoryEngine"
ROOT = Path(__file__).parents[1]


def event(**over) -> Event:
    fields = {
        "id": "e1", "t": T0, "user": "u1", "session": "s1", "repo": "acme/api", "scope": "team",
        "team": "team-a", "signal": "self", "kind": "agent_record", "outcome": "unknown",
        "text": "deploy uses make release",
    }
    return Event(**(fields | over))


def query(**over) -> Query:
    fields = {
        "text": "how to deploy", "user": "u1", "team": "team-a", "session": "s2",
        "repo": "acme/api", "t": T0 + timedelta(days=1), "k": 6,
    }
    return Query(**(fields | over))


def bridge() -> CoreBridge:
    engine = CoreBridge(PromoteAllEngine())
    engine.reset("ns")
    return engine


def test_agent_record_keeps_its_fields_and_meta_becomes_conditions():
    core = to_core_event(event(meta={"service": "api"}))
    assert (core.kind, core.signal, core.outcome) == ("record", "self", "unknown")
    assert (core.id, core.at, core.user, core.team, core.session, core.scope, core.repo) == (
        "e1", T0, "u1", "team-a", "s1", "team", "acme/api",
    )
    assert dict(core.conditions) == {"service": "api"}


def test_ci_result_that_judges_an_event_is_an_outcome():
    core = to_core_event(event(kind="ci_result", signal="test", outcome="failure", refs=("e0",)))
    assert (core.kind, core.signal, core.outcome, core.refs) == ("outcome", "test", "failure", ("e0",))


def test_ci_result_without_refs_is_standalone_evidence():
    core = to_core_event(event(kind="ci_result", signal="test", outcome="success"))
    assert (core.kind, core.signal, core.outcome) == ("record", "test", "success")


def test_human_confirm_without_refs_is_standalone_evidence():
    # Runtime.append refuses a human_confirm without refs; the scenario's event is still evidence.
    core = to_core_event(event(kind="human_confirm", signal="human", outcome="success"))
    assert (core.kind, core.signal) == ("record", "human")


def test_human_confirm_with_refs_stays_human_confirm():
    core = to_core_event(event(kind="human_confirm", signal="human", outcome="success", refs=("e0",)))
    assert (core.kind, core.refs) == ("human_confirm", ("e0",))


def test_commit_stays_commit():
    assert to_core_event(event(kind="commit")).kind == "commit"


def test_search_returns_what_the_runtime_lets_through():
    engine = bridge()
    engine.record(event())
    [result] = engine.search(query())
    assert result.text == "deploy uses make release"
    assert result.sources == ["e1"]


def test_search_hides_another_users_personal_record():
    engine = bridge()
    engine.record(event(scope="personal"))
    assert engine.search(query(user="u2")) == []


def test_search_shows_the_callers_own_personal_record():
    engine = bridge()
    engine.record(event(scope="personal"))
    assert [r.sources for r in engine.search(query())] == [["e1"]]


def test_search_logs_no_recall():
    # Design 4.5: a recall event would differ per engine, and engines must all see the same events.
    engine = bridge()
    engine.record(event())
    engine.search(query())
    engine.search(query())
    assert [e.kind for _, e in engine.runtime.events.since(0)] == ["record"]


def test_reset_starts_empty():
    engine = bridge()
    engine.record(event())
    engine.reset("other")
    assert engine.search(query()) == []


def test_engine_failure_reaches_the_replay():
    class Broken(PromoteAllEngine):
        def observe(self, event, ctx):
            raise RuntimeError("boom")

    engine = CoreBridge(Broken())
    engine.reset("ns")
    with pytest.raises(RuntimeError, match="boom"):
        engine.record(event())


def test_flush_fails_when_the_runtime_skipped_an_event():
    engine = bridge()
    engine.runtime.skipped.append("stand-in")
    with pytest.raises(RuntimeError, match="skipped"):
        engine.flush()


def test_info_names_the_wrapped_engine_and_its_capabilities():
    info = CoreBridge(PromoteAllEngine()).info()
    assert info.name == "PromoteAllEngine"
    assert info.virtual_clock and info.sources and info.scopes
    assert info.config["store"] == "InMemoryDatabase"
    assert len(info.config["core_commit"]) == 40  # the git pin in pyproject.toml


@pytest.mark.parametrize("name", [PROMOTE_ALL, NO_MEMORY])
def test_loader_wraps_a_core_engine(name):
    cfg = load_config(ROOT / "config/eval-v0.1.yaml")
    assert isinstance(make_engine(name, None, cfg), BaseEngine)


def test_dev_set_runs_without_errors_through_the_bridge():
    cfg = load_config(ROOT / "config/eval-v0.1.yaml")
    for scenario in load_set([ROOT / "scenarios/dev"], include_drafts=False).scenarios:
        run = run_scenario(make_engine(PROMOTE_ALL, scenario, cfg), scenario, run_id="t", cfg=cfg)
        assert run.error is None, f"{scenario.id}: {run.error}"


def test_bridge_reproduces_all_store_probe_by_probe():
    # With all_store's rules on the core port, any remaining difference is the bridge's fault.
    cfg = load_config(ROOT / "config/eval-v0.1.yaml")
    for scenario in load_set([ROOT / "scenarios/dev"], include_drafts=False).scenarios:
        want = run_scenario(make_engine("all_store", scenario, cfg), scenario, run_id="t", cfg=cfg)
        got = run_scenario(make_engine("tests.core_engines:AllStoreOnCore", scenario, cfg), scenario, run_id="t", cfg=cfg)
        assert got.error is None, f"{scenario.id}: {got.error}"
        assert [(p.probe_id, p.outcome, [r.sources for r in p.results]) for p in got.probes] == [
            (p.probe_id, p.outcome, [r.sources for r in p.results]) for p in want.probes
        ], scenario.id


def test_a_core_engine_without_the_core_extra_says_how_to_install_it(monkeypatch):
    for name in [m for m in sys.modules if m == "cortex_mem" or m.startswith("cortex_mem.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "cortex_mem", None)
    cfg = load_config(ROOT / "config/eval-v0.1.yaml")
    with pytest.raises(ModuleNotFoundError, match="uv sync --extra core"):
        make_engine(PROMOTE_ALL, None, cfg)
