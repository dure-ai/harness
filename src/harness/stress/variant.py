"""Label-preserving variants of an approved scenario, one difficulty knob at a time.

Every variant keeps the scenario's facts, probes and their order on the timeline, so the hidden
labels stay true and the oracle still answers every probe; only what surrounds the evidence
changes. See the design note "Harness discrimination design draft (2026-11-21)" in the team's
engineering docs, section 5.
"""

import random
from collections import Counter
from datetime import datetime, timedelta

from pydantic import ValidationError

from harness.scenario.model import Fact, Knob, Probe, Review, Scenario, ScenarioEvent, Variant
from harness.stress.groups import GAP_VALUES, Position, is_position, variant_id
from harness.stress.noise import NoisePool, NoiseText

GAPS = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "30d": timedelta(days=30)}
assert tuple(GAPS) == GAP_VALUES

POSITION_NOISE = 100  # noise events of a position variant
NOISE_POSITION: Position = "middle"  # position of the noise in a noise variant
LATE_WINDOW = timedelta(days=7)  # how far before the first event "late" noise starts


class VariantError(ValueError):
    """A variant cannot be built.

    Too few usable noise texts, askers on several teams, no event by the askers' team, no history
    before the first probe, an unknown knob value, or a result that is not a valid scenario.
    """


def _rng(seed: int, *parts: object) -> random.Random:
    # A string seed is hashed with SHA-512 by `random`, so it is the same in every process.
    return random.Random(":".join(map(str, (seed, *parts))))


MINUTE = timedelta(minutes=1)


def _minutes(start: datetime, end: datetime, n: int, rng: random.Random) -> list[datetime]:
    """`n` whole-minute times strictly inside (start, end), drawn by `rng`, in increasing order.

    Labeled events are on whole minutes, so noise is too, at irregular steps: an even cadence or
    a finer grain would tell noise from evidence by its time alone. The minutes are distinct
    while the window has enough of them and repeat otherwise. With no whole minute inside the
    window, every time is `start`.
    """
    if n == 0:
        return []
    first = start.replace(second=0, microsecond=0) + MINUTE
    count = max(0, -(-(end - first) // MINUTE))  # minutes first, first + 1 min, ... before end
    if count == 0:
        return [start] * n
    picks = rng.sample(range(count), n) if count >= n else rng.choices(range(count), k=n)
    return [first + MINUTE * i for i in sorted(picks)]


def _noise_times(s: Scenario, n: int, position: Position, rng: random.Random) -> list[datetime]:
    """Times for `n` noise events, all before the first probe, in increasing order.

    The history recorded by the first probe is the events at or before its time. "early": the
    evidence comes first; the noise sits after the last of those events (at its time when it
    shares the probe's time, after it in file order). "late": the evidence is the most recent;
    the noise comes before the first event. "middle": half each way.

    Raises:
        VariantError: No event is recorded by the first probe.
    """
    p0 = min(p.t for p in s.probes)
    recorded = [e.t for e in s.events if e.t <= p0]
    if not recorded:
        raise VariantError(f"{s.id}: no event before the first probe to place noise around")
    first, last = min(recorded), max(recorded)
    after = {"early": n, "late": 0, "middle": n - n // 2}[position]
    return _minutes(first - LATE_WINDOW, first, n - after, rng) + _minutes(last, p0, after, rng)


def _draw(s: Scenario, pool: NoisePool, seed: int) -> list[NoiseText]:
    """The usable texts of `pool` for `s` in one order per (seed, scenario).

    Every noise level takes a prefix of this order and every position takes the same first
    `POSITION_NOISE` texts, so neighbouring knob values differ only in what the knob changes.
    """
    candidates = pool.usable_for(s)
    return _rng(seed, s.id).sample(candidates, len(candidates))


def _add_noise(s: Scenario, pool: NoisePool, n: int, position: Position, seed: int) -> tuple[Scenario, list[str]]:
    if n == 0:
        return s, []
    teams = {s.team_of(p.user) for p in s.probes}
    if len(teams) > 1:  # team-scoped noise would be hidden from some asker and add leak material
        raise VariantError(f"{s.id}: probes are asked from {len(teams)} teams; noise must be visible to every asker")
    order = _draw(s, pool, seed)
    if len(order) < n:
        raise VariantError(f"{s.id}: {len(order)} usable noise texts, {n} needed")
    times = _noise_times(s, n, position, _rng(seed, s.id, "when", position, n))
    members = s.world.teams[teams.pop()]
    # A user in a session they never used would set the noise apart, so authors are pairs the history has.
    authors = sorted({(e.user, e.session) for e in s.events if e.user in members})
    if not authors:
        raise VariantError(f"{s.id}: no event by the askers' team to take a noise author from")
    repo = Counter(e.repo for e in s.events).most_common(1)[0][0]
    who = _rng(seed, s.id, "who")  # drawn in text order, so the first n authors are the same for every n
    taken = {e.id for e in s.events}
    noise: list[ScenarioEvent] = []
    for i, (t, pick) in enumerate(zip(times, order, strict=False), start=1):
        event_id = f"noise-{i:03d}"
        while event_id in taken:
            event_id += "x"
        taken.add(event_id)
        user, session = who.choice(authors)
        noise.append(
            ScenarioEvent(
                id=event_id, t=t, user=user, session=session, repo=repo,
                scope="team", signal="self", kind="agent_record", outcome="unknown", text=pick.text,
            )
        )
    # Stable sort: a noise time equals an event's time only at the first probe's time, where the
    # noise comes after the event (it was appended later), as "early" intends.
    events = sorted([*s.events, *noise], key=lambda e: e.t)
    return s.model_copy(update={"events": events}), [p.trace_id for p in order[:n]]


def add_noise(s: Scenario, pool: NoisePool, n: int, position: Position, *, seed: int) -> Scenario:
    """`s` with `n` unlabeled events every asker can see, placed by `position` before the first probe.

    Each noise event has no fact, no secret marker and team scope, a (user, session) pair that an
    event by the askers' team already has, its most common repository, and a whole-minute time
    drawn by the seed (see `_minutes`). The same arguments give the
    same scenario, and a smaller `n` gives a prefix of a larger one's texts and authors.

    Raises:
        VariantError: Askers on several teams, too few usable texts (see `NoisePool.usable_for`),
            no event by the askers' team, or no event recorded by the first probe.
    """
    return _add_noise(s, pool, n, position, seed)[0]


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


def make_variant(s: Scenario, knob: Knob, value: str, pool: NoisePool, *, seed: int) -> Scenario:
    """The stress-set variant of approved scenario `s` with `knob` set to `value`.

    "noise": `value` noise events placed "middle". "position": `POSITION_NOISE` noise events
    placed at `value` ("early", "middle", "late"). "gap": `widen_gap` by `GAPS[value]`.

    The variant's id is `variant_id(s.id, knob, value)`. It is a draft (its approval was for the
    base content), its provenance records the base, knob, value, seed, noise trajectories and
    pool fingerprint, and the base's generation hashes are dropped since they describe other
    content. The result is validated as a whole, so an over-long id is a `VariantError`.

    Raises:
        VariantError: See `add_noise`, an unknown knob value, or an invalid result.
    """
    match knob:
        case "noise" if value.isdecimal() and value == str(int(value)):
            out, traces = _add_noise(s, pool, int(value), NOISE_POSITION, seed)
        case "noise":
            raise VariantError(f"noise value must be a whole number of events, got {value!r}")
        case "position" if is_position(value):
            out, traces = _add_noise(s, pool, POSITION_NOISE, value, seed)
        case "gap" if value in GAPS:
            out, traces = widen_gap(s, GAPS[value]), []
        case _:
            raise VariantError(f"unknown {knob} value {value!r}")
    variant = Variant(base=s.id, knob=knob, value=value, seed=seed, noise_traces=traces, pool_sha256=pool.sha256)
    provenance = s.provenance.model_copy(update={"variant": variant, "generated_hash": None, "label_fingerprint": None})
    review = Review(status="draft", notes=f"stress variant of approved scenario {s.id}")
    built = out.model_copy(update={"id": variant_id(s.id, knob, value), "review": review, "provenance": provenance})
    try:
        return Scenario.model_validate(built.model_dump())
    except ValidationError as e:
        raise VariantError(f"{built.id}: not a valid scenario: {e.errors()[0]['msg']}") from None
