"""Simple baseline engines: none, all_store, and repeat_n. All deterministic, no models."""

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result, can_see
from harness.text import BM25, jaccard


class _NamespacedEngine(BaseEngine):
    """Keeps a dict of namespace -> stored events; `reset` selects and clears one."""

    def __init__(self) -> None:
        self._stores: dict[str, list[Event]] = {}
        self._current: str | None = None

    def _store(self) -> list[Event]:
        if self._current is None:
            raise RuntimeError("reset(namespace) must be called before record/search")
        return self._stores[self._current]

    def reset(self, namespace: str) -> None:
        self._stores[namespace] = []
        self._current = namespace

    def record(self, event: Event) -> None:
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
    def info(self) -> EngineInfo:
        return EngineInfo(name="none", version="0.1", sources=True, scopes=True)

    def search(self, query: Query) -> list[Result]:
        self._store()
        return []


class AllStoreEngine(_NamespacedEngine):
    def __init__(self, acl: bool = True) -> None:
        super().__init__()
        self.acl = acl

    def info(self) -> EngineInfo:
        name = "all_store" if self.acl else "all_store_noacl"
        return EngineInfo(name=name, version="0.1", config={"acl": self.acl}, sources=True, scopes=True)

    def search(self, query: Query) -> list[Result]:
        events = self._store()
        if self.acl:
            events = [e for e in events if can_see(e, query.user, query.team)]
        return _rank(events, query)


class RepeatNEngine(_NamespacedEngine):
    def __init__(self, n: int, tau: float) -> None:
        super().__init__()
        self.n = n
        self.tau = tau

    def info(self) -> EngineInfo:
        return EngineInfo(
            name="repeat_n", version="0.1", config={"n": self.n, "tau": self.tau},
            sources=True, scopes=True,
        )

    def search(self, query: Query) -> list[Result]:
        visible = [e for e in self._store() if can_see(e, query.user, query.team)]
        kept = [
            e for e in visible
            if sum(1 for x in visible if jaccard(e.text, x.text) >= self.tau) >= self.n
        ]
        return _rank(kept, query)
