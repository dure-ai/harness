from collections import Counter
from datetime import UTC, datetime

from harness.bench.datasets.longmemeval import LongMemEvalS, convert_question, load_ku_labels, parse_lme_date
from harness.scenario.model import Scenario
from harness.scenario.validate import validate_scenario
from tests.bench.lme_fixture import lme_question, write_lme

QDATE = "2023/07/01 (Sat) 10:00"


def ku(qid: str, old_text: str = "I use Vim", new_text: str = "I switched to Emacs") -> dict:
    return lme_question(
        qid,
        "knowledge-update",
        [
            ("2023/05/20 (Sat) 02:21", [("user", old_text), ("assistant", "ok")]),
            ("2023/06/20 (Tue) 08:00", [("user", new_text), ("assistant", "noted")]),
        ],
        [0, 1],
        QDATE,
    )


def first_event_time(s: Scenario, fact: str) -> datetime:
    return min(e.t for e in s.events if e.fact == fact)


def errors(s: Scenario) -> list:
    return [i for i in validate_scenario(s) if i.level == "error"]


def test_knowledge_update_new_value_becomes_a_block_and_promote_pair():
    c = convert_question(ku("q1", old_text="I use Vim", new_text="I switched to Emacs"), {"q1": "new"})
    s = c.scenario
    assert s is not None
    assert s.family == "ext_update" and c.group == "knowledge-update"
    assert s.id == "lme-q1" and s.mode == "read_only" and s.language == "en"
    assert [(p.id, p.kind, p.session, p.about) for p in s.probes] == [
        ("p-block", "block", "sess-2", ["f0", "f1"]),
        ("p-promote", "promote", "sess-3", ["f1"]),
    ]
    assert s.facts["f0"].valid_until == first_event_time(s, "f1") and s.facts["f1"].valid_until is None
    assert s.facts["f0"].text == "I use Vim" and s.facts["f1"].text == "I switched to Emacs"
    assert not errors(s)


def test_knowledge_update_old_and_both_labels_are_recall_only():
    for label, about in (("old", ["f0"]), ("both", ["f0", "f1"])):
        s = convert_question(ku("q1"), {"q1": label}).scenario
        assert s is not None and s.family == "ext_recall"
        assert [(p.id, p.kind, p.session, p.about) for p in s.probes] == [("p-promote", "promote", "sess-2", about)]
        assert all(f.valid_until is None and f.valid_from is None for f in s.facts.values())
        assert not errors(s)


def test_other_types_promote_every_evidence_session():
    q = lme_question(
        "m1",
        "multi-session",
        [
            ("2023/05/01 (Mon) 09:00", [("user", "I bought a red bike")]),
            ("2023/05/10 (Wed) 09:00", [("user", "weather is nice")]),
            ("2023/05/20 (Sat) 09:00", [("user", "I bought a blue hat")]),
        ],
        [0, 2],
        QDATE,
    )
    c = convert_question(q, {})
    s = c.scenario
    assert s is not None and c.group == "multi-session" and s.family == "ext_recall"
    assert [p.about for p in s.probes] == [["f0", "f2"]]
    assert s.probes[0].t == datetime(2023, 7, 1, 10, 0, tzinfo=UTC)
    assert s.probes[0].user == "u1" and s.probes[0].repo == "longmemeval-s" and s.probes[0].query == "question of m1?"
    assert [e.fact for e in s.events] == ["f0", None, "f2"]
    assert s.provenance.author == "generator" and s.provenance.source_traces == ["m1"]
    assert s.world.teams == {"t1": ["u1", "assistant"]} and s.world.timezone == "UTC"
    assert not errors(s)


def test_fact_text_joins_evidence_turns_and_is_cut_to_300_chars():
    q = lme_question(
        "m2",
        "single-session-user",
        [("2023/05/01 (Mon) 09:00", [("user", "a" * 200), ("assistant", "middle"), ("user", "b" * 200)])],
        [0],
        QDATE,
        has_answer={0: [0, 2]},
    )
    s = convert_question(q, {}).scenario
    assert s is not None
    assert s.facts["f0"].text == ("a" * 200 + "\n" + "b" * 200)[:300]
    assert [e.fact for e in s.events] == ["f0", None, "f0"]


def test_questions_with_bad_evidence_are_skipped_with_a_reason():
    late = lme_question(
        "l1", "multi-session", [("2023/08/01 (Tue) 09:00", [("user", "x")])], [0], QDATE
    )
    assert convert_question(late, {}).skipped == "evidence session after the question"
    no_mark = lme_question(
        "n1", "multi-session", [("2023/05/01 (Mon) 09:00", [("user", "x")])], [0], QDATE, has_answer={0: []}
    )
    assert convert_question(no_mark, {}).skipped == "answer session without evidence turns"
    abstain = lme_question(
        "a1_abs", "single-session-user", [("2023/05/01 (Mon) 09:00", [("user", "x")])], [0], QDATE
    )
    c = convert_question(abstain, {})
    assert (c.scenario, c.skipped, c.source_id, c.group) == (None, "abstention", "a1_abs", "single-session-user")
    assert convert_question(ku("q1"), {"q1": "exclude"}).skipped == "excluded by label"
    assert convert_question(ku("q1"), {}).skipped == "no label"


def test_events_have_strictly_increasing_times_within_and_across_sessions():
    q = lme_question(
        "t1",
        "multi-session",
        [
            ("2023/05/01 (Mon) 09:00", [("user", f"turn {i}") for i in range(5)]),
            ("2023/05/01 (Mon) 09:00", [("user", "same date"), ("assistant", "again")]),
            ("2023/05/01 (Mon) 09:02", [("user", "overlaps the first session")]),
        ],
        [0, 1],
        QDATE,
    )
    s = convert_question(q, {}).scenario
    assert s is not None
    times = [e.t for e in s.events]
    assert all(a < b for a, b in zip(times, times[1:]))
    assert [e.id for e in s.events][:2] == ["s0-t0", "s0-t1"]
    assert times[0] == datetime(2023, 5, 1, 9, 0, tzinfo=UTC)
    assert not errors(s)


def test_noise_sessions_after_the_question_are_dropped():
    q = lme_question(
        "d1",
        "multi-session",
        [
            ("2023/05/01 (Mon) 09:00", [("user", "evidence")]),
            ("2023/09/01 (Fri) 09:00", [("user", "from the future")]),
        ],
        [0],
        QDATE,
    )
    s = convert_question(q, {}).scenario
    assert s is not None and [e.text for e in s.events] == ["evidence"]


def test_sessions_are_ordered_by_date():
    q = ku("q1")
    q["haystack_dates"].reverse()
    q["haystack_sessions"].reverse()
    q["haystack_session_ids"].reverse()
    s = convert_question(q, {"q1": "new"}).scenario
    assert s is not None
    assert s.facts["f0"].text == "I use Vim" and s.facts["f1"].text == "I switched to Emacs"


def test_assistant_turns_are_events_with_role_meta():
    s = convert_question(ku("q1"), {"q1": "new"}).scenario
    assert s is not None
    by_id = {e.id: e for e in s.events}
    a, u = by_id["s0-t1"], by_id["s0-t0"]
    assert (a.user, a.meta, a.text, a.session) == ("assistant", {"role": "assistant"}, "ok", "sess-0")
    assert (u.user, u.meta, u.fact) == ("u1", {"role": "user"}, "f0")
    assert (u.repo, u.scope, u.signal, u.kind, u.outcome) == ("longmemeval-s", "team", "self", "agent_record", "unknown")


def test_dates_are_utc():
    assert parse_lme_date("2023/05/20 (Sat) 02:21") == datetime(2023, 5, 20, 2, 21, tzinfo=UTC)


def test_label_file_has_72_ids_and_the_counts_from_the_spec():
    labels = load_ku_labels()
    assert len(labels) == 72 and Counter(labels.values()) == {"new": 58, "old": 9, "both": 2, "exclude": 3}


def test_dataset_declares_the_file_and_converts_from_the_cache(tmp_path):
    ds = LongMemEvalS()
    (f,) = ds.files()
    assert (f.name, f.size) == ("longmemeval_s_cleaned.json", 277383467)
    assert ds.name == "longmemeval-s" and ds.license == "MIT" and not ds.commit_ok
    write_lme(
        tmp_path / f.name,
        [
            lme_question(
                "m1", "multi-session", [("2023/05/01 (Mon) 09:00", [("user", "x")])], [0], QDATE
            ),
            lme_question(
                "m2", "single-session-user", [("2023/05/01 (Mon) 09:00", [("user", "y")])], [0], QDATE
            ),
        ],
    )
    got = list(ds.convert(tmp_path, types=None, limit=None))
    assert [c.source_id for c in got] == ["m1", "m2"]
    assert [c.source_id for c in ds.convert(tmp_path, types={"single-session-user"}, limit=None)] == ["m2"]
    assert [c.source_id for c in ds.convert(tmp_path, types=None, limit=1)] == ["m1"]


def test_knowledge_update_needs_exactly_two_evidence_sessions():
    three = lme_question(
        "k3",
        "knowledge-update",
        [
            ("2023/05/01 (Mon) 09:00", [("user", "I use Vim")]),
            ("2023/05/20 (Sat) 09:00", [("user", "I use Emacs")]),
            ("2023/06/20 (Tue) 09:00", [("user", "I use VS Code")]),
        ],
        [0, 1, 2],
        QDATE,
    )
    one = lme_question(
        "k1", "knowledge-update", [("2023/05/01 (Mon) 09:00", [("user", "I use Vim")])], [0], QDATE
    )
    for q in (three, one):
        for label in ("new", "old", "both"):
            c = convert_question(q, {q["question_id"]: label})
            assert (c.scenario, c.skipped) == (None, "knowledge-update without exactly two evidence sessions")


def test_no_engine_visible_probe_field_names_the_probe_or_family():
    from harness.scenario.model import Scenario

    scenarios: list[Scenario] = []
    for label in ("new", "old", "both"):
        s = convert_question(ku("q1"), {"q1": label}).scenario
        assert s is not None
        scenarios.append(s)
    s = convert_question(
        lme_question("m1", "multi-session", [("2023/05/01 (Mon) 09:00", [("user", "x")])], [0], QDATE), {}
    ).scenario
    assert s is not None
    scenarios.append(s)
    for s in scenarios:
        for probe in s.probes:
            q = s.query_for(probe, 5)
            visible = f"{q.session} {q.repo} {q.user} {q.team} {q.meta}".lower()
            for word in ("block", "promote", "q-", s.family, "ext_", "knowledge", "multi-session"):
                assert word not in visible, (s.id, probe.id, word)
        sessions = [p.session for p in s.probes]
        assert len(set(sessions)) == len(sessions)
        assert not {e.session for e in s.events} & set(sessions)  # the probe sessions are new ones


def test_turns_timed_at_or_after_the_question_are_dropped():
    sessions = [("2023/07/01 (Sat) 09:58", [("user", "hi"), ("assistant", "a"), ("user", "b"), ("assistant", "c")])]
    q = lme_question("late", "single-session-user", sessions, [0], QDATE)
    s = convert_question(q, {}).scenario
    assert s is not None
    assert [e.id for e in s.events] == ["s0-t0", "s0-t1"]
    assert all(e.t < p.t for e in s.events for p in s.probes)


def test_an_evidence_turn_timed_after_the_question_skips_it():
    sessions = [("2023/07/01 (Sat) 09:59", [("user", "hi"), ("assistant", "a"), ("user", "I own a red bike")])]
    q = lme_question("late_ev", "single-session-user", sessions, [0], QDATE, has_answer={0: [2]})
    c = convert_question(q, {})
    assert c.scenario is None and c.skipped == "evidence turn pushed past the question by turn spacing"


def test_the_dataset_url_is_pinned_to_a_revision():
    (f,) = LongMemEvalS().files()
    assert "/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/" in f.url
    assert "/resolve/main/" not in f.url


def test_label_values_are_checked():
    import pytest

    from harness.bench.datasets.longmemeval import parse_ku_labels

    assert parse_ku_labels("a: new\nb: old\nc: both\nd: exclude\n") == {
        "a": "new", "b": "old", "c": "both", "d": "exclude"
    }
    with pytest.raises(ValueError, match="nwe"):
        parse_ku_labels("a: nwe\n")


def test_a_dropped_late_turn_does_not_push_the_next_session_past_the_question():
    sessions = [
        ("2023/07/01 (Sat) 09:58", [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")]),
        ("2023/07/01 (Sat) 09:59", [("user", "I own a red bike")]),
    ]
    q = lme_question("late2", "single-session-user", sessions, [1], QDATE)
    s = convert_question(q, {}).scenario
    assert s is not None
    assert [e.id for e in s.events] == ["s0-t0", "s0-t1", "s1-t0"]
    assert s.events[-1].fact == "f1"


def test_converted_files_are_written_like_generated_drafts():
    from harness.bench.convert import scenario_text
    from harness.generate.pipeline import draft_text

    s = convert_question(ku("q1"), {"q1": "new"}).scenario
    assert s is not None
    assert scenario_text(s) == draft_text(s)
    assert "too_easy_repairs" not in scenario_text(s)  # a default older files do not write
