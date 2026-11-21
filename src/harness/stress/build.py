"""Building the stress set: every approved scenario × every value of the chosen knobs."""

from collections.abc import Iterable, Iterator, Sequence

from harness.bench.dataset import Converted
from harness.scenario.model import Scenario
from harness.stress.noise import NoiseText
from harness.stress.variant import VALUES, Knob, VariantError, make_variant


def build_variants(
    scenarios: Iterable[Scenario], texts: Sequence[NoiseText], knobs: Sequence[Knob], *, seed: int
) -> Iterator[Converted]:
    """One item per (scenario, knob, value), in that order; a variant that cannot be built is a skip.

    Args:
        scenarios: Approved scenarios, none of them a variant.
        texts: Noise texts to draw from.
        knobs: Knobs to sweep, each over all of its values in `VALUES`, one knob at a time.
        seed: Seed of every random choice; the same arguments give the same variants.
    """
    for s in scenarios:
        for knob in knobs:
            for value in VALUES[knob]:
                variant_id = f"{s.id}--{knob}-{value}"
                group = f"{s.family}/{knob}={value}"
                try:
                    v = make_variant(s, knob, value, texts, seed=seed)
                except VariantError as e:
                    yield Converted(None, variant_id, group, str(e))
                    continue
                yield Converted(v, variant_id, group, None)
