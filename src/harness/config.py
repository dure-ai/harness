"""Evaluation configuration: typed, strict, and hashable for pre-registration."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Judgment(_Strict):
    m1_ratio: float = 0.5
    m2_drop: float = 0.10
    m1_abs_cap: float | None = None


class Stats(_Strict):
    bootstrap_iters: int = 10000
    seed: int = 20261019


class RepeatN(_Strict):
    n: int = 3
    tau: float = 0.5


class FeedbackDelays(_Strict):
    action: timedelta = timedelta(minutes=5)
    on_false: timedelta = timedelta(days=1)
    on_true: timedelta = timedelta(0)


class LLM(_Strict):
    provider: str = "upstage"
    model: str | None = None
    embedding_model: str | None = None


class EvalConfig(_Strict):
    version: str = "0.1"
    k: int = 5
    judgment: Judgment = Judgment()
    stats: Stats = Stats()
    repeat_n: RepeatN = RepeatN()
    feedback: FeedbackDelays = FeedbackDelays()
    llm_repeats: int = 3
    llm: LLM = LLM()


def load_config(path: Path) -> EvalConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvalConfig.model_validate(data or {})


def config_hash(cfg: EvalConfig) -> str:
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
