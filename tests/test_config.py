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
