"""Engine contract: the data types and abstract interface every memory engine implements."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

Scope = Literal["personal", "team"]
Signal = Literal["self", "test", "review", "human", "doc"]
Kind = Literal["agent_record", "ci_result", "human_confirm", "commit"]
Outcome = Literal["success", "failure", "unknown"]


class Event(BaseModel, frozen=True):
    id: str
    t: AwareDatetime
    user: str
    session: str
    repo: str
    scope: Scope
    team: str
    signal: Signal
    kind: Kind
    outcome: Outcome
    text: str
    meta: dict[str, str] = {}


class Query(BaseModel, frozen=True):
    text: str
    user: str
    team: str
    session: str
    repo: str
    t: AwareDatetime
    k: int
    meta: dict[str, str] = {}


class Result(BaseModel):
    text: str
    sources: list[str] = []
    meta: dict[str, Any] = {}


class EngineInfo(BaseModel):
    name: str
    version: str
    config: dict[str, Any] = {}
    virtual_clock: bool = False
    sources: bool = False
    scopes: bool = False


class BaseEngine(ABC):
    @abstractmethod
    def info(self) -> EngineInfo: ...

    @abstractmethod
    def reset(self, namespace: str) -> None: ...

    @abstractmethod
    def record(self, event: Event) -> None: ...

    @abstractmethod
    def search(self, query: Query) -> list[Result]: ...

    def tick(self, t: datetime) -> None:
        """Advance the virtual clock. Default: no-op."""

    def flush(self) -> None:
        """Finish any pending work. Default: no-op."""


def can_see(event: Event, user: str, team: str) -> bool:
    if event.scope == "personal":
        return event.user == user
    return event.team == team
