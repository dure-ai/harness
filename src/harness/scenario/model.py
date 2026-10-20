"""Scenario data model: what a scenario file contains, including the hidden labels."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict

from harness.contract import Event, Kind, Outcome, Query, Scope, Signal

Family = Literal[
    "normal_repeat",
    "coincidence",
    "single_source_poison",
    "echo",
    "failure_feedback",
    "truth_change",
    "conditional",
    "workaround_then_root",
    "leak_personal",
    "leak_cross_team",
]
FAMILIES: tuple[str, ...] = get_args(Family)
FEEDBACK_FAMILIES = ("echo", "failure_feedback")


class _Strict(BaseModel):
    """Base for scenario models: unknown keys are errors, never silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Fact(_Strict):
    text: str
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    valid: bool = True
    conditions: dict[str, str] = {}
    fix: Literal["root", "workaround"] | None = None
    markers: list[str] = []


class ScenarioEvent(_Strict):
    """An Event plus hidden labels; `team` may be omitted (resolved from the user's team)."""

    id: str
    t: AwareDatetime
    user: str
    session: str
    repo: str
    scope: Scope
    team: str | None = None
    signal: Signal
    kind: Kind
    outcome: Outcome
    text: str
    meta: dict[str, str] = {}
    fact: str | None = None
    secret_markers: list[str] = []


class FeedbackTemplate(_Strict):
    text: str
    fact: str | None = None
    delay: timedelta | None = None
    signal: Signal = "self"
    kind: Kind = "agent_record"
    outcome: Outcome = "unknown"
    scope: Scope = "team"


class Feedback(_Strict):
    action: dict[str, FeedbackTemplate] = {}
    abstain: FeedbackTemplate | None = None
    on_false: FeedbackTemplate | None = None
    on_true: FeedbackTemplate | None = None


class Probe(_Strict):
    id: str
    kind: Literal["block", "promote", "leak"]
    t: AwareDatetime
    user: str
    session: str
    repo: str
    query: str
    about: list[str]
    meta: dict[str, str] = {}
    k: int | None = None
    feedback: Feedback | None = None


class World(_Strict):
    timezone: str = "Asia/Seoul"
    teams: dict[str, list[str]]


class Review(_Strict):
    status: Literal["draft", "approved", "rejected"] = "draft"
    reviewer: str | None = None
    notes: str = ""


class Provenance(_Strict):
    author: Literal["human", "llm", "generator"]
    source_traces: list[str] = []
    llm_model: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class LabeledEvent:
    event: Event
    fact: str | None
    secret_markers: tuple[str, ...]


class Scenario(_Strict):
    id: str
    family: Family
    mode: Literal["read_only", "feedback"]
    language: str = "en"
    world: World
    facts: dict[str, Fact]
    events: list[ScenarioEvent]
    probes: list[Probe]
    review: Review = Review()
    provenance: Provenance

    def team_of(self, user: str) -> str:
        for team, members in self.world.teams.items():
            if user in members:
                return team
        raise KeyError(user)

    def labeled_events(self) -> list[LabeledEvent]:
        labeled = []
        for se in self.events:
            fields = se.model_dump(exclude={"fact", "secret_markers", "team"})
            event = Event(**fields, team=se.team or self.team_of(se.user))
            labeled.append(LabeledEvent(event, se.fact, tuple(se.secret_markers)))
        return labeled

    def query_for(self, probe: Probe, k: int) -> Query:
        return Query(
            text=probe.query,
            user=probe.user,
            team=self.team_of(probe.user),
            session=probe.session,
            repo=probe.repo,
            t=probe.t,
            k=probe.k or k,
            meta=probe.meta,
        )
