"""Running converted scenarios against engines."""

import hashlib
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from importlib import resources
from datetime import datetime
from typing import Literal, TypedDict

from harness.bench.config import BenchConfig
from harness.bench.dataset import Converted, Dataset
from harness.bench.gold import gold_copy
from harness.bench.timing import TimedEngine
from harness.contract import EngineInfo, EngineUnavailable
from harness.engines import make_engine
from harness.replay import ScenarioRun, error_kind_of, run_scenario
from harness.scenario.model import Scenario


@dataclass
class BenchRun:
    """The outcome of one bench run in one mode.

    Attributes:
        dataset: Dataset name.
        mode: "normal", or "gold" (only events of facts some probe asks about).
        runs: Engine name to its scenario runs, in conversion order.
        groups: Scenario id to the dataset's own type of the item (e.g. LongMemEval `question_type`).
        skipped: (source id, reason) of every item that was not converted or did not pass screening.
        infos: Engine name to its `info()`.
        timed: Engine name to its `TimedEngine` wrappers, one per scenario that built an engine.
        tripped: Engines whose remaining scenarios were skipped because they stayed unavailable.
    """

    dataset: str
    mode: Literal["normal", "gold"]
    runs: dict[str, list[ScenarioRun]]
    groups: dict[str, str] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    infos: dict[str, EngineInfo] = field(default_factory=dict)
    timed: dict[str, list[TimedEngine]] = field(default_factory=dict)
    tripped: set[str] = field(default_factory=set)


def run_bench(
    converted: Iterable[Converted],
    engines: list[str],
    cfg: BenchConfig,
    *,
    run_id: str,
    key: bytes,
    gold: bool = False,
    dataset: str = "",
    max_unavailable: int | None = None,
    tripped: Set[str] = frozenset(),
) -> BenchRun:
    """Replay every converted scenario against a fresh instance of every engine.

    Skipped items go to `BenchRun.skipped`. An engine that fails to build or raises during a
    scenario gets an errored `ScenarioRun` for it, and the run goes on. Like `harness run`, an
    engine that is `unavailable` for `max_unavailable` scenarios in a row is tripped: its
    remaining scenarios are recorded as `skipped` without building it, so a dead server cannot
    stall the run. An engine in `tripped` (tripped in an earlier pass) is tried on the first
    scenario only; if it is still unavailable the rest is skipped, otherwise it is measured again.

    Args:
        converted: Items to run, typically `screened(dataset.convert(...), cfg.eval)`; read once.
        engines: Engine names as `make_engine` takes them.
        cfg: Bench config.
        run_id: Id of the run, part of every engine namespace.
        key: Namespace key, see `replay.blind_key`.
        gold: Drop the events no probe asks about (spec section 6).
        dataset: Dataset name, recorded in the result.
        max_unavailable: Unavailable scenarios in a row that trip an engine; None never trips.
        tripped: Engines tripped in an earlier pass over the same items.
    """
    out = BenchRun(dataset=dataset, mode="gold" if gold else "normal", runs={name: [] for name in engines})
    streak = dict.fromkeys(engines, 0)
    skip: dict[str, tuple[str, str | None]] = {}  # tripped engine to the reason and error kind its skips carry
    checking = {name for name in engines if name in tripped}
    for c in converted:
        if c.scenario is None:
            out.skipped.append((c.source_id, c.skipped or "skipped"))
            continue
        s = gold_copy(c.scenario) if gold else c.scenario
        out.groups[s.id] = c.group
        for name in engines:
            if name in skip:
                reason, kind = skip[name]
                out.runs[name].append(ScenarioRun(s.id, s.family, s.mode, error=reason, status="skipped", error_kind=kind))
                continue
            run = _run_one(name, s, cfg, run_id, key, out)
            out.runs[name].append(run)
            down = run.status == "unavailable"
            if name in checking:
                checking.discard(name)
                if down:
                    skip[name] = (f"skipped: {name} still unavailable; last: {run.error}", run.error_kind)
                    out.tripped.add(name)
                continue
            streak[name] = streak[name] + 1 if down else 0
            if max_unavailable is not None and streak[name] >= max_unavailable:
                reason = f"skipped: {name} was unavailable for {streak[name]} scenarios in a row; last: {run.error}"
                skip[name] = (reason, run.error_kind)
                out.tripped.add(name)
    return out


def _run_one(name: str, s: Scenario, cfg: BenchConfig, run_id: str, key: bytes, out: BenchRun) -> ScenarioRun:
    """Build `name` for `s` and replay it; its first `info()` and its timings go to `out`.

    The engine is released (`TimedEngine.finish`) before returning, so none outlives its scenario.
    """
    try:
        engine = make_engine(name, s, cfg.eval)
        if name not in out.infos:
            out.infos[name] = engine.info()
    except Exception as e:
        return ScenarioRun(
            s.id,
            s.family,
            s.mode,
            error=f"{type(e).__name__}: {e}",
            error_kind=error_kind_of(e),
            status="unavailable" if isinstance(e, EngineUnavailable) else "error",
        )
    wrapper = TimedEngine(engine)
    out.timed.setdefault(name, []).append(wrapper)
    try:
        return run_scenario(wrapper, s, run_id=run_id, cfg=cfg.eval, blind_key=key)
    finally:
        wrapper.finish()


class DatasetFacts(TypedDict):
    """What a report records about the data it ran on."""

    dataset: str
    license: str
    file_sha256: dict[str, str]
    converter: str
    label_sha256: str | None


class BenchMeta(DatasetFacts):
    """Run-level facts of a bench report; `blind_seed` goes to engine_ids.json only."""

    run_id: str
    started_at: datetime
    harness_version: str
    config_hash: str
    machine: str
    blind_seed: int


def dataset_facts(ds: Dataset, file_sha256: Mapping[str, str]) -> DatasetFacts:
    """The facts of a dataset run: file checksums, converter version and label file checksum.

    Args:
        ds: The dataset.
        file_sha256: File name to the sha256 computed from the cached file, see `verify_cached`.
    """
    label_sha = None
    if ds.label_file is not None:
        label_sha = hashlib.sha256((resources.files("harness.bench.datasets") / ds.label_file).read_bytes()).hexdigest()
    return DatasetFacts(
        dataset=ds.name,
        license=ds.license,
        file_sha256=dict(file_sha256),
        converter=f"{ds.name}/{ds.converter_version}",
        label_sha256=label_sha,
    )


def scenario_facts(source: str) -> DatasetFacts:
    """The facts of a run on scenario files (`--scenarios`), which have no dataset behind them."""
    return DatasetFacts(dataset=source, license="n/a", file_sha256={}, converter="n/a", label_sha256=None)
