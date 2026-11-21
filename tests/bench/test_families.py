from pathlib import Path
from typing import get_args

from harness.config import load_config
from harness.metrics import compute_metrics
from harness.replay import blind_key
from harness.scenario.load import load_set
from harness.scenario.model import EXT_FAMILIES, FAMILIES, Family, Scenario
from harness.scenario.validate import validate_scenario
from harness.selfcheck import run_selfcheck
from tests.factories import T, ev, fact, probe, scenario

ROOT = Path(__file__).resolve().parents[2]

MAIN = (
    "normal_repeat",
    "coincidence",
    "single_source_poison",
    "unconfirmed_poison",
    "echo",
    "failure_feedback",
    "truth_change",
    "conditional",
    "workaround_then_root",
    "leak_personal",
    "leak_cross_team",
)


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def promote_only(family: str) -> Scenario:
    """A valid scenario with one promote probe and no block probe."""
    facts = {"f_new": fact(valid_from=T("10-09 00:00"), markers=["merged directly"])}
    events = [ev("e2", T("10-09 14:00"), fact="f_new")]
    probes = [probe("p_promote", "promote", T("10-14 11:00"), ["f_new"])]
    return scenario(facts, events, probes, family=family)


def block_and_promote(family: str) -> Scenario:
    """The truth_change pairing: an old fact that stopped holding, a new one that replaced it."""
    facts = {
        "f_old": fact(valid_until=T("10-09 00:00"), markers=["do not modify"]),
        "f_new": fact(valid_from=T("10-09 00:00"), markers=["merged directly"]),
    }
    events = [
        ev("e1", T("10-01 10:00"), fact="f_old"),
        ev("e2", T("10-09 14:00"), fact="f_new"),
    ]
    probes = [
        probe("p_block", "block", T("10-14 10:00"), ["f_old", "f_new"], session="s8"),
        probe("p_promote", "promote", T("10-14 11:00"), ["f_new"], session="s9"),
    ]
    return scenario(facts, events, probes, family=family)


def test_main_families_are_unchanged():
    assert FAMILIES == MAIN
    assert EXT_FAMILIES == ("ext_recall", "ext_update")
    assert set(get_args(Family)) == set(MAIN) | set(EXT_FAMILIES)


def test_ext_recall_needs_only_promote():
    assert "pairing_missing" not in codes(validate_scenario(promote_only("ext_recall")))


def test_ext_update_needs_block_and_promote():
    assert "pairing_missing" in codes(validate_scenario(promote_only("ext_update")))
    assert "pairing_missing" not in codes(validate_scenario(block_and_promote("ext_update")))


def test_a_dev_selfcheck_has_no_ext_rows():
    loaded = load_set([ROOT / "scenarios" / "dev"], False)
    checks, runs = run_selfcheck(
        loaded.scenarios, load_config(ROOT / "config" / "eval-v0.1.yaml"), blind_key(1)
    )
    by_id = {s.id: s for s in loaded.scenarios}
    assert all(c.ok for c in checks)
    for name, engine_runs in runs.items():
        m = compute_metrics(engine_runs, by_id)
        assert not [f for f in (*m.by_family, *m.feedback_by_family) if f.startswith("ext_")], name
