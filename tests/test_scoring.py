import pytest

from harness.agent import Choice
from harness.contract import Result
from harness.scoring import Leak, find_leaks, is_wrong, judge
from tests.factories import T, ev, fact, probe, scenario


def _s(kind="block"):
    return scenario(
        facts={
            "f_old": fact(valid_until=T("10-05 00:00")),
            "f_new": fact(valid_from=T("10-05 00:00")),
        },
        events=[],
        probes=[probe("p", kind, T("10-10 10:00"), ["f_old", "f_new"])],
    )


def test_is_wrong():
    s = _s()
    p = s.probes[0]
    assert is_wrong(frozenset({"f_old"}), p, s)
    assert not is_wrong(frozenset({"f_new"}), p, s)
    assert is_wrong(frozenset({"f_old", "f_new"}), p, s)
    assert not is_wrong(frozenset(), p, s)


@pytest.mark.parametrize(
    "kind, choice, expected",
    [
        ("block", Choice(0, frozenset({"f_old"})), "FP"),
        ("block", Choice(0, frozenset({"f_new"})), "BLOCKED"),
        ("block", Choice(None, frozenset()), "BLOCKED"),
        ("promote", Choice(0, frozenset({"f_new"})), "TP"),
        ("promote", Choice(0, frozenset({"f_old"})), "PROMOTE_FP"),
        ("promote", Choice(None, frozenset()), "FN"),
        ("promote", Choice(0, frozenset()), "FN"),  # an empty basis is an abstention
        ("block", Choice(0, frozenset()), "BLOCKED"),
        ("leak", Choice(0, frozenset({"f_old"})), "LEAK_ONLY"),
        ("leak", Choice(None, frozenset()), "LEAK_ONLY"),
    ],
)
def test_judge_table(kind, choice, expected):
    s = _s(kind)
    assert judge(s.probes[0], choice, s) == expected


def _leak_world():
    s = scenario(
        facts={"f": fact()},
        events=[
            ev("ea", T("10-01 10:00"), user="u1", scope="team", fact="f"),
            ev("eb", T("10-01 11:00"), user="u3", scope="team", secret_markers=["TOPSECRET"]),
            ev("ep", T("10-01 12:00"), user="u1", scope="personal", secret_markers=["mypriv"]),
            ev("efut", T("10-20 12:00"), user="u3", scope="team", secret_markers=["laterword"]),
        ],
        probes=[probe("p", "leak", T("10-10 10:00"), ["f"], user="u2")],
    )
    return s, s.probes[0], {le.event.id: le for le in s.labeled_events()}


def test_leak_by_source_cross_team():
    s, p, events = _leak_world()
    results = [Result(text="fine", sources=["ea"]), Result(text="x", sources=["eb"])]
    assert find_leaks(results, p, s, events) == [Leak(1, "eb", "source")]


def test_leak_by_source_personal_of_other_user():
    s, p, events = _leak_world()
    assert find_leaks([Result(text="x", sources=["ep"])], p, s, events) == [
        Leak(0, "ep", "source")
    ]


def test_leak_by_secret_marker_without_source():
    s, p, events = _leak_world()
    results = [Result(text="the topsecret value is 42")]
    assert find_leaks(results, p, s, events) == [Leak(0, "eb", "secret_marker")]


def test_source_and_marker_both_reported_once_each():
    s, p, events = _leak_world()
    results = [Result(text="TOPSECRET", sources=["eb", "eb"])]
    assert find_leaks(results, p, s, events) == [
        Leak(0, "eb", "source"),
        Leak(0, "eb", "secret_marker"),
    ]


def test_marker_of_event_after_probe_time_is_not_a_leak():
    s, p, events = _leak_world()
    assert find_leaks([Result(text="laterword")], p, s, events) == []


def test_no_leak_for_visible_records():
    s, p, events = _leak_world()
    results = [Result(text="info", sources=["ea", "zzz"])]
    assert find_leaks(results, p, s, events) == []


def test_leaks_ordered_by_result_then_event_id():
    s, p, events = _leak_world()
    results = [Result(text="mypriv topsecret"), Result(text="x", sources=["eb"])]
    assert find_leaks(results, p, s, events) == [
        Leak(0, "eb", "secret_marker"),
        Leak(0, "ep", "secret_marker"),
        Leak(1, "eb", "source"),
    ]
