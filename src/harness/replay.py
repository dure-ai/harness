"""Read-only replay: feed a scenario to an engine in virtual-time order and score each probe."""

import hashlib
import heapq
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from itertools import count

from harness.agent import Choice, choose, result_basis
from harness.config import EvalConfig
from harness.contract import BaseEngine, Event, Result
from harness.scenario.model import FeedbackTemplate, LabeledEvent, Probe, Scenario
from harness.scoring import Leak, ProbeOutcome, find_leaks, is_wrong, judge

EVENT, PROBE = 0, 1  # tie-break order: events at the same time come before probes


@dataclass
class ProbeRecord:
    probe_id: str
    kind: str
    results: list[Result]
    choice: Choice
    outcome: ProbeOutcome
    leaks: list[Leak]
    bases: list[frozenset[str]]
    contaminated: bool
    reached: bool
    search_ms: float
    context_chars: int


@dataclass
class ScenarioRun:
    scenario_id: str
    family: str
    mode: str
    probes: list[ProbeRecord] = field(default_factory=list)
    record_ms: list[float] = field(default_factory=list)
    generated: list[LabeledEvent] = field(default_factory=list)
    error: str | None = None
    # (probe id, "action" | "outcome") -> generated event id; harness-side only, never sent to engines
    generated_ids: dict[tuple[str, str], str] = field(default_factory=dict)


class Timeline:
    """Priority queue keyed by (t, order, seq); seq keeps insertion order and avoids comparing items."""

    def __init__(self) -> None:
        self._heap: list[tuple] = []
        self._seq = count()

    def push(self, t, order: int, item: LabeledEvent | Probe) -> None:
        heapq.heappush(self._heap, (t, order, next(self._seq), item))

    def pop(self) -> tuple:
        t, order, _, item = heapq.heappop(self._heap)
        return t, order, item

    def __bool__(self) -> bool:
        return bool(self._heap)


def push_event(timeline: Timeline, labeled: LabeledEvent) -> None:
    timeline.push(labeled.event.t, EVENT, labeled)


def push_probe(timeline: Timeline, probe: Probe) -> None:
    timeline.push(probe.t, PROBE, probe)


def _score_probe(
    probe: Probe,
    results: list[Result],
    scenario: Scenario,
    events: dict[str, LabeledEvent],
    search_ms: float,
) -> ProbeRecord:
    choice = choose(results, probe, scenario, events)
    bases = [result_basis(r, probe, scenario, events) for r in results]
    wrong = [bool(b) and is_wrong(b, probe, scenario) for b in bases]
    return ProbeRecord(
        probe_id=probe.id,
        kind=probe.kind,
        results=results,
        choice=choice,
        outcome=judge(probe, choice, scenario),
        leaks=find_leaks(results, probe, scenario, events),
        bases=bases,
        contaminated=any(wrong),
        reached=any(bool(b) and not w for b, w in zip(bases, wrong)),
        search_ms=search_ms,
        context_chars=sum(len(r.text) for r in results),
    )


def generated_event_id(scenario: Scenario, probe: Probe, slot: str, taken: Collection[str]) -> str:
    """Opaque id for a generated event: it names neither the probe nor the harness. A clash with
    an id in `taken` gets "-2", "-3", ... appended."""
    base = "e" + hashlib.sha256(f"{scenario.id}:{probe.id}:{slot}".encode()).hexdigest()[:12]
    event_id, n = base, 1
    while event_id in taken:
        n += 1
        event_id = f"{base}-{n}"
    return event_id


def _generated(
    probe: Probe, tmpl: FeedbackTemplate, event_id: str, default_delay, scenario: Scenario
) -> LabeledEvent:
    """Build the agent-side record for `probe`; no marker says it followed a search result."""
    event = Event(
        id=event_id,
        t=probe.t + (tmpl.delay if tmpl.delay is not None else default_delay),
        user=probe.user,
        session=probe.session,
        repo=probe.repo,
        scope=tmpl.scope,
        team=scenario.team_of(probe.user),
        signal=tmpl.signal,
        kind=tmpl.kind,
        outcome=tmpl.outcome,
        text=tmpl.text,
        meta={},
    )
    return LabeledEvent(event=event, fact=tmpl.fact, secret_markers=())


def feedback_events(
    probe: Probe, choice: Choice, scenario: Scenario, cfg: EvalConfig, taken: Collection[str] = ()
) -> list[tuple[str, LabeledEvent]]:
    """(slot, event) for the action record and outcome record the simulated agent writes after
    acting on a probe; event ids avoid every id in `taken`."""
    used = set(taken)

    def make(tmpl: FeedbackTemplate, slot: str, delay) -> tuple[str, LabeledEvent]:
        event_id = generated_event_id(scenario, probe, slot, used)
        used.add(event_id)
        return slot, _generated(probe, tmpl, event_id, delay, scenario)

    feedback = probe.feedback
    if feedback is None:
        return []
    abstained = choice.index is None
    if abstained:
        action_tmpl = feedback.abstain
    else:
        wrong = sorted(f for f in choice.basis if is_wrong(frozenset({f}), probe, scenario))
        pick = wrong[0] if wrong else min(choice.basis)
        action_tmpl = feedback.action.get(pick)
    generated = []
    if action_tmpl is not None:
        generated.append(make(action_tmpl, "action", cfg.feedback.action))
    if not abstained:
        if is_wrong(choice.basis, probe, scenario):
            tmpl, delay = feedback.on_false, cfg.feedback.on_false
        else:
            tmpl, delay = feedback.on_true, cfg.feedback.on_true
        if tmpl is not None:
            generated.append(make(tmpl, "outcome", delay))
    return generated


def _as_result(item: object) -> Result:
    if isinstance(item, Result):
        return item
    if isinstance(item, dict):
        return Result.model_validate(item)
    raise TypeError(f"search returned {type(item).__name__}, expected Result or dict")


def run_scenario(
    engine: BaseEngine, scenario: Scenario, *, run_id: str, cfg: EvalConfig
) -> ScenarioRun:
    run = ScenarioRun(scenario.id, scenario.family, scenario.mode)
    labeled = scenario.labeled_events()
    events: dict[str, LabeledEvent] = {le.event.id: le for le in labeled}
    timeline = Timeline()
    for le in labeled:
        push_event(timeline, le)
    for probe in scenario.probes:
        push_probe(timeline, probe)

    try:
        virtual_clock = engine.info().virtual_clock
        engine.reset(f"{run_id}:{scenario.id}")
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}"
        return run

    while timeline:
        t, order, item = timeline.pop()
        query = scenario.query_for(item, cfg.k) if order == PROBE else None  # harness code: not wrapped
        try:
            if order == EVENT:
                if virtual_clock:
                    engine.tick(t)
                start = time.perf_counter()
                engine.record(item.event)
                run.record_ms.append((time.perf_counter() - start) * 1000)
                continue
            if virtual_clock:
                engine.tick(t)
            engine.flush()
            start = time.perf_counter()
            raw = list(engine.search(query))
            search_ms = (time.perf_counter() - start) * 1000
            raw = [_as_result(r) for r in raw]  # a malformed return errors this scenario only
        except Exception as e:
            run.error = f"{type(e).__name__}: {e}"
            return run
        results = raw[: item.k or cfg.k]
        record = _score_probe(item, results, scenario, events, search_ms)
        run.probes.append(record)
        if scenario.mode == "feedback" and item.feedback is not None:
            for slot, generated in feedback_events(item, record.choice, scenario, cfg, events.keys()):
                events[generated.event.id] = generated
                run.generated.append(generated)
                run.generated_ids[(item.id, slot)] = generated.event.id
                push_event(timeline, generated)
    return run
