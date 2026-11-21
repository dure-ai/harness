"""Gold mode: the same scenario with only the events a probe asks about (spec section 6)."""

from harness.replay import ScenarioRun
from harness.scenario.model import Scenario

CELLS = ("both_right", "retrieval_or_noise", "judgment_or_use", "gold_only_wrong")
MAX_EXAMPLES = 5


def gold_copy(s: Scenario) -> Scenario:
    """`s` without the events no probe asks about: noise, and facts outside every probe's `about`.

    The facts a block probe's `about` names (the old, now false ones) stay. Probes, facts and times
    are unchanged.
    """
    wanted = {f for p in s.probes for f in p.about}
    return s.model_copy(update={"events": [e for e in s.events if e.fact in wanted]})


def probe_correct(kind: str, outcome: str) -> bool:
    """Whether a block or promote probe was answered right: block unless FP, promote only on TP.

    Other probe kinds (leak) are never right by this rule; callers keep them out.
    """
    if kind == "block":
        return outcome != "FP"
    return kind == "promote" and outcome == "TP"


def _verdicts(runs: list[ScenarioRun]) -> dict[tuple[str, str], bool]:
    return {
        (r.scenario_id, p.probe_id): probe_correct(p.kind, p.outcome)
        for r in runs
        if r.error is None
        for p in r.probes
        if p.kind in ("block", "promote")
    }


def _cells(normal: list[ScenarioRun], gold: list[ScenarioRun]) -> dict[str, list[tuple[str, str]]]:
    n, g = _verdicts(normal), _verdicts(gold)
    out: dict[str, list[tuple[str, str]]] = {c: [] for c in CELLS}
    for key in sorted(n.keys() & g.keys()):
        cell = {
            (True, True): "both_right",
            (False, True): "retrieval_or_noise",
            (False, False): "judgment_or_use",
            (True, False): "gold_only_wrong",
        }[n[key], g[key]]
        out[cell].append(key)
    return out


def compare_modes(normal: list[ScenarioRun], gold: list[ScenarioRun]) -> dict[str, int]:
    """Count probes by whether the engine was right on the full stream and on gold events.

    Only probes of finished runs (`error is None`) present in both modes count, keyed by
    `(scenario_id, probe_id)`; leak probes are ignored. A block probe is right unless its outcome
    is `FP`; a promote probe only on `TP`.

    Returns:
        `both_right`; `retrieval_or_noise` (wrong on the full stream, right on gold);
        `judgment_or_use` (wrong in both); `gold_only_wrong` (right on the full stream, wrong on gold).
    """
    return {c: len(keys) for c, keys in _cells(normal, gold).items()}


def gold_only_wrong_examples(
    normal: list[ScenarioRun], gold: list[ScenarioRun], limit: int = MAX_EXAMPLES
) -> list[tuple[str, str]]:
    """Up to `limit` `(scenario_id, probe_id)` of the `gold_only_wrong` cell, in sorted order."""
    return _cells(normal, gold)["gold_only_wrong"][:limit]
