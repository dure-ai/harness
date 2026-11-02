"""Check that the models an engine was started with match the eval config."""

import os
from collections.abc import Mapping

from harness.config import LLM

ENV_LLM = "HARNESS_LLM_MODEL"
ENV_EMBEDDING = "HARNESS_EMBEDDING_MODEL"


def expected_models(llm: LLM, environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return the chat and embedding model names, checked against the environment.

    Args:
        llm: The eval config's model section.
        environ: Environment to check; defaults to `os.environ`.

    Returns:
        The (llm_model, embedding_model) pair from the config.

    Raises:
        ValueError: If the config leaves either model unset, or the environment variables
            are missing or differ from the config.
    """
    if llm.model is None or llm.embedding_model is None:
        raise ValueError("set llm.model and llm.embedding_model in the eval config")
    env = os.environ if environ is None else environ
    got = (env.get(ENV_LLM), env.get(ENV_EMBEDDING))
    want = (llm.model, llm.embedding_model)
    if got != want:
        raise ValueError(
            "engine models differ from the eval config: "
            + f"config has {ENV_LLM}={want[0]!r} and {ENV_EMBEDDING}={want[1]!r}, "
            + f"environment has {ENV_LLM}={got[0]!r} and {ENV_EMBEDDING}={got[1]!r}"
        )
    return want
