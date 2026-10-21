from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from harness.config import LLM, EvalConfig
from harness.contract import Event, Query
from harness.oss.hindsight import HindsightEngine
from harness.oss.http import EngineHTTPError
from harness.oss.models import ENV_EMBEDDING, ENV_LLM
from harness.oss.text import engine_ns
from tests.oss_fakes import FakeHindsight, free_port

MODELS_ENV = {ENV_LLM: "gpt-4.1-mini", ENV_EMBEDDING: "text-embedding-3-small"}
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def cfg() -> EvalConfig:
    return EvalConfig(llm=LLM(provider="openai", model="gpt-4.1-mini", embedding_model="text-embedding-3-small"))


def ev(id: str, **kw) -> Event:
    defaults = dict(t=T0, user="u1", session="s1", repo="shop-api", scope="team", team="team-a",
                    signal="self", kind="agent_record", outcome="unknown", text=f"text of {id}")
    return Event(id=id, **{**defaults, **kw})


def q(**kw) -> Query:
    defaults = dict(text="question", user="u1", team="team-a", session="s2", repo="shop-api", t=T0, k=5)
    return Query(**{**defaults, **kw})


def make(fake: FakeHindsight, **kw) -> HindsightEngine:
    kw.setdefault("sleep", lambda _: None)
    return HindsightEngine(cfg(), base_url=fake.url, environ=MODELS_ENV, **kw)


@pytest.fixture
def fake():
    with FakeHindsight() as server:
        yield server


@pytest.fixture
def engine(fake):
    return make(fake)


def test_reset_deletes_hashed_bank(fake, engine):
    engine.reset("run-r1:echo-widget-abc123")
    bank = engine_ns("run-r1:echo-widget-abc123")
    assert fake.requests[-1].method == "DELETE" and fake.requests[-1].path == f"/v1/default/banks/{bank}"
    assert "echo" not in fake.requests[-1].path


def test_record_sends_format_time_id_tag(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", scope="personal", user="u1", text="Use replica"))
    item = fake.requests[-1].body["items"][0]
    assert item == {"content": "[shop-api] u1: Use replica", "timestamp": "2026-10-01T09:00:00+09:00",
                    "document_id": "e1", "tags": ["user:u1"]}


def test_flush_waits_then_checks_failed(fake, engine):
    fake.pending_rounds = 2; engine.reset("ns"); engine.record(ev("e1")); engine.flush()
    assert sum(r.query.get("status") == "pending" for r in fake.requests) == 3
    assert sum(r.query.get("status") == "processing" for r in fake.requests) == 3
    assert fake.requests[-1].query["status"] == "failed"


def test_flush_timeout(fake):
    fake.pending_rounds = 10**6
    eng = make(fake, clock=iter(range(0, 10**6, 100)).__next__, flush_timeout=600)
    eng.reset("ns"); eng.record(ev("e1"))
    with pytest.raises(TimeoutError):
        eng.flush()


def test_failed_background_operation_errors(fake, engine):
    fake.failed_total = 1; engine.reset("ns"); engine.record(ev("e1"))
    with pytest.raises(RuntimeError, match="1 background operations failed"):
        engine.flush()


def test_search_request_and_sources(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="Use the replica for reads"))
    res = engine.search(q(text="replica reads", user="u4", team="team-a", k=5))
    body = fake.requests[-1].body
    assert body["tags"] == ["user:u4", "team:team-a"] and body["tags_match"] == "any_strict"
    assert body["query_timestamp"] == "2026-10-01T09:00:00+09:00" and body["include"] == {"source_facts": {}}
    assert body["query"] == "[shop-api] replica reads"
    assert res[0].sources == ["e1"] and res[0].meta == {"id": "m0", "type": "world"}


def test_observation_sources_from_source_facts(fake, engine):
    fake.observation_of = ["e1", "e2"]  # the fake puts one observation built from e1 and e2 first
    engine.reset("ns"); engine.record(ev("e1", text="a b")); engine.record(ev("e2", text="a c"))
    assert engine.search(q(text="a"))[0].sources == ["e1", "e2"]


def test_observation_sources_dedupe_in_order(fake, engine):
    fake.extra_results = [{"id": "o", "text": "t", "type": "observation", "source_fact_ids": ["m1", "m0", "m1"]}]
    engine.reset("ns"); engine.record(ev("e1", text="zzz")); engine.record(ev("e2", text="zzz"))
    assert engine.search(q(text="nothing"))[-1].sources == ["e2", "e1"]


def test_unknown_or_null_document_ids_dropped(fake, engine):
    fake.extra_results = [{"id": "m9", "text": "x", "type": "world", "document_id": None},
                          {"id": "m8", "text": "y", "type": "world", "document_id": "other"},
                          {"id": "m7", "text": "z", "type": "observation", "source_fact_ids": ["nope"]}]
    engine.reset("ns"); engine.record(ev("e1", text="x"))
    results = engine.search(q(text="x"))
    assert [r.meta["id"] for r in results[-3:]] == ["m9", "m8", "m7"]
    assert [r.sources for r in results[-3:]] == [[], [], []]


def test_null_source_facts_and_empty_results(fake, engine):
    fake.source_facts_null = True; engine.reset("ns"); engine.record(ev("e1"))
    assert engine.search(q(text="nothing")) == []


def test_cut_to_k(fake, engine):
    engine.reset("ns")
    for i in range(8):
        engine.record(ev(f"e{i}", text=f"replica {i}"))
    assert len(engine.search(q(text="replica", k=3))) == 3


def test_reset_clears_known_event_ids(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="replica"))
    engine.reset("ns"); engine.record(ev("e2", text="replica"))
    fake.extra_results = [{"id": "m", "text": "t", "type": "world", "document_id": "e1"}]
    assert engine.search(q(text="replica"))[-1].sources == []


def test_empty_bank_flush_and_search_tolerate_404(fake, engine):
    engine.reset("ns")
    engine.flush()
    assert engine.search(q(text="anything")) == []


def test_404_after_record_is_an_error(fake, engine):
    engine.reset("ns"); engine.record(ev("e1"))
    fake.banks.clear()  # the bank vanished behind the adapter's back
    with pytest.raises(EngineHTTPError) as flush_err:
        engine.flush()
    assert flush_err.value.status == 404
    with pytest.raises(EngineHTTPError) as search_err:
        engine.search(q(text="x"))
    assert search_err.value.status == 404


@pytest.mark.parametrize("reply", [None, [], {}, {"total": None}, {"total": "0"}, {"total": 1.5}, {"total": True}, {"total": -1}])
def test_malformed_operations_reply_raises(fake, engine, reply):
    fake.operations_reply = reply
    engine.reset("ns"); engine.record(ev("e1"))
    with pytest.raises(RuntimeError, match="unexpected operations reply"):
        engine.flush()


GOOD_ROW = {"id": "m", "text": "fine", "type": "world", "document_id": "e1"}


@pytest.mark.parametrize("reply", [
    "text", None, [], {"results": None}, {"results": {}}, {"source_facts": {}},
    {"results": [GOOD_ROW, "row"]}, {"results": [GOOD_ROW, {"id": "m"}]},
    {"results": [GOOD_ROW, {"id": "m", "text": None}]}, {"results": [GOOD_ROW, {"id": "m", "text": 3}]},
    {"results": [GOOD_ROW], "source_facts": []}, {"results": [GOOD_ROW], "source_facts": "x"},
])
def test_malformed_recall_reply_raises_after_a_record(fake, engine, reply):
    fake.recall_reply = reply
    engine.reset("ns"); engine.record(ev("e1"))
    with pytest.raises(RuntimeError, match="hindsight: unexpected recall reply"):
        engine.search(q())


def test_wellformed_recall_reply_is_accepted(fake, engine):
    fake.recall_reply = {"results": [GOOD_ROW], "source_facts": None}
    engine.reset("ns"); engine.record(ev("e1"))
    assert [r.text for r in engine.search(q())] == ["fine"]


def test_flush_before_reset_is_an_error(engine):
    with pytest.raises(RuntimeError):
        engine.flush()


def test_url_from_environ(fake):
    eng = HindsightEngine(cfg(), environ={**MODELS_ENV, "HARNESS_HINDSIGHT_URL": fake.url}, sleep=lambda _: None)
    assert eng.info().config["url"] == fake.url


def test_models_must_match_environment(fake):
    with pytest.raises(ValueError):
        HindsightEngine(cfg(), base_url=fake.url, environ={ENV_LLM: "other", ENV_EMBEDDING: "x"})


def test_constructor_fails_when_engine_down():
    with pytest.raises(EngineHTTPError):
        HindsightEngine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=lambda _: None)


def test_first_build_against_a_down_engine_does_not_retry():
    waits: list[float] = []
    with pytest.raises(EngineHTTPError):
        HindsightEngine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=waits.append)
    assert waits == []


def test_later_builds_retry_a_blip(fake):
    make(fake)  # the first build verifies the address
    fake.fail_next(503, 1)
    waits: list[float] = []
    HindsightEngine(cfg(), base_url=fake.url, environ=MODELS_ENV, sleep=waits.append)
    assert waits == [2.0]


def test_first_build_does_not_retry_a_blip(fake):
    fake.fail_next(503, 1)
    waits: list[float] = []
    with pytest.raises(EngineHTTPError):
        HindsightEngine(cfg(), base_url=fake.url, environ=MODELS_ENV, sleep=waits.append)
    assert waits == []


def test_info(engine):
    i = engine.info()
    assert (i.name, i.version, i.virtual_clock, i.sources, i.scopes) == ("hindsight", "1.0.0", True, True, True)
    assert i.config["llm_model"] == "gpt-4.1-mini" and i.config["api_version"] == "1.0.0"
    assert "OPENAI_API_KEY" not in str(i.config)
