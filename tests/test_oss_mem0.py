from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from harness.config import LLM, EvalConfig
from harness.contract import Event, Query
from harness.oss.http import EngineHTTPError
from harness.oss.mem0 import Mem0Engine
from harness.oss.models import ENV_EMBEDDING, ENV_LLM
from harness.oss.text import engine_ns
from tests.oss_fakes import FakeMem0, free_port

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


def make(fake: FakeMem0, **kw) -> Mem0Engine:
    kw.setdefault("sleep", lambda _: None)
    return Mem0Engine(cfg(), base_url=fake.url, environ=MODELS_ENV, **kw)


@pytest.fixture
def fake():
    with FakeMem0() as server:
        yield server


@pytest.fixture
def engine(fake):
    return make(fake)


def test_reset_deletes_hashed_run(fake, engine):
    engine.reset("run-r1:leak_personal-x"); r = fake.requests[-1]
    assert (r.method, r.path, r.query) == ("DELETE", "/memories", {"run_id": engine_ns("run-r1:leak_personal-x")})


def test_record_body(fake, engine):
    engine.reset("ns"); engine.record(ev("e3", scope="team", team="team-a", user="u2", text="Use replica"))
    assert fake.requests[-1].body == {"messages": [{"role": "user", "content": "[shop-api] u2: Use replica"}],
                                      "run_id": engine_ns("ns"), "user_id": "team:team-a",
                                      "metadata": {"harness_event": "e3"}}


def test_search_filter_and_sources(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="replica reads"))
    res = engine.search(q(text="replica", user="u4", team="team-a", k=2))
    assert fake.requests[-1].body["filters"] == {"run_id": engine_ns("ns"),
                                                 "OR": [{"user_id": "user:u4"}, {"user_id": "team:team-a"}]}
    assert fake.requests[-1].body["top_k"] == 2 and res[0].sources == ["e1"]


def test_search_hides_other_users_personal_memory(fake, engine):
    engine.reset("ns")
    engine.record(ev("e1", scope="personal", user="u1", text="secret replica"))
    engine.record(ev("e2", scope="team", text="team replica"))
    res = engine.search(q(text="replica", user="u4"))
    assert [r.sources for r in res] == [["e2"]]


def test_missing_or_foreign_metadata(fake, engine):
    fake.extra_results = [{"id": "m1", "memory": "x", "score": 0.9},
                          {"id": "m2", "memory": "y", "score": 0.8, "metadata": {"harness_event": "zz"}},
                          {"id": "m3", "memory": "z", "score": 0.7, "metadata": None}]
    engine.reset("ns")
    assert [r.sources for r in engine.search(q(text="x"))] == [[], [], []]


def test_results_are_cut_to_k_without_reordering(fake, engine):
    fake.extra_results = [{"id": f"x{i}", "memory": f"m{i}", "score": 0.1} for i in range(5)]
    engine.reset("ns")
    res = engine.search(q(text="x", k=3))
    assert [r.text for r in res] == ["m0", "m1", "m2"]
    assert res[0].meta == {"id": "x0", "score": 0.1}


def test_event_sources_forgotten_on_reset(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="replica"))
    fake.extra_results = [{"id": "m1", "memory": "x", "metadata": {"harness_event": "e1"}}]
    assert any(r.sources == ["e1"] for r in engine.search(q(text="replica")))
    engine.reset("ns2")
    assert [r.sources for r in engine.search(q(text="replica"))] == [[]]


@pytest.mark.parametrize("reply", [None, [], {"results": None}, {"results": {}}, {"other": []}])
def test_unexpected_search_reply(fake, engine, reply):
    fake.search_reply = reply; engine.reset("ns")
    with pytest.raises(RuntimeError, match="mem0: unexpected search reply"):
        engine.search(q())


@pytest.mark.parametrize("row", [{"id": "m"}, {"id": "m", "memory": None}, {"id": "m", "memory": 3}, "text"])
def test_search_row_without_string_memory(fake, engine, row):
    fake.search_reply = {"results": [{"id": "ok", "memory": "fine"}, row]}; engine.reset("ns")
    with pytest.raises(RuntimeError, match="mem0: unexpected search reply"):
        engine.search(q())


def test_search_before_reset_errors(engine):
    with pytest.raises(RuntimeError, match="reset"):
        engine.search(q())


def test_flush_is_a_no_op(fake, engine):
    before = len(fake.requests)
    engine.flush()
    assert len(fake.requests) == before


def test_running_model_mismatch_stops(fake):
    fake.llm_model = "gpt-5-mini"
    with pytest.raises(ValueError, match="gpt-5-mini"):
        make(fake)


def test_running_embedding_mismatch_stops(fake):
    fake.embedding_model = "text-embedding-3-large"
    with pytest.raises(ValueError, match="text-embedding-3-large"):
        make(fake)


@pytest.mark.parametrize("reply", [None, [], {}, {"llm": {"config": {"model": "gpt-4.1-mini"}}},
                                   {"llm": {"config": {}}, "embedder": {"config": {"model": "m"}}},
                                   {"llm": {"config": {"model": "gpt-4.1-mini"}}, "embedder": "x"},
                                   {"llm": {"config": {"model": 3}}, "embedder": {"config": {"model": "m"}}}])
def test_unexpected_configure_reply(fake, reply):
    fake.configure_reply = reply
    with pytest.raises(RuntimeError, match="mem0: unexpected configure reply"):
        make(fake)


def test_constructor_fails_when_engine_down():
    with pytest.raises(EngineHTTPError):
        Mem0Engine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=lambda _: None)


def test_first_build_against_a_down_engine_does_not_retry():
    waits: list[float] = []
    with pytest.raises(EngineHTTPError):
        Mem0Engine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=waits.append)
    assert waits == []


def test_later_builds_retry_a_blip(fake):
    make(fake)  # the first build verifies the address
    fake.fail_next(503, 1)
    waits: list[float] = []
    Mem0Engine(cfg(), base_url=fake.url, environ=MODELS_ENV, sleep=waits.append)
    assert waits == [2.0]


def test_first_build_does_not_retry_a_blip(fake):
    fake.fail_next(503, 1)
    waits: list[float] = []
    with pytest.raises(EngineHTTPError):
        Mem0Engine(cfg(), base_url=fake.url, environ=MODELS_ENV, sleep=waits.append)
    assert waits == []


def test_constructor_rejects_non_local_url():
    with pytest.raises(ValueError):
        Mem0Engine(cfg(), base_url="http://example.com:8890", environ=MODELS_ENV, sleep=lambda _: None)


def test_info(engine):
    i = engine.info()
    assert (i.name, i.version, i.virtual_clock, i.sources, i.scopes) == ("mem0", "2.2.1", False, True, True)
    assert i.config["llm_model"] == "gpt-4.1-mini" and i.config["embedding_model"] == "text-embedding-3-small"
    assert i.config["reranker"] == "none (engine default)"
    assert "OPENAI_API_KEY" not in str(i.config)
