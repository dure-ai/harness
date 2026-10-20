"""Engine contract: the data types and abstract interface every memory engine implements.

Engines only ever see `Event` and `Query`. Ground truth (which fact an event states, when it
is true, what counts as a secret) lives in the scenario model and never crosses this boundary.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

Scope = Literal["personal", "team"]
Signal = Literal["self", "test", "review", "human", "doc"]
Kind = Literal["agent_record", "ci_result", "human_confirm", "commit"]
Outcome = Literal["success", "failure", "unknown"]


class Event(BaseModel, frozen=True):
    """One observation handed to an engine through `record()`. Carries no ground truth."""

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
    """What the simulated agent asks. `team` is the asker's team, `t` the virtual time of asking."""

    text: str
    user: str
    team: str
    session: str
    repo: str
    t: AwareDatetime
    k: int
    meta: dict[str, str] = {}


class Result(BaseModel):
    """One item an engine hands to the agent.

    `sources` are the ids of the events the item is based on. Scoring follows them to the hidden
    facts, so engines should fill them; with no sources the scorer falls back to fact markers.
    """

    text: str
    sources: list[str] = []
    meta: dict[str, Any] = {}


class EngineInfo(BaseModel):
    """Shown in the report. `virtual_clock=True` makes the replay call `tick(t)` before every call."""

    name: str
    version: str
    config: dict[str, Any] = {}
    virtual_clock: bool = False
    sources: bool = False
    scopes: bool = False


class BaseEngine(ABC):
    """A memory engine under evaluation, treated as a black box.

    Per scenario the replay calls `reset(namespace)` once, then `record(event)` and
    `search(query)` in virtual-time order. With a virtual clock, `tick(t)` comes before each
    call; `flush()` comes before each search. An exception from any of these ends that
    scenario as errored and the engine cannot pass the verdict.
    """

    @abstractmethod
    def info(self) -> EngineInfo:
        """Describe the engine (name, version, config, capabilities) for the report."""

    @abstractmethod
    def reset(self, namespace: str) -> None:
        """Start an empty, isolated memory for one scenario; called once before anything else."""

    @abstractmethod
    def record(self, event: Event) -> None:
        """Receive one observation, in virtual-time order."""

    @abstractmethod
    def search(self, query: Query) -> list[Result]:
        """Return what to put in the agent's context, best first.

        Only the first `query.k` results are used. Fill `Result.sources` with event ids so the
        result can be scored precisely, and never return events the asker cannot see.
        """

    def tick(self, t: datetime) -> None:
        """Advance the virtual clock. Default: no-op."""

    def flush(self) -> None:
        """Finish any pending work. Default: no-op."""


def can_see(event: Event, user: str, team: str) -> bool:
    """Visibility rule shared by leak scoring and the baselines.

    Args:
        event: The event in question.
        user: The asker.
        team: The asker's team.

    Returns:
        True if a personal event is the asker's own, or a team event belongs to the asker's team.
    """
    if event.scope == "personal":
        return event.user == user
    return event.team == team
