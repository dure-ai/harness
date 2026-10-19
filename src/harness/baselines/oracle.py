"""Oracle baseline: reads the hidden labels and returns the currently-true event for each fact."""

from harness.baselines.simple import _NamespacedEngine
from harness.contract import EngineInfo, Event, Query, Result, can_see
from harness.scenario.model import LabeledEvent, Scenario
from harness.world import probe_ctx, true_at


class OracleEngine(_NamespacedEngine):
    """Upper bound for the metrics; it is the only engine allowed to see scenario labels."""

    def __init__(self, scenario: Scenario) -> None:
        super().__init__()
        self._scenario = scenario
        self._labeled: dict[str, LabeledEvent] = {le.event.id: le for le in scenario.labeled_events()}
        self._probes = {}
        for p in scenario.probes:
            self._probes.setdefault((p.t, p.user, p.query), p)  # first in file order wins

    def info(self) -> EngineInfo:
        return EngineInfo(name="oracle", version="0.1", sources=True, scopes=True)

    def record(self, event: Event) -> None:
        if event.id in self._labeled:  # ignore harness-generated feedback events
            super().record(event)

    def search(self, query: Query) -> list[Result]:
        recorded = self._store()
        probe = self._probes.get((query.t, query.user, query.text))
        if probe is None:
            return []
        ctx = probe_ctx(probe)
        results: list[Result] = []
        for fact_id in probe.about:
            if not true_at(self._scenario.facts[fact_id], query.t, ctx):
                continue
            candidates = [
                e for e in recorded
                if self._labeled[e.id].fact == fact_id and can_see(e, query.user, query.team) and e.t <= query.t
            ]
            if candidates:
                best = max(enumerate(candidates), key=lambda ie: (ie[1].t, ie[0]))[1]
                results.append(Result(text=best.text, sources=[best.id]))
        return results[: query.k]
