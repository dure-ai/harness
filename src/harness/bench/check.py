"""Screening of converted scenarios: structure, and whether the labels survive the oracle and `none`."""

from collections.abc import Callable, Iterable, Iterator

from harness.baselines.oracle import OracleEngine
from harness.baselines.simple import NoneEngine
from harness.bench.dataset import Converted
from harness.bench.gold import probe_correct
from harness.config import EvalConfig
from harness.contract import BaseEngine
from harness.replay import ScenarioRun, blind_key, run_scenario
from harness.scenario.model import Scenario
from harness.scenario.validate import validate_scenario

_RUN_ID = "bench-check"


def _wrong(run: ScenarioRun, kinds: dict[str, str]) -> list[str]:
    """Ids of the block and promote probes the engine answered wrongly, see `probe_correct`."""
    return [
        r.probe_id
        for r in run.probes
        if kinds[r.probe_id] in ("block", "promote") and not probe_correct(kinds[r.probe_id], r.outcome)
    ]


def check_conversion(
    s: Scenario, cfg: EvalConfig, *, none: Callable[[], BaseEngine] = NoneEngine
) -> str | None:
    """Check that the oracle answers every probe correctly and `none` scores no promote hit.

    Args:
        s: A valid converted scenario.
        cfg: Evaluation config.
        none: Factory of the "no memory" engine; replaceable for tests.

    Returns:
        None when both hold, else a one-line reason.
    """
    key = blind_key(0)
    kinds = {p.id: p.kind for p in s.probes}
    oracle = run_scenario(OracleEngine(s), s, run_id=_RUN_ID, cfg=cfg, blind_key=key)
    if oracle.error is not None:
        return f"oracle failed: {oracle.error}"
    bad = _wrong(oracle, kinds)
    if bad:
        return f"oracle answers wrongly on {', '.join(bad)}"
    empty = run_scenario(none(), s, run_id=_RUN_ID, cfg=cfg, blind_key=key)
    if empty.error is not None:
        return f"none failed: {empty.error}"
    hits = [r.probe_id for r in empty.probes if kinds[r.probe_id] == "promote" and r.outcome == "TP"]
    if hits:
        return f"none scores M2 > 0 on {', '.join(hits)}"
    return None


def screened(converted: Iterable[Converted], cfg: EvalConfig) -> Iterator[Converted]:
    """Pass converted items on, turning invalid or label-inconsistent scenarios into skips.

    Skipped items pass through unchanged. A scenario with an error issue becomes
    `skipped="invalid: <codes>"`; one that fails `check_conversion` becomes
    `skipped="conversion check: <reason>"`.
    """
    for c in converted:
        if c.scenario is None:
            yield c
            continue
        codes = sorted({i.code for i in validate_scenario(c.scenario) if i.level == "error"})
        if codes:
            yield Converted(None, c.source_id, c.group, f"invalid: {', '.join(codes)}")
            continue
        reason = check_conversion(c.scenario, cfg)
        if reason is not None:
            yield Converted(None, c.source_id, c.group, f"conversion check: {reason}")
            continue
        yield c
