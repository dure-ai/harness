"""Embeddings for the engine arms: the harness's local Qwen transport behind a disk cache.

Every engine arm stores the same records, so the same texts would be embedded once per engine.
The cache keys a vector by (model, the exact text sent), which makes a second engine, or a rerun,
cost nothing for the texts it shares with an earlier one.

The harness's `QwenEmbedder` words its query instruction for software-engineering memories. The
benchmarks here ask about conversations, so queries get `QUERY_INSTRUCTION` instead, in Qwen's
`Instruct: ... / Query: ...` form (two lines). The formatted query goes to the transport as a passage, which
sends text unchanged, so the instruction is applied exactly once.

Settings come from the environment; without `EXTBENCH_EMBED_URL` engines run with no index, as
before.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

QUERY_INSTRUCTION = "Given a question about a conversation, retrieve the turns that help answer it."
_PASSAGE = "passage"
_SQLITE_CHUNK = 500  # keys per lookup, under SQLite's bound-variable limit


class Transport(Protocol):
    """What the cache needs from the embedder behind it (`harness.core_embedding.QwenEmbedder`)."""

    model: str
    failure: Exception | None

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        """One vector per text, in order."""
        ...


class CachedEmbedder:
    """A core `Embedder` that asks `inner` only for texts it has not embedded before."""

    def __init__(self, inner: Transport, path: Path, query_instruction: str = QUERY_INSTRUCTION) -> None:
        self.inner = inner
        self.model: str = inner.model
        self.query_instruction = query_instruction
        self._dim: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        # Several runs may share one cache; a writer waits rather than failing.
        self._db = sqlite3.connect(path, timeout=60)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
        self._db.commit()
        self.hits = self.misses = 0

    @property
    def failure(self) -> Exception | None:
        """The transport's first failure, which the engine arm checks after each step."""
        return self.inner.failure

    @property
    def dim(self) -> int:
        """The vector width, known once any vector was served."""
        if self._dim is None:
            raise ValueError("dim is unknown until the first embedding")
        return self._dim

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        """One vector per text, in order; queries get the conversation instruction first."""
        sent = [self._format(t) for t in texts] if kind == "query" else list(texts)
        keys = [self._key(t) for t in sent]
        found = self._lookup(keys)
        missing = sorted({t for t, k in zip(sent, keys, strict=True) if k not in found})
        self.hits += sum(1 for k in keys if k in found)
        if missing:
            vectors = self.inner.embed(missing, _PASSAGE)
            self.misses += len(missing)
            fresh = {self._key(t): v for t, v in zip(missing, vectors, strict=True)}
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in sorted(fresh.items())],
            )
            self._db.commit()
            found = found | fresh
        out = [found[k] for k in keys]
        if out:
            self._dim = len(out[0])
        return out

    def _format(self, text: str) -> str:
        return f"Instruct: {self.query_instruction}\nQuery: {text}"

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def _lookup(self, keys: list[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        unique = sorted(set(keys))
        for start in range(0, len(unique), _SQLITE_CHUNK):
            chunk = unique[start : start + _SQLITE_CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = self._db.execute(f"SELECT key, vector FROM vectors WHERE key IN ({marks})", chunk)
            found.update((k, json.loads(v)) for k, v in rows)
        return found


def settings_from_env() -> dict | None:
    """The embedding settings for engine arms, validated, or None when no index is wanted.

    Raises:
        SystemExit: If the settings are set but not valid (e.g. a non-loopback URL).
    """
    url = os.environ.get("EXTBENCH_EMBED_URL")
    if not url:
        return None
    from pydantic import ValidationError

    from harness.config import CoreEmbedding

    fields: dict = {"base_url": url}
    if model := os.environ.get("EXTBENCH_EMBED_MODEL"):
        fields["model"] = model
    if threshold := os.environ.get("EXTBENCH_GROUP_THRESHOLD"):
        fields["group_threshold"] = threshold
    try:
        config = CoreEmbedding.model_validate(fields)
    except ValidationError as exc:
        raise SystemExit(f"invalid embedding settings: {exc.errors()[0]['msg']}") from None
    return {"base_url": config.base_url, "model": config.model, "group_threshold": config.group_threshold,
            "query_instruction": QUERY_INSTRUCTION}


def make(settings: dict, root: Path) -> CachedEmbedder:
    """Build the cached Qwen embedder the settings describe; the cache lives under `root/cache/`."""
    from harness.config import CoreEmbedding
    from harness.core_embedding import QwenEmbedder

    config = CoreEmbedding(base_url=settings["base_url"], model=settings["model"],
                           group_threshold=settings["group_threshold"])
    return CachedEmbedder(QwenEmbedder(config), root / "cache" / "embeddings.sqlite3")
