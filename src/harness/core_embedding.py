"""Qwen's query format and local llama.cpp embedding transport for Core evaluation."""

import http.client
import json
from collections.abc import Sequence
from typing import final
from urllib.parse import urlsplit

from cortex_mem.embed import EmbeddingError, EmbedKind
from pydantic import BaseModel, FiniteFloat, ValidationError

from harness.config import CoreEmbedding
from harness.contract import EngineUnavailable

_QUERY_INSTRUCTION = "Retrieve relevant software engineering memories that answer the query."
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


class EmbeddingUnavailable(EmbeddingError, EngineUnavailable):
    """The embedding server could not be reached or answered with a retryable status.

    It is an `EngineUnavailable`, so the run loop counts it toward stopping the engine
    and reports the scenario as unavailable rather than as an engine error.
    """


class _Vector(BaseModel):
    index: int
    embedding: list[FiniteFloat]


class _Response(BaseModel):
    data: list[_Vector]


@final
class QwenEmbedder:
    """A local Core embedder that remembers failures Core may deliberately hide from callers.

    The first failure stays in `failure` for the rest of the scenario, even if a later
    request succeeds, so a scenario that saw any embedding failure is never scored.
    """

    def __init__(self, config: CoreEmbedding) -> None:
        self.model: str = config.model
        self._url = urlsplit(config.base_url)
        self._dim: int | None = None
        self.failure: EmbeddingError | None = None

    @property
    def dim(self) -> int:
        """Return the output width learned from the first successful request."""
        if self._dim is None:
            raise ValueError("dim is unknown until the first Qwen embedding response")
        return self._dim

    def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        """Embed the texts in input order and surface malformed responses as failures."""
        if not texts:
            return []
        body = list(texts)
        if kind == "query":
            body = [f"Instruct: {_QUERY_INSTRUCTION}\nQuery: {text}" for text in texts]
        try:
            return self._request(body)
        except EmbeddingError as exc:
            self.failure = exc
            raise

    def _request(self, texts: list[str]) -> list[list[float]]:
        # Direct loopback transport keeps scenario text out of redirects and proxies.
        connection = http.client.HTTPConnection(self._url.netloc, timeout=30.0)
        try:
            connection.request(
                "POST", self._url.path + "/embeddings",
                json.dumps({"model": self.model, "input": texts}),
                {"Content-Type": "application/json"},
            )
            with connection.getresponse() as response:
                if response.status != 200:
                    error = EmbeddingUnavailable if response.status in _RETRYABLE_STATUS else EmbeddingError
                    raise error(f"Qwen embedding request failed with HTTP {response.status}")
                payload = _Response.model_validate_json(response.read(), strict=True)
        except OSError as exc:
            raise EmbeddingUnavailable(f"Qwen embedding request failed ({type(exc).__name__})") from None
        except (http.client.HTTPException, ValidationError) as exc:
            raise EmbeddingError(f"Qwen embedding request failed ({type(exc).__name__})") from None
        finally:
            connection.close()

        items = sorted(payload.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(texts))):
            raise EmbeddingError("Qwen embedding response does not match the input indexes")
        width = self._dim if self._dim is not None else len(items[0].embedding)
        if not width or any(len(item.embedding) != width for item in items):
            raise EmbeddingError("Qwen embedding response has empty or inconsistent vector lengths")
        self._dim = width
        return [item.embedding for item in items]
