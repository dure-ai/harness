"""Label-preserving variants of an approved scenario, one difficulty knob at a time.

Every variant keeps the scenario's facts, probes and their order on the timeline, so the hidden
labels stay true and the oracle still answers every probe; only what surrounds the evidence
changes. See the design note "하네스 변별력 강화 설계 초안 (2026-11-21)", section 5.
"""

import random
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, get_args

from harness.scenario.model import Fact, Probe, Review, Scenario, ScenarioEvent, Variant
from harness.stress.noise import NoiseText
from harness.text import contains_secret

Knob = Literal["noise", "position", "gap"]
Position = Literal["early", "middle", "late"]

KNOBS: tuple[str, ...] = get_args(Knob)
NOISE_LEVELS = ("0", "20", "100", "300")
POSITIONS: tuple[str, ...] = get_args(Position)  # where the evidence sits among the noise
GAPS = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "30d": timedelta(days=30)}
# Values of each knob, in the order a difficulty curve reads them.
VALUES: dict[str, tuple[str, ...]] = {"noise": NOISE_LEVELS, "position": POSITIONS, "gap": tuple(GAPS)}

POSITION_NOISE = 100  # noise events of a position variant
NOISE_POSITION: Position = "middle"  # position of the noise in a noise variant
LATE_WINDOW = timedelta(days=7)  # how far before the first event "late" noise starts


class VariantError(ValueError):
    """A variant cannot be built, e.g. too few noise texts are usable for the scenario."""


def _rng(seed: int, *parts: object) -> random.Random:
    # A string seed is hashed with SHA-512 by `random`, so it is the same in every process.
    return random.Random(":".join(map(str, (seed, *parts))))


def _between(start: datetime, end: datetime, n: int) -> list[datetime]:
    """`n` times evenly spaced strictly inside (start, end)."""
    step = (end - start) / (n + 1)
    return [start + step * (i + 1) for i in range(n)]


def _usable(s: Scenario, texts: Sequence[NoiseText]) -> list[NoiseText]:
    """Texts that can be noise for `s`.

    A usable text states none of the scenario's facts (by marker), holds none of its secrets and
    comes from a trajectory the scenario was not made from.
    """
    markers = [m.casefold() for f in s.facts.values() for m in f.markers if m.strip()]
    secrets = [m for e in s.events for m in e.secret_markers if m.strip()]
    own = set(s.provenance.source_traces)
    return [
        t
        for t in texts
        if t.trace_id not in own
        and not any(m in t.text.casefold() for m in markers)
        and not any(contains_secret(t.text, m) for m in secrets)
    ]


def _noise_times(s: Scenario, n: int, position: Position) -> list[datetime]:
    """Times for `n` noise events, all before the first probe, in increasing order.

    "early": the evidence comes first; the noise sits between the last event before the first
    probe and that probe. "late": the evidence is the most recent; the noise comes before the
    first event. "middle": half each way.
    """
    p0 = min(p.t for p in s.probes)
    first = min(e.t for e in s.events)
    before = [e.t for e in s.events if e.t < p0]
    last = max(before) if before else p0 - timedelta(hours=1)
    after = {"early": n, "late": 0, "middle": n - n // 2}[position]
    return _between(first - LATE_WINDOW, first, n - after) + _between(last, p0, after)


def _add_noise(
    s: Scenario, texts: Sequence[NoiseText], n: int, position: Position, seed: int
) -> tuple[Scenario, list[str]]:
    if n == 0:
        return s, []
    candidates = _usable(s, texts)
    if len(candidates) < n:
        raise VariantError(f"{s.id}: {len(candidates)} usable noise texts, {n} needed")
    rng = _rng(seed, s.id, n, position)
    picks = rng.sample(candidates, n)
    members = sorted(s.world.teams[s.team_of(s.probes[0].user)])
    sessions = sorted({e.session for e in s.events})
    repo = Counter(e.repo for e in s.events).most_common(1)[0][0]
    taken = {e.id for e in s.events}
    noise: list[ScenarioEvent] = []
    for i, (t, pick) in enumerate(zip(_noise_times(s, n, position), picks, strict=True), start=1):
        event_id = f"noise-{i:03d}"
        while event_id in taken:
            event_id += "x"
        taken.add(event_id)
        noise.append(
            ScenarioEvent(
                id=event_id, t=t, user=rng.choice(members), session=rng.choice(sessions), repo=repo,
                scope="team", signal="self", kind="agent_record", outcome="unknown", text=pick.text,
            )
        )
    # Stable sort: noise times never equal an event's time (they lie where no event is).
    events = sorted([*s.events, *noise], key=lambda e: e.t)
    return s.model_copy(update={"events": events}), [p.trace_id for p in picks]


def add_noise(s: Scenario, texts: Sequence[NoiseText], n: int, position: Position, *, seed: int) -> Scenario:
    """`s` with `n` unlabeled events the asker can see, placed by `position` before the first probe.

    Each noise event has no fact, no secret marker and team scope, a user from the first
    asker's team, one of the scenario's sessions and its most common repository. The same
    arguments give the same scenario.

    Raises:
        VariantError: Fewer than `n` texts are usable for this scenario (see `_usable`).
    """
    return _add_noise(s, texts, n, position, seed)[0]


def _shift(t: datetime | None, p0: datetime, gap: timedelta) -> datetime | None:
    return t + gap if t is not None and t >= p0 else t


def widen_gap(s: Scenario, gap: timedelta) -> Scenario:
    """`s` with everything from the first probe on moved `gap` later.

    Events, probes and fact validity edges at or after the first probe move together, so the
    order of every item and the truth of every fact at every probe stay the same; only the
    time between the history before the first probe and that probe grows by `gap`.
    """
    p0 = min(p.t for p in s.probes)
    events = [e.model_copy(update={"t": _shift(e.t, p0, gap)}) for e in s.events]
    probes: list[Probe] = [p.model_copy(update={"t": p.t + gap}) for p in s.probes]
    facts: dict[str, Fact] = {
        fid: f.model_copy(
            update={"valid_from": _shift(f.valid_from, p0, gap), "valid_until": _shift(f.valid_until, p0, gap)}
        )
        for fid, f in s.facts.items()
    }
    return s.model_copy(update={"events": events, "probes": probes, "facts": facts})


def make_variant(s: Scenario, knob: Knob, value: str, texts: Sequence[NoiseText], *, seed: int) -> Scenario:
    """The stress-set variant of approved scenario `s` with `knob` set to `value`.

    "noise": `value` noise events placed "middle". "position": `POSITION_NOISE` noise events
    placed at `value` ("early", "middle", "late"). "gap": `widen_gap` by `GAPS[value]`.

    The variant's id is "<base>--<knob>-<value>". It is a draft (its approval was for the base
    content), its provenance records the base, knob, value, seed and noise trajectories, and
    the base's generation hashes are dropped since they describe other content.

    Raises:
        VariantError: Too few usable noise texts, or an unknown knob value.
    """
    if knob == "noise" and not (value.isdigit() and value == str(int(value))):
        raise VariantError(f"noise value must be a whole number of events, got {value!r}")
    if knob != "noise" and value not in VALUES[knob]:
        raise VariantError(f"unknown {knob} value {value!r}; choose from {', '.join(VALUES[knob])}")
    if knob == "gap":
        out, traces = widen_gap(s, GAPS[value]), []
    elif knob == "noise":
        out, traces = _add_noise(s, texts, int(value), NOISE_POSITION, seed)
    else:
        position: Position = "early" if value == "early" else "late" if value == "late" else "middle"
        out, traces = _add_noise(s, texts, POSITION_NOISE, position, seed)
    variant = Variant(base=s.id, knob=knob, value=value, seed=seed, noise_traces=traces)
    provenance = s.provenance.model_copy(
        update={"variant": variant, "generated_hash": None, "label_fingerprint": None}
    )
    review = Review(status="draft", notes=f"stress variant of approved scenario {s.id}")
    return out.model_copy(update={"id": f"{s.id}--{knob}-{value}", "review": review, "provenance": provenance})


def group_of(s: Scenario) -> str:
    """The report group of a scenario: "<family>/<knob>=<value>" for a variant, else its family."""
    v = s.provenance.variant
    return s.family if v is None else f"{s.family}/{v.knob}={v.value}"


def base_of(s: Scenario) -> str:
    """The approved scenario a variant came from; a scenario that is not a variant is its own base."""
    v = s.provenance.variant
    return s.id if v is None else v.base


def group_sort_key(group: str) -> tuple[str, int, int | str]:
    """Order "<family>/<knob>=<value>" groups by family, then knob, then the knob's value order."""
    family, _, rest = group.partition("/")
    knob, _, value = rest.partition("=")
    if knob not in KNOBS:
        return family, -1, ""
    order: int | str = int(value) if knob == "noise" and value.isdigit() else (
        VALUES[knob].index(value) if value in VALUES[knob] else value
    )
    return family, KNOBS.index(knob), order
