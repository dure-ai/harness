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


def scope_event(id: str, user: str, scope: str, text: str) -> Event:
    return Event(id=id, t=T0, user=user, session="s1", repo="shop-api", scope=scope, team="team-a",
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
