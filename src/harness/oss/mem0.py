"""Adapter for a local Mem0 REST server (one run id per scenario, scopes as user ids)."""

import os
import time
from collections.abc import Callable, Mapping
from typing import Any, override

from harness.config import EvalConfig
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.oss.http import JsonClient, check_local_url
from harness.oss.models import expected_models
from harness.oss.text import engine_ns, event_unit, format_event, format_query, query_units

MEM0_VERSION = "2.2.1"
ENV_URL = "HARNESS_MEM0_URL"
DEFAULT_URL = "http://127.0.0.1:8890"
EVENT_KEY = "harness_event"


def _config_model(reply: Any, section: str) -> str:
    node: Any = reply
    for key in (section, "config", "model"):
        node = node.get(key) if isinstance(node, dict) else None  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(node, str):
        raise RuntimeError("mem0: unexpected configure reply")
    return node


class Mem0Engine(BaseEngine):
    """Evaluates Mem0 through its REST API.

    Each scenario is one `run_id`. Personal events are stored under `user_id` `user:<u>` and team
    events under `team:<t>`; one search with an `OR` filter over the asker's units finds what
    the asker may see.
    """

    def __init__(
        self,
        cfg: EvalConfig,
        *,
        base_url: str | None = None,
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Connect to the server and check the model configuration.

        Args:
            cfg: Eval config; its `llm` models must match the environment and the server.
            base_url: Server address; defaults to `environ[HARNESS_MEM0_URL]`, then
                `http://127.0.0.1:8890`.
            environ: Environment to read; defaults to `os.environ`.
            sleep: Function used to wait between request retries.

        Raises:
            ValueError: If the address is not an allowed local one or a model differs from the
                one the server runs.
            EngineHTTPError: If the server cannot be reached; this is not retried.
            RuntimeError: If the server's configuration reply lacks the model names.
        """
        env = os.environ if environ is None else environ
        url = check_local_url(base_url or env.get(ENV_URL) or DEFAULT_URL)
        self._llm_model: str
        self._embedding_model: str
        self._llm_model, self._embedding_model = expected_models(cfg.llm, env)
        self._client: JsonClient = JsonClient(url, sleep=sleep)
        # The reply holds the server's whole configuration; only the two model names are read.
        reply: Any = self._client.request("GET", "/configure", retry=False)
        running_llm = _config_model(reply, "llm")
        running_embedding = _config_model(reply, "embedder")
        if running_llm != self._llm_model or running_embedding != self._embedding_model:
            running = f"llm={running_llm!r}, embedding={running_embedding!r}"
            expected = f"llm={self._llm_model!r}, embedding={self._embedding_model!r}"
            raise ValueError(f"mem0 runs {running}, but the eval expects {expected}")
        self._run: str | None = None
        self._event_ids: set[str] = set()

    @override
    def info(self) -> EngineInfo:
        """Describe the engine for the report."""
        return EngineInfo(
            name="mem0",
            version=MEM0_VERSION,
            config={
                "llm_model": self._llm_model,
                "embedding_model": self._embedding_model,
                "reranker": "none (engine default)",
                "scope_mapping": "run_id per scenario; user_id user:<u> / team:<t>; search OR filter",
                "url": self._client.base_url,
                "note": "OSS add() ignores timestamps; memories carry wall-clock time",
            },
            virtual_clock=False,
            sources=True,
            scopes=True,
        )

    def _run_id(self) -> str:
        if self._run is None:
            raise RuntimeError("mem0: reset() must be called first")
        return self._run

    @override
    def reset(self, namespace: str) -> None:
        """Delete any memories left under this namespace and forget the known event ids.

        Args:
            namespace: The replay namespace; it is hashed into the run id.
        """
        self._run = engine_ns(namespace)
        self._client.request("DELETE", "/memories", query={"run_id": self._run})
        self._event_ids = set()

    @override
    def record(self, event: Event) -> None:
        """Store one event as a memory under its visibility unit.

        Args:
            event: The observation to store.
        """
        body = {
            "messages": [{"role": "user", "content": format_event(event)}],
            "run_id": self._run_id(),
            "user_id": event_unit(event),
            "metadata": {EVENT_KEY: event.id},
        }
        self._client.request("POST", "/memories", body=body)
        self._event_ids.add(event.id)

    @override
    def search(self, query: Query) -> list[Result]:
        """Search memories visible to the asker.

        Args:
            query: The question and the asker's identity.

        Returns:
            The first `query.k` results in the engine's order, with event ids as sources.

        Raises:
            RuntimeError: If the reply is not a `results` list of rows with a text `memory`.
        """
        body: dict[str, Any] = {
            "query": format_query(query),
            "filters": {"run_id": self._run_id(), "OR": [{"user_id": u} for u in query_units(query)]},
            "top_k": query.k,
        }
        reply: Any = self._client.request("POST", "/search", body=body)
        rows: Any = reply.get("results") if isinstance(reply, dict) else None  # pyright: ignore[reportUnknownMemberType]
        if not isinstance(rows, list):
            raise RuntimeError("mem0: unexpected search reply")
        results: list[Result] = []
        for row in rows[: query.k]:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(row, dict) or not isinstance(row.get("memory"), str):  # pyright: ignore[reportUnknownMemberType]
                raise RuntimeError("mem0: unexpected search reply")
            results.append(
                Result(
                    text=row["memory"],
                    sources=self._sources(row),  # pyright: ignore[reportUnknownArgumentType]
                    meta={"id": row.get("id"), "score": row.get("score")},  # pyright: ignore[reportUnknownMemberType]
                )
            )
        return results

    def _sources(self, row: dict[str, Any]) -> list[str]:
        metadata: Any = row.get("metadata")
        event_id: Any = metadata.get(EVENT_KEY) if isinstance(metadata, dict) else None  # pyright: ignore[reportUnknownMemberType]
        if isinstance(event_id, str) and event_id in self._event_ids:
            return [event_id]
        return []
