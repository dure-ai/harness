"""Family templates: code that decides every label of a scenario skeleton (spec 5.3, generation design 5.4)."""

from harness.generate.candidates import FactCandidate
from harness.generate.families.base import SkeletonBuilder, Template, candidate_problem
from harness.generate.families.read_only import (
    coincidence,
    conditional,
    normal_repeat,
    single_source_poison,
    truth_change,
    workaround_then_root,
)
from harness.generate.slots import Skeleton

TEMPLATES: dict[str, Template] = {
    "normal_repeat": normal_repeat,
    "coincidence": coincidence,
    "single_source_poison": single_source_poison,
    "truth_change": truth_change,
    "conditional": conditional,
    "workaround_then_root": workaround_then_root,
}


def why_not(family: str, cand: FactCandidate) -> str | None:
    """Why `cand` cannot seed a scenario of `family`, or None when it can.

    Args:
        family: Scenario family.
        cand: Fact candidate.

    Returns:
        A one-line reason, or None when the family's template accepts the candidate.
    """
    if family not in TEMPLATES:
        return f"no template for family {family!r}"
    return candidate_problem(family, cand)


def fits(family: str, cand: FactCandidate) -> bool:
    """Whether `cand` can seed a scenario of `family` (see `why_not`)."""
    return why_not(family, cand) is None


__all__ = ["TEMPLATES", "Skeleton", "SkeletonBuilder", "Template", "fits", "why_not"]
