from datetime import timedelta
from pathlib import Path

import pytest

from harness.bench.check import check_conversion
from harness.config import EvalConfig
from harness.scenario.load import load_set
from harness.scenario.model import Scenario
from harness.scenario.validate import validate_scenario
from harness.stress.noise import NoiseText
from harness.stress.variant import GAPS, VariantError, add_noise, make_variant, widen_gap

DEV = Path("scenarios/dev")


def dev() -> list[Scenario]:
    return load_set([DEV], include_drafts=False).scenarios


def one(family: str) -> Scenario:
    return next(s for s in dev() if s.family == family)


def pool(n: int = 400) -> list[NoiseText]:
    return [NoiseText(f"t{i % 7}", f"ran the linter on module_{i} and looked at the import order of package {i}") for i in range(n)]


def errors(s: Scenario) -> list[str]:
    return [i.code for i in validate_scenario(s) if i.level == "error"]


def first_probe(s: Scenario):
    return min(p.t for p in s.probes)


def test_noise_adds_unlabeled_visible_events_before_the_first_probe():
    s = one("truth_change")
    v = add_noise(s, pool(), 20, "middle", seed=1)
    noise = [e for e in v.events if e.id not in {x.id for x in s.events}]
    assert len(noise) == 20
    assert all(e.fact is None and not e.secret_markers and e.scope == "team" for e in noise)
    team = s.team_of(s.probes[0].user)
    assert all(s.team_of(e.user) == team for e in noise)
    assert all(e.t < first_probe(s) for e in noise)
    assert len({e.id for e in v.events}) == len(v.events)
    assert v.facts == s.facts and v.probes == s.probes  # labels untouched
    assert errors(v) == []
    assert check_conversion(v, EvalConfig()) is None  # the oracle still answers everything


def test_position_places_noise_after_before_or_around_the_events():
    s = one("coincidence")
    first_event = min(e.t for e in s.events)
    last_event = max(e.t for e in s.events if e.t < first_probe(s))
    ids = {e.id for e in s.events}

    def noise_times(position):
        v = add_noise(s, pool(), 10, position, seed=3)
        return [e.t for e in v.events if e.id not in ids]

    assert all(last_event < t < first_probe(s) for t in noise_times("early"))  # evidence comes first
    assert all(t < first_event for t in noise_times("late"))  # evidence is the most recent
    middle = noise_times("middle")
    assert any(t < first_event for t in middle) and any(t > last_event for t in middle)


def test_noise_is_deterministic_for_a_seed_and_differs_across_seeds():
    s = one("conditional")
    assert add_noise(s, pool(), 20, "middle", seed=5) == add_noise(s, pool(), 20, "middle", seed=5)
    assert add_noise(s, pool(), 20, "middle", seed=5) != add_noise(s, pool(), 20, "middle", seed=6)


def test_noise_never_carries_a_fact_marker_or_a_source_trace():
    s = one("truth_change")
    marker = next(m for f in s.facts.values() for m in f.markers)
    texts = [NoiseText("bad", f"note: {marker} is what we use"), *pool(50)]
    if s.provenance.source_traces:
        texts.append(NoiseText(s.provenance.source_traces[0], "a perfectly harmless line of text from the same trace"))
    v = make_variant(s, "noise", "50", texts, seed=2)
    assert all(marker.casefold() not in e.text.casefold() for e in v.events if e.fact is None)
    assert v.provenance.variant is not None
    used = set(v.provenance.variant.noise_traces)
    assert "bad" not in used and not used & set(s.provenance.source_traces)


def test_too_few_usable_texts_is_an_error():
    with pytest.raises(VariantError, match="noise"):
        add_noise(one("conditional"), pool(5), 20, "middle", seed=1)


def test_gap_moves_the_probes_and_later_history_but_keeps_the_labels():
    for family in ("truth_change", "echo", "single_source_poison"):
        s = one(family)
        v = widen_gap(s, GAPS["30d"])
        p0 = first_probe(s)
        before = max(e.t for e in s.events if e.t < p0)
        assert first_probe(v) - before == (p0 - before) + timedelta(days=30)
        assert [p.t - q.t for p, q in zip(v.probes, s.probes, strict=True)] == [timedelta(days=30)] * len(s.probes)
        assert errors(v) == [], family
        assert check_conversion(v, EvalConfig()) is None, family


def test_variant_records_its_origin_and_becomes_a_draft():
    s = one("coincidence")
    v = make_variant(s, "noise", "20", pool(), seed=9)
    assert v.id == f"{s.id}--noise-20"
    assert v.review.status == "draft" and v.review.reviewer is None
    assert v.provenance.variant is not None
    assert (v.provenance.variant.base, v.provenance.variant.knob, v.provenance.variant.value) == (s.id, "noise", "20")
    assert len(v.provenance.variant.noise_traces) == 20
    assert make_variant(s, "gap", "1d", pool(), seed=9).provenance.variant.noise_traces == []


def test_every_dev_scenario_survives_every_default_variant():
    for s in dev():
        for knob, value in (("noise", "300"), ("position", "late"), ("position", "early"), ("gap", "30d")):
            v = make_variant(s, knob, value, pool(), seed=0)
            assert errors(v) == [], (s.id, knob, value)
            assert check_conversion(v, EvalConfig()) is None, (s.id, knob, value)
