"""Ground truth: whether a fact is true at a given time and context."""

from collections.abc import Mapping
from datetime import datetime

from harness.scenario.model import Fact, Probe


def true_at(fact: Fact, t: datetime, ctx: Mapping[str, str]) -> bool:
    if not fact.valid:
        return False
    if fact.valid_from is not None and t < fact.valid_from:
        return False
    if fact.valid_until is not None and t >= fact.valid_until:
        return False
    return all(ctx.get(key) == value for key, value in fact.conditions.items())


def probe_ctx(probe: Probe) -> dict[str, str]:
    return {"repo": probe.repo, **probe.meta}
