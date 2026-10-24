"""Opt-in live check: both open-source engines, real containers, real model calls."""

import json
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from harness.config import LLM, EvalConfig
from harness.contract import BaseEngine, Event, Query
from harness.oss.hindsight import HindsightEngine
from harness.oss.mem0 import Mem0Engine
from tests.test_oss_cli import EXAMPLES, any_errors, run_cli

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESS_LIVE_OSS") != "1",
    reason="set HARNESS_LIVE_OSS=1 with both containers up",
)


def test_live_examples_both_engines(tmp_path):
    result, out = run_cli(
        tmp_path, EXAMPLES, "--include-drafts",
        "--engine", "hindsight", "--engine", "mem0", "--repeat", "1",
    )
    assert result.exit_code == 0, result.output
    assert not any_errors(json.loads((next(out.iterdir()) / "summary.json").read_text()))


def scope_event(id: str, user: str, scope: str, text: str, team: str = "team-a") -> Event:
    return Event(id=id, t=T0, user=user, session="s1", repo="shop-api", scope=scope, team=team,
                 signal="self", kind="agent_record", outcome="unknown", text=text)


@pytest.mark.parametrize("engine_class", [HindsightEngine, Mem0Engine], ids=["hindsight", "mem0"])
def test_live_scope_isolation(engine_class: type[BaseEngine]):
    """The asker finds their own personal memory and the team's, and never another user's personal one."""
    cfg = EvalConfig(llm=LLM(provider="openai", model=os.environ["HARNESS_LLM_MODEL"],
                             embedding_model=os.environ["HARNESS_EMBEDDING_MODEL"]))
    engine = engine_class(cfg)
    namespace = f"live-scope-{uuid.uuid4().hex}"
    engine.reset(namespace)
    try:
        engine.record(scope_event("p1", "u1", "personal", "u1 keeps the pineapple migration notes in a private scratch file."))
        engine.record(scope_event("p2", "u2", "personal", "u2 keeps the walnut migration notes in a private scratch file."))
        engine.record(scope_event("t1", "u3", "team", "The team agreed to run the cinnamon migration notes review every Friday."))
        engine.flush()
        results = engine.search(Query(text="Where are the pineapple, walnut and cinnamon migration notes?",
                                      user="u1", team="team-a", session="s2", repo="shop-api", t=T0, k=10))
        sources = {source for result in results for source in result.sources}
        assert {"p1", "t1"} <= sources, sources
        assert "p2" not in sources, sources
    finally:
        engine.reset(namespace)  # deletes what the test stored


# --- Mem0 visibility cases (PR #3 review): the adapter relies on the server's `OR` filter and on
# its hash dedupe being scoped to run_id / user_id, which only a real server can show. ---


def live_cfg() -> EvalConfig:
    return EvalConfig(llm=LLM(provider="openai", model=os.environ["HARNESS_LLM_MODEL"],
                              embedding_model=os.environ["HARNESS_EMBEDDING_MODEL"]))


def ask(engine: BaseEngine, text: str, user: str, team: str) -> list:
    return engine.search(Query(text=text, user=user, team=team, session="s2", repo="shop-api", t=T0, k=10))


def returned_sources(results: list) -> set[str]:
    return {source for result in results for source in result.sources}


def test_live_mem0_other_teams_team_record_is_not_returned():
    """A `team` record of team-b is not returned to a caller of team-a, and the other way round."""
    engine = Mem0Engine(live_cfg())
    namespace = f"live-team-{uuid.uuid4().hex}"
    engine.reset(namespace)
    try:
        engine.record(scope_event("ta1", "u3", "team", "Team A agreed to review the cinnamon migration notes every Friday.", "team-a"))
        engine.record(scope_event("tb1", "u9", "team", "Team B rotates the cobalt deploy keys every Monday morning.", "team-b"))
        engine.flush()
        question = "When are the cinnamon migration notes reviewed and the cobalt deploy keys rotated?"
        from_a = ask(engine, question, "u1", "team-a")
        assert "ta1" in returned_sources(from_a), "control: the own team's record must come back"
        assert "tb1" not in returned_sources(from_a)
        assert not any("cobalt" in r.text.lower() for r in from_a), [r.text for r in from_a]
        from_b = ask(engine, question, "u9", "team-b")
        assert "tb1" in returned_sources(from_b), "control: the own team's record must come back"
        assert "ta1" not in returned_sources(from_b)
        assert not any("cinnamon" in r.text.lower() for r in from_b), [r.text for r in from_b]
    finally:
        engine.reset(namespace)


def test_live_mem0_other_users_personal_record_is_not_returned():
    """A `personal` record of u2 is not returned to u1 (same team), and u2 still finds it."""
    engine = Mem0Engine(live_cfg())
    namespace = f"live-personal-{uuid.uuid4().hex}"
    engine.reset(namespace)
    try:
        engine.record(scope_event("p1", "u1", "personal", "u1 keeps the pineapple release checklist in a private scratch file."))
        engine.record(scope_event("p2", "u2", "personal", "u2 keeps the walnut release checklist in a private scratch file."))
        engine.flush()
        question = "Where are the pineapple and walnut release checklists kept?"
        from_u1 = ask(engine, question, "u1", "team-a")
        assert "p1" in returned_sources(from_u1), "control: the asker's own record must come back"
        assert "p2" not in returned_sources(from_u1)
        assert not any("walnut" in r.text.lower() for r in from_u1), [r.text for r in from_u1]
        from_u2 = ask(engine, question, "u2", "team-a")
        assert "p2" in returned_sources(from_u2), "control: the owner must find the record"
        assert "p1" not in returned_sources(from_u2)
    finally:
        engine.reset(namespace)


def test_live_mem0_same_sentence_in_another_run_is_kept_apart():
    """The hash dedupe does not swallow a sentence recorded under another run_id, and a run only returns its own."""
    sentence = "The team agreed to run the cinnamon migration notes review every Friday."
    first, second = Mem0Engine(live_cfg()), Mem0Engine(live_cfg())
    ns_first, ns_second = f"live-run-a-{uuid.uuid4().hex}", f"live-run-b-{uuid.uuid4().hex}"
    first.reset(ns_first)
    second.reset(ns_second)
    try:
        first.record(scope_event("a1", "u3", "team", sentence))
        second.record(scope_event("b1", "u3", "team", sentence))
        first.flush()
        second.flush()
        question = "When is the cinnamon migration notes review?"
        from_first = ask(first, question, "u1", "team-a")
        from_second = ask(second, question, "u1", "team-a")
        assert from_first and from_second, "the second recording must not be suppressed as a duplicate"
        # a row of the other run would come back without a known source, so no row may lack one
        assert returned_sources(from_first) == {"a1"} and all(r.sources == ["a1"] for r in from_first)
        assert returned_sources(from_second) == {"b1"} and all(r.sources == ["b1"] for r in from_second)
    finally:
        first.reset(ns_first)
        second.reset(ns_second)
