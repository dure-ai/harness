"""Running converted scenarios against engines."""

import hashlib
from collections.abc import Iterable
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
    """

    dataset: str
    mode: Literal["normal", "gold"]
    runs: dict[str, list[ScenarioRun]]
    groups: dict[str, str]
    skipped: list[tuple[str, str]]
    infos: dict[str, EngineInfo] = field(default_factory=dict)
    timed: dict[str, list[TimedEngine]] = field(default_factory=dict)


def run_bench(
    converted: Iterable[Converted],
    engines: list[str],
    cfg: BenchConfig,
    *,
    run_id: str,
    key: bytes,
    gold: bool = False,
    dataset: str = "",
) -> BenchRun:
    """Replay every converted scenario against a fresh instance of every engine.

    Skipped items go to `BenchRun.skipped`. An engine that fails to build or raises during a
    scenario gets an errored `ScenarioRun` for it, and the run goes on.

    Args:
        converted: Items to run, typically `screened(dataset.convert(...), cfg.eval)`; read once.
        engines: Engine names as `make_engine` takes them.
        cfg: Bench config.
        run_id: Id of the run, part of every engine namespace.
        key: Namespace key, see `replay.blind_key`.
        gold: Drop the events no probe asks about (spec section 6).
        dataset: Dataset name, recorded in the result.
    """
    out = BenchRun(dataset, "gold" if gold else "normal", {name: [] for name in engines}, {}, [], {}, {})
    for c in converted:
        if c.scenario is None:
            out.skipped.append((c.source_id, c.skipped or "skipped"))
            continue
        s = gold_copy(c.scenario) if gold else c.scenario
        out.groups[s.id] = c.group
        for name in engines:
            try:
                engine = make_engine(name, s, cfg.eval)
                if name not in out.infos:
                    out.infos[name] = engine.info()
            except Exception as e:
                run = ScenarioRun(
                    s.id,
                    s.family,
                    s.mode,
                    error=f"{type(e).__name__}: {e}",
                    error_kind=error_kind_of(e),
                    status="unavailable" if isinstance(e, EngineUnavailable) else "error",
                )
            else:
                wrapper = TimedEngine(engine)
                out.timed.setdefault(name, []).append(wrapper)
                try:
                    run = run_scenario(wrapper, s, run_id=run_id, cfg=cfg.eval, blind_key=key)
                finally:
                    wrapper.finish()
                    del engine, wrapper  # keep no engine alive between scenarios
            out.runs[name].append(run)
    return out


_LABEL_FILES = {"longmemeval-s": "longmemeval_ku_labels.yaml"}
CONVERTER_VERSION = 1


def dataset_facts(ds: Dataset) -> dict[str, Any]:
    """What a report records about the data: file checksums, converter and label file checksum."""
    label = _LABEL_FILES.get(ds.name)
    label_sha = (
        hashlib.sha256((resources.files("harness.bench.datasets") / label).read_bytes()).hexdigest() if label else None
    )
    return {
        "dataset": ds.name,
        "license": ds.license,
        "file_sha256": {f.name: f.sha256 for f in ds.files()},
        "converter": f"{ds.name}/{CONVERTER_VERSION}",
        "label_sha256": label_sha,
    }
