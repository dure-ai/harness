import re
from datetime import datetime

import pytest

from harness.contract import Event, Query
from harness.oss.text import engine_ns, event_unit, format_event, format_query, query_units


def ev(**overrides) -> Event:
    fields = dict(
        id="e1",
        t=datetime.fromisoformat("2026-10-01T09:00:00+09:00"),
        user="u1",
        session="s1",
        repo="shop-api",
        scope="team",
        team="team-a",
        signal="self",
        kind="agent_record",
        outcome="unknown",
        text="x",
        meta={},
        refs=(),
    )
    fields.update(overrides)
    return Event(**fields)


def q(**overrides) -> Query:
    fields = dict(
        text="Can I fix it?",
        user="u1",
        team="team-a",
        session="s1",
        repo="shop-api",
        t=datetime.fromisoformat("2026-10-01T09:00:00+09:00"),
        k=5,
        meta={},
    )
    fields.update(overrides)
    return Query(**fields)


def test_plain_agent_record():
    assert format_event(ev(text="Use the replica", user="u2")) == "[shop-api] u2: Use the replica"


def test_meta_sorted_and_signal_phrase():
    e = ev(
        text="Fixed it",
        user="u3",
        signal="test",
        kind="ci_result",
        outcome="success",
        meta={"service": "billing", "env": "prod"},
    )
    assert format_event(e) == "[shop-api] {env=prod, service=billing} u3: Fixed it (tests passed)"


@pytest.mark.parametrize(
    "signal,outcome,phrase",
    [
        ("test", "failure", "(tests failed)"),
        ("test", "unknown", "(tests ran)"),
        ("review", "success", "(review approved)"),
        ("review", "failure", "(review rejected)"),
        ("review", "unknown", "(reviewed)"),
        ("human", "success", "(confirmed by a person)"),
        ("human", "failure", "(rejected by a person)"),
        ("human", "unknown", "(noted by a person)"),
        ("doc", "success", "(from docs)"),
        ("doc", "failure", "(from docs)"),
        ("doc", "unknown", "(from docs)"),
    ],
)
def test_every_signal_phrase(signal, outcome, phrase):
    assert format_event(ev(text="x", signal=signal, outcome=outcome)).endswith(" x " + phrase)


def test_self_signal_has_no_phrase_and_no_trailing_space():
    out = format_event(ev(text="x", signal="self", outcome="success"))
    assert out == "[shop-api] u1: x"


def test_commit_prefix_before_signal():
    assert format_event(ev(text="x", kind="commit", signal="self", outcome="success")).endswith(
        "x (commit)"
    )
    assert format_event(ev(text="x", kind="commit", signal="test", outcome="success")).endswith(
        "x (commit) (tests passed)"
    )


def test_refs_not_in_body():
    assert "e1" not in format_event(ev(text="CI ok", signal="test", kind="ci_result", refs=("e1",)))


def test_query_has_no_author():
    assert format_query(q(text="Can I fix it?", meta={"env": "prod"})) == (
        "[shop-api] {env=prod} Can I fix it?"
    )


def test_engine_ns_hides_scenario_id():
    ns = "20261021-120000-r1:leak_personal-widget-a1b2c3"
    out = engine_ns(ns)
    assert out.startswith("t5-") and len(out) == 27
    assert "leak" not in out and out == engine_ns(ns) and out != engine_ns(ns + "x")
    assert re.fullmatch(r"[a-z0-9-]+", out)


def test_units():
    assert event_unit(ev(scope="personal", user="u1")) == "user:u1"
    assert event_unit(ev(scope="team", team="team-a")) == "team:team-a"
    assert query_units(q(user="u4", team="team-a")) == ["user:u4", "team:team-a"]
