"""Simple baseline engines: none, all_store, and repeat_n. All deterministic, no models."""

from abc import ABC

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result, can_see
from harness.text import BM25, jaccard


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


def _rank(events: list[Event], q: Query) -> list[Result]:
    """BM25 over `events`; keep score > 0, order by score desc, t desc, id asc, top k."""
    index = BM25()
    for e in events:
        index.add(e.id, e.text)
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


class AllStoreEngine(_NamespacedEngine):
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
        return _rank(events, query)


class RepeatNEngine(_NamespacedEngine):
    """Hands over only events that recur: at least `n` similar visible events (itself included).

    Similarity is token-set Jaccard >= `tau`; who said it is ignored, so repeated injection by one
    source or echoed records fool it. Self-check expects it to fail the poison and echo families.

    Args:
        n: Minimum number of similar events.
        tau: Similarity threshold.
    """

    def __init__(self, n: int, tau: float) -> None:
        super().__init__()
        self.n = n
        self.tau = tau

    def info(self) -> EngineInfo:
        """Return the engine info with its parameters."""
        return EngineInfo(
            name="repeat_n", version="0.1", config={"n": self.n, "tau": self.tau},
            sources=True, scopes=True,
        )

    def search(self, query: Query) -> list[Result]:
        """Keep visible events that recur at least `n` times, then rank them like all_store."""
        visible = [e for e in self._store() if can_see(e, query.user, query.team)]
        kept = [
            e for e in visible
            if sum(1 for x in visible if jaccard(e.text, x.text) >= self.tau) >= self.n
        ]
        return _rank(kept, query)
