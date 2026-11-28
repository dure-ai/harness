"""Knob tables and the naming of stress variants: ids, report groups and their order.

A leaf module (it imports only the scenario model) so that `harness.bench` can group variant
runs without importing the variant builder.
"""

from typing import Literal, TypeGuard, get_args

from harness.scenario.model import Knob, Scenario

Position = Literal["early", "middle", "late"]  # where the evidence sits among the noise

KNOBS: tuple[Knob, ...] = get_args(Knob)
NOISE_LEVELS = ("0", "20", "100", "300")
# "middle" is not swept: a position variant has 100 noise events, so "middle" equals noise=100.
SWEPT_POSITIONS: tuple[Position, ...] = ("early", "late")
GAP_VALUES = ("1h", "1d", "30d")
# Values each knob sweeps, in the order a difficulty curve reads them.
VALUES: dict[Knob, tuple[str, ...]] = {"noise": NOISE_LEVELS, "position": SWEPT_POSITIONS, "gap": GAP_VALUES}


def is_knob(name: str) -> TypeGuard[Knob]:
    """Whether `name` is one of `KNOBS`."""
    return name in KNOBS


def is_position(value: str) -> TypeGuard[Position]:
    """Whether `value` is a position ("early", "middle" or "late")."""
    return value in get_args(Position)


def variant_id(base_id: str, knob: Knob, value: str) -> str:
    """The id of the variant of `base_id` with `knob` set to `value`: "<base>--<knob>-<value>"."""
    return f"{base_id}--{knob}-{value}"


def variant_group(family: str, knob: Knob, value: str) -> str:
    """The report group of a variant: "<family>/<knob>=<value>"."""
    return f"{family}/{knob}={value}"


def group_of(s: Scenario) -> str:
    """The report group of a scenario: `variant_group` for a variant, else its family."""
    v = s.provenance.variant
    return s.family if v is None else variant_group(s.family, v.knob, v.value)


def base_of(s: Scenario) -> str:
    """The approved scenario a variant came from; a scenario that is not a variant is its own base."""
    v = s.provenance.variant
    return s.id if v is None else v.base


def group_sort_key(group: str) -> tuple[str, int, int, int, str]:
    """Order groups by family, then knob, then the knob's value order.

    Groups that are not "<family>/<knob>=<value>" sort by name before any knob of their family;
    a value outside the knob's table sorts after the known ones, by name.
    """
    family, _, rest = group.partition("/")
    knob, _, value = rest.partition("=")
    if not is_knob(knob):
        return family, -1, 0, 0, rest
    if knob == "noise" and value.isdecimal():
        return family, KNOBS.index(knob), 0, int(value), ""
    if value in VALUES[knob]:
        return family, KNOBS.index(knob), 0, VALUES[knob].index(value), ""
    return family, KNOBS.index(knob), 1, 0, value
