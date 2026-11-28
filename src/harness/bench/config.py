"""Configuration of the external benchmark track, kept apart from `config/eval-*.yaml`."""

import hashlib
import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from harness.config import EvalConfig
from harness.yamlio import load_yaml


class BenchConfig(BaseModel):
    """Everything that affects bench scores; committed and hashed like the eval config.

    Attributes:
        version: Bench config version; bump it when a value changes and re-score every engine.
        eval: The evaluation settings the replay and the baselines use.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    version: str = "0.1"
    eval: EvalConfig = EvalConfig()


def load_bench_config(path: Path) -> BenchConfig:
    """Load a bench config file; an empty file means all defaults.

    Raises:
        pydantic.ValidationError: If a key is unknown or a value has the wrong type.
        yaml.YAMLError: If the YAML is invalid or a key appears twice.
    """
    data = load_yaml(Path(path).read_text(encoding="utf-8"))
    return BenchConfig.model_validate(data or {})


def bench_config_hash(cfg: BenchConfig) -> str:
    """SHA-256 of the config's canonical JSON, recorded in every bench report."""
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
