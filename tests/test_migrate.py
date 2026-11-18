from pathlib import Path

import pytest

from harness.config import EvalConfig
from harness.generate.config import load_gen_config
from harness.generate.pipeline import save_draft
from harness.review import blocking, set_status
from harness.scenario.labels import fingerprint_state, label_fingerprint
from harness.scenario.load import load_scenario
from harness.scenario.migrate import REVIEW_NOTE, overturn_option_b
from harness.scenario.model import Review
from harness.scenario.validate import validate_scenario
from tests.factories import T, ev, fact, probe, scenario


def old_shape(notes="ok", **over):
    facts = {
        "f_old": fact(valid_until=T("10-09 00:00"), markers=["do not modify"]),
        "f_new": fact(valid_from=T("10-09 00:00"), markers=["merged directly"]),
    }
    events = [
        ev("e1", T("10-01 10:00"), fact="f_old"),
        ev("e2", T("10-02 10:00"), fact="f_old"),
        ev(
            "e3",
            T("10-09 14:00"),
            user="u2",
            session="s2",
            signal="human",
            kind="human_confirm",
            outcome="success",
            fact="f_new",
            refs=["e1", "e2"],
            text="Changes are merged directly now.",
            meta={"k": "v"},
            scope="personal",
            team="a",
        ),
        ev("e4", T("10-10 10:00"), fact="f_new"),
    ]
    review = Review(status="approved", reviewer="x", notes=notes, approved_hash="h", check_config_hash="c")
    kw = dict(
        facts=facts,
        events=events,
        probes=[
            probe("p_block", "block", T("10-14 10:00"), ["f_old", "f_new"]),
            probe("p_promote", "promote", T("10-14 11:00"), ["f_new"]),
        ],
        review=review,
    )
    kw.update(over)
    return scenario(**kw)


def verified(s):
    prov = s.provenance.model_copy(update={"label_fingerprint": label_fingerprint(s)})
    return s.model_copy(update={"provenance": prov})


def test_overturn_splits_the_success_confirmation_into_two_events():
    s = old_shape()
    out = overturn_option_b(s)
    assert [e.id for e in out.events] == ["e1", "e2", "e3", "e5", "e4"]
    c, r = out.events[2], out.events[3]
    assert (c.id, c.t, c.outcome, c.fact, c.refs, c.kind, c.signal) == (
        "e3", s.events[2].t, "failure", None, ["e1", "e2"], "human_confirm", "human")
    assert c.text == "Review on e1, e2: please don't follow these steps; see my follow-up note."
    assert r.t == c.t + (T("10-09 14:30") - T("10-09 14:00"))
    assert (r.user, r.session, r.repo, r.scope, r.team, r.meta) == ("u2", "s2", "r", "personal", "a", {"k": "v"})
    assert (r.kind, r.signal, r.outcome, r.refs, r.fact) == ("agent_record", "human", "unknown", [], "f_new")
    assert out.events[0] == s.events[0] and out.events[4] == s.events[3]
    assert out.probes == s.probes and out.facts == s.facts


def test_overturn_keeps_the_reviewed_text_on_the_new_record():
    s = old_shape()
    assert overturn_option_b(s).events[3].text == s.events[2].text


def test_overturn_failure_text_has_no_fact_marker():
    out = overturn_option_b(old_shape())
    assert [i for i in validate_scenario(out) if i.level == "error"] == []
    assert "confirm_asserts_other_fact" not in {i.code for i in validate_scenario(out)}
    gen = load_gen_config(Path("config/gen-v0.1.yaml"))
    text = out.events[2].text.lower()
    assert not [w for w in gen.banned_cue_words if w.lower() in text]


def test_overturn_resets_the_review_to_draft():
    out = overturn_option_b(old_shape())
    rv = out.review
    assert (rv.status, rv.reviewer, rv.approved_hash, rv.check_config_hash) == ("draft", None, None, None)
    assert rv.notes.startswith("ok | ") and "#25" in rv.notes
    assert out.provenance.reviewer_edited is True


def test_overturn_refuses_a_file_without_exactly_one_old_confirmation():
    s = old_shape()
    none = s.model_copy(update={"events": [s.events[0], s.events[1], s.events[3]]})
    with pytest.raises(ValueError, match="scenario sc:"):
        overturn_option_b(none)
    twice = s.model_copy(update={"events": [*s.events, s.events[2].model_copy(update={"id": "e9", "t": T("10-11 10:00")})]})
    with pytest.raises(ValueError, match="scenario sc:"):
        overturn_option_b(twice)
    with pytest.raises(ValueError):
        overturn_option_b(overturn_option_b(s))


def test_overturn_refuses_when_the_new_record_would_break_time_order():
    s = old_shape()
    events = list(s.events)
    events[3] = events[3].model_copy(update={"t": T("10-09 14:10")})
    with pytest.raises(ValueError, match="after event e4"):
        overturn_option_b(s.model_copy(update={"events": events}))
    late = s.model_copy(update={"probes": [s.probes[0].model_copy(update={"t": T("10-09 14:20")}), s.probes[1]]})
    with pytest.raises(ValueError, match="probe"):
        overturn_option_b(late)
    early = s.model_copy(update={"probes": [s.probes[0], s.probes[1].model_copy(update={"t": T("10-05 10:00")})]})
    overturn_option_b(early)  # a probe before the confirmation is unaffected


def test_overturn_keeps_a_verified_fingerprint_verified():
    s = verified(old_shape())
    assert fingerprint_state(s) == "verified"
    out = overturn_option_b(s)
    assert fingerprint_state(out) == "verified"
    assert out.provenance.generated_hash == s.provenance.generated_hash


def test_overturn_refuses_a_file_whose_labels_changed_outside_review():
    s = verified(old_shape())
    prov = s.provenance.model_copy(update={"label_fingerprint": "l1:" + "0" * 64})
    with pytest.raises(ValueError, match="scenario sc:.*labels"):
        overturn_option_b(s.model_copy(update={"provenance": prov}))


def test_overturn_leaves_an_unverified_fingerprint_alone():
    out = overturn_option_b(old_shape())
    assert out.provenance.label_fingerprint is None


def approvable_old_shape():
    old = fact(text="use vendor v1", valid_until=T("10-09 00:00"), markers=["vendor v1"])
    new = fact(text="use vendor v2", valid_from=T("10-09 00:00"), markers=["vendor v2"])
    events = [
        ev(f"e{i}", T(f"10-0{i} 10:00"), fact="f_old",
           text="vendor module vendor v1 works for the build, vendor module v1 setup")
        for i in (1, 2, 3)
    ]
    events.append(ev("e4", T("10-09 14:00"), signal="human", kind="human_confirm", outcome="success",
                     fact="f_new", refs=["e1", "e2", "e3"], text="vendor v2 is required now"))
    probes = [
        probe("p_block", "block", T("10-14 10:00"), ["f_old", "f_new"], query="vendor module"),
        probe("p_promote", "promote", T("10-14 10:00"), ["f_new"], query="vendor v2"),
    ]
    return scenario({"f_old": old, "f_new": new}, events, probes)


def test_migrated_file_can_be_approved_again(tmp_path):
    out = overturn_option_b(verified(approvable_old_shape()))
    path = tmp_path / "m.yaml"
    save_draft(out, path)
    gen = load_gen_config(Path("config/gen-v0.1.yaml"))
    issues = set_status(path, "approved", "kim", None, EvalConfig(), gen)
    assert not blocking(issues), issues
    assert load_scenario(path).review.status == "approved"


def test_failure_text_joins_three_refs_with_commas():
    s = old_shape()
    extra = ev("e0", T("10-03 10:00"), fact="f_old")
    events = [s.events[0], s.events[1], extra, *s.events[2:]]
    events = [e.model_copy(update={"refs": ["e1", "e2", "e0"]}) if e.id == "e3" else e for e in events]
    out = overturn_option_b(s.model_copy(update={"events": events}))
    assert out.events[3].text == "Review on e1, e2, e0: please don't follow these steps; see my follow-up note."
    assert out.events[4].id == "e5"


def test_review_notes_are_the_bare_sentence_when_empty():
    out = overturn_option_b(old_shape(notes=""))
    assert out.review.notes == REVIEW_NOTE and not out.review.notes.startswith(" |")
    assert overturn_option_b(old_shape(notes="ok")).review.notes == f"ok | {REVIEW_NOTE}"


def test_next_id_is_max_plus_one_for_gappy_ids():
    s = old_shape()
    ren = {"e1": "e1", "e2": "e2", "e3": "e3", "e4": "e9"}
    s = s.model_copy(update={"events": [e.model_copy(update={"id": ren[e.id]}) for e in s.events]})
    assert [e.id for e in overturn_option_b(s).events] == ["e1", "e2", "e3", "e10", "e9"]


def test_overturn_refuses_a_confirmation_with_secret_markers():
    s = old_shape()
    events = [e.model_copy(update={"secret_markers": ["sek"]}) if e.id == "e3" else e for e in s.events]
    with pytest.raises(ValueError, match=s.id):
        overturn_option_b(s.model_copy(update={"events": events}))


def test_overturn_refuses_a_confirmation_listed_in_secret_events():
    s = old_shape()
    prov = s.provenance.model_copy(update={"secret_events": ["e3"]})
    with pytest.raises(ValueError, match=s.id):
        overturn_option_b(s.model_copy(update={"provenance": prov}))


def test_overturn_refuses_a_probe_exactly_at_the_new_record_time():
    s = old_shape()
    p = s.probes[0].model_copy(update={"t": T("10-09 14:30")})
    with pytest.raises(ValueError, match="probe"):
        overturn_option_b(s.model_copy(update={"probes": [p, s.probes[1]]}))
