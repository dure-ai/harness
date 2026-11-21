"""Wall-clock timing and engine-reported usage of a bench run (table 4)."""

import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import override

from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.metrics import _percentile
from harness.replay import ScenarioRun

UNKNOWN = "알 수 없음"
USAGE_KEYS = ("llm_calls", "input_tokens", "output_tokens", "embed_calls")


class TimedEngine(BaseEngine):
    """Wraps an engine and records the wall-clock milliseconds of each call.

    Results, `info()` and `tick` pass through unchanged, so replaying the wrapper scores like
    replaying the engine.

    Attributes:
        ms: Call name (`reset`, `record`, `flush`, `search`) to the milliseconds of each call.
    """

    def __init__(self, inner: BaseEngine) -> None:
        self.inner: BaseEngine | None = inner
        self.reported: dict[str, float] | None = None
        self.ms: dict[str, list[float]] = {"reset": [], "record": [], "flush": [], "search": []}

    def _engine(self) -> BaseEngine:
        if self.inner is None:
            raise RuntimeError("the engine was released by finish()")
        return self.inner

    def finish(self) -> None:
        """Snapshot the engine's `usage()` once and release the engine, keeping only the timings.

        An engine whose `usage()` raises reports nothing: its failure must not replace the
        scenario's own outcome or stop the run.
        """
        if self.inner is not None:
            try:
                self.reported = self.inner.usage()
            except Exception:
                self.reported = None
            finally:
                self.inner = None

    def _timed[T](self, name: str, call: Callable[[], T]) -> T:
        t0 = time.perf_counter()
        try:
            return call()
        finally:
            self.ms[name].append((time.perf_counter() - t0) * 1000)

    @override
    def info(self) -> EngineInfo:
        return self._engine().info()

    @override
    def reset(self, namespace: str) -> None:
        self._timed("reset", lambda: self._engine().reset(namespace))

    @override
    def record(self, event: Event) -> None:
        self._timed("record", lambda: self._engine().record(event))

    @override
    def search(self, query: Query) -> list[Result]:
        return self._timed("search", lambda: self._engine().search(query))

    @override
    def tick(self, t: datetime) -> None:
        self._engine().tick(t)

    @override
    def flush(self) -> None:
        self._timed("flush", self._engine().flush)

    @override
    def usage(self) -> dict[str, float] | None:
        return self.reported if self.inner is None else self.inner.usage()


def _ms(values: list[float], p: int) -> str:
    v = _percentile(values, p)
    return "n/a" if v is None else f"{v:.2f}"


def cost_rows(timed: Mapping[str, list[TimedEngine]], runs: Mapping[str, list[ScenarioRun]]) -> list[list[str]]:
    """Table 4 rows, one per engine that has at least one wrapper, in `timed` order.

    Columns: engine name, p50 and p95 of record, flush and search ms, mean `context_chars` per
    probe, then `llm_calls`, `input_tokens`, `output_tokens`, `embed_calls`. A usage cell sums
    the scenarios whose `usage()` returned that key; it says unknown when none did.

    Args:
        timed: Engine name to its wrappers, one per scenario run.
        runs: Engine name to its scenario runs.
    """
    rows: list[list[str]] = []
    for name, wrappers in timed.items():
        if not wrappers:
            continue
        ms = {k: [v for w in wrappers for v in w.ms[k]] for k in ("record", "flush", "search")}
        chars = [p.context_chars for r in runs.get(name, []) for p in r.probes]
        usages = [u for w in wrappers if (u := w.usage()) is not None]
        row = [name]
        for k in ("record", "flush", "search"):
            row += [_ms(ms[k], 50), _ms(ms[k], 95)]
        row.append(f"{sum(chars) / len(chars):.1f}" if chars else "n/a")
        for key in USAGE_KEYS:
            vals = [u[key] for u in usages if key in u]
            row.append(f"{sum(vals):g}" if vals else UNKNOWN)
        rows.append(row)
    return rows

