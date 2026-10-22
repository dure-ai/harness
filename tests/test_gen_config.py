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
    assert cfg.prompt_version == "gen-v0.4"
    assert cfg.min_quote_chars == 8
    assert cfg.max_candidates_per_trace == 5
    assert cfg.max_output_tokens == 16000
    assert (cfg.timeout_s, cfg.max_retries, cfg.token_param) == (300, 2, "max_completion_tokens")
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
        "false",
        "no longer true",
        "legacy",
        "obsolete",
        "superseded",
        "stale",
        "incorrect",
        "wrong",
        "retired",
        "replaced by",
        "not valid anymore",
    ]


def test_gen_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "gen.yaml"
    p.write_text(GEN_CONFIG.read_text(encoding="utf-8") + "temprature: 0.5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_gen_config(p)


def test_gen_config_rejects_duplicate_key(tmp_path):
    import yaml

    p = tmp_path / "gen.yaml"
    p.write_text(GEN_CONFIG.read_text(encoding="utf-8") + "model: other\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match="duplicate key 'model'"):
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


def _minimal(**over):
    data = {"provider": "openai", "model": "m", "prompt_version": "v", "license_allowlist": [],
            "banned_cue_words": []}
    data.update(over)
    return data


def test_gen_config_request_limit_defaults_and_bounds():
    cfg = GenConfig.model_validate(_minimal())
    assert (cfg.timeout_s, cfg.max_retries, cfg.token_param) == (300, 2, "max_completion_tokens")
    assert GenConfig.model_validate(_minimal(max_retries=0, token_param="none")).max_retries == 0
    for bad in ({"timeout_s": 0}, {"timeout_s": -1}, {"max_retries": -1}, {"token_param": "x"}):
        with pytest.raises(ValidationError):
            GenConfig.model_validate(_minimal(**bad))
