"""Evaluation configuration: typed, strict, and hashable for pre-registration."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    """Base for config models: unknown keys are errors."""

    model_config = ConfigDict(extra="forbid")


class Judgment(_Strict):
    """Pass criteria relative to the all_store baseline (pre-registered).

    Attributes:
        m1_ratio: An engine's M1 must be at most `m1_ratio` times the baseline M1.
        m2_drop: An engine's M2 may be at most `m2_drop` below the baseline M2.
        m1_abs_cap: Optional absolute upper bound on M1; None disables it.
    """

    m1_ratio: float = 0.5
    m2_drop: float = 0.10
    m1_abs_cap: float | None = None


class Stats(_Strict):
    """Bootstrap settings for comparing engines.

    Attributes:
        bootstrap_iters: Number of scenario-level resamples.
        seed: Random seed, fixed so intervals are reproducible.
    """

    bootstrap_iters: int = Field(default=10000, ge=1)
    seed: int = 20261019


class RepeatN(_Strict):
    """Parameters of the repeat_n baseline.

    Attributes:
        n: Minimum number of similar events (itself included) before an event is handed over.
        tau: Token-set Jaccard similarity at or above which two events count as similar.
    """

    n: int = 3
    tau: float = 0.5


class FeedbackDelays(_Strict):
    """Default delays of harness-generated events in feedback mode, relative to the probe time.

    Attributes:
        action: Delay of the agent's action record.
        on_false: Delay of the outcome record after acting on a wrong basis.
        on_true: Delay of the outcome record after acting on a right basis.
    """

    action: timedelta = timedelta(minutes=5)
    on_false: timedelta = timedelta(days=1)
    on_true: timedelta = timedelta(0)

    @field_validator("action", "on_false", "on_true")
    @classmethod
    def _not_negative(cls, value: timedelta) -> timedelta:
        """Reject a negative delay: a generated record would then precede its probe."""
        if value < timedelta(0):
            raise ValueError("delay must be zero or positive")
        return value


class LLM(_Strict):
    """The model shared by every engine that calls an LLM (same model for fairness).

    Attributes:
        provider: Model provider.
        model: Chat model name; None until decided.
        embedding_model: Embedding model name; None until decided.
    """

    provider: str = "upstage"
    model: str | None = None
    embedding_model: str | None = None


class EvalConfig(_Strict):
    """Everything that affects scores; committed and hashed before results are seen.

    Attributes:
        version: Config version; bump it when a value changes and re-score every engine.
        k: Default number of results the agent looks at per probe.
        judgment: Pass criteria.
        stats: Bootstrap settings.
        repeat_n: repeat_n baseline parameters.
        feedback: Feedback-mode delays.
        llm_repeats: Default number of runs per candidate engine.
        llm: Shared LLM settings.
    """

    version: str = "0.1"
    k: int = Field(default=5, ge=1)
    judgment: Judgment = Judgment()
    stats: Stats = Stats()
    repeat_n: RepeatN = RepeatN()
    feedback: FeedbackDelays = FeedbackDelays()
    llm_repeats: int = Field(default=3, ge=1)
    llm: LLM = LLM()


def load_config(path: Path) -> EvalConfig:
    """Load a config file; an empty file means all defaults.

    Args:
        path: YAML file to read.

    Returns:
        The validated config.

    Raises:
        pydantic.ValidationError: If a key is unknown or a value has the wrong type.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvalConfig.model_validate(data or {})


def config_hash(cfg: EvalConfig) -> str:
    """SHA-256 of the config's canonical JSON, recorded in every report.

    Args:
        cfg: The config to hash.

    Returns:
        A hex digest that changes whenever any value changes.
    """
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
