from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from harness.contract import BaseEngine, Event, Query, can_see


def make_event(**overrides) -> Event:
    fields = dict(
        id="e1",
        t=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        user="u1",
        session="s1",
        repo="r",
        scope="team",
        team="t",
        signal="self",
        kind="agent_record",
        outcome="unknown",
        text="x",
    )
    fields.update(overrides)
    return Event(**fields)


def test_can_see_personal_only_owner():
    e = make_event(user="u1", team="payments", scope="personal")
    assert can_see(e, "u1", "payments")
    assert not can_see(e, "u2", "payments")


def test_can_see_team_members_only():
    e = make_event(user="u1", team="payments", scope="team")
    assert can_see(e, "u2", "payments")
    assert not can_see(e, "u3", "search")


def test_event_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        make_event(t=datetime(2026, 10, 1, 10, 0))


def test_event_and_query_are_frozen():
    e = make_event()
    with pytest.raises(ValidationError):
        e.text = "changed"
    q = Query(
        text="x",
        user="u1",
        team="t",
        session="s1",
        repo="r",
        t=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        k=3,
    )
    with pytest.raises(ValidationError):
        q.k = 5


def test_base_engine_tick_flush_default_noop():
    class E(BaseEngine):
        def info(self):
            raise NotImplementedError

        def reset(self, namespace):
            pass

        def record(self, event):
            pass

        def search(self, query):
            return []

    E().tick(datetime.now(UTC))
    E().flush()
