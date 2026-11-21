"""Simple baseline engines: none, all_store, and repeat_n. All deterministic, no models."""

from abc import ABC
from collections import Counter
from typing import override

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result, can_see
from harness.text import BM25, counted, set_jaccard_at_least


class _NamespacedEngine(BaseEngine, ABC):
    """Keeps a dict of namespace -> stored events; `reset` selects and clears one."""

    def __init__(self) -> None:
        self._stores: dict[str, list[Event]] = {}
        self._current: str | None = None

    def _store(self) -> list[Event]:
        if self._current is None:
            raise RuntimeError("reset(namespace) must be called before record/search")
        return self._stores[self._current]

    def reset(self, namespace: str) -> None:
        """Create or clear `namespace` and make it current."""
        self._stores[namespace] = []
        self._current = namespace

    def record(self, event: Event) -> None:
        """Store the event in the current namespace."""
        self._store().append(event)


class _IndexedEngine(_NamespacedEngine, ABC):
    """Also keeps every stored event's term counts, made once when it is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self._counts: dict[str, dict[str, tuple[Counter[str], int]]] = {}

    @override
    def reset(self, namespace: str) -> None:
        """Create or clear `namespace` and make it current."""
        super().reset(namespace)
        self._counts[namespace] = {}

    @override
    def record(self, event: Event) -> None:
        """Store the event and its term counts in the current namespace."""
        super().record(event)
        self._counts[self._namespace()][event.id] = counted(event.text)

    def _namespace(self) -> str:
        if self._current is None:
            raise RuntimeError("reset(namespace) must be called before record/search")
        return self._current

    def _rank(self, events: list[Event], q: Query) -> list[Result]:
        return _rank(events, q, self._counts[self._namespace()])


def _rank(
    events: list[Event], q: Query, counts: dict[str, tuple[Counter[str], int]] | None = None
) -> list[Result]:
    """BM25 over `events`; keep score > 0, order by score desc, t desc, id asc, top k.

    `counts` holds each event's term counts by id (`text.counted`); without it every event is
    tokenized here.
    """
    index = BM25()
    for e in events:
        if counts is None:
            index.add(e.id, e.text)
        else:
            index.add_counted(e.id, *counts[e.id])
    scores = index.scores(q.text)
    hits = [e for e in events if e.id in scores]
    hits.sort(key=lambda e: (-scores[e.id], -e.t.timestamp(), e.id))
    return [Result(text=e.text, sources=[e.id]) for e in hits[: q.k]]


class NoneEngine(_NamespacedEngine):
    """Hands over nothing: the "no memory" floor (M1 = M2 = 0)."""

    def info(self) -> EngineInfo:
        """Return the engine info."""
        return EngineInfo(name="none", version="0.1", sources=True, scopes=True)

    def search(self, query: Query) -> list[Result]:
        """Return no results (after checking that `reset` was called)."""
        self._store()
        return []


class AllStoreEngine(_IndexedEngine):
    """Stores everything and hands over the best BM25 matches: the judgment baseline.

    Args:
        acl: Respect visibility (default). False builds "all_store_noacl", a diagnostic used only
            to check that leak probes can actually leak.
    """

    def __init__(self, acl: bool = True) -> None:
        super().__init__()
        self.acl = acl

    def info(self) -> EngineInfo:
        """Return the engine info, including whether visibility is enforced."""
        name = "all_store" if self.acl else "all_store_noacl"
        return EngineInfo(name=name, version="0.1", config={"acl": self.acl}, sources=True, scopes=True)

    def search(self, query: Query) -> list[Result]:
        """Rank the visible stored events with BM25 and return the top `query.k`."""
        events = self._store()
        if self.acl:
            events = [e for e in events if can_see(e, query.user, query.team)]
        return self._rank(events, query)


class RepeatNEngine(_IndexedEngine):
    """Hands over only events that recur: at least `n` similar visible events (itself included).

    Similarity is token-set Jaccard >= `tau`; who said it is ignored, so repeated injection by one
    source or echoed records fool it. Self-check expects it to fail the poison family.

    Args:
        n: Minimum number of similar events.
        tau: Similarity threshold.
    """

    def __init__(self, n: int, tau: float) -> None:
        super().__init__()
        self.n = n
        self.tau = tau
        # Per namespace, by position in the store: each event's token set, and the positions of
        # the events similar to it (itself included when it is similar to itself). Found once,
        # when an event is recorded, so a query only counts them.
        self._sets: dict[str, list[set[str]]] = {}
        self._similar: dict[str, list[list[int]]] = {}

    @override
    def reset(self, namespace: str) -> None:
        """Create or clear `namespace` and make it current."""
        super().reset(namespace)
        self._sets[namespace] = []
        self._similar[namespace] = []

    @override
    def record(self, event: Event) -> None:
        """Store the event and note which stored events are similar to it."""
        super().record(event)
        namespace = self._namespace()
        sets, similar = self._sets[namespace], self._similar[namespace]
        tokens = set(self._counts[namespace][event.id][0])
        position = len(sets)
        mine = [position] if set_jaccard_at_least(tokens, tokens, self.tau) else []
        for j, other in enumerate(sets):
            if set_jaccard_at_least(tokens, other, self.tau):
                mine.append(j)
                similar[j].append(position)
        sets.append(tokens)
        similar.append(mine)

    def info(self) -> EngineInfo:
        """Return the engine info with its parameters."""
        return EngineInfo(
            name="repeat_n", version="0.1", config={"n": self.n, "tau": self.tau},
            sources=True, scopes=True,
        )

    def search(self, query: Query) -> list[Result]:
        """Keep visible events that recur at least `n` times, then rank them like all_store."""
        store = self._store()
        similar = self._similar[self._namespace()]
        visible = [i for i, e in enumerate(store) if can_see(e, query.user, query.team)]
        shown = set(visible)
        kept = [store[i] for i in visible if sum(1 for j in similar[i] if j in shown) >= self.n]
        return self._rank(kept, query)
