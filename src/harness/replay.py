"""Read-only replay: feed a scenario to an engine in virtual-time order and score each probe."""

import heapq
import time
from dataclasses import dataclass, field
from itertools import count

from harness.agent import Choice, choose, result_basis
from harness.config import EvalConfig
from harness.contract import BaseEngine, Result
from harness.scenario.model import LabeledEvent, Probe, Scenario
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
        except Exception as e:
            run.error = f"{type(e).__name__}: {e}"
            return run
        results = raw[: item.k or cfg.k]
        run.probes.append(_score_probe(item, results, scenario, events, search_ms))
    return run
