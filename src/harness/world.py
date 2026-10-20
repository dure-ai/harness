"""Ground truth: whether a fact is true at a given time and context."""

from collections.abc import Mapping
from datetime import datetime

from harness.scenario.model import Fact, Probe


def true_at(fact: Fact, t: datetime, ctx: Mapping[str, str]) -> bool:
    """Whether `fact` holds at time `t` in context `ctx`.

    `valid_from` is inclusive and `valid_until` exclusive; `valid=False` is never true. Every
    condition must equal the context value, and a condition key missing from the context is false.

    Args:
        fact: The fact to evaluate.
        t: Timezone-aware time.
        ctx: The probe context, see `probe_ctx`.

    Returns:
        True if the fact holds.
    """
    if not fact.valid:
        return False
    if fact.valid_from is not None and t < fact.valid_from:
        return False
    if fact.valid_until is not None and t >= fact.valid_until:
        return False
    return all(ctx.get(key) == value for key, value in fact.conditions.items())


def probe_ctx(probe: Probe) -> dict[str, str]:
    """Return the context a probe is asked in, matched against `Fact.conditions`.

    Returns:
        The probe's repo under "repo", plus its meta (such as "service").
    """
    return {"repo": probe.repo, **probe.meta}
