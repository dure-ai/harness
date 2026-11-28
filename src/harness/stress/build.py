"""Building the stress set: every approved scenario and every value of the chosen knobs."""

from collections.abc import Iterable, Iterator, Sequence

from harness.bench.dataset import Converted
from harness.scenario.model import Knob, Scenario
from harness.stress.groups import VALUES, variant_group, variant_id
from harness.stress.noise import NoisePool
from harness.stress.variant import VariantError, make_variant


def build_variants(
    scenarios: Iterable[Scenario], pool: NoisePool, knobs: Sequence[Knob], *, seed: int
) -> Iterator[Converted]:
    """One item per (scenario, knob, value), in that order; a variant that cannot be built is a skip.

    Args:
        scenarios: Approved scenarios, none of them a variant.
        pool: Noise texts to draw from.
        knobs: Knobs to sweep, each over all of its values in `VALUES`, one knob at a time.
        seed: Seed of every random choice; the same arguments give the same variants.
    """
    for s in scenarios:
        for knob in knobs:
            for value in VALUES[knob]:
                group = variant_group(s.family, knob, value)
                try:
                    v = make_variant(s, knob, value, pool, seed=seed)
                except VariantError as e:
                    yield Converted(None, variant_id(s.id, knob, value), group, str(e))
                    continue
                yield Converted(v, v.id, group, None)
