from datetime import UTC, datetime, timedelta

import pytest

from harness.baselines.simple import AllStoreEngine, NoneEngine, RepeatNEngine
from harness.contract import Event, Query

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ev(id, text, *, user="u1", team="a", scope="team", dt=0):
    return Event(
        id=id, t=T0 + timedelta(minutes=dt), user=user, session="s", repo="r",
        scope=scope, team=team, signal="self", kind="agent_record",
        outcome="unknown", text=text,
    )


def q(text, *, user="u1", team="a", k=5):
    return Query(text=text, user=user, team=team, session="s", repo="r", t=T0 + timedelta(days=1), k=k)


def test_infos():
    for eng, name in [
        (NoneEngine(), "none"),
        (AllStoreEngine(), "all_store"),
        (AllStoreEngine(acl=False), "all_store_noacl"),
        (RepeatNEngine(n=3, tau=0.5), "repeat_n"),
    ]:
        info = eng.info()
        assert info.name == name
        assert info.virtual_clock is False
        assert info.sources is True
        assert info.scopes is True


def test_none_returns_empty():
    e = NoneEngine()
    e.reset("n")
    e.record(ev("e1", "vendor module"))
    assert e.search(q("vendor module")) == []


def test_all_store_basic_result():
    e = AllStoreEngine()
    e.reset("n")
    e.record(ev("e1", "vendor module rule"))
    e.record(ev("e2", "unrelated"))
    res = e.search(q("vendor"))
    assert [(r.text, r.sources) for r in res] == [("vendor module rule", ["e1"])]


def test_all_store_respects_acl():
    e = AllStoreEngine()
    e.reset("n")
    e.record(ev("e1", "vendor secret", team="a", scope="team", user="u1"))
    e.record(ev("e2", "vendor mine", team="a", scope="personal", user="u1"))
    assert [r.sources for r in e.search(q("vendor", user="u3", team="b"))] == []
    assert {r.sources[0] for r in e.search(q("vendor", user="u2", team="a"))} == {"e1"}
    assert {r.sources[0] for r in e.search(q("vendor", user="u1", team="a"))} == {"e1", "e2"}

    n = AllStoreEngine(acl=False)
    n.reset("n")
    n.record(ev("e1", "vendor secret", team="a", scope="team", user="u1"))
    assert [r.sources for r in n.search(q("vendor", user="u3", team="b"))] == [["e1"]]


def test_all_store_order_tiebreak():
    e = AllStoreEngine()
    e.reset("n")
    e.record(ev("b", "vendor", dt=0))
    e.record(ev("a", "vendor", dt=0))
    e.record(ev("c", "vendor", dt=5))
    assert [r.sources[0] for r in e.search(q("vendor"))] == ["c", "a", "b"]


def test_all_store_top_k():
    e = AllStoreEngine()
    e.reset("n")
    for i in range(4):
        e.record(ev(f"e{i}", "vendor", dt=i))
    assert [r.sources[0] for r in e.search(q("vendor", k=2))] == ["e3", "e2"]


def test_all_store_reset_isolates_namespaces():
    e = AllStoreEngine()
    e.reset("n1")
    e.record(ev("e1", "vendor"))
    e.reset("n2")
    assert e.search(q("vendor")) == []  # n2 does not see n1's events
    e.record(ev("e2", "vendor"))
    assert [r.sources[0] for r in e.search(q("vendor"))] == ["e2"]
    e.reset("n1")  # reset clears the namespace it names and makes it current
    assert e.search(q("vendor")) == []


def test_use_before_reset_raises():
    for e in (NoneEngine(), AllStoreEngine(), RepeatNEngine(n=3, tau=0.5)):
        with pytest.raises(RuntimeError):
            e.record(ev("e1", "x"))
        with pytest.raises(RuntimeError):
            e.search(q("x"))


def test_repeat_n_requires_n_similar():
    e = RepeatNEngine(n=3, tau=0.5)
    e.reset("n")
    e.record(ev("e1", "vendor module rule", dt=0))
    e.record(ev("e2", "vendor module rule", dt=1))
    assert e.search(q("vendor")) == []
    e.record(ev("e3", "vendor module rule", dt=2))
    assert [r.sources[0] for r in e.search(q("vendor"))] == ["e3", "e2", "e1"]


def test_repeat_n_counts_only_visible_events():
    e = RepeatNEngine(n=3, tau=0.5)
    e.reset("n")
    e.record(ev("e1", "vendor module rule", dt=0))
    e.record(ev("e2", "vendor module rule", dt=1))
    e.record(ev("e3", "vendor module rule", dt=2, user="u9", scope="personal"))
    assert e.search(q("vendor")) == []


def test_repeat_n_dissimilar_not_counted():
    e = RepeatNEngine(n=2, tau=0.5)
    e.reset("n")
    e.record(ev("e1", "vendor module rule"))
    e.record(ev("e2", "vendor totally different words here now"))
    assert e.search(q("vendor")) == []
