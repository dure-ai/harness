"""Tests for the label fingerprint and the list of changed label fields."""

from datetime import timedelta

from harness.scenario.labels import changed_label_fields, label_fingerprint
from harness.scenario.model import Feedback, FeedbackTemplate, Scenario
from tests.factories import T, ev, fact, probe, scenario


def _scenario() -> Scenario:
    return scenario(
        {
            "f_old": fact(valid_until=T("10-05 09:00"), markers=["old way"]),
            "f_new": fact(valid_from=T("10-05 09:00"), markers=["new way"]),
        },
        [
            ev("e1", T("10-01 10:00"), fact="f_old"),
            ev("e2", T("10-06 10:00"), fact="f_new"),
            ev("e3", T("10-07 10:00"), signal="test", kind="ci_result", fact="f_new", refs=["e1"]),
        ],
        [probe("p_block", "block", T("10-08 10:00"), ["f_old"])],
    )


def test_fingerprint_ignores_text_changes() -> None:
    a = _scenario()
    b = a.model_copy(deep=True)
    for f in b.facts.values():
        f.text += " edited"
        f.markers = [*f.markers, "extra marker"]
    for e in b.events:
        e.text += " edited"
    for p in b.probes:
        p.query += " edited"
    b.review.notes = "looks fine"
    b.review.status = "approved"
    b.review.reviewer = "me"
    b.provenance.reviewer_edited = True
    assert label_fingerprint(a) == label_fingerprint(b)
    assert changed_label_fields(a, b) == []


def test_fingerprint_ignores_feedback_template_text() -> None:
    a = _scenario()
    a.probes[0].feedback = Feedback(abstain=FeedbackTemplate(text="one", fact=None))
    b = a.model_copy(deep=True)
    assert b.probes[0].feedback is not None and b.probes[0].feedback.abstain is not None
    b.probes[0].feedback.abstain.text = "two"
    assert label_fingerprint(a) == label_fingerprint(b)


def test_fingerprint_detects_label_changes() -> None:
    a = _scenario()
    b = a.model_copy(deep=True)
    b.facts["f_old"].valid_until = T("10-06 09:00")
    assert label_fingerprint(a) != label_fingerprint(b)
    assert changed_label_fields(a, b) == ["facts.f_old.valid_until"]


def test_changed_fields_cover_events_probes_and_feedback() -> None:
    a = _scenario()
    a.probes[0].feedback = Feedback(
        on_false=FeedbackTemplate(text="x", fact=None, delay=timedelta(minutes=5))
    )
    b = a.model_copy(deep=True)
    b.events[0].refs = ["other"]
    b.events[0].fact = None
    b.probes[0].about = []
    assert b.probes[0].feedback is not None and b.probes[0].feedback.on_false is not None
    b.probes[0].feedback.on_false.outcome = "failure"
    b.probes[0].feedback.on_false.delay = timedelta(minutes=9)
    b.provenance.fabricated_facts = ["f_x"]
    e, p = a.events[0].id, a.probes[0].id
    assert changed_label_fields(a, b) == sorted(
        [
            f"events.{e}.refs",
            f"events.{e}.fact",
            f"probes.{p}.about",
            f"probes.{p}.feedback.on_false.outcome",
            f"probes.{p}.feedback.on_false.delay",
            "provenance.fabricated_facts",
        ]
    )


def test_added_and_removed_slots_are_reported() -> None:
    a = _scenario()
    b = a.model_copy(deep=True)
    removed = b.events.pop()
    b.probes[0].feedback = Feedback(abstain=FeedbackTemplate(text="x"))
    assert changed_label_fields(a, b) == sorted(
        ["event_order", f"events.{removed.id}", f"probes.{a.probes[0].id}.feedback"]
    )


def test_swapping_same_timestamp_events_is_a_label_change() -> None:
    a = scenario(
        {"f": fact(markers=["m"])},
        [ev("e1", T("10-01 10:00"), fact="f"), ev("e2", T("10-01 10:00"), fact="f")],
        [probe("p", "promote", T("10-02 10:00"), ["f"])],
    )
    b = a.model_copy(update={"events": list(reversed(a.events))})
    assert label_fingerprint(a) != label_fingerprint(b)
    assert changed_label_fields(a, b) == ["event_order"]


def test_probe_order_and_language_are_label_fields() -> None:
    a = scenario(
        {"f": fact(markers=["m"])},
        [ev("e1", T("10-01 10:00"), fact="f")],
        [probe("p1", "promote", T("10-02 10:00"), ["f"]), probe("p2", "promote", T("10-02 10:00"), ["f"])],
    )
    b = a.model_copy(update={"probes": list(reversed(a.probes)), "language": "ko"})
    assert changed_label_fields(a, b) == ["language", "probe_order"]


def test_secret_markers_are_a_label_field() -> None:
    # They decide what counts as a leak, so a reviewer cannot change them (generation design 7.2).
    a = scenario(
        {"f": fact(markers=["m"])},
        [ev("e1", T("10-01 10:00"), fact="f", text="m with key k-1", secret_markers=["k-1"])],
        [probe("p", "promote", T("10-02 10:00"), ["f"])],
    )
    for edited in (["k-2"], [], ["k-1", "junk"]):
        b = a.model_copy(deep=True)
        b.events[0].secret_markers = edited
        assert label_fingerprint(a) != label_fingerprint(b), edited
        assert changed_label_fields(a, b) == ["events.e1.secret_markers"]


def test_recorded_secret_events_are_part_of_the_fingerprint() -> None:
    a = _scenario()
    a.provenance.secret_events = ["e1"]
    b = a.model_copy(deep=True)
    b.provenance.secret_events = []
    assert label_fingerprint(a) != label_fingerprint(b)
    assert changed_label_fields(a, b) == ["provenance.secret_events"]
