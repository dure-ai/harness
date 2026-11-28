import functools
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from harness.bench.check import check_conversion
from harness.config import EvalConfig
from harness.contract import can_see
from harness.scenario.load import load_set
from harness.scenario.model import Scenario
from harness.scenario.validate import validate_scenario
from harness.stress.groups import VALUES, group_sort_key, variant_id
from harness.stress.noise import NoisePool, NoiseText
from harness.stress.variant import GAPS, VariantError, add_noise, make_variant, widen_gap
from harness.world import probe_ctx, true_at

DEV = Path("scenarios/dev")


@functools.cache
def dev() -> tuple[Scenario, ...]:
    return tuple(load_set([DEV], include_drafts=False).scenarios)


def one(family: str) -> Scenario:
    return next(s for s in dev() if s.family == family)


def texts(n: int = 400) -> list[NoiseText]:
    return [
        NoiseText(f"t{i % 7}", f"ran the linter on module_{i} and looked at the import order of package {i}", "acme/tools")
        for i in range(n)
    ]


@functools.cache
def pool() -> NoisePool:
    return NoisePool(texts())


def errors(s: Scenario) -> list[str]:
    return [i.code for i in validate_scenario(s) if i.level == "error"]


def first_probe(s: Scenario) -> datetime:
    return min(p.t for p in s.probes)


def noise_events(v: Scenario, base: Scenario):
    ids = {e.id for e in base.events}
    return [e for e in v.events if e.id not in ids]


def labels_at_probes(s: Scenario) -> list[tuple]:
    """Per probe: the truth of every fact, and the labeled events recorded before it."""
    out = []
    for p in s.probes:
        truth = tuple(sorted((fid, true_at(f, p.t, probe_ctx(p))) for fid, f in s.facts.items()))
        recorded = tuple(sorted(e.id for e in s.events if e.fact is not None and e.t <= p.t))
        out.append((p.id, truth, recorded))
    return out


def every_variant():
    for s in dev():
        for knob, values in VALUES.items():
            for value in values:
                try:
                    yield s, make_variant(s, knob, value, pool(), seed=0)
                except VariantError:
                    continue


def test_noise_adds_unlabeled_events_before_the_first_probe():
    s = one("truth_change")
    v = add_noise(s, pool(), 20, "middle", seed=1)
    noise = noise_events(v, s)
    assert len(noise) == 20
    assert all(e.fact is None and not e.secret_markers and e.scope == "team" for e in noise)
    assert all(e.t < first_probe(s) for e in noise)
    assert len({e.id for e in v.events}) == len(v.events)
    assert v.facts == s.facts and v.probes == s.probes
    assert errors(v) == []
    assert check_conversion(v, EvalConfig()) is None


def test_every_variant_keeps_the_labels_and_shows_noise_to_every_asker():
    built = 0
    for s, v in every_variant():
        built += 1
        assert labels_at_probes(v) == labels_at_probes(s), v.id
        engine_events = {le.event.id: le.event for le in v.labeled_events()}
        for e in noise_events(v, s):
            seen = engine_events[e.id]
            assert all(can_see(seen, p.user, v.team_of(p.user)) for p in v.probes), (v.id, e.id)
        assert errors(v) == [], v.id
        assert check_conversion(v, EvalConfig()) is None, v.id
    assert built > len(dev()) * 5


def test_askers_on_two_teams_get_no_noise_variant():
    s = one("leak_cross_team")
    if len({s.team_of(p.user) for p in s.probes}) < 2:
        pytest.skip("this leak_cross_team scenario asks from one team")
    with pytest.raises(VariantError, match="teams"):
        make_variant(s, "noise", "20", pool(), seed=0)
    assert make_variant(s, "gap", "1d", pool(), seed=0).id == variant_id(s.id, "gap", "1d")


def test_position_places_noise_after_or_before_the_events():
    s = one("coincidence")
    first_event = min(e.t for e in s.events)
    last_event = max(e.t for e in s.events if e.t <= first_probe(s))

    def times(position):
        return [e.t for e in noise_events(add_noise(s, pool(), 10, position, seed=3), s)]

    assert all(last_event < t < first_probe(s) for t in times("early"))
    assert all(t < first_event for t in times("late"))
    middle = times("middle")
    assert any(t < first_event for t in middle) and any(t > last_event for t in middle)


def on_the_minute(t: datetime) -> bool:
    return t.second == 0 and t.microsecond == 0


def test_noise_times_have_the_grain_of_the_history():
    """Every labeled event is on a whole minute, so noise must be too, or its time alone gives it away."""
    for s, v in every_variant():
        assert all(on_the_minute(e.t) for e in s.events), s.id
        assert all(on_the_minute(e.t) for e in noise_events(v, s)), v.id


def test_noise_times_are_not_evenly_spaced():
    s = one("coincidence")
    times = [e.t for e in noise_events(add_noise(s, pool(), 20, "early", seed=3), s)]
    assert times == sorted(times)
    assert len({b - a for a, b in zip(times, times[1:], strict=False)}) > 3


def test_noise_authors_are_user_and_session_pairs_the_history_has():
    """A user in a session they never used would set the noise apart; every base session has one user."""
    for s, v in every_variant():
        pairs = {(e.user, e.session) for e in s.events}
        assert all((e.user, e.session) in pairs for e in noise_events(v, s)), v.id


def test_neighbouring_knob_values_share_their_noise():
    s = one("conditional")

    def drawn(knob: str, value: str) -> set[str]:
        return {e.text for e in noise_events(make_variant(s, knob, value, pool(), seed=4), s)}  # pyright: ignore[reportArgumentType]

    assert drawn("noise", "20") < drawn("noise", "100")
    assert drawn("position", "early") == drawn("position", "late") == drawn("noise", "100")


def test_noise_is_deterministic_for_a_seed_and_differs_across_seeds():
    s = one("conditional")
    assert add_noise(s, pool(), 20, "middle", seed=5) == add_noise(s, pool(), 20, "middle", seed=5)
    assert add_noise(s, pool(), 20, "middle", seed=5) != add_noise(s, pool(), 20, "middle", seed=6)


def test_noise_never_carries_a_marker_a_source_trace_or_the_same_repository():
    s = one("truth_change")
    marker = next(m for f in s.facts.values() for m in f.markers)
    repo = s.events[0].repo
    candidates = [
        NoiseText("bad", f"note: {marker} is what we use for this one, every single time", "other/repo"),
        NoiseText("sibling", "another model's attempt at the same task in the very same repository", repo),
        NoiseText("short-name", "the same repository named only by its short name, which also counts", repo.rsplit("/", 1)[-1]),
        *texts(60),
    ]
    if s.provenance.source_traces:
        candidates.append(NoiseText(s.provenance.source_traces[0], "a harmless line of text from the same trace", "x/y"))
    usable = NoisePool(candidates).usable_for(s)
    assert {t.trace_id for t in usable} == {f"t{i}" for i in range(7)}


def test_noise_never_carries_a_secret_in_any_spelling():
    """Noise is filtered by the `contains_secret` rule, so case and separator variants are caught too."""
    s = one("leak_cross_team")
    secret = next(m for e in s.events for m in e.secret_markers)
    spelled = secret.upper().replace("_", " - ")
    candidates = [
        NoiseText("exact", f"the token {secret} was printed in the build log of the last nightly run", "other/repo"),
        NoiseText("spelled", f"the token {spelled} was printed in the build log of the last nightly run", "other/repo"),
        *texts(60),
    ]
    usable = NoisePool(candidates).usable_for(s)
    assert {t.trace_id for t in usable} == {f"t{i}" for i in range(7)}


def test_too_few_usable_texts_is_an_error():
    with pytest.raises(VariantError, match="noise"):
        add_noise(one("conditional"), NoisePool(texts(5)), 20, "middle", seed=1)


def test_no_history_before_the_first_probe_is_an_error():
    s = one("coincidence")
    late = s.model_copy(update={"events": [e.model_copy(update={"t": first_probe(s) + timedelta(hours=1)}) for e in s.events]})
    with pytest.raises(VariantError, match="no event before the first probe"):
        add_noise(late, pool(), 10, "middle", seed=1)


def test_early_noise_follows_an_event_at_the_first_probe_time():
    s = one("coincidence")
    p0 = first_probe(s)
    last = max(s.events, key=lambda e: e.t)
    moved = s.model_copy(update={"events": [e.model_copy(update={"t": p0}) if e is last else e for e in s.events]})
    v = add_noise(moved, pool(), 5, "early", seed=1)
    order = [e.id for e in v.events]
    assert all(order.index(e.id) > order.index(last.id) for e in noise_events(v, moved))
    assert all(e.t <= p0 for e in v.events if e.fact is None)


def test_gap_moves_the_probes_and_later_history_but_keeps_the_labels():
    for family in ("truth_change", "echo", "single_source_poison"):
        s = one(family)
        v = widen_gap(s, GAPS["30d"])
        p0 = first_probe(s)
        before = max(e.t for e in s.events if e.t < p0)
        assert first_probe(v) - before == (p0 - before) + timedelta(days=30)
        assert [p.t - q.t for p, q in zip(v.probes, s.probes, strict=True)] == [timedelta(days=30)] * len(s.probes)
        assert [(p, t, r) for p, t, r in labels_at_probes(v)] == labels_at_probes(s), family
        assert errors(v) == [], family


def test_variant_records_its_origin_and_becomes_a_draft():
    s = one("coincidence")
    v = make_variant(s, "noise", "20", pool(), seed=9)
    assert v.id == variant_id(s.id, "noise", "20")
    assert v.review.status == "draft" and v.review.reviewer is None
    assert v.provenance.variant is not None
    variant = v.provenance.variant
    assert (variant.base, variant.knob, variant.value, variant.pool_sha256) == (s.id, "noise", "20", pool().sha256)
    assert len(variant.noise_traces) == 20
    gap = make_variant(s, "gap", "1d", pool(), seed=9).provenance.variant
    assert gap is not None and gap.noise_traces == []


def test_bad_values_and_over_long_ids_are_variant_errors():
    s = one("coincidence")
    for knob, value in (("noise", "²"), ("noise", "-1"), ("noise", "007"), ("gap", "2y"), ("position", "top")):
        with pytest.raises(VariantError):
            make_variant(s, knob, value, pool(), seed=0)  # pyright: ignore[reportArgumentType]
    long = s.model_copy(update={"id": "x" * 115})  # + "--gap-1d" exceeds the 120-character id limit
    with pytest.raises(VariantError, match="not a valid scenario"):
        make_variant(long, "gap", "1d", pool(), seed=0)


def test_pool_fingerprint_follows_its_texts():
    assert NoisePool(texts(10)).sha256 == NoisePool(texts(10)).sha256
    assert NoisePool(texts(10)).sha256 != NoisePool(texts(11)).sha256


def test_groups_sort_by_knob_value_order():
    groups = ["f/noise=300", "f/noise=20", "f/gap=30d", "f/position=late", "f/noise=0", "f/position=early", "f/gap=1h"]
    assert sorted(groups, key=group_sort_key) == [
        "f/noise=0", "f/noise=20", "f/noise=300", "f/position=early", "f/position=late", "f/gap=1h", "f/gap=30d",
    ]
    assert sorted(["f/noise=abc", "f/noise=20", "f"], key=group_sort_key) == ["f", "f/noise=20", "f/noise=abc"]
