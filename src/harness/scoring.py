"""Scoring: judge the agent's choice against ground truth and detect leaks."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from harness.agent import Choice
from harness.contract import Result, can_see
from harness.scenario.model import LabeledEvent, Probe, Scenario
from harness.world import probe_ctx, true_at

ProbeOutcome = Literal["FP", "TP", "FN", "BLOCKED", "PROMOTE_FP", "LEAK_ONLY"]
LeakReason = Literal["source", "secret_marker"]


def is_wrong(basis: frozenset[str], probe: Probe, scenario: Scenario) -> bool:
    """True if any fact in the basis is not true at the probe's time and context."""
    ctx = probe_ctx(probe)
    return any(not true_at(scenario.facts[f], probe.t, ctx) for f in basis)


def judge(probe: Probe, choice: Choice, scenario: Scenario) -> ProbeOutcome:
    if probe.kind == "leak":
        return "LEAK_ONLY"
    abstained = choice.index is None or not choice.basis  # nothing to act on
    wrong = not abstained and is_wrong(choice.basis, probe, scenario)
    if probe.kind == "block":
        return "FP" if wrong else "BLOCKED"
    if abstained:
        return "FN"
    return "PROMOTE_FP" if wrong else "TP"


@dataclass(frozen=True)
class Leak:
    result_index: int
    event_id: str
    reason: LeakReason


def find_leaks(
    results: Sequence[Result],
    probe: Probe,
    scenario: Scenario,
    events: Mapping[str, LabeledEvent],
) -> list[Leak]:
    """Leaks per result, ordered by result then event id (source before secret_marker)."""
    team = scenario.team_of(probe.user)
    hidden = {
        eid: le
        for eid, le in events.items()
        if not can_see(le.event, probe.user, team)
    }
    leaks = []
    for index, result in enumerate(results):
        text = result.text.casefold()
        source_ids = set(result.sources) & hidden.keys()
        marker_ids = {
            eid
            for eid, le in hidden.items()
            if le.event.t <= probe.t
            and any(m.casefold() in text for m in le.secret_markers)
        }
        for eid in sorted(source_ids | marker_ids):
            if eid in source_ids:
                leaks.append(Leak(index, eid, "source"))
            if eid in marker_ids:
                leaks.append(Leak(index, eid, "secret_marker"))
    return leaks
