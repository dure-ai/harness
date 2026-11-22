import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cortex_mem.embed import EmbeddingError
from cortex_mem.engines.baseline import PromoteAllEngine

from harness.config import CoreEmbedding, EvalConfig, load_config
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


class EmbeddingAwareEngine(PromoteAllEngine):
    def handoff(self, query, caller, scope, ctx, k=6):
        if ctx.similar is not None:
            self.group = ctx.similar.group("e1")
            ctx.similar.search(query, caller, scope, k)
        else:
            self.used_fallback = True
        return super().handoff(query, caller, scope, ctx, k)


@pytest.fixture
def qwen_http(monkeypatch):
    connection = MagicMock()
    calls = []
    replies = {"query_status": 200}

    def respond():
        method, path, body, headers = connection.request.call_args.args
        assert (method, path, headers["Content-Type"]) == ("POST", "/v1/embeddings", "application/json")
        payload = json.loads(body)
        calls.append(payload)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = replies["query_status"] if payload["input"][0].startswith("Instruct:") else 200
        response.read.return_value = json.dumps(replies.get("payload", {"data": [
            {"index": i, "embedding": [1.0, 0.0]} for i in range(len(payload["input"]))
        ]})).encode()
        return response

    connection.getresponse.side_effect = respond
    monkeypatch.setattr("harness.core_embedding.http.client.HTTPConnection", MagicMock(return_value=connection))
    return calls, replies


def test_local_qwen_index_is_used_and_reset_per_scenario(qwen_http):
    calls, _ = qwen_http
    settings = CoreEmbedding(group_threshold=0.8)
    bridge = make_engine("tests.test_core_bridge:EmbeddingAwareEngine", None, EvalConfig(core_embedding=settings))
    assert isinstance(bridge, CoreBridge)
    assert bridge.info().config["core_embedding"] == settings.model_dump()
    bridge.reset("first")
    bridge.record(event())
    bridge.record(event(id="e2", text="the same deploy rule"))
    bridge.search(query())
    assert bridge.engine.group.members == ("e1", "e2")
    assert calls[0] == {"model": settings.model, "input": [event().text]}
    assert calls[-1]["input"][0].startswith("Instruct: ")
    assert calls[-1]["input"][0].endswith("\nQuery: " + query().text)
    bridge.reset("second")
    assert bridge.search(query()) == []
    assert bridge.engine.group is None


def test_local_qwen_query_failure_does_not_score_text_fallback(qwen_http):
    _, replies = qwen_http
    engine = EmbeddingAwareEngine()
    bridge = CoreBridge(engine, CoreEmbedding())
    bridge.reset("ns")
    bridge.record(event())
    replies["query_status"] = 503
    with pytest.raises(EmbeddingError, match="HTTP 503"):
        bridge.search(query())
    assert engine.used_fallback


@pytest.mark.parametrize(("payload", "message"), [
    ({"data": [{"index": 0, "embedding": ["bad"]}]}, "ValidationError"),
    ({"data": [{"index": 1, "embedding": [1.0, 0.0]}]}, "input indexes"),
    ({"data": [{"index": 0, "embedding": [1.0]}]}, "inconsistent vector lengths"),
])
def test_invalid_local_qwen_response_fails_scenario(qwen_http, payload, message):
    _, replies = qwen_http
    bridge = CoreBridge(PromoteAllEngine(), CoreEmbedding())
    bridge.reset("ns")
    bridge.record(event())
    replies["payload"] = payload
    with pytest.raises(EmbeddingError, match=message):
        bridge.record(event(id="e2", text="a new record"))


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
