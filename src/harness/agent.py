"""Rule-based agent: decides which search result the simulated agent follows."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from harness.contract import Result
from harness.scenario.model import LabeledEvent, Probe, Scenario


@dataclass(frozen=True)
class Choice:
    index: int | None
    basis: frozenset[str]


def _marker_hits(result: Result, scenario: Scenario, fact_ids) -> set[str]:
    text = result.text.casefold()
    return {
        f
        for f in fact_ids
        if any(m.casefold() in text for m in scenario.facts[f].markers)
    }


def result_basis(
    result: Result,
    probe: Probe,
    scenario: Scenario,
    events: Mapping[str, LabeledEvent],
) -> frozenset[str]:
    """The set of probe-relevant facts the agent would act on when following `result`."""
    about = set(probe.about)
    if not result.sources:
        return frozenset(_marker_hits(result, scenario, about))
    basis = {
        fact
        for s in set(result.sources)
        if s in events and (fact := events[s].fact) is not None and fact in about
    }
    if len(basis) >= 2:
        narrowed = _marker_hits(result, scenario, basis)
        if narrowed:
            return frozenset(narrowed)
    return frozenset(basis)


def choose(
    results: Sequence[Result],
    probe: Probe,
    scenario: Scenario,
    events: Mapping[str, LabeledEvent],
) -> Choice:
    """Follow the first result with a non-empty basis; abstain if there is none."""
    for index, result in enumerate(results):
        basis = result_basis(result, probe, scenario, events)
        if basis:
            return Choice(index, basis)
    return Choice(None, frozenset())
