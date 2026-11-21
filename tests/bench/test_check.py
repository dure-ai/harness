from datetime import timedelta

from harness.baselines.oracle import OracleEngine
from harness.bench.check import check_conversion, screened
from harness.bench.dataset import Converted
from harness.bench.datasets.longmemeval import convert_question
from harness.config import EvalConfig
from harness.scenario.model import Fact, Probe, Provenance, Scenario, ScenarioEvent, World
from tests.bench.test_longmemeval import QDATE, ku
from tests.factories import T, ev, probe


def _scenario(sid: str, family: str, facts: dict, events: list, probes: list) -> Scenario:
    return Scenario(
        id=sid,
        family=family,  # pyright: ignore[reportArgumentType]
        mode="read_only",
        world=World(timezone="UTC", teams={"t1": ["u1"]}),
        facts=facts,
        events=events,
        probes=probes,
        provenance=Provenance(author="generator"),
    )


def _mislabeled(sid: str) -> Scenario:
    """Two events with identical content state different facts, which the oracle cannot tell apart."""
    return _scenario(
        sid,
        "ext_recall",
        {"f0": Fact(text="fact zero"), "f1": Fact(text="fact one")},
        [ev("e0", T("10-01 10:00"), fact="f0", text="same"), ev("e1", T("10-01 10:00"), fact="f1", text="same")],
        [probe("p", "promote", T("10-02 10:00"), ["f1"], user="u1")],
    )


def _good() -> Scenario:
    s = convert_question(ku("q1"), {"q1": "new"}).scenario
    assert s is not None
    return s


def test_a_correct_conversion_passes_the_check():
    assert check_conversion(_good(), EvalConfig()) is None


def test_a_wrong_label_is_caught_by_the_oracle():
    reason = check_conversion(_mislabeled("wrong"), EvalConfig())  # identical events
    assert reason is not None and "oracle" in reason


def test_none_must_score_zero():
    s = _good()
    # An engine that answers like the oracle stands in for a `none` that somehow earns promote hits.
    reason = check_conversion(s, EvalConfig(), none=lambda: OracleEngine(s))
    assert reason is not None and "none" in reason


def test_screened_turns_invalid_and_failing_scenarios_into_skips():
    ok = Converted(_good(), "ok", "knowledge-update", None)
    invalid_s = _scenario(
        "inv",
        "ext_update",
        {"f0": Fact(text="a")},
        [ev("e0", T("10-01 10:00"), fact="f0")],
        [probe("p", "promote", T("10-02 10:00"), ["f0"], user="u1")],
    )
    invalid = Converted(invalid_s, "inv", "knowledge-update", None)
    wrong = Converted(_mislabeled("wrong"), "wrong", "multi-session", None)
    already = Converted(None, "abs", "multi-session", "abstention")
    out = list(screened([ok, invalid, wrong, already], EvalConfig()))
    assert [(c.scenario is not None, c.skipped) for c in out] == [
        (True, None),
        (False, "invalid: pairing_missing"),
        (False, out[2].skipped),
        (False, "abstention"),
    ]
    assert out[2].skipped is not None and out[2].skipped.startswith("conversion check: ")
    assert [c.source_id for c in out] == ["ok", "inv", "wrong", "abs"]
