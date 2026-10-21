"""Adapter for a local Hindsight server (one bank per scenario, scopes as tags)."""

import os
import time
from collections.abc import Callable, Mapping
from typing import Any, override

from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.oss.http import JsonClient, check_local_url
from harness.oss.models import expected_models
from harness.oss.text import engine_ns, event_unit, format_event, format_query, query_units

HINDSIGHT_VERSION = "1.0.0"
ENV_URL = "HARNESS_HINDSIGHT_URL"
DEFAULT_URL = "http://127.0.0.1:8888"

_BANKS = "/v1/default/banks"

# Base URLs whose engine already answered once in this process. Only the first build against a
# URL fails fast; later builds retry like any other request (an engine that was up may blip).
_verified_urls: set[str] = set()


class HindsightEngine(BaseEngine):
    """Evaluates Hindsight through its HTTP API.

    Each scenario gets its own bank. Personal events carry a `user:<u>` tag and team events a
    `team:<t>` tag; one recall with `any_strict` matching finds what the asker may see.
    """

    def __init__(
        self,
        cfg: EvalConfig,
        *,
        base_url: str | None = None,
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.5,
        flush_timeout: float = 600.0,
    ) -> None:
        """Connect to the server and check the model configuration.

        Args:
            cfg: Eval config; its `llm` models must match the environment.
            base_url: Server address; defaults to `environ[HARNESS_HINDSIGHT_URL]`, then
                `http://127.0.0.1:8888`.
            environ: Environment to read; defaults to `os.environ`.
            sleep: Function used to wait, for flush polling and request retries.
            clock: Monotonic clock in seconds, for the flush timeout.
            poll_interval: Seconds between flush polls.
            flush_timeout: Seconds `flush()` waits for background work before giving up.

        Raises:
            ValueError: If the address is not an allowed local one or the models differ.
            EngineHTTPError: If the server cannot be reached; the first build against an address is
                not retried, later ones are.
        """
        env = os.environ if environ is None else environ
        url = check_local_url(base_url or env.get(ENV_URL) or DEFAULT_URL)
        self._llm_model: str
        self._embedding_model: str
        self._llm_model, self._embedding_model = expected_models(cfg.llm, env)
        self._client: JsonClient = JsonClient(url, sleep=sleep)
        self._sleep: Callable[[float], None] = sleep
        self._clock: Callable[[], float] = clock
        self._poll_interval: float = poll_interval
        self._flush_timeout: float = flush_timeout
        version: Any = self._client.request("GET", "/version", retry=url in _verified_urls)
        _verified_urls.add(url)
        self._api_version: str | None = version.get("api_version") if isinstance(version, dict) else None  # pyright: ignore[reportUnknownMemberType]
        self._bank: str | None = None
        self._event_ids: set[str] = set()

    @override
    def info(self) -> EngineInfo:
        """Describe the engine for the report."""
        return EngineInfo(
            name="hindsight",
            version=HINDSIGHT_VERSION,
            config={
                "llm_model": self._llm_model,
                "embedding_model": self._embedding_model,
                "reranker": "engine default (local cross-encoder)",
                "scope_mapping": "one bank per scenario; tags user:<u> / team:<t>; recall tags_match any_strict",
                "url": self._client.base_url,
                "api_version": self._api_version,
            },
            virtual_clock=True,
            sources=True,
            scopes=True,
        )

    def _bank_path(self) -> str:
        if self._bank is None:
            raise RuntimeError("hindsight: reset() must be called first")
        return f"{_BANKS}/{self._bank}"

    @override
    def reset(self, namespace: str) -> None:
        """Delete any bank left under this namespace and forget the known event ids.

        Args:
            namespace: The replay namespace; it is hashed into the bank id.
        """
        self._bank = engine_ns(namespace)
        self._client.request("DELETE", self._bank_path(), ok_missing=True)
        self._event_ids = set()

    @override
    def record(self, event: Event) -> None:
        """Store one event as a memory tagged with its visibility unit.

        Args:
            event: The observation to store.
        """
        item = {
            "content": format_event(event),
            "timestamp": event.t.isoformat(),
            "document_id": event.id,
            "tags": [event_unit(event)],
        }
        self._client.request("POST", f"{self._bank_path()}/memories", body={"items": [item]})
        self._event_ids.add(event.id)

    def _total(self, status: str) -> int:
        # Hindsight answers 404 for a bank that holds no memories. That is normal before the
        # first record of a scenario; after one it means the bank vanished, so it must raise.
        reply: Any = self._client.request(
            "GET",
            f"{self._bank_path()}/operations",
            query={"status": status, "limit": "1"},
            ok_missing=not self._event_ids,
        )
        if reply is None and not self._event_ids:
            return 0
        total: Any = reply.get("total") if isinstance(reply, dict) else None  # pyright: ignore[reportUnknownMemberType]
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise RuntimeError("hindsight: unexpected operations reply")
        return total

    @override
    def flush(self) -> None:
        """Wait until Hindsight has no pending or processing background operations.

        Raises:
            TimeoutError: If the work does not finish within the flush timeout.
            RuntimeError: If any background operation failed.
        """
        start = self._clock()
        while True:
            busy = self._total("pending") + self._total("processing")
            if busy == 0:
                break
            if self._clock() - start > self._flush_timeout:
                raise TimeoutError(f"hindsight: background work not finished after {self._flush_timeout}s")
            self._sleep(self._poll_interval)
        failed = self._total("failed")
        if failed > 0:
            raise RuntimeError(f"hindsight: {failed} background operations failed")

    @override
    def search(self, query: Query) -> list[Result]:
        """Recall memories visible to the asker.

        Args:
            query: The question and the asker's identity.

        Returns:
            The first `query.k` results in the engine's order, with event ids as sources.
        """
        body: dict[str, Any] = {
            "query": format_query(query),
            "query_timestamp": query.t.isoformat(),
            "tags": query_units(query),
            "tags_match": "any_strict",
            "include": {"source_facts": {}},
        }
        reply: Any = self._client.request(
            "POST", f"{self._bank_path()}/memories/recall", body=body, ok_missing=not self._event_ids
        )
        if not self._event_ids and not (isinstance(reply, dict) and reply):
            return []  # nothing recorded yet, so an empty answer is the true one
        rows, facts = self._parse_recall(reply)
        return [
            Result(text=row["text"], sources=self._sources(row, facts), meta={"id": row.get("id"), "type": row.get("type")})
            for row in rows[: query.k]
        ]

    def _parse_recall(self, reply: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # Called once something was recorded, so only a well-formed reply is acceptable.
        if not isinstance(reply, dict):
            raise RuntimeError("hindsight: unexpected recall reply")
        rows: Any = reply.get("results")  # pyright: ignore[reportUnknownMemberType]
        facts: Any = reply.get("source_facts")  # pyright: ignore[reportUnknownMemberType]
        valid = (
            isinstance(rows, list)
            and all(isinstance(row, dict) and isinstance(row.get("text"), str) for row in rows)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            and (facts is None or isinstance(facts, dict))
        )
        if not valid:
            raise RuntimeError("hindsight: unexpected recall reply")
        return rows, facts or {}

    def _sources(self, row: dict[str, Any], facts: dict[str, Any]) -> list[str]:
        if row.get("type") == "observation":
            found: list[Any] = [(facts.get(fid) or {}).get("document_id") for fid in row.get("source_fact_ids") or []]
        else:
            found = [row.get("document_id")]
        sources: list[str] = []
        for doc in found:
            if isinstance(doc, str) and doc in self._event_ids and doc not in sources:
                sources.append(doc)
        return sources
