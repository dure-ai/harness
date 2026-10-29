"""Harness self-check (do the four baselines order as designed?) and per-scenario discrimination check."""

from collections.abc import Mapping
from dataclasses import dataclass

from harness.config import EvalConfig
from harness.engines import BASELINES, make_engine
from harness.metrics import FamilyRow, Metrics, compute_metrics
from harness.replay import DEFAULT_BLIND_KEY, ScenarioRun, run_scenario
from harness.scenario.model import Scenario
from harness.scenario.validate import Issue
from harness.stats import Rate
from harness.world import probe_ctx, true_at

RUN_ID = "selfcheck"


@dataclass
class CheckResult:
    """One self-check.

    Attributes:
        name: Check name, such as "oracle_perfect".
        ok: Whether it passed.
        detail: The measured values (hidden in sealed mode).
    """

    name: str
    ok: bool
    detail: str


def _fmt(r: Rate) -> str:
    return "n/a (no probes)" if r.value is None else f"{r.value:.3f} ({r.k}/{r.n})"


def _is(r: Rate, expected: float) -> bool:
    """A rate with no probes (value None) cannot contradict `expected`."""
    return r.value is None or r.value == expected


def _run_all(
    engine_name: str, scenarios: list[Scenario], cfg: EvalConfig, blind_key: bytes
) -> list[ScenarioRun]:
    return [
        run_scenario(make_engine(engine_name, s, cfg), s, run_id=RUN_ID, cfg=cfg, blind_key=blind_key)
        for s in scenarios
    ]


def _no_errors(runs: dict[str, list[ScenarioRun]]) -> CheckResult:
    bad = [f"{name}:{r.scenario_id}" for name, rs in runs.items() for r in rs if r.error is not None]
    detail = "errors in " + ", ".join(bad) if bad else "no baseline run errored"
    return CheckResult("no_errors", not bad, detail)


def _oracle_perfect(m: Metrics) -> CheckResult:
    ok = _is(m.m1, 0) and _is(m.m2, 1) and m.m3 == 0
    detail = f"oracle m1={_fmt(m.m1)} (want 0), m2={_fmt(m.m2)} (want 1), m3={m.m3} (want 0)"
    return CheckResult("oracle_perfect", ok, detail)


def _none_empty(m: Metrics) -> CheckResult:
    ok = _is(m.m1, 0) and _is(m.m2, 0) and m.m3 == 0
    detail = f"none m1={_fmt(m.m1)} (want 0), m2={_fmt(m.m2)} (want 0), m3={m.m3} (want 0)"
    return CheckResult("none_empty", ok, detail)


def _all_store_max_m1(metrics: dict[str, Metrics]) -> CheckResult:
    mine = metrics["all_store"].m1.value
    others = {n: metrics[n].m1.value for n in BASELINES if n != "all_store"}
    ok = mine is not None and mine > 0 and all(v is None or mine >= v for v in others.values())
    shown = ", ".join(f"{n}={'n/a' if v is None else f'{v:.3f}'}" for n, v in others.items())
    detail = f"all_store m1={'n/a' if mine is None else f'{mine:.3f}'} (want > 0 and >= others: {shown})"
    return CheckResult("all_store_max_m1", ok, detail)


def _family_m1(table: Mapping[str, FamilyRow], family: str) -> Rate | None:
    row = table.get(family)
    return None if row is None else row["m1"]


def _repeat_n_beats_oracle(
    name: str, family: str, present: bool, repeat_n: Rate | None, oracle: Rate | None
) -> CheckResult:
    if not present:
        return CheckResult(name, True, f"skipped: no {family} scenarios")
    r = None if repeat_n is None else repeat_n.value
    o = 0.0 if oracle is None or oracle.value is None else oracle.value
    ok = r is not None and r > o
    detail = f"repeat_n {family} m1={'n/a' if r is None else f'{r:.3f}'} vs oracle {o:.3f} (want repeat_n > oracle)"
    return CheckResult(name, ok, detail)


def _false_served(run: ScenarioRun, s: Scenario) -> list[int]:
    """Per block probe, in time order: how many served results state a fact false at that probe."""
    probes = {p.id: p for p in s.probes}
    counts = []
    for rec in run.probes:
        if rec.kind != "block":
            continue
        p = probes[rec.probe_id]
        false = {f for f in s.facts if not true_at(s.facts[f], p.t, probe_ctx(p))}
        counts.append(sum(1 for basis in rec.bases if basis & false))
    return counts


def _echo_buildup(runs: list[ScenarioRun], scenarios: list[Scenario]) -> CheckResult:
    """all_store serves the false record from the first probe and follows it; the echo must pile up.

    all_store hands over every visible record, so it serves the initial false records from the
    first block probe on and each answer acted on writes one more echo record. If the number of
    false-fact results it is served does not grow from the first to the last block probe of
    every echo scenario, the echo family does not build up an echo and measures nothing.
    """
    by_id = {s.id: s for s in scenarios}
    echo = [r for r in runs if r.family == "echo"]
    if not echo:
        return CheckResult("echo_buildup", True, "skipped: no echo scenarios")
    counts = {r.scenario_id: _false_served(r, by_id[r.scenario_id]) for r in echo}
    flat = sorted(sid for sid, c in counts.items() if len(c) < 2 or c[-1] <= c[0])
    shown = ", ".join(f"{sid}={'/'.join(map(str, c))}" for sid, c in counts.items())
    detail = (
        f"all_store false-fact results served per echo block probe: {shown} "
        "(want more at the last block probe than at the first in every echo scenario)"
    )
    if flat:
        detail += "; no build-up in " + ", ".join(flat)
    return CheckResult("echo_buildup", not flat, detail)


def run_selfcheck(
    scenarios: list[Scenario], cfg: EvalConfig, blind_key: bytes = DEFAULT_BLIND_KEY
) -> tuple[list[CheckResult], dict[str, list[ScenarioRun]]]:
    """Run the four baselines and check that they order as designed.

    If the baselines do not behave as expected, the scenarios or the harness are wrong, and no
    engine result on this set should be trusted.

    Args:
        scenarios: The scenario set.
        cfg: Evaluation config.
        blind_key: Namespace key, see `replay.blind_key`; `harness run` passes its run's key
            because these runs go into its report.

    Returns:
        The checks, and each baseline's runs (reused for the report).
    """
    runs = {name: _run_all(name, scenarios, cfg, blind_key) for name in BASELINES}
    by_id = {s.id: s for s in scenarios}
    metrics = {name: compute_metrics(rs, by_id) for name, rs in runs.items()}
    families = {s.family for s in scenarios}

    repeat_n, oracle = metrics["repeat_n"], metrics["oracle"]
    checks = [
        _no_errors(runs),
        _oracle_perfect(oracle),
        _none_empty(metrics["none"]),
        _all_store_max_m1(metrics),
        _repeat_n_beats_oracle(
            "repeat_n_poison",
            "single_source_poison",
            "single_source_poison" in families,
            _family_m1(repeat_n.by_family, "single_source_poison"),
            _family_m1(oracle.by_family, "single_source_poison"),
        ),
        _echo_buildup(runs["all_store"], scenarios),
    ]
    return checks, runs


def check_discrimination(s: Scenario, cfg: EvalConfig) -> list[Issue]:
    """Does this scenario separate an engine that knows the truth from one that stores everything?"""
    has_leak = any(p.kind == "leak" for p in s.probes)
    names = ["oracle", "all_store", *(["all_store_noacl"] if has_leak else [])]
    runs = {n: run_scenario(make_engine(n, s, cfg), s, run_id=RUN_ID, cfg=cfg) for n in names}

    errors = [
        Issue("error", "engine_error", f"{n} failed on {s.id}: {r.error}")
        for n, r in runs.items()
        if r.error is not None
    ]
    if errors:
        return errors

    issues: list[Issue] = []
    for rec in runs["oracle"].probes:
        if rec.kind == "block" and rec.outcome == "FP":
            issues.append(Issue("error", "oracle_wrong", f"oracle follows a false fact on block probe {rec.probe_id}"))
        if rec.kind == "promote" and rec.outcome != "TP":
            issues.append(Issue("error", "oracle_wrong", f"oracle outcome {rec.outcome} (want TP) on promote probe {rec.probe_id}"))
        if rec.leaks:
            issues.append(Issue("error", "oracle_wrong", f"oracle leaks on probe {rec.probe_id}"))

    has_block = any(p.kind == "block" for p in s.probes)
    if has_block and not any(r.kind == "block" and r.outcome == "FP" for r in runs["all_store"].probes):
        issues.append(Issue("warning", "too_easy", "all_store never follows a false fact on any block probe"))

    if has_leak:
        records = {r.probe_id: r for r in runs["all_store_noacl"].probes}
        for p in s.probes:
            if p.kind == "leak" and not (records.get(p.id) and records[p.id].leaks):
                issues.append(Issue("error", "leak_not_triggered", f"all_store_noacl does not leak on probe {p.id}"))
    return issues
