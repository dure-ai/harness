"""Run an engine written to the cortex-mem port (`observe/tick/handoff`) under the harness.

The harness drives `BaseEngine`; this wraps a core engine in a fresh `Runtime` and
`InMemoryDatabase` per scenario, so a score describes the code the MCP server runs, decision
checks and handoff filter included. Comparisons stay on the in-memory store because store
implementations score search differently (core `docs/contract.md` section 4).
"""

from cortex_mem.contract import CONTRACT_VERSION, Caller
from cortex_mem.contract import Engine as CoreEngine
from cortex_mem.contract import Event as CoreEvent
from cortex_mem.runtime import Runtime
from cortex_mem.store.memory import InMemoryDatabase

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result

# The harness has no org scope, and "team" sees what `can_see` allows: own records and the team's.
_QUERY_SCOPE = "team"
_KINDS = {"agent_record": "record", "ci_result": "outcome", "human_confirm": "human_confirm", "commit": "commit"}


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
    """

    def __init__(self, engine: CoreEngine) -> None:
        self.engine = engine
        self.runtime: Runtime | None = None
        self._recalls = 0

    def info(self) -> EngineInfo:
        """Describe the wrapped engine: the runtime gives it a virtual clock, sources and scopes."""
        return EngineInfo(
            name=type(self.engine).__name__,
            version=f"contract-{CONTRACT_VERSION}",
            virtual_clock=True,
            sources=True,
            scopes=True,
        )

    def reset(self, namespace: str) -> None:
        """Start a new database and runtime; `namespace` is not needed since nothing is shared."""
        db = InMemoryDatabase()
        self.runtime = Runtime(self.engine, db.events, db.knowledge, db.state("engine"), db.transaction)
        self._recalls = 0

    def record(self, event: Event) -> None:
        """Append the event; the runtime delivers it to the engine."""
        self.runtime.append(to_core_event(event))

    def search(self, query: Query) -> list[Result]:
        """Recall through the runtime, as the server does, so the engine observes what it handed off."""
        self._recalls += 1
        caller = Caller(
            user=query.user,
            team=query.team,
            session=query.session,
            at=query.t,
            repo=query.repo,
            context=query.meta,
        )
        handoffs = self.runtime.recall(query.text, caller, _QUERY_SCOPE, f"recall-{self._recalls}", query.k)
        return [Result(text=h.content, sources=list(h.sources)) for h in handoffs]

    def tick(self, t) -> None:
        """Pass virtual time on to the engine."""
        self.runtime.tick(t)

    def flush(self) -> None:
        """Deliver anything still pending."""
        self.runtime.sync()
