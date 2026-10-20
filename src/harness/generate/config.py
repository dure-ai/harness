"""Generation settings. Kept apart from `EvalConfig`: they never affect scores or the config hash."""

import os
from pathlib import Path
from typing import Literal

import yaml

from harness.config import _Strict


class GenConfig(_Strict):
    """How candidate scenarios are generated and which sources are allowed.

    Attributes:
        provider: LLM backend; `fake` replays canned responses for tests.
        model: Model name sent to the provider.
        effort: Reasoning effort (anthropic).
        temperature: Sampling temperature (openai only).
        base_url: Override of the provider endpoint, e.g. for an OpenAI-compatible server.
        api_key_env: Name of the environment variable holding the API key.
        fake_responses: File of canned responses for the `fake` provider.
        prompt_version: Prompt revision, recorded in each scenario's provenance.
        max_candidates_per_trace: Upper bound on candidate scenarios taken from one trace.
        license_allowlist: SPDX ids of source-repo licenses that may be used.
        banned_cue_words: Words that give away a fact's validity when they appear in event text.
        max_output_tokens: Output token limit of one generation call.
    """

    provider: Literal["anthropic", "openai", "fake"]
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    temperature: float = 0.2
    base_url: str | None = None
    api_key_env: str | None = None
    fake_responses: Path | None = None
    prompt_version: str
    max_candidates_per_trace: int = 5
    license_allowlist: list[str]
    banned_cue_words: list[str]
    max_output_tokens: int = 16000


def load_gen_config(path: Path) -> GenConfig:
    """Load a generation config file.

    Args:
        path: YAML file to read.

    Returns:
        The validated config.

    Raises:
        pydantic.ValidationError: If a key is unknown or a value has the wrong type.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return GenConfig.model_validate(data or {})


def cache_root() -> Path:
    """Directory for downloaded traces and other generation caches.

    Returns:
        `$T5_HARNESS_CACHE` if set, else `~/.cache/t5-harness`.
    """
    env = os.environ.get("T5_HARNESS_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "t5-harness"
