"""Shared machinery of the family templates: seeded dates, users, sessions, ids and provenance."""

import hashlib
import random
import re
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Literal

from harness.contract import Kind, Outcome, Scope, Signal
from harness.generate.candidates import FactCandidate
from harness.generate.slots import Skeleton
from harness.generate.traces import Trace
from harness.scenario.model import (
    FEEDBACK_FAMILIES,
    Fact,
    Family,
    Feedback,
    Probe,
    Provenance,
    Scenario,
    ScenarioEvent,
    SourceRepo,
    World,
)

Template = Callable[[FactCandidate, Sequence[Trace], int], Skeleton | None]
"""(candidate, traces of the same bundle with the candidate's first, seed) -> skeleton, or None
when the candidate does not fit the family."""

KST = timezone(timedelta(hours=9))
BASE = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
"""First possible event time; every event and probe falls between 09:00 and 17:45 of its day."""

MANUAL = "manual"
_SLUG_DROP = re.compile(r"[^a-z0-9-]+")
_SLOTS_PER_DAY = 36  # quarter hours from 09:00


def repo_name(repo: str) -> str:
    """Last path element of a repository, e.g. `widget` for `acme/widget`."""
    return repo.rstrip("/").rsplit("/", 1)[-1]


def repo_slug(repo: str) -> str:
    """Repository name for scenario ids: lowercased, only `[a-z0-9-]`, other runs become `-`."""
    return _SLUG_DROP.sub("-", repo_name(repo).lower()).strip("-") or MANUAL


def scenario_id(family: str, repo: str, trajectory_id: str, candidate_index: int, seed: int) -> str:
    """Scenario id: `<family>-<repo slug>-<first 6 hex of sha256(trajectory:index:family:seed)>`."""
    digest = hashlib.sha256(f"{trajectory_id}:{candidate_index}:{family}:{seed}".encode()).hexdigest()
    return f"{family}-{repo_slug(repo)}-{digest[:6]}"


class SkeletonBuilder:
    """Collects the labels of one skeleton and places them in time, deterministically per seed.

    Days count from `BASE` (2026-10-01). Times are random quarter hours between 09:00 and
    17:45, so a fact boundary at midnight of a day (`midnight`) always falls before that day's
    events. Users are `u1, u2, ...` in order of creation, sessions `sN, sN+1, ...` from a
    seeded start. Without traces the repository and trajectory id are `manual`.

    Args:
        family: Scenario family.
        cand: Candidate the scenario is built from.
        traces: Traces of the candidate's bundle, the candidate's own first; may be empty.
        seed: Seed of every random choice.
    """

    def __init__(self, family: Family, cand: FactCandidate, traces: Sequence[Trace], seed: int) -> None:
        self.family: Family = family
        self.cand = cand
        self.seed = seed
        self.rng = random.Random(seed)
        self.repo = traces[0].repo if traces else MANUAL
        self.trajectory_id = traces[0].trajectory_id if traces else MANUAL
        self._traces = list(traces)
        self._teams: dict[str, list[str]] = {}
        self._n_users = 0
        self._next_session = self.rng.randint(1, 20)
        self._day = -1
        self.facts: dict[str, Fact] = {}
        self.fabricated: list[str] = []
        self.events: list[ScenarioEvent] = []
        self.probes: list[Probe] = []

    # People ---------------------------------------------------------------------------------

    def users(self, n: int, team: str = "team-a") -> list[str]:
        """Create `n` new users on `team`, returned in a seeded random order."""
        made = []
        for _ in range(n):
            self._n_users += 1
            made.append(f"u{self._n_users}")
        self._teams.setdefault(team, []).extend(made)
        self.rng.shuffle(made)
        return made

    def session(self) -> str:
        """A new session id."""
        sid = f"s{self._next_session}"
        self._next_session += 1
        return sid

    # Time -----------------------------------------------------------------------------------

    def next_day(self, min_gap: int = 1, max_gap: int = 2) -> int:
        """Advance to a later day (the first call returns day 0) and return its number."""
        self._day = 0 if self._day < 0 else self._day + self.rng.randint(min_gap, max_gap)
        return self._day

    def at(self, day: int) -> datetime:
        """A random quarter hour between 09:00 and 17:45 of `day`."""
        return self.times_on(day, 1)[0]

    def times_on(self, day: int, n: int) -> list[datetime]:
        """`n` distinct random quarter hours of `day`, in order."""
        slots = sorted(self.rng.sample(range(_SLOTS_PER_DAY), n))
        return [BASE + timedelta(days=day, minutes=15 * s) for s in slots]

    @staticmethod
    def midnight(day: int) -> datetime:
        """00:00 of `day`, used as a fact's `valid_from` / `valid_until` boundary."""
        return BASE.replace(hour=0) + timedelta(days=day)

    # Labels ---------------------------------------------------------------------------------

    def fact(
        self,
        fid: str,
        *,
        fabricated: bool = False,
        valid: bool = True,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        conditions: dict[str, str] | None = None,
        fix: Literal["root", "workaround"] | None = None,
    ) -> str:
        """Add a fact with empty text; `fabricated` lists it in `provenance.fabricated_facts`."""
        self.facts[fid] = Fact(
            text="",
            valid=valid,
            valid_from=valid_from,
            valid_until=valid_until,
            conditions=dict(conditions or {}),
            fix=fix,
        )
        if fabricated:
            self.fabricated.append(fid)
        return fid

    def event(
        self,
        t: datetime,
        user: str,
        fact: str | None,
        *,
        session: str | None = None,
        signal: Signal = "self",
        kind: Kind = "agent_record",
        outcome: Outcome = "unknown",
        scope: Scope = "team",
        refs: Iterable[str] = (),
        repo: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> str:
        """Add an event with empty text and return its id (`e1, e2, ...` in order of creation).

        Pass `refs` only for judging records: a CI result or human confirmation about earlier
        events. Its `fact` is the fact the record asserts as true.
        """
        eid = f"e{len(self.events) + 1}"
        self.events.append(
            ScenarioEvent(
                id=eid,
                t=t,
                user=user,
                session=session or self.session(),
                repo=repo or self.repo,
                scope=scope,
                signal=signal,
                kind=kind,
                outcome=outcome,
                text="",
                meta=dict(meta or {}),
                refs=list(refs),
                fact=fact,
            )
        )
        return eid

    def events_of(self, fact: str) -> list[str]:
        """Ids of the events added so far that state `fact`."""
        return [e.id for e in self.events if e.fact == fact]

    def probe(
        self,
        pid: str,
        kind: Literal["block", "promote", "leak"],
        t: datetime,
        user: str,
        about: list[str],
        *,
        repo: str | None = None,
        meta: dict[str, str] | None = None,
        feedback: Feedback | None = None,
    ) -> str:
        """Add a probe with an empty query, asked in a new session.

        `feedback` (feedback families only) is copied, so one value can serve several probes.
        """
        self.probes.append(
            Probe(
                id=pid,
                kind=kind,
                t=t,
                user=user,
                session=self.session(),
                repo=repo or self.repo,
                query="",
                about=list(about),
                meta=dict(meta or {}),
                feedback=None if feedback is None else feedback.model_copy(deep=True),
            )
        )
        return pid

    # Output ---------------------------------------------------------------------------------

    def _provenance(self) -> Provenance:
        traces = list(dict.fromkeys(t.trajectory_id for t in self._traces))
        repos = list(dict.fromkeys((t.repo, t.license or "unknown") for t in self._traces))
        return Provenance(
            author="generator",
            source_traces=traces,
            source_repos=[SourceRepo(repo=r, license=lic) for r, lic in repos],
            fabricated_facts=list(self.fabricated),
        )

    def build(self, secret_event_ids: Iterable[str] = ()) -> Skeleton:
        """The skeleton: events and probes sorted by time (stable), teams in creation order."""
        scenario = Scenario(
            id=scenario_id(self.family, self.repo, self.trajectory_id, self.cand.index, self.seed),
            family=self.family,
            mode="feedback" if self.family in FEEDBACK_FAMILIES else "read_only",
            world=World(teams={team: list(members) for team, members in self._teams.items()}),
            facts=dict(self.facts),
            events=sorted(self.events, key=lambda e: e.t),
            probes=sorted(self.probes, key=lambda p: p.t),
            provenance=self._provenance(),
        )
        return Skeleton(scenario=scenario, secret_event_ids=tuple(secret_event_ids))


CONFIRMS: tuple[tuple[Signal, Kind], ...] = (
    ("test", "ci_result"),
    ("review", "human_confirm"),
    ("human", "human_confirm"),
)
"""(signal, kind) of the records that confirm earlier ones."""


def confirm(b: SkeletonBuilder, day: int, user: str, fact: str, judged: list[str]) -> str:
    """A judging record by `user` on `day` that confirms `judged` and asserts `fact`."""
    signal, kind = b.rng.choice(CONFIRMS)
    return b.event(b.at(day), user, fact, signal=signal, kind=kind, outcome="success", refs=judged)


def _needs_fix(cand: FactCandidate) -> str | None:
    if cand.fix is None:
        return "workaround_then_root needs a candidate whose fix is workaround or root"
    return None


_FIT_RULES: dict[str, Callable[[FactCandidate], str | None]] = {
    "workaround_then_root": _needs_fix,
}


def candidate_problem(family: str, cand: FactCandidate) -> str | None:
    """Why `cand` cannot seed a scenario of `family`, or None when it can.

    Families without a rule accept any candidate; `conditional` attaches a service condition
    to candidates that have none.
    """
    rule = _FIT_RULES.get(family)
    return None if rule is None else rule(cand)
