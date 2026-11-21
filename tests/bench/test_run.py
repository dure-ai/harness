import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from harness.bench.check import screened
from harness.bench.config import BenchConfig
from harness.bench.dataset import Converted, bench_cache
from harness.bench.datasets import longmemeval
from harness.bench.datasets.longmemeval import LongMemEvalS, convert_question
from harness.bench.run import run_bench
from harness.cli import app
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.replay import blind_key
from tests.bench.lme_fixture import lme_question, write_lme

runner = CliRunner()
QDATE = "2023/07/01 (Sat) 10:00"
HEADER = "**외부 벤치마크 트랙. 사전 등록한 판정 기준이 아니며 `harness run` 결과와 섞지 않는다.**"
LABELS = {"ku_new": "new", "ku_old": "old"}


def ku(qid: str) -> dict[str, Any]:
    return lme_question(
        qid,
        "knowledge-update",
        [
            ("2023/05/20 (Sat) 02:21", [("user", f"{qid} I use Vim"), ("assistant", "ok")]),
            ("2023/06/20 (Tue) 08:00", [("user", f"{qid} I switched to Emacs"), ("assistant", "noted")]),
        ],
        [0, 1],
        QDATE,
    )


def fixture_questions() -> list[dict[str, Any]]:
    return [
        ku("ku_new"),
        ku("ku_old"),
        lme_question(
            "o1",
            "single-session-user",
            [
                ("2023/05/01 (Mon) 09:00", [("user", "I bought a red bike"), ("assistant", "nice")]),
                ("2023/05/02 (Tue) 09:00", [("user", "weather talk")]),
            ],
            [0],
            QDATE,
        ),
        lme_question("x_abs", "single-session-user", [("2023/05/01 (Mon) 09:00", [("user", "q")])], [0], QDATE),
    ]


@pytest.fixture
def cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The fixture dataset in the cache, with the declared size adjusted and the labels replaced."""
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = bench_cache("longmemeval-s") / "longmemeval_s_cleaned.json"
    path.parent.mkdir(parents=True)
    write_lme(path, fixture_questions())
    real = LongMemEvalS.files
    size = path.stat().st_size
    monkeypatch.setattr(LongMemEvalS, "files", lambda self: [dataclasses.replace(f, size=size) for f in real(self)])
    monkeypatch.setattr(longmemeval, "load_ku_labels", lambda: LABELS)
    return path


def converted() -> list[Converted]:
    return [convert_question(q, LABELS) for q in fixture_questions()]


def bench_run(tmp_path: Path, *extra: str):
    out = tmp_path / "out"
    r = runner.invoke(
        app,
        ["bench", "run", "--dataset", "longmemeval-s", "--yes", "--out", str(out), "--blind-seed", "1", *extra],
    )
    return r, out


def report_dir(out: Path) -> Path:
    (d,) = list((out / "longmemeval-s").iterdir())
    return d


def test_run_without_the_data_says_how_to_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "empty"))
    r, out = bench_run(tmp_path)
    assert r.exit_code == 2
    assert "run 'harness bench fetch --dataset longmemeval-s' first" in r.output
    assert not out.exists()


def test_run_rejects_an_unknown_dataset_and_an_unknown_engine(tmp_path: Path, cached: Path):
    r = runner.invoke(app, ["bench", "run", "--dataset", "nope", "--yes"])
    assert r.exit_code == 2 and "unknown dataset" in r.output
    r, _ = bench_run(tmp_path, "--engine", "bogus")
    assert r.exit_code == 2 and "unknown engine 'bogus'" in r.output


def test_run_on_the_fixture_scores_baselines_and_writes_the_report(tmp_path: Path, cached: Path):
    before = sorted(p.name for p in Path("config").iterdir()), sorted(p.name for p in Path("scenarios").iterdir())
    r, out = bench_run(tmp_path)
    assert r.exit_code == 0, r.output
    assert "scenarios: 3 (skipped 1), events: " in r.output
    d = report_dir(out)
    assert sorted(p.name for p in d.iterdir()) == ["engine_ids.json", "report.md", "results.jsonl", "summary.json"]
    text = (d / "report.md").read_text(encoding="utf-8")
    assert text.splitlines()[0] == HEADER
    assert "longmemeval-s/1" in text and "MIT" in text and "x_abs" in text and "abstention" in text
    rows = {
        (cells[0], cells[1]): cells
        for line in text.splitlines()
        if line.startswith("| ") and len(cells := [c.strip() for c in line.strip("| ").split(" | ")]) >= 5
    }
    all_recall = [c for k, c in rows.items() if k == ("oracle", "ext_recall")][0]
    assert any(c.startswith("1.000") for c in all_recall)  # oracle M2
    none_rows = [c for k, c in rows.items() if k[0] == "none"]
    assert none_rows and all(any(c.startswith("0.000") for c in row) for row in none_rows)
    assert "## 표 2. 근거 도달(M2)" in text.splitlines()
    assert ("oracle", "knowledge-update") in rows and ("oracle", "single-session-user") in rows
    assert "knowledge-update" in text.split("## 표 5.")[1]
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["mode"] == "normal"
    assert summary["skipped"]["count"] == 1
    assert summary["skipped"]["reasons"] == {"abstention": 1} and summary["skipped"]["ids"] == ["x_abs"]
    assert summary["engines"]["oracle"]["by_family"]["ext_update"]["m2"]["value"] == 1.0
    assert summary["engines"]["none"]["by_family"]["ext_update"]["m2"]["value"] == 0.0
    assert summary["engines"]["oracle"]["by_group"]["knowledge-update"]["m2"]["n"] == 2
    lines = [json.loads(x) for x in (d / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {x["engine_id"] for x in lines} == {"none", "all_store", "repeat_n", "oracle"}
    assert json.loads((d / "engine_ids.json").read_text(encoding="utf-8")) == {"seed": 1, "ids": {}}
    after = sorted(p.name for p in Path("config").iterdir()), sorted(p.name for p in Path("scenarios").iterdir())
    assert before == after


def test_run_with_an_extra_engine_blinds_it(tmp_path: Path, cached: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    r, out = bench_run(tmp_path, "--engine", "tests.dummy_engine:Dummy")
    assert r.exit_code == 0, r.output
    d = report_dir(out)
    ids = json.loads((d / "engine_ids.json").read_text(encoding="utf-8"))["ids"]
    assert ids == {"E1": "tests.dummy_engine:Dummy"}
    assert "tests.dummy_engine" not in (d / "report.md").read_text(encoding="utf-8")


def test_gold_mode_runs_both_and_keeps_only_events_of_facts_a_probe_asks_about(tmp_path: Path, cached: Path):
    r, out = bench_run(tmp_path, "--gold")
    assert r.exit_code == 0, r.output
    d = report_dir(out)
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert summary["gold"]["engines"]["oracle"]["by_family"]["ext_update"]["m2"]["value"] == 1.0
    items = converted()
    assert all(c.scenario is not None for c in items[:3])
    cfg = BenchConfig()
    normal = run_bench(items[:3], ["all_store"], cfg, run_id="t", key=blind_key(1))
    gold = run_bench(items[:3], ["all_store"], cfg, run_id="t", key=blind_key(1), gold=True)
    assert normal.mode == "normal" and gold.mode == "gold"
    for run in gold.runs["all_store"]:
        for rec in run.probes:
            for res in rec.results:
                assert "weather talk" not in res.text and "ok" != res.text and "noted" != res.text


class FlakyOnO1(BaseEngine):
    """Answers nothing, and fails on the question whose id is in the query."""

    def info(self) -> EngineInfo:
        return EngineInfo(name="flaky", version="0")

    def reset(self, namespace: str) -> None:
        pass

    def record(self, event: Event) -> None:
        pass

    def search(self, query: Query) -> list[Result]:
        if "o1" in query.text:
            raise RuntimeError("boom")
        return []


def test_an_engine_error_is_counted_and_the_rest_still_score(tmp_path: Path):
    b = run_bench(converted(), ["oracle", "tests.bench.test_run:FlakyOnO1"], BenchConfig(), run_id="t", key=blind_key(1))
    flaky = {r.scenario_id: r for r in b.runs["tests.bench.test_run:FlakyOnO1"]}
    assert flaky["lme-o1"].error is not None and flaky["lme-o1"].error.startswith("RuntimeError")
    assert flaky["lme-ku_new"].error is None
    assert all(r.error is None for r in b.runs["oracle"])
    assert b.groups == {
        "lme-ku_new": "knowledge-update",
        "lme-ku_old": "knowledge-update",
        "lme-o1": "single-session-user",
    }
    assert b.skipped == [("x_abs", "abstention")]
    assert set(b.infos) == {"oracle", "tests.bench.test_run:FlakyOnO1"}


def test_run_bench_takes_screened_items_and_records_their_skips():
    b = run_bench(screened(converted(), BenchConfig().eval), ["none"], BenchConfig(), run_id="t", key=blind_key(1))
    assert len(b.runs["none"]) == 3 and b.skipped == [("x_abs", "abstention")]


def _strip(obj: Any) -> Any:
    drop = re.compile(r"_ms|latency|started_at|run_id|harness_version|machine")
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not drop.search(k)}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _harness_run(out: Path) -> tuple[Any, list[Any]]:
    r = runner.invoke(app, ["run", "--scenarios", "scenarios/dev", "--blind-seed", "1", "--out", str(out)])
    assert r.exit_code == 0, r.output
    (d,) = list(out.iterdir())
    summary = _strip(json.loads((d / "summary.json").read_text(encoding="utf-8")))
    results = [_strip(json.loads(x)) for x in (d / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    return summary, results


def test_bench_run_does_not_change_harness_run(tmp_path: Path, cached: Path):
    first = _harness_run(tmp_path / "one")
    r, _ = bench_run(tmp_path)
    assert r.exit_code == 0, r.output
    assert _harness_run(tmp_path / "two") == first
