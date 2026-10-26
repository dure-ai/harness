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
    """Judge the outcome of one probe.

    - block probe: acted on a wrong basis -> FP (counts toward M1); otherwise BLOCKED.
    - promote probe: right basis -> TP (M2), wrong basis -> PROMOTE_FP, abstained -> FN.
    - leak probe: LEAK_ONLY; only `find_leaks` matters.

    A basis is wrong if any of its facts is false at the probe's time and context.

    Args:
        probe: The probe.
        choice: What the agent followed.
        scenario: The scenario (facts).

    Returns:
        The outcome label.
    """
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
    """A result the asker should not have received.

    Attributes:
        result_index: Position of the result in the returned list.
        event_id: The invisible event it came from, or the event whose secret marker it contains.
        reason: "source" (a source the asker cannot see) or "secret_marker" (its text contains one).
    """

    result_index: int
    event_id: str
    reason: LeakReason


def find_leaks(
    results: Sequence[Result],
    probe: Probe,
    scenario: Scenario,
    events: Mapping[str, LabeledEvent],
) -> list[Leak]:
    """Find results the asker was not allowed to see.

    A result leaks if it cites an event the asker cannot see ("source"), or if its text contains
    a secret marker of such an event recorded by the probe time ("secret_marker"; this catches
    engines that rewrite text and drop sources). Every probe is checked, not only leak probes.
    Ids of events not recorded yet are ignored: the engine has not been given them, so citing
    them leaks nothing.

    Args:
        results: Search results.
        probe: The probe (asker and time).
        scenario: The scenario (teams).
        events: Event id to labeled event, recorded events only.

    Returns:
        Leaks ordered by result, then event id, with "source" before "secret_marker".
    """
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
