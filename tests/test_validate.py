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
