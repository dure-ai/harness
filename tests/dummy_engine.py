"""Minimal engine used to test import-path engine loading."""

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result


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
