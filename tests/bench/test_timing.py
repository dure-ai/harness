import gc
import weakref
from dataclasses import asdict
from typing import Any

from typing import override

from harness.baselines.simple import NoneEngine
from harness.bench.config import BenchConfig
from harness.bench.report import write_bench_report
from harness.bench.run import run_bench
from harness.bench.timing import UNKNOWN, TimedEngine, cost_rows
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.engines import make_engine
from harness.replay import ScenarioRun, blind_key, run_scenario
from tests.bench.test_run import converted


class Reporting(BaseEngine):
    """Echoes nothing, and reports two LLM calls per scenario."""

    @override
    def info(self) -> EngineInfo:
        return EngineInfo(name="reporting", version="0")

    @override
    def reset(self, namespace: str) -> None:
        pass

    @override
    def record(self, event: Event) -> None:
        pass

    @override
    def search(self, query: Query) -> list[Result]:
        return []

    @override
    def usage(self) -> dict[str, float] | None:
        return {"llm_calls": 2, "input_tokens": 10}


def scenario():
    s = converted()[0].scenario
    assert s is not None
    return s


def probe_view(run: ScenarioRun) -> list[dict[str, Any]]:
    drop = {"search_ms"}
    return [{k: v for k, v in asdict(p).items() if k not in drop} for p in run.probes]


def test_usage_defaults_to_none():
    assert NoneEngine().usage() is None


def test_timed_engine_delegates_and_times_each_call():
    s = scenario()
    cfg = BenchConfig().eval
    plain = run_scenario(make_engine("oracle", s, cfg), s, run_id="t", cfg=cfg, blind_key=blind_key(1))
    inner = make_engine("oracle", s, cfg)
    timed = TimedEngine(inner)
    wrapped = run_scenario(timed, s, run_id="t", cfg=cfg, blind_key=blind_key(1))
    assert timed.info() == inner.info()
    assert probe_view(wrapped) == probe_view(plain) and wrapped.error is None
    assert [p.probe_id for p in wrapped.probes] == [p.probe_id for p in plain.probes]
    assert len(timed.ms["reset"]) == 1
    assert len(timed.ms["record"]) == len(s.events)
    assert len(timed.ms["search"]) == len(s.probes) == len(timed.ms["flush"])
    assert all(ms >= 0 for v in timed.ms.values() for ms in v)


def test_cost_table_says_unknown_without_usage():
    b = run_bench(converted(), ["oracle"], BenchConfig(), run_id="t", key=blind_key(1))
    (row,) = cost_rows(b.timed, b.runs)
    assert row[0] == "oracle"
    assert row[-4:] == ["알 수 없음"] * 4
    assert float(row[7]) > 0  # mean context chars of the oracle


def test_cost_table_sums_reported_usage():
    b = run_bench(converted(), ["tests.bench.test_timing:Reporting"], BenchConfig(), run_id="t", key=blind_key(1))
    assert len(b.timed["tests.bench.test_timing:Reporting"]) == 3
    (row,) = cost_rows(b.timed, b.runs)
    # llm_calls 2 per scenario, input_tokens 10 per scenario; output and embed never reported
    assert row[-4:] == ["6", "30", "알 수 없음", "알 수 없음"]


def test_an_engine_that_fails_to_build_has_no_row():
    b = run_bench(converted(), ["oracle", "tests.bench.test_timing:Missing"], BenchConfig(), run_id="t", key=blind_key(1))
    assert b.timed.get("tests.bench.test_timing:Missing", []) == []
    assert [r[0] for r in cost_rows(b.timed, b.runs)] == ["oracle"]


def test_report_has_table_4(tmp_path):
    b = run_bench(converted(), ["oracle"], BenchConfig(), run_id="t", key=blind_key(1))
    meta = {
        "run_id": "t", "started_at": "x", "harness_version": "0", "config_hash": "h", "machine": "m",
        "dataset": "d", "license": "MIT", "file_sha256": {}, "converter": "c", "label_sha256": None, "blind_seed": 1,
    }
    write_bench_report(tmp_path, meta, b, None)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 표 4. 비용" in text
    assert "| 엔진 |" in text.split("## 표 4.")[1]


class Counting(Reporting):
    """Reports a usage that changes after the scenario is over."""

    instances: "list[weakref.ref[Counting]]" = []
    calls = 0

    def __init__(self) -> None:
        self.after = False
        Counting.instances.append(weakref.ref(self))

    @override
    def reset(self, namespace: str) -> None:
        self.after = False

    @override
    def usage(self) -> dict[str, float] | None:
        Counting.calls += 1
        return {"llm_calls": 100 if self.after else 1}


def test_usage_is_read_once_right_after_each_scenario_and_no_engine_is_kept():
    Counting.instances, Counting.calls = [], 0
    b = run_bench(converted(), ["tests.bench.test_timing:Counting"], BenchConfig(), run_id="t", key=blind_key(1))
    assert Counting.calls == 3 and len(Counting.instances) == 3
    gc.collect()
    assert all(ref() is None for ref in Counting.instances)
    (row,) = cost_rows(b.timed, b.runs)
    assert row[-4] == "3"
    assert Counting.calls == 3  # the report reads the snapshot, not the engine


class BadUsage(Reporting):
    @override
    def usage(self) -> dict[str, float] | None:
        raise RuntimeError("usage is down")


def test_an_engine_whose_usage_raises_ends_no_more_than_its_scenario():
    b = run_bench(converted(), ["tests.bench.test_timing:BadUsage", "oracle"], BenchConfig(), run_id="t", key=blind_key(1))
    runs = b.runs["tests.bench.test_timing:BadUsage"]
    assert len(runs) == 3 and all(r.error is None for r in runs)  # the scenario itself ran fine
    assert all(r.error is None for r in b.runs["oracle"])
    assert all(w.reported is None and w.inner is None for w in b.timed["tests.bench.test_timing:BadUsage"])
    (row, _) = cost_rows(b.timed, b.runs)
    assert row[-4:] == [UNKNOWN] * 4
