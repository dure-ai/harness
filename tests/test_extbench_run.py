import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from harness import extbench_run as xr
from harness.cli import app

RUN_SH = Path("/x/tools/extbench/run.sh")
HARNESS = Path("/x/.venv/bin/harness")
SUPERSEDE = "engine:cortex_mem.engines.supersede:SupersedeEngine"
LEDGER = "engine:cortex_mem.engines.ledger:LedgerEngine"


def config(tmp_path: Path, **over: object) -> xr.ExtbenchRunConfig:
    data: dict[str, object] = {
        "name": "cmp",
        "root": str(tmp_path / "bench"),
        "reader": "openai:qwen",
        "judge": "openai:gemma",
        "engines": [
            {"id": "full", "arm": "full"},
            {"id": "supersede", "arm": SUPERSEDE},
            {"id": "ledger", "arm": LEDGER, "core": "/w/core-ledger/src"},
            {"id": "mem0", "arm": "oss:mem0"},
        ],
        "dev": {"scenarios": ["scenarios/dev"], "blind_seed": 7},
        "locomo": {"data": "/d/locomo10.json"},
        "longmemeval": {"questions": 20},
    }
    return xr.ExtbenchRunConfig.model_validate(data | over)


def by_key(steps: Sequence[xr.Step]) -> dict[str, xr.Step]:
    return {s.key: s for s in steps}


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(data))


class FakeRunner:
    """Records each call and writes the summary the step would have written, unless told to fail."""

    def __init__(self, steps: Sequence[xr.Step], fail: set[str] | None = None, usd: float = 0.0) -> None:
        self.steps = steps
        self.fail = fail or set()
        self.usd = usd
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> int:
        self.calls.append(list(argv))
        step = next(s for s in self.steps if list(s.argv) == list(argv[: len(s.argv)]))
        if step.key in self.fail:
            return 1
        target = step.out / "20261129-000000" / "summary.json" if step.kind == "dev" else step.out / "summary.json"
        write_json(target, {"estimated_usd": self.usd})
        return 0


def test_config_rejects_duplicate_engine_ids(tmp_path: Path) -> None:
    # Act / Assert
    with pytest.raises(ValidationError, match="engine ids must be unique"):
        _ = config(tmp_path, engines=[{"id": "a", "arm": "none"}, {"id": "a", "arm": "full"}])


@pytest.mark.parametrize("arm", ["engine:no_class", "oss:zep", "everything"])
def test_config_rejects_an_arm_extbench_does_not_know(tmp_path: Path, arm: str) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        _ = config(tmp_path, engines=[{"id": "a", "arm": arm}])


def test_dev_runs_once_per_core_with_one_blind_seed_and_without_the_bounds(tmp_path: Path) -> None:
    # Act
    steps = by_key(xr.plan(config(tmp_path), RUN_SH, HARNESS, base=tmp_path))

    # Assert
    pinned, ledger = steps["dev/supersede+mem0"], steps["dev/ledger"]
    assert pinned.argv == (
        str(HARNESS), "run", "--config", str(tmp_path / "config/eval-v0.1.yaml"),
        "--out", str(tmp_path / "bench/runs/cmp-dev-1"), "--scenarios", str(tmp_path / "scenarios/dev"), "--engine", "cortex_mem.engines.supersede:SupersedeEngine",
        "--engine", "mem0", "--blind-seed", "7",
    )
    assert pinned.env == {}
    assert ledger.env == {"PYTHONPATH": "/w/core-ledger/src"}
    assert ledger.argv[-2:] == ("--blind-seed", "7")
    assert not any("full" in s.engines for s in steps.values() if s.kind == "dev")


def test_relative_paths_are_taken_against_where_the_comparison_starts(tmp_path: Path) -> None:
    # Arrange: run.sh changes into tools/extbench, so relative paths must not reach it as they are
    cfg = config(tmp_path, root="xbench", engines=[{"id": "l", "arm": LEDGER, "core": "../core-ledger/src"}],
                 locomo={"data": "data/locomo10.json"}, longmemeval={"data": "lme.json"})

    # Act
    steps = by_key(xr.plan(cfg, RUN_SH, HARNESS, base=tmp_path))

    # Assert
    run = steps["locomo/l"]
    assert run.env == {"EXTBENCH_ROOT": str(tmp_path / "xbench"), "EXTBENCH_CORE": str(tmp_path / "../core-ledger/src")}
    assert run.argv[run.argv.index("--data") + 1] == str(tmp_path / "data/locomo10.json")
    assert run.out == tmp_path / "xbench/runs/cmp-locomo-l"
    lme = steps["longmemeval/l"].argv
    assert lme[lme.index("--data") + 1] == str(tmp_path / "lme.json")


def test_locomo_is_answered_then_judged_per_engine_with_its_core(tmp_path: Path) -> None:
    # Act
    steps = by_key(xr.plan(config(tmp_path), RUN_SH, HARNESS))

    # Assert
    run, judge = steps["locomo/ledger"], steps["locomo-judge/ledger"]
    assert run.argv[:2] == (str(RUN_SH), "locomo")
    assert run.argv[run.argv.index("--arm") : run.argv.index("--arm") + 2] == ("--arm", LEDGER)
    assert run.env == {"EXTBENCH_ROOT": str(tmp_path / "bench"), "EXTBENCH_CORE": "/w/core-ledger/src"}
    assert run.budgeted
    assert judge.after == run.key
    assert judge.argv[-2:] == ("--answers", str(run.out / "answers.jsonl"))
    assert judge.argv[judge.argv.index("--model") + 1] == "openai:gemma"


def test_longmemeval_gets_reader_judge_and_size(tmp_path: Path) -> None:
    # Act
    step = by_key(xr.plan(config(tmp_path), RUN_SH, HARNESS))["longmemeval/mem0"]

    # Assert
    argv = list(step.argv)
    assert argv[argv.index("--model") + 1] == "openai:qwen"
    assert argv[argv.index("--judge-model") + 1] == "openai:gemma"
    assert argv[argv.index("--questions") + 1] == "20"
    assert "--data" not in argv
    assert step.env == {"EXTBENCH_ROOT": str(tmp_path / "bench")}


def test_unconfigured_benchmarks_have_no_steps(tmp_path: Path) -> None:
    # Act
    steps = xr.plan(config(tmp_path, dev=None, locomo=None), RUN_SH, HARNESS)

    # Assert
    assert {s.kind for s in steps} == {"longmemeval"}


def test_execution_skips_done_steps_and_blocks_a_judge_whose_run_failed(tmp_path: Path) -> None:
    # Arrange
    steps = xr.plan(config(tmp_path), RUN_SH, HARNESS)
    done = by_key(steps)["longmemeval/full"]
    write_json(done.out / "summary.json", {})
    runner = FakeRunner(steps, fail={"locomo/supersede"})

    # Act
    outcomes = {o.key: o.status for o in xr.Execution(steps, None, runner).run()}

    # Assert
    assert outcomes["longmemeval/full"] == "skipped"
    assert outcomes["locomo/supersede"] == "failed"
    assert outcomes["locomo-judge/supersede"] == "blocked"
    assert outcomes["locomo-judge/ledger"] == "done"
    assert outcomes["dev/supersede+mem0"] == "done"
    assert len(runner.calls) == len(steps) - 2


def test_a_step_that_exits_zero_without_a_summary_counts_as_failed(tmp_path: Path) -> None:
    # Arrange
    steps = xr.plan(config(tmp_path, dev=None, locomo=None, engines=[{"id": "full", "arm": "full"}]), RUN_SH, HARNESS)

    # Act
    outcomes = xr.Execution(steps, None, lambda argv, env: 0).run()

    # Assert
    assert [o.status for o in outcomes] == ["failed"]


def test_an_interrupted_step_resumes_from_its_answers(tmp_path: Path) -> None:
    # Arrange
    steps = xr.plan(config(tmp_path, dev=None, locomo=None, engines=[{"id": "full", "arm": "full"}]), RUN_SH, HARNESS)
    step = steps[0]
    write_json(step.out / "answers.jsonl", {"arm": "full", "question_id": "q1"})
    runner = FakeRunner(steps)

    # Act
    _ = xr.Execution(steps, None, runner).run()

    # Assert
    earlier = step.out / "answers.earlier.jsonl"
    assert runner.calls[0][-2:] == ["--resume", str(earlier)]
    assert json.loads(earlier.read_text()) == {"arm": "full", "question_id": "q1"}


def test_budgeted_steps_get_what_is_left_of_the_budget(tmp_path: Path) -> None:
    # Arrange
    cfg = config(tmp_path, dev=None, locomo=None, budget=10.0)
    steps = xr.plan(cfg, RUN_SH, HARNESS)
    runner = FakeRunner(steps, usd=3.0)

    # Act
    _ = xr.Execution(steps, cfg.budget, runner).run()

    # Assert
    budgets = [call[call.index("--budget") + 1] for call in runner.calls]
    assert budgets == ["10.0000", "7.0000", "4.0000", "1.0000"]


def test_summary_maps_blind_dev_ids_back_and_reads_every_benchmark(tmp_path: Path) -> None:
    # Arrange
    cfg = config(tmp_path, engines=[{"id": "supersede", "arm": SUPERSEDE}])
    steps = by_key(xr.plan(cfg, RUN_SH, HARNESS))
    dev = steps["dev/supersede"].out / "20261129-000000"
    rate = {"k": 1, "n": 4}
    write_json(dev / "summary.json", {"engines": {"E1": {
        "metrics": {"m1": rate | {"value": 0.25}, "m2": rate | {"value": 0.5}, "m3": 0}, "verdict": {"passed": True},
    }}})
    write_json(dev / "engine_ids.json", {"seed": 7, "ids": {"E1": "cortex_mem.engines.supersede:SupersedeEngine"}})
    write_json(steps["locomo/supersede"].out / "summary.json", {"failed": [], "arms": {SUPERSEDE: {
        "n": 10, "f1": 0.41, "evidence_recall": 0.6}}})
    write_json(steps["locomo-judge/supersede"].out / "summary.json", {"arms": {SUPERSEDE: {"judged_correct": 0.52}}})
    write_json(steps["longmemeval/supersede"].out / "summary.json", {"failed": [["x", "q", "e"]], "arms": {SUPERSEDE: {
        "n": 20, "accuracy": 0.45, "abstention": {"n": 2, "accuracy": 0.5}, "evidence_recall": 0.7}}})

    # Act
    [row] = xr.collect(cfg, list(steps.values()))
    md = xr.write_summary(cfg, list(steps.values()), [xr.Outcome("dev/supersede", "done")]).read_text()

    # Assert
    assert row.model_dump(exclude={"id", "arm", "core"}) == {
        "dev_m1": 0.25, "dev_m2": 0.5, "dev_m3": 0, "dev_passed": True,
        "locomo_f1": 0.41, "locomo_judged": 0.52, "locomo_evidence": 0.6, "locomo_failed": 0,
        "lme_accuracy": 0.45, "lme_abstention": 0.5, "lme_evidence": 0.7, "lme_failed": 1,
    }
    assert "| supersede | 0.250 | 0.500 | 0 | yes | 0.410 | 0.520 | 0.600 | 0.450 | 0.500 | 0.700 |" in md
    assert "not the pre-registered measurement" in md
    assert json.loads((tmp_path / "bench/runs/cmp-summary.json").read_text())["rows"][0]["lme_failed"] == 1


def test_summary_leaves_cells_empty_for_missing_or_malformed_outputs(tmp_path: Path) -> None:
    # Arrange
    cfg = config(tmp_path, engines=[{"id": "full", "arm": "full"}])
    steps = xr.plan(cfg, RUN_SH, HARNESS)
    write_json(by_key(steps)["locomo/full"].out / "summary.json", {"arms": "not a mapping"})

    # Act
    [row] = xr.collect(cfg, steps)

    # Assert
    assert row.locomo_f1 is None and row.lme_accuracy is None and row.dev_m1 is None


def test_cli_dry_run_lists_steps_without_running_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    path = tmp_path / "cmp.yaml"
    _ = path.write_text(config(tmp_path).model_dump_json())
    monkeypatch.setattr(xr, "default_paths", lambda: (RUN_SH, HARNESS))
    monkeypatch.setattr(xr, "run_subprocess", lambda argv, env: pytest.fail("ran a step"))

    # Act
    result = CliRunner().invoke(app, ["extbench", "run", "--config", str(path), "--dry-run"])

    # Assert
    assert result.exit_code == 0, result.output
    assert "todo  dev/supersede+mem0" in result.output
    assert "EXTBENCH_CORE=/w/core-ledger/src" in result.output
    assert not (tmp_path / "bench" / "runs").exists()


def test_cli_rejects_an_unreadable_config(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "cmp.yaml"
    _ = path.write_text("name: cmp\nunknown: 1\n")

    # Act
    result = CliRunner().invoke(app, ["extbench", "run", "--config", str(path)])

    # Assert
    assert result.exit_code == 2
    assert "cannot load config" in result.output


def test_the_example_config_is_valid() -> None:
    # Act
    cfg = xr.load_run_config(Path(__file__).parents[1] / "tools" / "extbench" / "all-engines.example.yaml")

    # Assert
    assert len(cfg.engines) == 10
    assert cfg.price == "0,0"
