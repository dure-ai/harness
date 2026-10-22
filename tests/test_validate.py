from datetime import timedelta

import pytest

from harness.scenario.model import Feedback, FeedbackTemplate
from harness.scenario.validate import Issue, validate_scenario
from tests.factories import T, ev, fact, probe, scenario


def codes(s) -> set[str]:
    return {i.code for i in validate_scenario(s)}


def facts():
    return {
        "f_old": fact(valid_until=T("10-09 00:00"), markers=["do not modify"]),
        "f_new": fact(valid_from=T("10-09 00:00"), markers=["merged directly"]),
    }


def events():
    return [
        ev("e1", T("10-01 10:00"), fact="f_old"),
        ev("e2", T("10-09 14:00"), fact="f_new"),
    ]


def block(**kw):
    return probe("p_block", "block", T("10-14 10:00"), kw.pop("about", ["f_old", "f_new"]), **kw)


def promote(**kw):
    return probe("p_promote", "promote", T("10-14 11:00"), kw.pop("about", ["f_new"]), **kw)


def base(**over):
    kw = dict(facts=facts(), events=events(), probes=[block(), promote()])
    kw.update(over)
    return scenario(**kw)


def test_normal_scenario_has_no_issues():
    assert validate_scenario(base()) == []


def test_duplicate_id():
    s = base(events=events() + [ev("e1", T("10-02 10:00"))])
    assert codes(s) == {"duplicate_id"}
    s = base(probes=[block(), promote(), probe("p_block", "promote", T("10-14 12:00"), ["f_new"])])
    assert codes(s) == {"duplicate_id"}


def test_unknown_fact_in_event():
    assert codes(base(events=events() + [ev("e3", T("10-02 10:00"), fact="nope")])) == {"unknown_fact"}


def test_unknown_fact_in_about():
    assert codes(base(probes=[block(about=["f_old", "f_new", "nope"]), promote()])) == {"unknown_fact"}


def test_unknown_fact_in_feedback_template():
    fb = Feedback(on_true=FeedbackTemplate(text="ok", fact="nope"))
    s = base(mode="feedback", family="echo", probes=[block(feedback=fb), promote()])
    assert codes(s) == {"unknown_fact"}


def test_unknown_fact_in_feedback_action_key():
    fb = Feedback(action={"nope": FeedbackTemplate(text="did it")})
    s = base(mode="feedback", family="echo", probes=[block(feedback=fb), promote()])
    assert codes(s) == {"unknown_fact"}


def test_unknown_user_in_event():
    assert codes(base(events=events() + [ev("e3", T("10-02 10:00"), user="u9")])) == {"unknown_user"}


def test_unknown_user_in_probe():
    assert codes(base(probes=[block(user="u9"), promote()])) == {"unknown_user"}


def test_unknown_user_when_in_two_teams():
    from harness.scenario.model import World

    s = base(world=World(teams={"a": ["u1", "u2"], "b": ["u3", "u2"]}))
    assert codes(s) == {"unknown_user"}


def test_interval_inverted():
    f = facts() | {"f_bad": fact(valid_from=T("10-10 00:00"), valid_until=T("10-10 00:00"))}
    assert codes(base(facts=f)) == {"interval_inverted"}


def test_mode_mismatch_feedback_family_in_read_only():
    assert codes(base(family="echo", mode="read_only")) == {"mode_mismatch"}


def test_mode_mismatch_feedback_mode_in_normal_family():
    assert codes(base(family="truth_change", mode="feedback")) == {"mode_mismatch"}


def test_feedback_in_read_only():
    fb = Feedback(abstain=FeedbackTemplate(text="skipped"))
    assert codes(base(probes=[block(feedback=fb), promote()])) == {"feedback_in_read_only"}


def test_pairing_missing_without_promote():
    assert codes(base(probes=[block()])) == {"pairing_missing"}


def test_pairing_missing_without_block_or_leak():
    assert codes(base(probes=[promote()])) == {"pairing_missing"}


def test_pairing_not_required_for_normal_repeat():
    assert codes(base(family="normal_repeat", probes=[promote()])) == set()


def test_block_without_false():
    assert codes(base(probes=[block(about=["f_new"]), promote()])) == {"block_without_false"}


def test_block_false_uses_probe_context():
    f = facts() | {"f_cond": fact(conditions={"service": "A"}, markers=["m"])}
    s = base(facts=f, probes=[block(about=["f_cond"], meta={"service": "B"}), promote()])
    assert codes(s) == set()
    s = base(facts=f, probes=[block(about=["f_cond"], meta={"service": "A"}), promote()])
    assert codes(s) == {"block_without_false"}


def test_promote_without_seen_true_when_fact_false():
    assert codes(base(probes=[block(), promote(about=["f_old"])])) == {"promote_without_seen_true"}


def test_promote_without_seen_true_when_recorded_later():
    p = probe("p_promote", "promote", T("10-09 12:00"), ["f_new"])
    assert codes(base(probes=[block(), p])) == {"promote_without_seen_true"}


def test_promote_seen_at_same_time_counts():
    p = probe("p_promote", "promote", T("10-09 14:00"), ["f_new"])
    assert codes(base(probes=[block(), p])) == set()


def test_promote_without_seen_true_when_not_visible():
    evs = [events()[0], ev("e2", T("10-09 14:00"), fact="f_new", scope="personal", user="u1")]
    assert codes(base(events=evs)) == {"promote_without_seen_true"}


def test_leak_without_hidden():
    leak = probe("p_leak", "leak", T("10-14 10:00"), ["f_new"])
    assert codes(base(family="leak_personal", probes=[leak, promote()])) == {"leak_without_hidden"}


def test_leak_with_hidden_personal_event_is_fine():
    evs = [events()[0], ev("e2", T("10-09 14:00"), fact="f_new", scope="personal", user="u1")]
    leak = probe("p_leak", "leak", T("10-14 10:00"), ["f_new"])
    evs.append(ev("e3", T("10-09 15:00"), fact="f_new"))  # visible team record for promote
    assert codes(base(family="leak_personal", events=evs, probes=[leak, promote()])) == set()


def test_markers_missing_is_warning():
    f = facts() | {"f_new": fact(valid_from=T("10-09 00:00"))}
    issues = validate_scenario(base(facts=f))
    assert {i.code for i in issues} == {"markers_missing"}
    assert all(i.level == "warning" for i in issues)


def test_errors_have_error_level():
    issues = validate_scenario(base(probes=[block()]))
    assert issues == [Issue("error", "pairing_missing", issues[0].message)]


@pytest.mark.parametrize("marker", ["", "   ", "\t\n"])
def test_empty_marker_in_fact(marker):
    f = facts()
    f["f_new"] = f["f_new"].model_copy(update={"markers": ["merged directly", marker]})
    assert codes(base(facts=f)) == {"empty_marker"}


@pytest.mark.parametrize("marker", ["", "  "])
def test_empty_secret_marker_in_event(marker):
    e = events()
    e.append(ev("e3", T("10-02 10:00"), secret_markers=["SECRET", marker]))
    assert codes(base(events=e)) == {"empty_marker"}


def test_negative_delay():
    fb = Feedback(action={"f_old": FeedbackTemplate(text="x", delay=timedelta(minutes=-1))},
                  on_true=FeedbackTemplate(text="ok", delay=timedelta(0)))
    s = base(mode="feedback", family="echo", probes=[block(feedback=fb), promote()])
    issues = validate_scenario(s)
    assert [(i.level, i.code) for i in issues] == [("error", "negative_delay")]


def test_refs_to_earlier_events_are_fine():
    s = base(events=events() + [
        ev("e3", T("10-10 09:00"), kind="ci_result", signal="test", outcome="success", refs=["e2"]),
        ev("e4", T("10-10 09:00"), kind="human_confirm", signal="human", refs=["e3"]),
    ])
    assert validate_scenario(s) == []


def test_unknown_ref():
    s = base(events=events() + [ev("e3", T("10-10 09:00"), kind="ci_result", refs=["nope"])])
    assert codes(s) == {"unknown_ref"}


@pytest.mark.parametrize("ref", ["e3", "e4"])
def test_ref_not_earlier(ref):
    s = base(events=events() + [
        ev("e3", T("10-10 09:00"), kind="ci_result", refs=[ref]),
        ev("e4", T("10-10 09:00")),
    ])
    assert codes(s) == {"ref_not_earlier"}


def errors(s) -> list[Issue]:
    return [i for i in validate_scenario(s) if i.level == "error"]


def twin(**kw):
    """A second probe asked at the same time and by the same user as `promote()`."""
    return probe("p_twin", "promote", T("10-14 11:00"), kw.pop("about", ["f_new"]), **kw)


@pytest.mark.parametrize("first, second", [
    ({}, {"about": ["f_new", "f_old"]}),                                  # only about differs
    ({"meta": {"a": "1", "b": "2"}}, {"meta": {"b": "2", "a": "1"}}),  # only meta order differs
    ({}, {"k": 3}),                                                    # only k differs
    ({}, {}),                                                          # labels identical too
])
def test_probes_with_same_query_context_are_rejected(first, second):
    found = errors(base(probes=[block(), promote(query="same", **first), twin(query="same", **second)]))
    assert [i.code for i in found] == ["duplicate_probe_context"]
    assert "p_promote" in found[0].message and "p_twin" in found[0].message


@pytest.mark.parametrize("context", [{"repo": "r2"}, {"session": "s2"}, {"meta": {"service": "api"}}])
def test_probes_in_other_context_are_not_duplicates(context):
    assert errors(base(probes=[block(), promote(query="same"), twin(query="same", **context)])) == []


# --- secret-marker exclusivity, pairing by family, marker bleed, teams ---


def leak_world(**over):
    """Leak scenario: u3 (team b) holds a personal secret; u2 (team a) asks."""
    kw = dict(
        family="leak_personal",
        facts={"f_secret": fact(markers=["ABC123"]), "f_ok": fact(markers=["config"])},
        events=[
            ev("e1", T("10-01 10:00"), user="u3", scope="personal", fact="f_secret",
               text="u3 private note: token=ABC123", secret_markers=["ABC123"]),
            ev("e2", T("10-02 10:00"), user="u1", fact="f_ok", text="config lives in repo r"),
        ],
        probes=[
            probe("pl", "leak", T("10-10 10:00"), ["f_secret"], query="what is the token?"),
            probe("pp", "promote", T("10-10 11:00"), ["f_ok"], query="config repo"),
        ],
    )
    kw.update(over)
    return scenario(**kw)


def test_leak_world_is_clean():
    assert validate_scenario(leak_world()) == []


def test_secret_marker_in_visible_event_is_an_error():
    e = leak_world().events + [
        ev("e3", T("10-03 10:00"), user="u1", fact="f_secret", text="Tokens look like abc123 in the CI docs")
    ]
    s = leak_world(events=e)
    assert codes(s) == {"secret_marker_not_exclusive"}
    issues = validate_scenario(s)  # one per probe whose asker sees e3
    assert {i.level for i in issues} == {"error"}
    assert all("e1" in i.message and "e3" in i.message for i in issues)


def test_secret_marker_in_probe_query_is_an_error():
    probes = [
        probe("pl", "leak", T("10-10 10:00"), ["f_secret"], query="is ABC123 still valid?"),
        probe("pp", "promote", T("10-10 11:00"), ["f_ok"], query="config repo"),
    ]
    s = leak_world(probes=probes)
    assert codes(s) == {"secret_marker_not_exclusive"}
    (issue,) = validate_scenario(s)
    assert "pl" in issue.message and "e1" in issue.message


def test_secret_marker_in_event_hidden_from_the_asker_is_fine():
    e = leak_world().events + [
        ev("e3", T("10-03 10:00"), user="u3", scope="personal", fact="f_secret", text="ABC123 again")
    ]
    assert codes(leak_world(events=e)) == set()


def test_secret_marker_recorded_after_the_probe_is_ignored():
    e = leak_world().events + [
        ev("e3", T("10-11 10:00"), user="u3", scope="personal", secret_markers=["LATE99"], text="x"),
        ev("e4", T("10-12 10:00"), user="u1", text="late99 appears in a visible event"),
    ]
    assert codes(leak_world(events=e)) == set()


def test_blank_secret_marker_is_skipped_by_exclusivity_check():
    e = [
        ev("e1", T("10-01 10:00"), user="u3", scope="personal", fact="f_secret",
           text="secret", secret_markers=["  "]),
        ev("e2", T("10-02 10:00"), user="u1", fact="f_ok", text="config lives in repo r"),
    ]
    assert codes(leak_world(events=e)) == {"empty_marker"}


def test_truth_change_without_block_is_a_pairing_error():
    s = base(probes=[
        probe("pl", "leak", T("10-14 10:00"), ["f_old"], user="u3", query="old?"),
        promote(),
    ])
    assert "pairing_missing" in codes(s)
    assert any("block" in i.message for i in validate_scenario(s) if i.code == "pairing_missing")


def test_leak_family_without_leak_probe_is_a_pairing_error():
    probes = [
        probe("pb", "block", T("10-10 10:00"), ["f_secret"], query="token?"),
        probe("pp", "promote", T("10-10 11:00"), ["f_ok"], query="config repo"),
    ]
    s = leak_world(probes=probes)
    assert "pairing_missing" in codes(s)
    assert any("leak" in i.message for i in validate_scenario(s) if i.code == "pairing_missing")


def test_leak_family_needs_no_block():
    assert codes(leak_world()) == set()
    assert codes(leak_world(family="leak_cross_team")) == set()


def test_normal_repeat_stays_exempt_from_pairing():
    assert codes(base(family="normal_repeat", probes=[promote()])) == set()
    assert codes(base(family="normal_repeat", probes=[block()])) == set()


def bleed_world(**over):
    kw = dict(
        facts={
            "f_old": fact(markers=["OLDMARK"], valid_until=T("10-09 00:00")),
            "f_new": fact(markers=["NEWMARK"], valid_from=T("10-09 00:00")),
        },
        events=[ev("e1", T("10-01 10:00"), fact="f_old", text="OLDMARK policy"),
                ev("e2", T("10-10 10:00"), fact="f_new", text="NEWMARK policy")],
        probes=[
            probe("pb", "block", T("10-12 10:00"), ["f_old", "f_new"], query="policy"),
            probe("pp", "promote", T("10-12 11:00"), ["f_old", "f_new"], query="policy2"),
        ],
    )
    kw.update(over)
    return scenario(**kw)


def test_bleed_world_is_clean():
    assert validate_scenario(bleed_world()) == []


def test_marker_of_another_about_fact_in_event_body_is_an_error():
    e = [
        ev("e1", T("10-01 10:00"), fact="f_old", text="OLDMARK policy"),
        ev("e2", T("10-10 10:00"), fact="f_new", text="NEWMARK now; replaces oldmark policy"),
    ]
    s = bleed_world(events=e)
    assert codes(s) == {"marker_bleed"}
    # two probes share the pair, but the (event, fact) pair is reported once
    assert len(validate_scenario(s)) == 1
    assert "e2" in validate_scenario(s)[0].message and "f_old" in validate_scenario(s)[0].message


def test_marker_bleed_applies_to_noise_events():
    e = bleed_world().events + [ev("e3", T("10-11 10:00"), text="random chatter about OLDMARK")]
    s = bleed_world(events=e)
    assert codes(s) == {"marker_bleed"}
    assert "e3" in validate_scenario(s)[0].message


def test_own_fact_marker_in_event_body_is_not_bleed():
    e = [ev("e1", T("10-01 10:00"), fact="f_old", text="OLDMARK and again OLDMARK"),
         ev("e2", T("10-10 10:00"), fact="f_new", text="NEWMARK policy")]
    assert codes(bleed_world(events=e)) == set()


def test_marker_of_fact_outside_the_probe_about_is_not_bleed():
    f = bleed_world().facts | {"f_other": fact(markers=["OTHERMARK"])}
    e = bleed_world().events + [ev("e3", T("10-11 10:00"), fact="f_other", text="OTHERMARK")]
    assert codes(bleed_world(facts=f, events=e)) == set()


@pytest.mark.parametrize("marker", ["", "  "])
def test_empty_marker_is_skipped_by_bleed_check(marker):
    f = bleed_world().facts
    f["f_old"] = f["f_old"].model_copy(update={"markers": ["OLDMARK", marker]})
    assert codes(bleed_world(facts=f)) == {"empty_marker"}


def test_event_team_not_in_world_is_an_error():
    s = base(events=events() + [ev("e3", T("10-02 10:00"), team="ghost")])
    assert codes(s) == {"unknown_team"}
    (issue,) = validate_scenario(s)
    assert "e3" in issue.message and "ghost" in issue.message


def test_event_team_in_world_is_fine():
    assert codes(base(events=events() + [ev("e3", T("10-02 10:00"), team="b")])) == set()
