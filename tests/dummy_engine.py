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
