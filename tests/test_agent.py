from harness.agent import Choice, choose, result_basis
from harness.contract import Result
from tests.factories import T, ev, fact, probe, scenario


def _world():
    s = scenario(
        facts={
            "f_old": fact(markers=["old-flag"], valid_until=T("10-05 00:00")),
            "f_new": fact(markers=["new-flag"], valid_from=T("10-05 00:00")),
            "f_other": fact(markers=["other-flag"]),
        },
        events=[
            ev("e1", T("10-01 10:00"), fact="f_old"),
            ev("e2", T("10-06 10:00"), fact="f_new"),
            ev("e3", T("10-02 10:00"), fact="f_other"),
        ],
        probes=[probe("p1", "block", T("10-10 10:00"), ["f_old", "f_new"])],
    )
    events = {le.event.id: le for le in s.labeled_events()}
    return s, s.probes[0], events


def test_first_relevant_result_is_followed():
    s, p, events = _world()
    results = [
        Result(text="nothing", sources=["e3"]),
        Result(text="uses old", sources=["e1"]),
        Result(text="uses new", sources=["e2"]),
    ]
    assert choose(results, p, s, events) == Choice(1, frozenset({"f_old"}))


def test_abstain_when_nothing_relevant():
    s, p, events = _world()
    results = [Result(text="unrelated"), Result(text="x", sources=["e3"])]
    assert choose(results, p, s, events) == Choice(None, frozenset())
    assert choose([], p, s, events) == Choice(None, frozenset())


def test_unknown_and_duplicate_sources_ignored():
    s, p, events = _world()
    r = Result(text="t", sources=["zzz", "e1", "e1"])
    assert result_basis(r, p, s, events) == frozenset({"f_old"})


def test_unknown_only_sources_is_empty_basis():
    s, p, events = _world()
    r = Result(text="old-flag", sources=["zzz"])
    assert result_basis(r, p, s, events) == frozenset()


def test_sourceless_uses_markers():
    s, p, events = _world()
    r = Result(text="Remember the NEW-FLAG setting")
    assert result_basis(r, p, s, events) == frozenset({"f_new"})


def test_sourceless_ignores_facts_outside_about():
    s, p, events = _world()
    r = Result(text="other-flag only")
    assert result_basis(r, p, s, events) == frozenset()


def test_mixed_basis_narrowed_by_markers():
    s, p, events = _world()
    r = Result(text="the new-flag applies", sources=["e1", "e2"])
    assert result_basis(r, p, s, events) == frozenset({"f_new"})


def test_mixed_basis_unresolved_keeps_both():
    from harness.scoring import is_wrong

    s, p, events = _world()
    r = Result(text="no marker here", sources=["e1", "e2"])
    basis = result_basis(r, p, s, events)
    assert basis == frozenset({"f_old", "f_new"})
    assert is_wrong(basis, p, s)
    both = Result(text="old-flag and new-flag", sources=["e1", "e2"])
    assert result_basis(both, p, s, events) == frozenset({"f_old", "f_new"})


def test_single_source_not_narrowed_by_markers():
    s, p, events = _world()
    r = Result(text="new-flag", sources=["e1"])
    assert result_basis(r, p, s, events) == frozenset({"f_old"})
