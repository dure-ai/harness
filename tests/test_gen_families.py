"""Tests for the family templates, the body slots and the fake body writer."""

import json
from pathlib import Path
from typing import Any

import pytest

from harness.config import EvalConfig
from harness.generate.candidates import CandidateList, FactCandidate
from harness.generate.config import GenConfig
from harness.generate.families import TEMPLATES, SkeletonBuilder, fits, why_not
from harness.generate.llm import make_client
from harness.generate.slots import (
    BodySlots,
    Skeleton,
    apply_slots,
    fake_slots,
    slots_for,
    slots_prompt_block,
)
from harness.scenario.model import Feedback, FeedbackTemplate, Scenario
from harness.scenario.validate import validate_scenario
from harness.selfcheck import check_discrimination, run_selfcheck
from harness.world import probe_ctx, true_at
from tests.gen_fixtures import make_trace

READ_ONLY = [
    "normal_repeat",
    "coincidence",
    "single_source_poison",
    "truth_change",
    "conditional",
    "workaround_then_root",
]
SEED = 20261020


def _cand(**over: Any) -> FactCandidate:
    data: dict[str, Any] = {
        "statement": "Unit tests run with `pytest -x tests/unit`.",
        "kind": "test",
        "quote": "pytest -x tests/unit",
        "message_index": 2,
        "fix": "workaround",
        "index": 1,
    }
    data.update(over)
    return FactCandidate(**data)


def _traces() -> list:
    return [
        make_trace(),
        make_trace(trajectory_id="traj-2", model="model-b"),
    ]


def _skeleton(family: str, cand: FactCandidate | None = None, seed: int = SEED, traces=None) -> Skeleton:
    sk = TEMPLATES[family](cand or _cand(), _traces() if traces is None else traces, seed)
    assert sk is not None
    return sk


def _filled(sk: Skeleton, cand: FactCandidate | None = None) -> Scenario:
    spec = slots_for(sk, cand or _cand())
    return apply_slots(sk, BodySlots(**fake_slots("", slots_prompt_block(spec))))


def _errors(issues) -> list:
    return [i for i in issues if i.level == "error"]


def _events_of(s: Scenario, fact: str) -> list:
    return [e for e in s.events if e.fact == fact]


def _gen_cfg(**over: Any) -> GenConfig:
    data: dict[str, Any] = {
        "provider": "fake",
        "model": "fake-1",
        "prompt_version": "gen-v0.1",
        "license_allowlist": ["MIT"],
        "banned_cue_words": ["outdated"],
    }
    data.update(over)
    return GenConfig.model_validate(data)


@pytest.mark.parametrize("family", READ_ONLY)
def test_skeleton_matches_family_row(family: str) -> None:
    s = _skeleton(family).scenario
    assert s.family == family
    assert s.mode == "read_only"
    kinds = [p.kind for p in s.probes]
    if family == "normal_repeat":
        assert set(kinds) == {"promote"}
    else:
        assert "block" in kinds and "promote" in kinds
    assert all(p.feedback is None for p in s.probes)
    # Text fields are left for the body writer.
    assert all(f.text == "" and f.markers == [] for f in s.facts.values())
    assert all(e.text == "" and e.secret_markers == [] for e in s.events)
    assert all(p.query == "" for p in s.probes)
    assert s.id.startswith(f"{family}-widget-")

    facts = s.facts
    if family == "normal_repeat":
        (fid,) = facts
        obs = _events_of(s, fid)
        assert len(obs) >= 3
        assert len({e.user for e in obs}) == len(obs)
        assert len({e.session for e in obs}) == len(obs)
        assert len({e.t.date() for e in obs}) == len(obs)
        assert any(e.signal in ("test", "review", "human") for e in obs)
        assert facts[fid].valid and facts[fid].valid_from is None and facts[fid].valid_until is None
    elif family == "coincidence":
        assert facts["f_false"].valid is False
        assert facts["f_true"].valid is True
        burst = _events_of(s, "f_false")
        assert len(burst) >= 3
        assert len({(e.user, e.session, e.t.date()) for e in burst}) == 1
        assert {e.signal for e in burst} == {"self"}
        true = _events_of(s, "f_true")
        assert len({e.user for e in true}) >= 2 and len({e.session for e in true}) >= 2
        assert not {e.user for e in true} & {e.user for e in burst}
    elif family == "single_source_poison":
        assert facts["f_false"].valid is False
        poison = _events_of(s, "f_false")
        assert len(poison) >= 3
        assert len({e.user for e in poison}) == 1
        assert len({e.session for e in poison}) == len(poison)
        assert len({e.t.date() for e in poison}) == len(poison)
        assert {e.signal for e in poison} == {"self"}
        true = _events_of(s, "f_true")
        users = {e.user for e in true}
        assert len(users) >= 2 and not users & {e.user for e in poison}
    elif family == "truth_change":
        d = facts["f_old"].valid_until
        assert d is not None and facts["f_new"].valid_from == d
        assert all(e.t < d for e in _events_of(s, "f_old"))
        assert all(e.t >= d for e in _events_of(s, "f_new"))
        assert any(e.signal != "self" for e in _events_of(s, "f_new"))
        block = next(p for p in s.probes if p.kind == "block")
        promote = next(p for p in s.probes if p.kind == "promote")
        assert block.t >= d and set(block.about) == {"f_old", "f_new"}
        assert promote.t < d and set(promote.about) == {"f_old", "f_new"}
    elif family == "conditional":
        a, b = facts["f_a"].conditions, facts["f_b"].conditions
        assert a == {"service": "widget"} and b and b != a
        block = next(p for p in s.probes if p.kind == "block")
        promote = next(p for p in s.probes if p.kind == "promote")
        assert not true_at(facts["f_a"], block.t, probe_ctx(block))
        assert true_at(facts["f_b"], block.t, probe_ctx(block))
        assert true_at(facts["f_a"], promote.t, probe_ctx(promote))
    elif family == "workaround_then_root":
        wa, root = facts["f_workaround"], facts["f_root"]
        assert (wa.fix, root.fix) == ("workaround", "root")
        r = wa.valid_until
        assert r is not None and root.valid_from == r
        assert len([e for e in _events_of(s, "f_workaround") if e.t < r]) >= 2
        assert all(e.t >= r for e in _events_of(s, "f_root"))
        block = next(p for p in s.probes if p.kind == "block")
        promote = next(p for p in s.probes if p.kind == "promote")
        assert block.t >= r and promote.t < r


@pytest.mark.parametrize("family", READ_ONLY)
def test_filled_skeleton_passes_validation(family: str) -> None:
    s = _filled(_skeleton(family))
    assert validate_scenario(s) == []


@pytest.mark.parametrize("family", READ_ONLY)
def test_filled_skeleton_discriminates(family: str) -> None:
    s = _filled(_skeleton(family))
    assert check_discrimination(s, EvalConfig()) == []  # no errors, and not too easy either


@pytest.mark.parametrize("family", READ_ONLY)
def test_filled_skeleton_passes_for_other_seeds(family: str) -> None:
    for seed in range(5):
        s = _filled(_skeleton(family, seed=seed))
        assert validate_scenario(s) == [], seed
        assert check_discrimination(s, EvalConfig()) == [], seed


def test_filled_set_passes_selfcheck() -> None:
    scenarios = [_filled(_skeleton(f, seed=seed)) for f in READ_ONLY for seed in range(3)]
    checks, _ = run_selfcheck(scenarios, EvalConfig())
    assert [c.name for c in checks if not c.ok] == []


@pytest.mark.parametrize("family", READ_ONLY)
def test_same_seed_same_skeleton(family: str) -> None:
    a, b = _skeleton(family), _skeleton(family)
    assert a.scenario.model_dump() == b.scenario.model_dump()
    assert a.scenario.id == b.scenario.id
    c = _skeleton(family, seed=SEED + 1)
    assert c.scenario.id != a.scenario.id
    assert c.scenario.model_dump(exclude={"id"}) != a.scenario.model_dump(exclude={"id"})


@pytest.mark.parametrize("family", READ_ONLY)
def test_template_works_without_traces(family: str) -> None:
    sk = _skeleton(family, traces=[])
    s = sk.scenario
    assert s.id.startswith(f"{family}-manual-")
    assert {e.repo for e in s.events} == {"manual"}
    assert s.provenance.source_traces == [] and s.provenance.source_repos == []
    assert validate_scenario(_filled(sk)) == []


def test_provenance_lists_bundle_traces_and_repos() -> None:
    p = _skeleton("truth_change").scenario.provenance
    assert p.source_traces == ["traj-1", "traj-2"]
    assert [(r.repo, r.license) for r in p.source_repos] == [("acme/widget", "MIT")]


def test_scenario_id_rule() -> None:
    import hashlib

    s = _skeleton("coincidence", traces=[make_trace(repo="Acme/My_Repo.py")]).scenario
    h = hashlib.sha256(f"traj-1:1:coincidence:{SEED}".encode()).hexdigest()[:6]
    assert s.id == f"coincidence-my-repo-py-{h}"


def test_judging_events_have_refs_and_true_fact() -> None:
    s = _skeleton("truth_change").scenario
    old_ids = {e.id for e in _events_of(s, "f_old")}
    confirms = [e for e in s.events if e.kind == "human_confirm"]
    assert confirms
    for e in confirms:
        assert e.fact == "f_new"
        assert e.refs and set(e.refs) <= old_ids
    # Standalone evidence and plain records carry no refs.
    assert all(not e.refs for e in s.events if e.kind == "agent_record")


@pytest.mark.parametrize("family", READ_ONLY)
def test_refs_point_to_earlier_events_of_the_judged_fact(family: str) -> None:
    s = _skeleton(family).scenario
    by_id = {e.id: e for e in s.events}
    for e in s.events:
        for r in e.refs:
            assert by_id[r].t < e.t
        if e.refs:
            assert e.kind in ("ci_result", "human_confirm")
            assert e.fact is not None


def test_workaround_needs_fix_label() -> None:
    cand = _cand(fix=None)
    assert TEMPLATES["workaround_then_root"](cand, _traces(), SEED) is None
    assert not fits("workaround_then_root", cand)
    reason = why_not("workaround_then_root", cand)
    assert reason is not None and "fix" in reason
    assert fits("truth_change", cand) and why_not("truth_change", cand) is None


def test_workaround_root_candidate_flips_roles() -> None:
    s = _skeleton("workaround_then_root", _cand(fix="root")).scenario
    assert s.provenance.fabricated_facts == ["f_workaround"]
    s2 = _skeleton("workaround_then_root", _cand(fix="workaround")).scenario
    assert s2.provenance.fabricated_facts == ["f_root"]


def test_conditional_uses_candidate_condition() -> None:
    cand = _cand(condition={"service": "api"})
    s = _skeleton("conditional", cand).scenario
    assert s.facts["f_a"].conditions == {"service": "api"}
    assert s.facts["f_b"].conditions.keys() == {"service"}
    assert s.facts["f_b"].conditions["service"] != "api"
    assert validate_scenario(_filled(_skeleton("conditional", cand), cand)) == []


@pytest.mark.parametrize(
    ("family", "fabricated"),
    [
        ("normal_repeat", []),
        ("coincidence", ["f_false"]),
        ("single_source_poison", ["f_false"]),
        ("truth_change", ["f_new"]),
        ("conditional", ["f_b"]),
        ("workaround_then_root", ["f_root"]),
    ],
)
def test_fabricated_facts_recorded(family: str, fabricated: list[str]) -> None:
    assert _skeleton(family).scenario.provenance.fabricated_facts == fabricated


def test_slots_for_carries_labels() -> None:
    cand = _cand()
    spec = slots_for(_skeleton("truth_change"), cand)
    assert spec.facts["f_old"].statement == cand.statement
    assert spec.facts["f_new"].fabricated and spec.facts["f_new"].statement is None
    assert spec.facts["f_old"].valid_until is not None
    block = next(p for p in spec.probes if p.kind == "block")
    assert block.true_facts == ["f_new"]
    block_text = slots_prompt_block(spec)
    assert block_text.startswith("<<<SLOTS\n") and block_text.endswith("\nSLOTS>>>")
    assert "valid_until" in block_text


def test_apply_slots_rejects_key_mismatch() -> None:
    sk = _skeleton("truth_change")
    good = fake_slots("", slots_prompt_block(slots_for(sk, _cand())))
    for part in ("facts", "events", "probes"):
        missing = json.loads(json.dumps(good))
        missing[part].pop(next(iter(missing[part])))
        with pytest.raises(ValueError, match=part):
            apply_slots(sk, BodySlots(**missing))
    extra = json.loads(json.dumps(good))
    extra["events"]["e99"] = {"text": "x", "origin": "written"}
    with pytest.raises(ValueError, match="e99"):
        apply_slots(sk, BodySlots(**extra))


def test_apply_slots_rejects_secret_markers_outside_secret_events() -> None:
    sk = _skeleton("truth_change")
    data = fake_slots("", slots_prompt_block(slots_for(sk, _cand())))
    first = next(iter(data["events"]))
    data["events"][first]["secret_markers"] = ["hunter2"]
    with pytest.raises(ValueError, match="secret"):
        apply_slots(sk, BodySlots(**data))


def test_apply_slots_fills_only_text_fields() -> None:
    sk = _skeleton("conditional")
    s = _filled(sk)
    blank = s.model_dump()
    for f in blank["facts"].values():
        f["text"], f["markers"] = "", []
    for e in blank["events"]:
        e["text"], e["secret_markers"] = "", []
    for p in blank["probes"]:
        p["query"] = ""
    assert blank == sk.scenario.model_dump()
    assert all(e.text for e in s.events) and all(p.query for p in s.probes)


def test_fake_slots_needs_a_slots_block() -> None:
    with pytest.raises(ValueError, match="SLOTS"):
        fake_slots("", "no block here")


def test_make_client_fake_uses_responders(tmp_path: Path) -> None:
    client = make_client(_gen_cfg(), tmp_path / "llm")
    sk = _skeleton("coincidence")
    user = slots_prompt_block(slots_for(sk, _cand()))
    got = client.complete_json("bodies", "system", user, BodySlots)
    assert got == BodySlots(**fake_slots("system", user))
    reply = client.complete_json(
        "candidates", "system", "Repository: acme/widget\n\n[#2 assistant] Looking at it.", CandidateList
    )
    assert len(reply.candidates) == 3



def test_fake_slots_fill_secret_and_feedback_slots() -> None:
    b = SkeletonBuilder("echo", _cand(), _traces(), SEED)
    owner, asker = b.users(2)
    b.fact("f_x")
    secret = b.event(b.at(b.next_day()), owner, "f_x", scope="personal")
    public = b.event(b.at(b.next_day()), owner, "f_x")
    b.probe("p1", "promote", b.at(b.next_day()), owner, ["f_x"])
    b.probes[0].feedback = Feedback(
        action={"f_x": FeedbackTemplate(text="", fact="f_x")},
        abstain=FeedbackTemplate(text=""),
        on_true=FeedbackTemplate(text="", fact="f_x", signal="test", kind="ci_result", outcome="success"),
    )
    sk = b.build(secret_event_ids=[secret])
    spec = slots_for(sk, _cand())
    assert set(spec.feedback) == {"p1.action:f_x", "p1.abstain", "p1.on_true"}
    s = apply_slots(sk, BodySlots(**fake_slots("", slots_prompt_block(spec))))
    events = {e.id: e for e in s.events}
    (mark,) = events[secret].secret_markers
    assert mark in events[secret].text
    assert events[public].secret_markers == [] and mark not in events[public].text
    fb = s.probes[0].feedback
    assert fb is not None and fb.on_true is not None and fb.abstain is not None
    marker = s.facts["f_x"].markers[0]
    assert marker in fb.action["f_x"].text and marker in fb.on_true.text
    assert marker not in fb.abstain.text
    assert asker in s.world.teams["team-a"]
