import json
from pathlib import Path

from typer.testing import CliRunner

from harness.agent import Choice
from harness.bench.config import BenchConfig
from harness.bench.gold import compare_modes, gold_copy, gold_only_wrong_examples
from harness.bench.run import run_bench
from harness.cli import app
from harness.contract import BaseEngine, EngineInfo, Event, Query, Result
from harness.replay import ProbeRecord, ScenarioRun, blind_key
from harness.scenario.load import load_set
from harness.scenario.model import Scenario
from harness.scenario.validate import validate_scenario
from tests.bench.test_run import converted

runner = CliRunner()


def ku_scenario() -> Scenario:
    s = converted()[0].scenario
    assert s is not None
    return s


def test_gold_copy_keeps_only_events_of_probed_facts():
    s = ku_scenario()
    assert [(e.id, e.fact) for e in s.events] == [("s0-t0", "f0"), ("s0-t1", None), ("s1-t0", "f1"), ("s1-t1", None)]
    g = gold_copy(s)
    # the block probe's old (false) fact f0 stays; the assistant noise events go
    assert [e.id for e in g.events] == ["s0-t0", "s1-t0"]
    assert g.probes == s.probes and g.facts == s.facts and g.world == s.world
    assert len(s.events) == 4  # the input is not modified
    only_new = s.model_copy(update={"probes": [p.model_copy(update={"about": ["f1"]}) for p in s.probes]})
    assert [e.id for e in gold_copy(only_new).events] == ["s1-t0"]  # f0 is no longer probed


def test_gold_copy_still_validates():
    scenarios = load_set([Path("scenarios/dev")], include_drafts=False).scenarios
    assert scenarios
    for s in scenarios:
        before = {i.code for i in validate_scenario(s) if i.level == "error"}
        after = {i.code for i in validate_scenario(gold_copy(s)) if i.level == "error"}
        assert after <= before, s.id


def probe(pid: str, kind: str, outcome: str) -> ProbeRecord:
    return ProbeRecord(pid, kind, [], Choice(None, frozenset()), outcome, [], [], False, False, 0.0, 0)  # type: ignore[arg-type]


def run(sid: str, *probes: ProbeRecord, error: str | None = None) -> ScenarioRun:
    return ScenarioRun(sid, "ext_update", "read_only", list(probes), error=error)


def test_compare_modes_counts_the_four_cells():
    normal = [
        run(
            "a",
            probe("b1", "block", "BLOCKED"),  # right, right -> both_right
            probe("p1", "promote", "FN"),  # wrong, right -> retrieval_or_noise
            probe("p2", "promote", "PROMOTE_FP"),  # wrong, wrong -> judgment_or_use
            probe("b2", "block", "BLOCKED"),  # right, wrong -> gold_only_wrong
            probe("l1", "leak", "LEAK_ONLY"),  # ignored
        ),
        run("b", probe("b1", "block", "FP"), probe("p1", "promote", "TP")),  # wrong/right and right/wrong
        run("broken", probe("b1", "block", "FP"), error="RuntimeError: x"),  # errored: ignored
        run("only_normal", probe("b1", "block", "FP")),  # missing from gold: ignored
    ]
    gold = [
        run(
            "a",
            probe("b1", "block", "BLOCKED"),
            probe("p1", "promote", "TP"),
            probe("p2", "promote", "FN"),
            probe("b2", "block", "FP"),
            probe("l1", "leak", "LEAK_ONLY"),
        ),
        run("b", probe("b1", "block", "BLOCKED"), probe("p1", "promote", "FN")),
        run("broken", probe("b1", "block", "BLOCKED")),
    ]
    assert compare_modes(normal, gold) == {
        "both_right": 1,
        "retrieval_or_noise": 2,
        "judgment_or_use": 1,
        "gold_only_wrong": 2,
    }
    assert gold_only_wrong_examples(normal, gold) == [("a", "b2"), ("b", "p1")]
    assert gold_only_wrong_examples(normal, gold, limit=1) == [("a", "b2")]


def test_gold_examples_are_capped_at_five():
    normal = [run("s", *(probe(f"b{i}", "block", "BLOCKED") for i in range(8)))]
    gold = [run("s", *(probe(f"b{i}", "block", "FP") for i in range(8)))]
    assert compare_modes(normal, gold)["gold_only_wrong"] == 8
    assert len(gold_only_wrong_examples(normal, gold)) == 5


class Everything(BaseEngine):
    """Returns every event it was given, noise included."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def info(self) -> EngineInfo:
        return EngineInfo(name="everything", version="0")

    def reset(self, namespace: str) -> None:
        self.events = []

    def record(self, event: Event) -> None:
        self.events.append(event)

    def search(self, query: Query) -> list[Result]:
        return [Result(text=e.text, sources=[e.id]) for e in self.events]


def test_run_bench_gold_uses_gold_copy():
    items = converted()[:1]
    cfg = BenchConfig()
    name = "tests.bench.test_gold:Everything"
    normal = run_bench(items, [name], cfg, run_id="t", key=blind_key(1))
    gold = run_bench(items, [name], cfg, run_id="t", key=blind_key(1), gold=True)

    def texts(b):
        return {res.text for rec in b.runs[name][0].probes for res in rec.results}

    assert normal.mode == "normal" and gold.mode == "gold"
    assert {"ok", "noted"} <= texts(normal)  # positive control: the noise is visible in normal mode
    assert texts(gold) == {"ku_new I use Vim", "ku_new I switched to Emacs"}


def test_gold_run_on_dev_writes_table_3(tmp_path: Path):
    out = tmp_path / "out"
    r = runner.invoke(
        app, ["bench", "run", "--scenarios", "scenarios/dev", "--gold", "--yes", "--out", str(out), "--blind-seed", "1"]
    )
    assert r.exit_code == 0, r.output
    (d,) = [p for p in out.rglob("report.md")]
    text = d.read_text(encoding="utf-8")
    assert "## 표 3. 골드 모드 비교" in text
    section = text.split("## 표 3.")[1].split("\n## ")[0]
    assert "| 엔진 | 둘 다 맞음 | 검색·잡음 | 판단·사용 | 골드에서만 틀림 |" in section
    summary = json.loads((d.parent / "summary.json").read_text(encoding="utf-8"))
    comp = summary["gold"]["comparison"]
    assert set(comp) == {"none", "all_store", "repeat_n", "oracle"}
    for row in comp.values():
        assert set(row["counts"]) == {"both_right", "retrieval_or_noise", "judgment_or_use", "gold_only_wrong"}
        assert len(row["gold_only_wrong_examples"]) <= 5
    assert sum(comp["oracle"]["counts"].values()) > 0
    assert summary["meta"]["dataset"] == "scenarios/dev"


def test_dataset_and_scenarios_are_mutually_exclusive(tmp_path: Path):
    base = ["bench", "run", "--yes", "--out", str(tmp_path)]
    r = runner.invoke(app, base)
    assert r.exit_code == 2 and "exactly one of --dataset and --scenarios" in r.output
    r = runner.invoke(app, [*base, "--dataset", "longmemeval-s", "--scenarios", "scenarios/dev"])
    assert r.exit_code == 2 and "exactly one of --dataset and --scenarios" in r.output
