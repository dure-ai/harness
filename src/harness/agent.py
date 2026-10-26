"""Rule-based agent: decides which search result the simulated agent follows.

The rule is deliberately simple and fixed: follow the first result that is relevant to the
probe, otherwise abstain. With no model in the loop, every score difference comes from what the
engine handed over. Engine-specific labels in `Result.meta` or the text are ignored.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from harness.contract import Result
from harness.scenario.model import LabeledEvent, Probe, Scenario


@dataclass(frozen=True)
class Choice:
    """What the agent followed.

    Attributes:
        index: Position of the followed result, or None when the agent abstained.
        basis: Hidden fact ids the agent acts on (empty when it abstained).
    """

    index: int | None
    basis: frozenset[str]


def _marker_hits(result: Result, scenario: Scenario, fact_ids) -> set[str]:
    # Facts whose markers appear in the text (case-insensitive). Used when an engine returns no
    # sources, and to narrow a result whose sources state several facts.
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
    """Return the facts the agent would act on when following `result` (design 7.2).

    1. With sources: the hidden facts of the known source events, those outside
       `probe.about` included (unknown ids, events without a fact and duplicates are ignored).
       If none of them is in `probe.about`, the result is not relevant: empty basis.
    2. If step 1 yields several facts, keep those whose markers appear in the text, when any
       do and at least one kept fact is in `probe.about`; otherwise keep all of step 1.
    3. Without sources: the `about` facts whose markers appear in the text.

    A result that is relevant through one `about` fact is judged on every fact it states, so a
    false fact outside `about` still makes the basis wrong, and markers of facts outside
    `about` cannot narrow the `about` facts away.

    Args:
        result: One search result.
        probe: The probe being answered (its hidden `about`).
        scenario: The scenario (fact markers).
        events: Event id to labeled event; only events already recorded to the engine, generated
            events included once they are recorded.

    Returns:
        Fact ids; empty means the result is not relevant to this probe.
    """
    about = set(probe.about)
    if not result.sources:
        return frozenset(_marker_hits(result, scenario, about))
    basis = {
        fact
        for s in set(result.sources)
        if s in events and (fact := events[s].fact) is not None
    }
    if not basis & about:
        return frozenset()
    if len(basis) >= 2:
        narrowed = _marker_hits(result, scenario, basis)
        if narrowed & about:
            return frozenset(narrowed)
    return frozenset(basis)


def choose(
    results: Sequence[Result],
    probe: Probe,
    scenario: Scenario,
    events: Mapping[str, LabeledEvent],
) -> Choice:
    """Follow the first result with a non-empty basis; abstain if there is none.

    Args:
        results: Search results, best first.
        probe: The probe being answered.
        scenario: The scenario.
        events: Event id to labeled event, recorded events only.

    Returns:
        The agent's choice.
    """
    for index, result in enumerate(results):
        basis = result_basis(result, probe, scenario, events)
        if basis:
            return Choice(index, basis)
    return Choice(None, frozenset())
