from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.bench.config import BenchConfig, bench_config_hash, load_bench_config
from harness.config import config_hash, load_config


def test_bench_config_matches_the_eval_config_values():
    b = load_bench_config(Path("config/bench-v0.1.yaml"))
    e = load_config(Path("config/eval-v0.1.yaml"))
    assert (b.eval.k, b.eval.repeat_n, b.eval.feedback) == (e.k, e.repeat_n, e.feedback)
    assert b.eval == e
    assert b.version == "0.1"


def test_unknown_keys_are_errors(tmp_path: Path):
    path = tmp_path / "bench.yaml"
    path.write_text("version: '0.1'\nextra: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_bench_config(path)
    path.write_text("eval:\n  kk: 5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_bench_config(path)


def test_an_empty_file_means_defaults(tmp_path: Path):
    path = tmp_path / "bench.yaml"
    path.write_text("", encoding="utf-8")
    assert load_bench_config(path) == BenchConfig()


def test_the_hash_changes_with_any_value_and_differs_from_the_eval_hash():
    b = load_bench_config(Path("config/bench-v0.1.yaml"))
    assert len(bench_config_hash(b)) == 64
    assert bench_config_hash(b) == bench_config_hash(load_bench_config(Path("config/bench-v0.1.yaml")))
    changed = b.model_copy(update={"eval": b.eval.model_copy(update={"k": 6})})
    assert bench_config_hash(changed) != bench_config_hash(b)
    assert bench_config_hash(b) != config_hash(b.eval)
