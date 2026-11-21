"""Running converted scenarios against engines."""

import dataclasses
import hashlib
from collections.abc import Iterable, Set
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Literal

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
    groups: dict[str, str]
    skipped: list[tuple[str, str]]
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
    out = BenchRun(dataset, "gold" if gold else "normal", {name: [] for name in engines}, {}, [], {}, {})
    streak = dict.fromkeys(engines, 0)
    skip: dict[str, ScenarioRun] = {}  # engine to the run that tripped it
    checking = {name for name in engines if name in tripped}
    for c in converted:
        if c.scenario is None:
            out.skipped.append((c.source_id, c.skipped or "skipped"))
            continue
        s = gold_copy(c.scenario) if gold else c.scenario
        out.groups[s.id] = c.group
        for name in engines:
            if name in skip:
                last = skip[name]
                out.runs[name].append(
                    ScenarioRun(s.id, s.family, s.mode, error=last.error, status="skipped", error_kind=last.error_kind)
                )
                continue
            run = _run_one(name, s, cfg, run_id, key, out)
            out.runs[name].append(run)
            down = run.status == "unavailable"
            if name in checking:
                checking.discard(name)
                if down:
                    skip[name] = dataclasses.replace(run, error=f"skipped: {name} still unavailable; last: {run.error}")
                    out.tripped.add(name)
                continue
            streak[name] = streak[name] + 1 if down else 0
            if max_unavailable is not None and streak[name] >= max_unavailable:
                reason = f"skipped: {name} was unavailable for {streak[name]} scenarios in a row; last: {run.error}"
                skip[name] = dataclasses.replace(run, error=reason)
                out.tripped.add(name)
    return out


def _run_one(name: str, s: Scenario, cfg: BenchConfig, run_id: str, key: bytes, out: BenchRun) -> ScenarioRun:
    """Build `name` for `s` and replay it, recording its info and timings in `out`."""
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
        del engine, wrapper  # keep no engine alive between scenarios


_LABEL_FILES = {"longmemeval-s": "longmemeval_ku_labels.yaml"}
CONVERTER_VERSION = 1


def dataset_facts(ds: Dataset, file_sha256: dict[str, str]) -> dict[str, Any]:
    """What a report records about the data: file checksums, converter and label file checksum.

    Args:
        ds: The dataset.
        file_sha256: File name to the sha256 computed from the cached file, see `verify_cached`.
    """
    label = _LABEL_FILES.get(ds.name)
    label_sha = (
        hashlib.sha256((resources.files("harness.bench.datasets") / label).read_bytes()).hexdigest() if label else None
    )
    return {
        "dataset": ds.name,
        "license": ds.license,
        "file_sha256": dict(file_sha256),
        "converter": f"{ds.name}/{CONVERTER_VERSION}",
        "label_sha256": label_sha,
    }
