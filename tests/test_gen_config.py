from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.config import config_hash, load_config
from harness.generate.config import GenConfig, cache_root, load_gen_config

REPO = Path(__file__).resolve().parent.parent
GEN_CONFIG = REPO / "config" / "gen-v0.1.yaml"


def test_gen_config_defaults_from_file():
    cfg = load_gen_config(GEN_CONFIG)
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-5-5"
    assert cfg.effort == "medium"
    assert cfg.temperature == 0.2
    assert cfg.prompt_version == "gen-v0.1"
    assert cfg.max_candidates_per_trace == 5
    assert cfg.max_output_tokens == 16000
    assert cfg.base_url is None and cfg.api_key_env is None and cfg.fake_responses is None
    assert cfg.license_allowlist == [
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "PSF-2.0",
        "MPL-2.0",
        "Unlicense",
    ]
    assert cfg.banned_cue_words == [
        "outdated",
        "no longer",
        "rumor",
        "unverified",
        "fake",
        "deprecated",
    ]


def test_gen_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "gen.yaml"
    p.write_text(GEN_CONFIG.read_text(encoding="utf-8") + "temprature: 0.5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_gen_config(p)


def test_gen_config_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        GenConfig.model_validate({"provider": "bogus", "model": "m", "prompt_version": "p"})


def test_gen_config_not_part_of_eval_config():
    assert "gen" not in load_config(REPO / "config" / "eval-v0.1.yaml").model_dump()
    assert config_hash(load_config(REPO / "config" / "eval-v0.1.yaml"))


def test_cache_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "c"))
    assert cache_root() == tmp_path / "c"


def test_cache_root_default(monkeypatch, tmp_path):
    monkeypatch.delenv("T5_HARNESS_CACHE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache_root() == tmp_path / ".cache" / "t5-harness"
