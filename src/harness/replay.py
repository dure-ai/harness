"""Read-only replay: feed a scenario to an engine in virtual-time order and score each probe."""

import hashlib
import heapq
import hmac
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count

from harness.agent import Choice, choose, result_basis
from harness.config import EvalConfig
from harness.contract import BaseEngine, Event, Result
from harness.scenario.model import FeedbackTemplate, LabeledEvent, Probe, Scenario
from harness.scoring import Leak, ProbeOutcome, find_leaks, is_wrong, judge

EVENT, PROBE = 0, 1  # tie-break order: events at the same time come before probes


def blind_key(seed: int) -> bytes:
    """Return the run's secret key for namespaces, derived from its blind seed.

    The same seed also shuffles candidate ids (`report.blind_ids`), so one recorded seed
    (`engine_ids.json`) reproduces both.

    Args:
        seed: The run's blind seed.

    Returns:
        SHA-256 digest of "t5-blind:{seed}".
    """
    return hashlib.sha256(f"t5-blind:{seed}".encode()).digest()


DEFAULT_BLIND_KEY = hashlib.sha256(b"t5-blind:default").digest()
"""Fixed key for tests and internal checks; `harness run` passes a key from its blind seed."""


def namespace(run_id: str, scenario_id: str, key: bytes) -> str:
    """Return the namespace passed to `reset`: the run id and a keyed hash of the scenario id.

    Without the key, a guessed scenario id cannot be checked against the namespace.

    Args:
        run_id: Run identifier.
        scenario_id: The scenario.
        key: The run's blind key, see `blind_key`.

    Returns:
        "{run_id}:{first 16 hex digits of HMAC-SHA256(key, 'ns:' + scenario_id)}".
    """
    digest = hmac.new(key, f"ns:{scenario_id}".encode(), hashlib.sha256).hexdigest()
    return f"{run_id}:{digest[:16]}"


@dataclass
class ProbeRecord:
    """What happened at one probe.

    Attributes:
        probe_id: The probe.
        kind: "block", "promote" or "leak".
        results: The first k results the engine returned.
        choice: Which result the agent followed and the facts it acted on.
        outcome: The judged outcome, see `scoring.judge`.
        leaks: Results the asker was not allowed to see.
        bases: Facts each result is judged to state, in result order.
        contaminated: Some result states a false fact.
        reached: Some result states only true facts.
        search_ms: `search` latency.
        context_chars: Total text length handed over.
    """

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
    """One engine's replay of one scenario.

    Attributes:
        scenario_id: The scenario.
        family: Its family.
        mode: "read_only" or "feedback".
        probes: Probe records in time order.
        record_ms: Latency of every `record` call.
        generated: Events the harness generated in feedback mode.
        error: "ExceptionType: message" if the engine failed; the run stops there.
        generated_ids: (probe id, "action" | "outcome") to generated event id; harness-side
            only, never sent to engines.
    """

    scenario_id: str
    family: str
    mode: str
    probes: list[ProbeRecord] = field(default_factory=list)
    record_ms: list[float] = field(default_factory=list)
    generated: list[LabeledEvent] = field(default_factory=list)
    error: str | None = None
    generated_ids: dict[tuple[str, str], str] = field(default_factory=dict)


class Timeline:
    """Priority queue keyed by (t, order, seq); seq keeps insertion order and avoids comparing items."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, int, LabeledEvent | Probe]] = []
        self._seq = count()

    def push(self, t: datetime, order: int, item: LabeledEvent | Probe) -> None:
        """Add `item` at time `t`; `order` is EVENT or PROBE."""
        heapq.heappush(self._heap, (t, order, next(self._seq), item))

    def pop(self) -> tuple[datetime, LabeledEvent | Probe]:
        """Remove and return the earliest (time, item)."""
        t, _, _, item = heapq.heappop(self._heap)
        return t, item

    def __bool__(self) -> bool:
        return bool(self._heap)


def push_event(timeline: Timeline, labeled: LabeledEvent) -> None:
    """Schedule an event at its own time."""
    timeline.push(labeled.event.t, EVENT, labeled)


def push_probe(timeline: Timeline, probe: Probe) -> None:
    """Schedule a probe; it runs after events with the same time."""
    timeline.push(probe.t, PROBE, probe)


def _score_probe(
    probe: Probe,
    results: list[Result],
    scenario: Scenario,
    recorded: dict[str, LabeledEvent],
    search_ms: float,
) -> ProbeRecord:
    # Only events already passed to `record` count as evidence (design 7.2).
    choice = choose(results, probe, scenario, recorded)
    bases = [result_basis(r, probe, scenario, recorded) for r in results]
    wrong = [bool(b) and is_wrong(b, probe, scenario) for b in bases]
    return ProbeRecord(
        probe_id=probe.id,
        kind=probe.kind,
        results=results,
        choice=choice,
        outcome=judge(probe, choice, scenario),
        leaks=find_leaks(results, probe, scenario, recorded),
        bases=bases,
        contaminated=any(wrong),
        reached=any(bool(b) and not w for b, w in zip(bases, wrong)),
        search_ms=search_ms,
        context_chars=sum(len(r.text) for r in results),
    )


def generated_event_id(taken: Collection[str]) -> str:
    """Return the id of the next generated event: "e{n}", like the usual scenario event ids.

    Numbering starts after the ids in use (the scenario's events plus the events generated so
    far) and skips any id already taken, so author ids such as "e5" or "tc_o1" never clash. The
    id is not computed from the scenario, probe or slot, and its format matches scenario ids
    such as "e1", so an engine cannot tell generated events apart by their id. The mapping to
    probes lives only in `ScenarioRun.generated_ids`. An engine that abstained earlier gets fewer
    generated records, so the same probe's record can get a different id per engine; scoring
    goes through `generated_ids` and is not affected.

    Args:
        taken: Every id in use: the scenario's events and the events generated so far.

    Returns:
        "e{n}" for the smallest n > len(taken) whose id is not in `taken`.
    """
    n = len(taken) + 1
    while f"e{n}" in taken:
        n += 1
    return f"e{n}"


def _generated(
    probe: Probe,
    tmpl: FeedbackTemplate,
    event_id: str,
    default_delay,
    scenario: Scenario,
    refs: Sequence[str] = (),
    not_before: datetime | None = None,
) -> LabeledEvent:
    """Build the agent-side record for `probe`; no marker says it followed a search result.

    The record is written `probe.t` plus the template's `delay` (or `default_delay`), but never
    earlier than `not_before`.
    """
    t = probe.t + (tmpl.delay if tmpl.delay is not None else default_delay)
    if not_before is not None:
        t = max(t, not_before)
    event = Event(
        id=event_id,
        t=t,
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
        refs=tuple(refs),
    )
    return LabeledEvent(event=event, fact=tmpl.fact, secret_markers=())


def feedback_events(
    probe: Probe,
    choice: Choice,
    scenario: Scenario,
    cfg: EvalConfig,
    taken: Collection[str] = (),
    judged: Sequence[str] = (),
) -> list[tuple[str, LabeledEvent]]:
    """Build the records the simulated agent writes after acting on a probe.

    Args:
        probe: The probe that was just answered.
        choice: What the agent followed.
        scenario: The scenario being replayed.
        cfg: Evaluation config (default delays).
        taken: Ids already in use (scenario events and earlier generated events); generated ids
            are numbered after them, see `generated_event_id`.
        judged: Sources of the followed result; the outcome record refs them.

    Returns:
        (slot, event) pairs: the action record, then the outcome record when the agent acted.
        The outcome record is never earlier than the action record (it is clamped to the action
        record's time); with no action record it is `probe.t` plus its delay.
    """
    used = set(taken)

    def make(
        tmpl: FeedbackTemplate,
        slot: str,
        delay,
        refs: Sequence[str] = (),
        not_before: datetime | None = None,
    ) -> tuple[str, LabeledEvent]:
        event_id = generated_event_id(used)
        used.add(event_id)
        return slot, _generated(probe, tmpl, event_id, delay, scenario, refs, not_before)

    feedback = probe.feedback
    if feedback is None:
        return []
    abstained = choice.index is None or not choice.basis  # same rule as scoring.judge
    if abstained:
        action_tmpl = feedback.abstain
    else:
        wrong = sorted(
            f for f in choice.basis if is_wrong(frozenset({f}), probe, scenario)
        )
        pick = wrong[0] if wrong else min(choice.basis)
        action_tmpl = feedback.action.get(pick)
    generated = []
    action_t = None
    if action_tmpl is not None:
        generated.append(make(action_tmpl, "action", cfg.feedback.action))
        action_t = generated[0][1].event.t
    if not abstained:
        if is_wrong(choice.basis, probe, scenario):
            tmpl, delay = feedback.on_false, cfg.feedback.on_false
        else:
            tmpl, delay = feedback.on_true, cfg.feedback.on_true
        if tmpl is not None:
            # The outcome never precedes the action record; equal times keep insertion order.
            generated.append(make(tmpl, "outcome", delay, judged, action_t))
    return generated


def _as_result(item: object) -> Result:
    if isinstance(item, Result):
        return item.model_copy(deep=True)  # the engine may keep and change what it returned
    if isinstance(item, dict):
        return Result.model_validate(item)
    raise TypeError(f"search returned {type(item).__name__}, expected Result or dict")


def run_scenario(
    engine: BaseEngine,
    scenario: Scenario,
    *,
    run_id: str,
    cfg: EvalConfig,
    blind_key: bytes = DEFAULT_BLIND_KEY,
) -> ScenarioRun:
    """Replay one scenario against a fresh engine in virtual-time order.

    Events and probes are merged on one timeline (events first at equal times). Each event is
    passed to `record`; each probe calls `flush` then `search`, and the agent's choice is scored.
    In feedback mode the agent's records are scheduled back onto the timeline. `tick` is called
    before each step when the engine asks for a virtual clock. A result's sources count as
    evidence, and in the leak check, only for events whose `record` call has returned by the
    probe; generated events included once they come off the timeline.

    Errors raised by the engine end this scenario only and are kept in `ScenarioRun.error`;
    errors in harness code are not caught.

    Args:
        engine: A new engine instance; `reset` is called with `namespace(run_id, scenario.id,
            blind_key)`, which does not reveal the scenario id.
        scenario: The scenario to replay.
        run_id: Run identifier used in the namespace.
        cfg: Evaluation config.
        blind_key: The run's secret key, see `blind_key`; `harness run` derives it from the
            run's blind seed. The fixed default is for tests and internal checks.

    Returns:
        The scenario run, partial if the engine failed.
    """
    run = ScenarioRun(scenario.id, scenario.family, scenario.mode)
    labeled = scenario.labeled_events()
    # Evidence: events whose `record` call returned. Ids in use: generated ids are numbered after them.
    recorded: dict[str, LabeledEvent] = {}
    taken: set[str] = {le.event.id for le in labeled}
    timeline = Timeline()
    for le in labeled:
        push_event(timeline, le)
    for probe in scenario.probes:
        push_probe(timeline, probe)

    try:
        virtual_clock = engine.info().virtual_clock
        engine.reset(namespace(run_id, scenario.id, blind_key))
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}"
        return run

    while timeline:
        t, item = timeline.pop()
        if isinstance(item, LabeledEvent):
            try:
                if virtual_clock:
                    engine.tick(t)
                start = time.perf_counter()
                engine.record(item.event)
                run.record_ms.append((time.perf_counter() - start) * 1000)
            except Exception as e:
                run.error = f"{type(e).__name__}: {e}"
                return run
            recorded[item.event.id] = item
            continue
        query = scenario.query_for(
            item, cfg.k
        )  # harness code: not wrapped, so harness bugs surface
        try:
            if virtual_clock:
                engine.tick(t)
            engine.flush()
            start = time.perf_counter()
            raw = list(engine.search(query))
            search_ms = (time.perf_counter() - start) * 1000
            raw = [
                _as_result(r) for r in raw
            ]  # a malformed return errors this scenario only
        except Exception as e:
            run.error = f"{type(e).__name__}: {e}"
            return run
        results = raw[: cfg.k if item.k is None else item.k]
        record = _score_probe(item, results, scenario, recorded, search_ms)
        run.probes.append(record)
        if scenario.mode == "feedback" and item.feedback is not None:
            index = record.choice.index
            judged = results[index].sources if index is not None else ()
            for slot, generated in feedback_events(
                item, record.choice, scenario, cfg, taken, judged
            ):
                taken.add(generated.event.id)  # evidence only once it is recorded
                run.generated.append(generated)
                run.generated_ids[(item.id, slot)] = generated.event.id
                push_event(timeline, generated)
    return run
