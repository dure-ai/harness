"""Oracle baseline: reads the hidden labels and returns the currently-true event for each fact."""

from harness.baselines.simple import _NamespacedEngine
from harness.contract import EngineInfo, Event, Query, Result, can_see
from harness.scenario.model import LabeledEvent, Probe, QueryKey, Scenario, probe_key, query_key
from harness.world import probe_ctx, true_at


def _content_key(e: Event) -> tuple:
    """Every field of `e` except its id and refs, which the replay renames."""
    return (e.t, e.user, e.session, e.repo, e.scope, e.team, e.signal, e.kind, e.outcome, e.text,
            tuple(sorted(e.meta.items())))


class OracleEngine(_NamespacedEngine):
    """Upper bound for the metrics; it is the only engine allowed to see scenario labels.

    For each `about` fact of the probe that is true at the query time, it hands over the most
    recent visible event stating that fact. Self-check expects M1 = 0 and M2 = 1 from it.

    Args:
        scenario: The scenario being replayed (its hidden labels).
    """

    def __init__(self, scenario: Scenario) -> None:
        super().__init__()
        self._scenario = scenario
        # Engines see opaque ids (`replay.engine_event_id`), so recorded events are matched to
        # their labels by content; the first of identical events wins.
        self._labeled: dict[tuple, LabeledEvent] = {}
        for le in scenario.labeled_events():
            self._labeled.setdefault(_content_key(le.event), le)
        self._facts: dict[str, str | None] = {}
        # Probes with the same key are rejected by validate_scenario, so the key is unique.
        self._probes: dict[QueryKey, Probe] = {probe_key(p): p for p in scenario.probes}

    def info(self) -> EngineInfo:
        """Return the oracle's engine info."""
        return EngineInfo(name="oracle", version="0.1", sources=True, scopes=True)

    def record(self, event: Event) -> None:
        """Store scenario events; harness-generated feedback events have no label and are ignored."""
        le = self._labeled.get(_content_key(event))
        if le is not None:  # ignore harness-generated feedback events
            self._facts[event.id] = le.fact
            super().record(event)

    def search(self, query: Query) -> list[Result]:
        """Hand over the currently true, visible event for each `about` fact of the matching probe."""
        recorded = self._store()
        probe = self._probes.get(query_key(query))
        if probe is None:
            return []
        ctx = probe_ctx(probe)
        results: list[Result] = []
        for fact_id in probe.about:
            if not true_at(self._scenario.facts[fact_id], query.t, ctx):
                continue
            candidates = [
                e for e in recorded
                if self._facts[e.id] == fact_id and can_see(e, query.user, query.team) and e.t <= query.t
            ]
            if candidates:
                best = max(enumerate(candidates), key=lambda ie: (ie[1].t, ie[0]))[1]
                results.append(Result(text=best.text, sources=[best.id]))
        return results[: query.k]
