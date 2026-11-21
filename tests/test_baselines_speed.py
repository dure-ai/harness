"""The baselines' search keeps its exact results while not re-reading stored texts per query.

The reference engines below are the original definitions, written plainly: every query
tokenizes every visible event again, and repeat_n compares every visible pair. The real
engines must return exactly what they return.
"""

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import harness.text as text_mod
from harness.baselines.simple import AllStoreEngine, RepeatNEngine
from harness.config import load_config
from harness.contract import Event, Query, Result, can_see
from harness.replay import blind_key, run_scenario
from harness.scenario.load import load_set
from harness.text import BM25, jaccard

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _reference_rank(events: list[Event], q: Query) -> list[Result]:
    index = BM25()
    for e in events:
        index.add(e.id, e.text)
    scores = index.scores(q.text)
    hits = [e for e in events if e.id in scores]
    hits.sort(key=lambda e: (-scores[e.id], -e.t.timestamp(), e.id))
    return [Result(text=e.text, sources=[e.id]) for e in hits[: q.k]]


class ReferenceAllStore(AllStoreEngine):
    def search(self, query):
        events = self._store()
        visible = [e for e in events if can_see(e, query.user, query.team)] if self.acl else list(events)
        return _reference_rank(visible, query)


class ReferenceRepeatN(RepeatNEngine):
    def search(self, query):
        visible = [e for e in self._store() if can_see(e, query.user, query.team)]
        kept = [e for e in visible if sum(1 for x in visible if jaccard(e.text, x.text) >= self.tau) >= self.n]
        return _reference_rank(kept, query)


def _event(i: int, text: str, rng: random.Random) -> Event:
    return Event(
        id=f"e{i}", t=T0 + timedelta(minutes=i // 2), user=rng.choice(["u1", "u2", "u3"]), session="s",
        repo="r", scope=rng.choice(["team", "personal"]), team=rng.choice(["a", "b"]), signal="self",
        kind="agent_record", outcome="unknown", text=text,
    )


def _texts(rng: random.Random, n: int) -> list[str]:
    vocab = [f"w{i}" for i in range(40)] + ["deploy", "api", "server", "배포", "서버를"]
    base = [" ".join(rng.choice(vocab) for _ in range(rng.randint(2, 7))) for _ in range(n // 4 + 1)]
    out = []
    for _ in range(n):
        roll = rng.random()
        out.append("" if roll < 0.05 else "!!" if roll < 0.08 else rng.choice(base) if roll < 0.6 else " ".join(rng.choice(vocab) for _ in range(5)))
    return out


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize(("n", "tau"), [(3, 0.5), (2, 0.8), (1, 0.0), (4, 1.0)])
def test_repeat_n_returns_what_the_pairwise_definition_returns(seed, n, tau):
    rng = random.Random(seed)
    engine, reference = RepeatNEngine(n, tau), ReferenceRepeatN(n, tau)
    for e in (engine, reference):
        e.reset("ns")
    for i, text in enumerate(_texts(rng, 60)):
        event = _event(i, text, rng)
        engine.record(event)
        reference.record(event)
        if i % 7 == 0:  # queries between records see a store that is still growing
            q = Query(text=rng.choice(["deploy api", "w1 w2", "배포 서버", ""]), user=rng.choice(["u1", "u2"]),
                      team=rng.choice(["a", "b"]), session="s", repo="r", t=T0 + timedelta(days=1), k=rng.randint(1, 8))
            assert engine.search(q) == reference.search(q)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("acl", [True, False])
def test_all_store_returns_what_a_fresh_bm25_per_query_returns(seed, acl):
    rng = random.Random(seed)
    engine, reference = AllStoreEngine(acl=acl), ReferenceAllStore(acl=acl)
    for e in (engine, reference):
        e.reset("ns")
    for i, text in enumerate(_texts(rng, 80)):
        event = _event(i, text, rng)
        engine.record(event)
        reference.record(event)
        q = Query(text=rng.choice(["deploy api", "w1 w2 w3", "배포", "nothing"]), user=rng.choice(["u1", "u2"]),
                  team=rng.choice(["a", "b"]), session="s", repo="r", t=T0 + timedelta(days=1), k=rng.randint(1, 8))
        assert engine.search(q) == reference.search(q)


def test_namespaces_keep_their_own_index():
    engine = RepeatNEngine(1, 0.5)
    engine.reset("a")
    rng = random.Random(0)
    engine.record(_event(0, "deploy api", rng))
    engine.reset("b")
    q = Query(text="deploy", user="u1", team="a", session="s", repo="r", t=T0 + timedelta(days=1), k=5)
    assert engine.search(q) == []
    engine.reset("a")  # reset clears the namespace it selects
    assert engine.search(q) == []


@pytest.mark.parametrize("make", [lambda: AllStoreEngine(), lambda: RepeatNEngine(2, 0.5)])
def test_a_search_tokenizes_the_query_only(monkeypatch, make):
    engine = make()
    engine.reset("ns")
    rng = random.Random(1)
    for i, text in enumerate(_texts(rng, 30)):
        engine.record(_event(i, text, rng))
    calls = []
    real = text_mod.tokenize

    def counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(text_mod, "tokenize", counting)
    monkeypatch.setattr("harness.baselines.simple.tokenize", counting, raising=False)
    engine.search(Query(text="deploy api", user="u1", team="a", session="s", repo="r", t=T0 + timedelta(days=1), k=5))
    assert calls == ["deploy api"]


@pytest.mark.parametrize("name", ["all_store", "repeat_n"])
def test_dev_scenarios_replay_exactly_as_with_the_reference(name):
    cfg = load_config("config/eval-v0.1.yaml")
    key = blind_key(7)
    loaded = load_set([Path("scenarios/dev")], False)
    for scenario in loaded.scenarios:
        if name == "all_store":
            new, ref = AllStoreEngine(), ReferenceAllStore()
        else:
            new, ref = RepeatNEngine(cfg.repeat_n.n, cfg.repeat_n.tau), ReferenceRepeatN(cfg.repeat_n.n, cfg.repeat_n.tau)
        a = run_scenario(new, scenario, run_id="x", cfg=cfg, blind_key=key)
        b = run_scenario(ref, scenario, run_id="x", cfg=cfg, blind_key=key)
        assert (a.status, a.error) == (b.status, b.error)
        assert [(p.probe_id, p.results, p.choice, p.outcome, p.leaks, p.bases) for p in a.probes] == [
            (p.probe_id, p.results, p.choice, p.outcome, p.leaks, p.bases) for p in b.probes
        ], scenario.id


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("tau", [0.0, 0.05, 0.3, 1 / 3, 0.5, 0.7, 0.9, 1.0])
def test_repeat_n_similar_pairs_equal_the_all_pairs_comparison(seed, tau):
    """Long texts with shared common words: every similar pair is found, and no other."""
    rng = random.Random(seed)
    common = ["the", "a", "to", "of", "and", "in", "it", "is"]
    rare = [f"word{i}" for i in range(60)] + ["서버를", "배포"]
    base = [" ".join(rng.choice(common + rare) for _ in range(rng.randint(0, 30))) for _ in range(25)]
    texts = []
    for _ in range(120):
        roll = rng.random()
        if roll < 0.05:
            texts.append("")
        elif roll < 0.5:
            words = rng.choice(base).split()
            texts.append(" ".join(w for w in words if rng.random() > 0.15))  # near-duplicates
        else:
            texts.append(" ".join(rng.choice(common + rare) for _ in range(rng.randint(1, 40))))
    engine = RepeatNEngine(2, tau)
    engine.reset("ns")
    for i, text in enumerate(texts):
        engine.record(_event(i, text, rng))
    sets = [set(text_mod.tokenize(t)) for t in texts]
    expected = [
        [i] * (text_mod.set_jaccard(a, a) >= tau) + [j for j, b in enumerate(sets) if j != i and text_mod.set_jaccard(a, b) >= tau]
        for i, a in enumerate(sets)
    ]
    assert engine._similar["ns"] == expected  # same pairs, in the same order (itself first, then by position)
