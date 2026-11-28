"""Memory arms. Every arm sees the same records and answers the same recall.

    none          nothing is handed over
    full          every record, in the order written (no retrieval)
    engine:<path> a cortex-mem engine (module:Class) behind the core Runtime with the
                  in-memory stores: records go in as `record` events, recall is `handoff`
    oss:mem0      a self-hosted Mem0 server, through the evaluation harness's own adapter
    oss:hindsight a self-hosted Hindsight server, likewise

The oss arms use the harness adapters unchanged (harness.oss.mem0 / harness.oss.hindsight):
one namespace per history, every record stored as one team-visible memory, recall as one
search. The tools' own LLM and embedder are whatever the containers were started with.
A history already stored under the same namespace is not stored again (ingest is the
expensive part); `root/ingested/` holds the markers.

A record is (text, author, at); recall returns texts, best first. Benchmarks here carry
no outside checks, so every record is an agent's own word (signal "self").
"""

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Record:
    """One stored item: an id, its text, who wrote it and when."""

    id: str
    text: str
    author: str
    at: datetime


class NoMemory:
    """Hands over nothing."""

    def __init__(self, records: list[Record]) -> None:
        pass

    def recall(self, query: str, k: int) -> list[str]:
        """Return the texts handed over for a query, best first."""
        return []


class FullContext:
    """Hands over every record, in the order written."""

    def __init__(self, records: list[Record]) -> None:
        self.records = records

    def recall(self, query: str, k: int) -> list[str]:
        """Return the texts handed over for a query, best first."""
        return [r.text for r in self.records]


class EngineMemory:
    """A cortex-mem engine behind the core Runtime over one in-memory database.

    Built the way the harness's `CoreBridge` builds it: one `InMemoryDatabase`, its transaction,
    and, when an embedder is given, an `Index` over the database's vectors with the configured
    group threshold. Without an embedder the engine runs with no index.

    Raises (from the constructor and `recall`):
        RuntimeError: If the runtime skipped an event, or the embedder failed at any point;
            an engine scored around its own failure would not be comparable.
    """

    def __init__(self, records: list[Record], engine: str, embedder=None, group_threshold: float | None = None) -> None:
        from cortex_mem.contract import Caller, Event
        from cortex_mem.index import Index
        from cortex_mem.runtime import Runtime
        from cortex_mem.store.memory import InMemoryDatabase

        module, _, cls = engine.partition(":")
        db = InMemoryDatabase()
        index = None
        if embedder is not None:
            if group_threshold is None:
                raise ValueError("an embedder needs a group threshold")
            index = Index(db.vectors, embedder, group_threshold, db.transaction)
        self.embedder = embedder
        self.rt = Runtime(getattr(importlib.import_module(module), cls)(), db.events, db.knowledge,
                          db.state("engine"), db.transaction, max_failures=None, index=index)
        last = max((r.at for r in records), default=datetime(2000, 1, 1, tzinfo=UTC))
        for r in records:
            _ = self.rt.append(Event(id=r.id, kind="record", at=r.at, user=r.author, team="t",
                                     session=f"s-{r.author}", scope="team", text=r.text, signal="self"))
        _ = self.rt.sync()
        self._check()
        self.caller = Caller(user="asker", team="t", session="s-asker", at=last + timedelta(seconds=1))

    def recall(self, query: str, k: int) -> list[str]:
        """Return the texts handed over for a query, best first."""
        # What the engine hands over, as it hands it over (its basis line included).
        handed = [h.content for h in self.rt.handoff(query, self.caller, "team", k)]
        self._check()
        return handed

    def _check(self) -> None:
        if self.rt.skipped:
            raise RuntimeError(f"the runtime skipped events: {self.rt.skipped}")
        # Core may answer without vectors during an outage, which would quietly change the engine.
        if self.embedder is not None and self.embedder.failure is not None:
            raise RuntimeError(f"embedding failed: {self.embedder.failure}")


class OssMemory:
    """A self-hosted tool behind the harness adapter, stored once per history."""

    def __init__(self, records: list[Record], tool: str, namespace: str, root) -> None:
        import json
        import os
        import time

        from harness.config import LLM, EvalConfig
        from harness.contract import Event, Query
        from harness.oss.text import engine_ns

        cfg = EvalConfig(llm=LLM(provider="upstage", model=os.environ["HARNESS_LLM_MODEL"],
                                 embedding_model=os.environ["HARNESS_EMBEDDING_MODEL"]))
        if tool == "mem0":
            from harness.oss.mem0 import Mem0Engine as Engine
        elif tool == "hindsight":
            from harness.oss.hindsight import HindsightEngine as Engine
        else:
            raise SystemExit(f"unknown tool {tool!r}")
        self.engine = Engine(cfg)
        self.Query = Query
        self.at = max((r.at for r in records), default=datetime(2000, 1, 1, tzinfo=UTC)) + timedelta(seconds=1)
        marker = root / "ingested" / f"{tool}-{engine_ns(namespace)}.json"
        self.ingest_seconds = 0.0
        self.asked = 0.0  # when the last search went out; searches are spaced (the embedder's per-minute limit)
        self.sources: list[str] = []  # ids of the records behind the last recall, as the tool reports them
        if stored(f"oss:{tool}", records, namespace, root):
            # Stored by an earlier run: point the adapter at that namespace without resetting it.
            setattr(self.engine, "_run" if tool == "mem0" else "_bank", engine_ns(namespace))
            self.engine._event_ids = {r.id for r in records}
            return
        started = time.time()
        # A history stopped part-way: `partial` says how many records, in order, the tool had taken.
        partial = marker.with_suffix(".partial.json")
        done = json.loads(partial.read_text())["records_done"] if partial.exists() else 0
        if done:
            setattr(self.engine, "_run" if tool == "mem0" else "_bank", engine_ns(namespace))
            self.engine._event_ids = {r.id for r in records[:done]}
        else:
            self.engine.reset(namespace)
        marker.parent.mkdir(parents=True, exist_ok=True)
        for n, r in enumerate(records):
            if n < done:
                continue
            event = Event(id=r.id, t=r.at, user=r.author, session="s", repo="chat", scope="team", team="t",
                          signal="self", kind="agent_record", outcome="unknown", text=r.text)
            _patiently(lambda: self.engine.record(event))
            partial.write_text(json.dumps({"namespace": namespace, "records_done": n + 1, "of": len(records)}))
        self.engine.flush()
        self.ingest_seconds = round(time.time() - started, 1)
        partial.unlink(missing_ok=True)
        marker.write_text(json.dumps({"namespace": namespace, "records": len(records), "seconds": self.ingest_seconds,
                                      "resumed_after": done,
                                      "info": self.engine.info().model_dump(mode="json")}))

    def recall(self, query: str, k: int) -> list[str]:
        """Return the texts handed over for a query, best first."""
        import time

        time.sleep(max(0.0, self.asked + SEARCH_GAP - time.time()))
        self.asked = time.time()
        q = self.Query(text=query, user="asker", team="t", session="s", repo="chat", t=self.at, k=k)
        results = _patiently(lambda: self.engine.search(q))
        self.sources = [s for r in results for s in r.sources]
        return [r.text for r in results]


SEARCH_GAP = 1.0  # seconds between two searches of a tool


def _patiently(call, attempts: int = 8):
    """Try again when the tool passes on its provider's rate limit as a server error."""
    import time

    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if attempt == attempts - 1 or "429" not in str(exc):
                raise
            time.sleep(min(10 * 2**attempt, 120))


def stored(arm: str, records: list[Record], namespace: str, root) -> bool:
    """Tell whether an oss arm already holds this history, so making it costs nothing.

    `namespace` here is the full one, as OssMemory gets it.
    """
    import json

    from harness.oss.text import engine_ns

    marker = root / "ingested" / f"{arm[len('oss:'):]}-{engine_ns(namespace)}.json"
    return marker.exists() and json.loads(marker.read_text())["records"] == len(records)


def make(arm: str, records: list[Record], namespace: str = "", root=None, embedder=None, embed_settings=None):
    """Build the memory an arm names over these records.

    `embedder` and `embed_settings` (see `embedding.settings_from_env`) apply to engine arms only.
    """
    if arm.startswith("oss:"):
        return OssMemory(records, arm[len("oss:"):], f"{namespace}|{arm}", root)
    if arm == "none":
        return NoMemory(records)
    if arm == "full":
        return FullContext(records)
    if arm.startswith("engine:"):
        threshold = embed_settings["group_threshold"] if embed_settings else None
        return EngineMemory(records, arm[len("engine:"):], embedder, threshold)
    raise SystemExit(f"unknown arm {arm!r}")


def core_facts() -> dict:
    """Which cortex-mem the engine arms import, so a summary says what ran.

    The commit comes from the checkout `EXTBENCH_CORE` points at when it is a git repository,
    otherwise from the installed package's pin; "unknown" when neither says.
    """
    import json
    import subprocess
    from importlib.metadata import PackageNotFoundError, distribution
    from pathlib import Path

    import cortex_mem

    path = Path(cortex_mem.__file__).resolve().parent
    commit, dirty = "unknown", None
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        commit = out.stdout.strip()
        status = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--", "."],
                                capture_output=True, text=True, check=True)
        dirty = bool(status.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        try:
            direct = distribution("cortex-mem").read_text("direct_url.json") or "{}"
            commit = json.loads(direct).get("vcs_info", {}).get("commit_id", "unknown")
        except PackageNotFoundError:
            pass
    return {"path": str(path), "commit": commit, "uncommitted_changes": dirty}
