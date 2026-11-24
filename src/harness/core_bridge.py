"""Run an engine written to the cortex-mem port (`observe/tick/handoff`) under the harness.

The harness drives `BaseEngine`; this wraps a core engine in a fresh `Runtime` and
`InMemoryDatabase` per scenario, so a score describes the code the MCP server runs, decision
checks and handoff filter included. Comparisons stay on the in-memory store because store
implementations score search differently (core `docs/contract.md` section 4).
"""

import json
from datetime import datetime
from importlib.metadata import PackageNotFoundError, distribution
from typing import override

from cortex_mem.contract import CONTRACT_VERSION, Caller, Kind
from cortex_mem.contract import Engine as CoreEngine
from cortex_mem.contract import Event as CoreEvent
from cortex_mem.index import Index
from cortex_mem.runtime import Runtime
from cortex_mem.store.memory import InMemoryDatabase

from harness.config import CoreEmbedding
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.core_embedding import QwenEmbedder

# The harness has no org scope, and "team" sees what `can_see` allows: own records and the team's.
_QUERY_SCOPE = "team"
_KINDS: dict[str, Kind] = {"agent_record": "record", "ci_result": "outcome", "human_confirm": "human_confirm", "commit": "commit"}


def _core_commit() -> str:
    """The cortex-mem commit pinned by the install, or "unknown" (e.g. an editable checkout)."""
    try:
        direct_url = distribution("cortex-mem").read_text("direct_url.json")
    except PackageNotFoundError:
        return "unknown"
    return json.loads(direct_url or "{}").get("vcs_info", {}).get("commit_id", "unknown")


def to_core_event(event: Event) -> CoreEvent:
    """Map one harness event to the core shape (core `docs/contract.md` section 4).

    A CI result or human confirmation with no refs judges nothing, and `Runtime.append` refuses
    such an outcome, so it goes in as a standalone record carrying the same signal.

    Args:
        event: The harness event.

    Returns:
        The core event; `meta` becomes `conditions`.
    """
    kind = _KINDS[event.kind]
    if kind in ("outcome", "human_confirm") and not event.refs:
        kind = "record"
    return CoreEvent(
        id=event.id,
        kind=kind,
        at=event.t,
        user=event.user,
        team=event.team,
        session=event.session,
        scope=event.scope,
        text=event.text,
        signal=event.signal,
        outcome=event.outcome,
        conditions=event.meta,
        refs=event.refs,
        repo=event.repo,
    )


class CoreBridge(BaseEngine):
    """A core engine as a harness engine.

    Args:
        engine: The core engine. Its failures propagate, so the replay marks the scenario errored
            rather than scoring around them.
        embedding: Optional local Qwen settings for the scenario index.
    """

    def __init__(self, engine: CoreEngine, embedding: CoreEmbedding | None = None) -> None:
        self.engine: CoreEngine = engine
        self.embedding: CoreEmbedding | None = embedding
        self.embedder: QwenEmbedder | None = None
        self.runtime: Runtime | None = None

    @override
    def info(self) -> EngineInfo:
        """Describe the wrapped engine: the runtime gives it a virtual clock, sources and scopes."""
        config: dict[str, object] = {"core_commit": _core_commit(), "store": InMemoryDatabase.__name__}
        if self.embedding is not None:
            config.update(core_embedding=self.embedding.model_dump())
        return EngineInfo(
            name=type(self.engine).__name__,
            version=f"contract-{CONTRACT_VERSION}",
            config=config,
            virtual_clock=True,
            sources=True,
            scopes=True,
        )

    @override
    def reset(self, namespace: str) -> None:
        """Start a new database and runtime; `namespace` is not needed since nothing is shared."""
        db = InMemoryDatabase()
        index = None
        if self.embedding is not None:
            self.embedder = QwenEmbedder(self.embedding)
            index = Index(db.vectors, self.embedder, self.embedding.group_threshold, db.transaction)
        # Explicit: a skipped event would score an engine around its own failure.
        self.runtime = Runtime(
            self.engine, db.events, db.knowledge, db.state("engine"), db.transaction, max_failures=None, index=index
        )

    @override
    def record(self, event: Event) -> None:
        """Append the event; the runtime delivers it to the engine."""
        _ = self._runtime().append(to_core_event(event))
        self._check_embedding()

    @override
    def search(self, query: Query) -> list[Result]:
        """Hand off through the runtime without logging a recall (design 4.5).

        A recall event would differ per engine, so engines would no longer see the same events,
        and read-only probes would change the memory they measure.
        """
        caller = Caller(
            user=query.user,
            team=query.team,
            session=query.session,
            at=query.t,
            repo=query.repo,
            context=query.meta,
        )
        handoffs = self._runtime().handoff(query.text, caller, _QUERY_SCOPE, query.k)
        self._check_embedding()
        return [Result(text=h.content, sources=list(h.sources)) for h in handoffs]

    @override
    def tick(self, t: datetime) -> None:
        """Pass virtual time on to the engine."""
        self._runtime().tick(t)
        self._check_embedding()

    @override
    def flush(self) -> None:
        """Deliver anything still pending; replay calls this before every probe.

        Raises:
            RuntimeError: If the runtime skipped an event (design 4.5).
        """
        runtime = self._runtime()
        _ = runtime.sync()
        self._check_embedding()
        if runtime.skipped:
            raise RuntimeError(f"the runtime skipped events: {runtime.skipped}")

    def _check_embedding(self) -> None:
        # Core may respond without embeddings during an outage, which would invalidate a comparison.
        if self.embedder is not None and self.embedder.failure is not None:
            raise self.embedder.failure

    def _runtime(self) -> Runtime:
        if self.runtime is None:
            raise RuntimeError("reset(namespace) must be called before record/search")
        return self.runtime
