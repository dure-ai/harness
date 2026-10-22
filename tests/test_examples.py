"""The shipped example scenarios must be valid, self-consistent and discriminating (smoke set)."""

from pathlib import Path

import pytest

from harness.config import load_config
from harness.scenario.load import load_set
from harness.scenario.validate import validate_scenario
from harness.selfcheck import check_discrimination, run_selfcheck

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "scenarios" / "examples"
CFG = load_config(ROOT / "config" / "eval-v0.1.yaml")


@pytest.fixture(scope="module")
def scenarios():
    loaded = load_set([EXAMPLES], include_drafts=True)
    return loaded.scenarios


def test_examples_cover_five_families(scenarios):
    assert {s.family for s in scenarios} == {
        "truth_change", "single_source_poison", "conditional", "echo", "leak_cross_team",
    }
    assert all(s.review.status == "draft" and s.provenance.author == "llm" for s in scenarios)


def test_examples_validate_clean(scenarios):
    for s in scenarios:
        issues = validate_scenario(s)
        assert issues == [], (s.id, issues)


def test_examples_pass_the_generator_checks(scenarios):
    """The shipped examples are also valid drafts: no automatic check finds an error."""
    from harness.generate.checks import check_draft
    from harness.generate.config import load_gen_config

    gen = load_gen_config(ROOT / "config" / "gen-v0.1.yaml")
    for s in scenarios:
        errors = [i for i in check_draft(s, CFG, gen) if i.level == "error"]
        assert errors == [], (s.id, errors)


def test_examples_selfcheck_passes(scenarios):
    checks, _ = run_selfcheck(scenarios, CFG)
    failed = [c for c in checks if not c.ok]
    assert failed == [], failed
    echo = next(c for c in checks if c.name == "repeat_n_echo")
    assert echo.ok and not echo.detail.startswith("skipped"), echo.detail
    poison = next(c for c in checks if c.name == "repeat_n_poison")
    assert not poison.detail.startswith("skipped"), poison.detail


def test_examples_discriminate(scenarios):
    for s in scenarios:
        issues = check_discrimination(s, CFG)
        assert issues == [], (s.id, issues)
