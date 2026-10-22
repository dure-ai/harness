from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.config import EvalConfig, config_hash, load_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config" / "eval-v0.1.yaml"


def test_config_defaults_and_hash(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg == EvalConfig()
    assert cfg.k == 5
    assert cfg.judgment.m1_ratio == 0.5
    assert cfg.judgment.m2_drop == 0.10
    assert cfg.judgment.m1_abs_cap is None
    assert cfg.stats.bootstrap_iters == 10000
    assert cfg.stats.seed == 20261019
    assert cfg.repeat_n.n == 3 and cfg.repeat_n.tau == 0.5
    assert cfg.feedback.action == timedelta(minutes=5)
    assert cfg.feedback.on_false == timedelta(days=1)
    assert cfg.feedback.on_true == timedelta(0)
    assert cfg.llm_repeats == 3
    assert cfg.llm.provider == "upstage"

    q = tmp_path / "changed.yaml"
    q.write_text("k: 7\n")
    changed = load_config(q)
    assert changed.k == 7
    assert config_hash(changed) != config_hash(cfg)
    assert config_hash(cfg) == config_hash(EvalConfig())
    assert len(config_hash(cfg)) == 64


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("kk: 1\n")
    with pytest.raises(ValidationError):
        load_config(p)
    p.write_text("judgment:\n  bogus: 1\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_config_parses_iso_durations(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("feedback:\n  action: PT10M\n  on_false: P2D\n  on_true: PT0S\n")
    cfg = load_config(p)
    assert cfg.feedback.action == timedelta(minutes=10)
    assert cfg.feedback.on_false == timedelta(days=2)


def test_repo_config_equals_defaults():
    assert load_config(REPO_CONFIG) == EvalConfig()
    assert config_hash(load_config(REPO_CONFIG)) == config_hash(EvalConfig())


@pytest.mark.parametrize("bad", [0, -1])
def test_config_rejects_k_below_one(tmp_path, bad):
    p = tmp_path / "c.yaml"
    p.write_text(f"k: {bad}\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_config_rejects_llm_repeats_zero(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("llm_repeats: 0\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_config_rejects_bootstrap_iters_zero(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("stats: {bootstrap_iters: 0}\n")
    with pytest.raises(ValidationError):
        load_config(p)


@pytest.mark.parametrize("slot", ["action", "on_false", "on_true"])
def test_config_rejects_negative_feedback_delay(tmp_path, slot):
    p = tmp_path / "c.yaml"
    p.write_text(f"feedback: {{{slot}: -PT1H}}\n")
    with pytest.raises(ValidationError):
        load_config(p)
    with pytest.raises(ValidationError):
        EvalConfig(feedback={slot: timedelta(seconds=-1)})


def test_config_accepts_zero_delays_and_minimums():
    cfg = EvalConfig(k=1, llm_repeats=1, stats={"bootstrap_iters": 1},
                     feedback={"action": timedelta(0), "on_false": timedelta(0), "on_true": timedelta(0)})
    assert cfg.k == 1
