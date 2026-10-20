"""Metrics: turn replay records into M1/M2/M3 and the auxiliary and observation numbers."""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict

from harness.replay import ProbeRecord, ScenarioRun
from harness.scenario.model import Probe, Scenario
from harness.stats import Delay, Rate, rate
from harness.world import probe_ctx, true_at

JUDGED = ("block", "promote")


class FamilyRow(TypedDict):
    """Per-family M1/M2/M3; a plain dict at runtime so summary.json keeps the same shape."""

    m1: Rate
    m2: Rate
    m3: int


@dataclass
class Metrics:
    """Everything measured for one engine over one run of the scenario set.

    The headline metrics and their breakdowns use read_only scenarios only; feedback scenarios
    are reported separately in `feedback_by_family`. Errored scenarios are left out of the
    rates but listed in `errors`, and their leaks still count toward M3.

    Attributes:
        m1: Block FP rate, the share of block probes where the agent acted on a false fact.
        m2: Promote TP rate, the share of promote probes where it acted on a true one.
        m3: Number of leaked results (must be 0).
        promote_fn: Share of promote probes where it acted on nothing.
        by_family: M1/M2/M3 per scenario family (read_only).
        feedback_by_family: The same for feedback scenarios.
        by_author: M1/M2 per scenario provenance author (human, llm, generator).
        shallow_fix: Of probes where a root-cause fix is true, the share that followed a workaround.
        reach: Share of promote probes with at least one result whose basis is all true.
        contamination: Share of block/promote probes with at least one result carrying a false fact.
        sourceless: Share of all results that came back without `sources`.
        retraction: Per fact with `valid_until`, how long it kept showing up in the basis.
        reflection: Per fact with `valid_from`, how long until it first showed up.
        worst_family: (family, M1) of the family with the highest M1, or None.
        errors: Ids of scenarios that errored.
        context_chars_mean: Mean characters handed over per probe.
        record_ms_p50: Median `record` latency.
        record_ms_p95: 95th-percentile `record` latency.
        search_ms_p50: Median `search` latency.
        search_ms_p95: 95th-percentile `search` latency.
    """

    m1: Rate
    m2: Rate
    m3: int
    promote_fn: Rate
    by_family: dict[str, FamilyRow]
    feedback_by_family: dict[str, FamilyRow]
    by_author: dict[str, dict[str, Rate]]
    shallow_fix: Rate
    reach: Rate
    contamination: Rate
    sourceless: Rate
    retraction: list[Delay]
    reflection: list[Delay]
    worst_family: tuple[str, float] | None
    errors: list[str]
    context_chars_mean: float | None
    record_ms_p50: float | None
    record_ms_p95: float | None
    search_ms_p50: float | None
    search_ms_p95: float | None


def _count(runs: Iterable[ScenarioRun], kind: str, hit: str) -> tuple[int, int]:
    """(probes of `kind` whose outcome is `hit`, probes of `kind`)."""
    k = n = 0
    for run in runs:
        for rec in run.probes:
            if rec.kind == kind:
                n += 1
                k += rec.outcome == hit
    return k, n


def _m1(runs: Iterable[ScenarioRun]) -> Rate:
    return rate(*_count(runs, "block", "FP"))


def _m2(runs: Iterable[ScenarioRun]) -> Rate:
    return rate(*_count(runs, "promote", "TP"))


def _leaking_results(rec: ProbeRecord) -> int:
    return len({leak.result_index for leak in rec.leaks})


def _m3(runs: Iterable[ScenarioRun]) -> int:
    return sum(_leaking_results(rec) for run in runs for rec in run.probes)


def _family_table(runs: list[ScenarioRun]) -> dict[str, FamilyRow]:
    grouped: dict[str, list[ScenarioRun]] = defaultdict(list)
    for run in runs:
        grouped[run.family].append(run)
    return {
        family: FamilyRow(m1=_m1(group), m2=_m2(group), m3=_m3(group))
        for family, group in sorted(grouped.items())
    }


def _author_table(
    runs: list[ScenarioRun], scenarios: Mapping[str, Scenario]
) -> dict[str, dict[str, Rate]]:
    grouped: dict[str, list[ScenarioRun]] = defaultdict(list)
    for run in runs:
        grouped[scenarios[run.scenario_id].provenance.author].append(run)
    return {a: {"m1": _m1(g), "m2": _m2(g)} for a, g in sorted(grouped.items())}


def _judged(run: ScenarioRun, scenario: Scenario) -> Iterable[tuple[Probe, ProbeRecord]]:
    """(scenario probe, record) for block/promote records, in record order."""
    probes = {p.id: p for p in scenario.probes}
    for rec in run.probes:
        if rec.kind in JUDGED and rec.probe_id in probes:
            yield probes[rec.probe_id], rec


def _shallow_fix(runs: list[ScenarioRun], scenarios: Mapping[str, Scenario]) -> Rate:
    k = n = 0
    for run in runs:
        scenario = scenarios[run.scenario_id]
        for probe, rec in _judged(run, scenario):
            ctx = probe_ctx(probe)
            has_root = any(
                scenario.facts[f].fix == "root" and true_at(scenario.facts[f], probe.t, ctx)
                for f in probe.about
            )
            if not has_root:
                continue
            n += 1
            k += any(scenario.facts[f].fix == "workaround" for f in rec.choice.basis)
    return rate(k, n)


def _delays(
    runs: list[ScenarioRun], scenarios: Mapping[str, Scenario], *, retraction: bool
) -> list[Delay]:
    """Measure how fast facts leave or enter the basis.

    Retraction is the first probe at or after `valid_until` with the fact in no basis.
    Reflection is the first probe at or after `valid_from` with the fact in some basis.
    `seconds` is None if that never happens.
    """
    delays = []
    for run in sorted(runs, key=lambda r: r.scenario_id):
        scenario = scenarios[run.scenario_id]
        judged = list(_judged(run, scenario))
        for fact_id in sorted(scenario.facts):
            fact = scenario.facts[fact_id]
            edge = fact.valid_until if retraction else fact.valid_from
            if edge is None:
                continue
            later = sorted(
                ((p, r) for p, r in judged if p.t >= edge and fact_id in p.about),
                key=lambda pr: pr[0].t,
            )
            if not later:
                continue
            seconds = None
            for probe, rec in later:
                present = any(fact_id in basis for basis in rec.bases)
                if present != retraction:
                    seconds = (probe.t - edge).total_seconds()
                    break
            delays.append(Delay(fact_id, run.scenario_id, seconds))
    return delays


def _percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(p / 100 * len(ordered)) - 1]


def compute_metrics(runs: list[ScenarioRun], scenarios: Mapping[str, Scenario]) -> Metrics:
    """Aggregate one engine's scenario runs into `Metrics`.

    Args:
        runs: One run per scenario, errored ones included.
        scenarios: Scenario id to scenario, for the hidden labels.

    Returns:
        The engine's metrics.
    """
    ok = [r for r in runs if r.error is None]
    errors = sorted(r.scenario_id for r in runs if r.error is not None)
    read_only = [r for r in ok if r.mode == "read_only"]
    feedback = [r for r in ok if r.mode == "feedback"]
    by_family = _family_table(read_only)
    m1_values = {f: v for f, t in by_family.items() if (v := t["m1"].value) is not None}
    worst = None
    if m1_values:
        family = min(m1_values, key=lambda f: (-m1_values[f], f))
        worst = (family, m1_values[family])

    ro_records = [rec for r in read_only for rec in r.probes]
    all_records = [rec for r in ok for rec in r.probes]
    all_results = [res for rec in all_records for res in rec.results]
    return Metrics(
        m1=_m1(read_only),
        m2=_m2(read_only),
        m3=_m3(runs),  # leaks recorded before an error still count
        promote_fn=rate(*_count(read_only, "promote", "FN")),
        by_family=by_family,
        feedback_by_family=_family_table(feedback),
        by_author=_author_table(read_only, scenarios),
        shallow_fix=_shallow_fix(read_only, scenarios),
        reach=rate(
            sum(rec.reached for rec in ro_records if rec.kind == "promote"),
            sum(rec.kind == "promote" for rec in ro_records),
        ),
        contamination=rate(
            sum(rec.contaminated for rec in ro_records if rec.kind in JUDGED),
            sum(rec.kind in JUDGED for rec in ro_records),
        ),
        sourceless=rate(sum(not res.sources for res in all_results), len(all_results)),
        retraction=_delays(read_only, scenarios, retraction=True),
        reflection=_delays(read_only, scenarios, retraction=False),
        worst_family=worst,
        errors=errors,
        context_chars_mean=(
            sum(rec.context_chars for rec in all_records) / len(all_records)
            if all_records
            else None
        ),
        record_ms_p50=_percentile([ms for r in ok for ms in r.record_ms], 50),
        record_ms_p95=_percentile([ms for r in ok for ms in r.record_ms], 95),
        search_ms_p50=_percentile([rec.search_ms for rec in all_records], 50),
        search_ms_p95=_percentile([rec.search_ms for rec in all_records], 95),
    )


def per_scenario_counts(
    runs: list[ScenarioRun], kind: Literal["block", "promote"], hit: Literal["FP", "TP"]
) -> list[tuple[int, int]]:
    """(hits, probes of `kind`) per read-only scenario, ordered by scenario id."""
    chosen = sorted(
        (r for r in runs if r.error is None and r.mode == "read_only"),
        key=lambda r: r.scenario_id,
    )
    return [_count([r], kind, hit) for r in chosen]


def flip_rate(repeats: list[list[ScenarioRun]]) -> Rate:
    """Share of (scenario, probe) pairs whose results differ between repeats."""
    if not repeats:
        return rate(0, 0)
    tables = []
    for repeat in repeats:
        tables.append(
            {
                (run.scenario_id, rec.probe_id): [(res.text, tuple(res.sources)) for res in rec.results]
                for run in repeat
                if run.error is None
                for rec in run.probes
            }
        )
    aligned = set(tables[0]).intersection(*tables[1:])
    flipped = sum(any(t[key] != tables[0][key] for t in tables[1:]) for key in aligned)
    return rate(flipped, len(aligned))
