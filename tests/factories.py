"""Shared builders for tests. Defaults here are relied on by every task's tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

from harness.scenario.model import (
    Fact,
    Probe,
    Provenance,
    Scenario,
    ScenarioEvent,
    World,
)

KST = ZoneInfo("Asia/Seoul")


def T(s: str) -> datetime:
    """`"10-01 10:00"` -> 2026-10-01 10:00 in Asia/Seoul."""
    return datetime.strptime(f"2026-{s}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)


def fact(**kw) -> Fact:
    kw.setdefault("text", "fact text")
    return Fact(**kw)


def ev(id: str, t: datetime, **kw) -> ScenarioEvent:
    defaults = dict(
        user="u1",
        session="s1",
        repo="r",
        scope="team",
        signal="self",
        kind="agent_record",
        outcome="unknown",
        text=f"text of {id}",
    )
    return ScenarioEvent(id=id, t=t, **{**defaults, **kw})


def probe(id: str, kind: str, t: datetime, about: list[str], **kw) -> Probe:
    defaults = dict(user="u2", session="s9", repo="r", query=f"query {id}")
    return Probe(id=id, kind=kind, t=t, about=about, **{**defaults, **kw})


def scenario(facts: dict, events: list, probes: list, **kw) -> Scenario:
    defaults = dict(
        id="sc",
        family="truth_change",
        mode="read_only",
        world=World(teams={"a": ["u1", "u2"], "b": ["u3"]}),
        provenance=Provenance(author="human"),
    )
    return Scenario(facts=facts, events=events, probes=probes, **{**defaults, **kw})
