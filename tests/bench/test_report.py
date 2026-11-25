import json
from pathlib import Path
from typing import Any, override

from harness.bench.config import BenchConfig
from harness.bench.report import write_bench_report
from harness.bench.run import run_bench
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.replay import blind_key
from tests.bench.test_run import converted

META: dict[str, Any] = {
    "run_id": "t", "started_at": "x", "harness_version": "0", "config_hash": "h", "machine": "m",
    "dataset": "d", "license": "MIT", "file_sha256": {}, "converter": "c", "label_sha256": None, "include_drafts": False, "blind_seed": 1,
}


class NoSources(BaseEngine):
    """Returns one result without sources for every query."""

    @override
    def info(self) -> EngineInfo:
        return EngineInfo(name="nosrc", version="0")

    @override
    def reset(self, namespace: str) -> None:
        pass

    @override
    def record(self, event: Event) -> None:
        pass

    @override
    def search(self, query: Query) -> list[Result]:
        return [Result(text="something")]


def write(tmp_path: Path, engines: list[str], items: Any = None) -> tuple[str, dict[str, Any]]:
    b = run_bench(items or converted(), engines, BenchConfig(), run_id="t", key=blind_key(1))
    write_bench_report(tmp_path, dict(META), b, None)
    return (tmp_path / "report.md").read_text(encoding="utf-8"), json.loads((tmp_path / "summary.json").read_text("utf-8"))


def test_the_report_shows_the_share_of_results_without_sources(tmp_path: Path):
    text, summary = write(tmp_path, ["oracle", "tests.bench.test_report:NoSources"])
    assert "출처 없는 결과" in text
    nosrc = summary["engines"]["E1"]["sourceless"]
    assert nosrc["value"] == 1.0 and nosrc["n"] > 0
    assert summary["engines"]["oracle"]["sourceless"]["value"] == 0.0


def test_a_dev_set_report_has_a_family_table_and_no_knowledge_update_note(tmp_path: Path):
    from harness.scenario.load import load_set

    from harness.bench.dataset import Converted

    scenarios = load_set([Path("scenarios/dev")], include_drafts=False).scenarios[:2]
    items = [Converted(s, s.id, s.family, None) for s in scenarios]
    text, summary = write(tmp_path, ["oracle"], items)
    table1 = text.split("## 표 1.")[1].split("## 표 2.")[0]
    families = {s.family for s in scenarios}
    assert all(f"| {f} |" in table1 for f in families)
    assert "## 표 5." not in text
    assert "knowledge-update" not in text.split("## 표 4.")[-1]


def test_ext_families_come_first_in_table_1(tmp_path: Path):
    text, _ = write(tmp_path, ["oracle"])
    table1 = text.split("## 표 1.")[1].split("## 표 1-1")[0]
    assert table1.index("ext_recall") < table1.index("ext_update")
