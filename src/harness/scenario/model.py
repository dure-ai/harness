"""Scenario data model: what a scenario file contains, including the hidden labels.

Hidden labels are the ground truth used only for scoring and are never sent to engines:
`ScenarioEvent.fact` and `.secret_markers`, every `Fact`, and the probe's `kind`, `about` and
`feedback`. `labeled_events()` and `query_for()` build the engine-facing objects without them.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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
    """A statement about the world and when it holds (see `world.true_at`).

    `markers` are strings that show a result states this fact, used when an engine returns no
    sources.
    """

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
    refs: list[str] = []  # engine-visible: earlier events this one judges
    # Hidden labels: the fact this event states (None = noise), and strings whose appearance in a
    # result shown to someone who cannot see this event counts as a leak.
    fact: str | None = None
    secret_markers: list[str] = []


class FeedbackTemplate(_Strict):
    """An event the harness writes back in feedback mode; `fact` is its hidden label."""

    text: str
    fact: str | None = None
    delay: timedelta | None = None
    signal: Signal = "self"
    kind: Kind = "agent_record"
    outcome: Outcome = "unknown"
    scope: Scope = "team"


class Feedback(_Strict):
    """Templates of the records the simulated agent writes in feedback mode.

    Attributes:
        action: Action record keyed by the fact the agent followed.
        abstain: Action record when it followed nothing.
        on_false: Outcome record after acting on a wrong basis.
        on_true: Outcome record after acting on a right basis.
    """

    action: dict[str, FeedbackTemplate] = {}
    abstain: FeedbackTemplate | None = None
    on_false: FeedbackTemplate | None = None
    on_true: FeedbackTemplate | None = None


class Probe(_Strict):
    """A question the simulated agent asks at time `t`.

    `kind`, `about` (facts that make a result relevant) and `feedback` are hidden labels and
    never reach the engine; see `Scenario.query_for`.
    """

    id: str
    kind: Literal["block", "promote", "leak"]
    t: AwareDatetime
    user: str
    session: str
    repo: str
    query: str
    about: list[str]
    meta: dict[str, str] = {}
    k: int | None = Field(default=None, ge=1)
    feedback: Feedback | None = None


class World(_Strict):
    """Who is on which team, and the timezone times are written in."""

    timezone: str = "Asia/Seoul"
    teams: dict[str, list[str]]


class Review(_Strict):
    """Human review state; only approved scenarios are evaluated by default."""

    status: Literal["draft", "approved", "rejected"] = "draft"
    reviewer: str | None = None
    notes: str = ""


class SourceRepo(_Strict):
    """A repository a scenario's source traces came from, with its license."""

    repo: str
    license: str


class Evidence(_Strict):
    """Where an event's content comes from in a source trace.

    `origin` is `quoted` when `quote` is copied from the trace message, and `written` when the
    generator wrote the event itself.
    """

    event_id: str
    trace_id: str | None = None
    message_index: int | None = None
    quote: str | None = None
    origin: Literal["quoted", "written"]


class Provenance(_Strict):
    """Where a scenario came from, reported so results can be split by author.

    Hidden: never reaches engines.

    Attributes:
        author: Who wrote the scenario.
        source_traces: Identifiers of the traces it was derived from.
        llm_model: Model that generated it.
        prompt_version: Prompt revision used.
        provider: LLM provider that served `llm_model`.
        source_repos: Repositories (with licenses) the source traces came from.
        fabricated_facts: Ids of facts the generator invented rather than found in the trace.
        evidence: Per-event links back to the source traces.
        secret_events: Ids of the events that hold the scenario's secrets (leak families),
            written at generation time. A file without the key (older or hand-written) counts
            as "not recorded"; see `generate.checks.secret_events`.
        reviewer_edited: Whether a human changed the scenario during review.
        generated_at: When the scenario was generated.
    """

    author: Literal["human", "llm", "generator"]
    source_traces: list[str] = []
    llm_model: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    source_repos: list[SourceRepo] = []
    fabricated_facts: list[str] = []
    evidence: list[Evidence] = []
    secret_events: list[str] = []
    reviewer_edited: bool = False
    generated_at: AwareDatetime | None = None


@dataclass(frozen=True)
class LabeledEvent:
    """The engine-facing `event` plus its hidden labels.

    Scoring keeps these; engines get only `.event`.
    """

    event: Event
    fact: str | None
    secret_markers: tuple[str, ...]


class Scenario(_Strict):
    """One evaluation scenario: a world, its facts, the events engines see, and probes.

    `id` is a plain name (letters, digits, `.`, `_`, `-`; at most 120 characters, starting with a
    letter or digit) because it ends up in file names (engine namespaces only see a keyed hash of it).
    """

    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
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
        """Return the team `user` belongs to.

        Raises:
            KeyError: If the user is on no team.
        """
        for team, members in self.world.teams.items():
            if user in members:
                return team
        raise KeyError(user)

    def labeled_events(self) -> list[LabeledEvent]:
        """Return engine-facing events in file order with hidden labels split off.

        An event's `team` defaults to its author's team.
        """
        labeled = []
        for se in self.events:
            fields = se.model_dump(exclude={"fact", "secret_markers", "team"})
            event = Event(**fields, team=se.team or self.team_of(se.user))
            labeled.append(LabeledEvent(event, se.fact, tuple(se.secret_markers)))
        return labeled

    def query_for(self, probe: Probe, k: int) -> Query:
        """The engine-facing query for `probe`; `kind`, `about` and `feedback` are left out."""
        return Query(
            text=probe.query,
            user=probe.user,
            team=self.team_of(probe.user),
            session=probe.session,
            repo=probe.repo,
            t=probe.t,
            k=k if probe.k is None else probe.k,
            meta=probe.meta,
        )


QueryKey = tuple[datetime, str, str, str, str, tuple[tuple[str, str], ...]]


def query_key(query: Query) -> QueryKey:
    """Identify a question by everything an engine sees of it except `k` and the asker's team.

    Two probes with the same key are the same question to an engine, so they must not carry
    different labels (`validate_scenario` rejects them). `meta` is sorted so key order does not
    matter.
    """
    return (query.t, query.user, query.session, query.repo, query.text, tuple(sorted(query.meta.items())))


def probe_key(probe: Probe) -> QueryKey:
    """The `query_key` of the query this probe produces (see `Scenario.query_for`)."""
    return (probe.t, probe.user, probe.session, probe.repo, probe.query, tuple(sorted(probe.meta.items())))

