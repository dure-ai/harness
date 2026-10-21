"""Minimal engine used to test import-path engine loading."""

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result, can_see


class Dummy(BaseEngine):
    def info(self) -> EngineInfo:
        return EngineInfo(name="dummy", version="0.1")

    def reset(self, namespace: str) -> None:
        pass

    def record(self, event: Event) -> None:
        pass

    def search(self, query: Query) -> list[Result]:
        return []


class NotAnEngine:
    """Importable class that is not a BaseEngine; used to test the make_engine type guard."""


class FailSecondBuild(Dummy):
    """Raises on its 2nd construction; the test resets `built` first."""

    built = 0

    def __init__(self) -> None:
        type(self).built += 1
        if type(self).built == 2:
            raise RuntimeError("second build fails")


class FailInit(Dummy):
    def __init__(self) -> None:
        raise RuntimeError("cannot construct")


class FailRecord(Dummy):
    def record(self, event: Event) -> None:
        raise RuntimeError("record fails")



class LatestWins(BaseEngine):
    """Answers every query with the most recent event the asker can see; passes on the CLI test set."""

    def info(self) -> EngineInfo:
        return EngineInfo(name="latest", version="0.1", sources=True)

    def reset(self, namespace: str) -> None:
        self.events: list[Event] = []

    def record(self, event: Event) -> None:
        self.events.append(event)

    def search(self, query: Query) -> list[Result]:
        seen = [e for e in self.events if can_see(e, query.user, query.team) and e.t <= query.t]
        if not seen:
            return []
        latest = max(seen, key=lambda e: e.t)
        return [Result(text=latest.text, sources=[latest.id])]


class FailOnTc(LatestWins):
    """Like LatestWins, but errors on scenario "tc" from its second search on."""

    def reset(self, namespace: str) -> None:
        super().reset(namespace)
        self.fail = namespace.endswith(":tc")
        self.searches = 0

    def search(self, query: Query) -> list[Result]:
        self.searches += 1
        if self.fail and self.searches > 1:
            raise RuntimeError("search fails")
        return super().search(query)


class ReturnsDicts(LatestWins):
    """LatestWins, but returns plain dicts instead of Result objects."""

    def search(self, query: Query) -> list:
        return [r.model_dump() for r in super().search(query)]


class ReturnsInts(Dummy):
    def search(self, query: Query) -> list:
        return [1, 2]


class FailOnSecondRepeat(LatestWins):
    """Like LatestWins, but errors on every scenario of the second repeat."""

    def reset(self, namespace: str) -> None:
        if "-r2:" in namespace:
            raise RuntimeError("second repeat fails")
        super().reset(namespace)
