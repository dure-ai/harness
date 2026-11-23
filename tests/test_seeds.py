import json
from pathlib import Path

from typer.testing import CliRunner

import harness.cli
from harness.cli import app
from harness.config import load_config
from harness.metrics import compute_metrics
from harness.replay import blind_key, run_scenario
from harness.seeds import _cell, metric_spread, unstable_probes
from harness.selfcheck import CheckResult
from tests.dummy_engine import LatestWins, LowestIdFirst
from tests.test_cli import good_dir
from tests.test_selfcheck import poison, truth_change

runner = CliRunner()
CFG = load_config(Path("config/eval-v0.1.yaml"))
ENGINE = "tests.dummy_engine:LowestIdFirst"
# Under LowestIdFirst, p_block of "tc" / "sp" is BLOCKED/FP, FP/FP and BLOCKED/BLOCKED (found by running it).
SEEDS = [2**40, 2**40 + 2, 2**40 + 4]


def _runs(engine_cls, seeds=SEEDS):
    scenarios = [truth_change(), poison()]
    return scenarios, [
        [run_scenario(engine_cls(), s, run_id="t", cfg=CFG, blind_key=blind_key(seed)) for s in scenarios]
        for seed in seeds
    ]


def test_unstable_probes_counts_outcomes_per_seed():
    _, per_seed = _runs(LowestIdFirst)
    assert unstable_probes(per_seed) == [
        {"scenario": "sp", "probe": "p_block", "kind": "block", "outcomes": {"BLOCKED": 1, "FP": 2}},
        {"scenario": "tc", "probe": "p_block", "kind": "block", "outcomes": {"BLOCKED": 2, "FP": 1}},
    ]


def test_unstable_probes_empty_for_an_engine_that_ignores_ids():
    _, per_seed = _runs(LatestWins)
    assert unstable_probes(per_seed) == []


def test_unstable_probes_compares_only_the_seeds_a_probe_ran():
    _, per_seed = _runs(LatestWins)
    per_seed[1] = per_seed[1][:1]  # "sp" did not run under the second seed
    assert unstable_probes(per_seed) == []


def test_metric_spread_mean_min_max_and_counts():
    scenarios, per_seed = _runs(LowestIdFirst)
    by_id = {s.id: s for s in scenarios}
    spread = metric_spread([compute_metrics(runs, by_id) for runs in per_seed])
    assert spread["m1"] == {"mean": 0.5, "min": 0.0, "max": 1.0, "k": [1, 2, 0], "n": [2, 2, 2]}
    assert spread["m2"]["min"] == spread["m2"]["max"] == 1.0
    assert spread["m3"] == {"mean": 0.0, "min": 0, "max": 0, "values": [0, 0, 0]}
    assert spread["by_family"]["truth_change"]["m1"]["k"] == [0, 1, 0]
    assert spread["by_family"]["single_source_poison"]["m1"]["k"] == [1, 1, 0]
    assert spread["feedback_by_family"] == {}
    assert spread["n_error_runs"] == [0, 0, 0]


def test_metric_spread_keeps_seed_order_when_a_family_is_missing():
    scenarios, per_seed = _runs(LowestIdFirst)
    by_id = {s.id: s for s in scenarios}
    per_seed[0] = per_seed[0][1:]  # "tc" (truth_change) did not run under the first seed
    spread = metric_spread([compute_metrics(runs, by_id) for runs in per_seed])
    tc = spread["by_family"]["truth_change"]
    assert tc["m1"]["k"] == [None, 1, 0]
    assert tc["m1"]["n"] == [None, 1, 1]
    assert tc["m1"]["mean"] == 0.5
    assert tc["m3"]["values"] == [None, 0, 0]
    assert spread["by_family"]["single_source_poison"]["m1"]["k"] == [1, 1, 0]


def _seeds_cli(tmp_path, *extra):
    d = good_dir(tmp_path)
    args = ["seeds", "--scenarios", str(d), "--out", str(tmp_path / "out"), *extra]
    return runner.invoke(app, args)


def _seed_args():
    return [a for s in SEEDS for a in ("--blind-seed", str(s))]


def test_a_cell_whose_n_differs_across_seeds_is_marked():
    """k and n ranges are taken separately, so with n varying they no longer pair up (2/1 never happened)."""
    varied = {"mean": 2 / 3, "min": 0.0, "max": 1.0, "k": [1, 2, 0], "n": [1, 2, 2]}
    assert _cell(varied) == "0.667 [0.000, 1.000] (0–2/1–2)*"
    same_n = {"mean": 0.5, "min": 0.0, "max": 1.0, "k": [1, 0], "n": [1, 1]}
    assert _cell(same_n) == "0.500 [0.000, 1.000] (0–1/1)"
    with_gap = {"mean": 1.0, "min": 1.0, "max": 1.0, "k": [2, None, 2], "n": [2, None, 2]}
    assert _cell(with_gap) == "1.000 (2/2)"  # a seed where the family is missing does not mark it


def test_seeds_cli_writes_spread_report(tmp_path):
    result = _seeds_cli(tmp_path, "--engine", ENGINE, *_seed_args())
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    assert report.parent.name.endswith("-seeds")
    ids = json.loads((report.parent / "engine_ids.json").read_text(encoding="utf-8"))
    assert ids == {"seeds": SEEDS, "ids": {"E1": ENGINE}}
    summary = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["n_seeds"] == 3
    assert list(summary["engines"]) == ["none", "all_store", "repeat_n", "oracle", "E1"]
    assert summary["engines"]["E1"]["m1"]["k"] == [1, 2, 0]
    assert [u["scenario"] for u in summary["engines"]["E1"]["unstable"]] == ["sp", "tc"]
    assert summary["engines"]["all_store"]["unstable"] == []
    md = report.read_text(encoding="utf-8")
    assert "| E1 | 0.500 [0.000, 1.000] (0–2/2) |" in md
    assert "| E1 | tc/p_block | block | BLOCKED 2, FP 1 |" in md
    assert "LowestIdFirst" not in md  # blind


def test_seeds_cli_sealed_hides_probe_ids(tmp_path):
    result = _seeds_cli(tmp_path, "--engine", ENGINE, "--sealed", *_seed_args())
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    summary = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert summary["engines"]["E1"]["unstable"] == 2
    assert "scenario_roots" not in summary["meta"]
    md = report.read_text(encoding="utf-8")
    assert "p_block" not in md and "tc/" not in md
    assert "| E1 | 0.500 [0.000, 1.000] (0–2/2) | 1.000 (2/2) | 0 | 2 | 0 |" in md


def test_seeds_cli_random_seeds(tmp_path):
    result = _seeds_cli(tmp_path, "--n", "2")
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    ids = json.loads((report.parent / "engine_ids.json").read_text(encoding="utf-8"))
    assert len(ids["seeds"]) == 2 and ids["ids"] == {}


def test_seeds_cli_needs_two_seeds(tmp_path):
    result = _seeds_cli(tmp_path, "--blind-seed", str(2**40), "--blind-seed", str(2**40))
    assert result.exit_code == 2
    assert "at least two" in result.output


def test_seeds_cli_rejects_unknown_engine(tmp_path):
    for name in ("all_store_noacl", "typo"):
        (tmp_path / name).mkdir()
        result = _seeds_cli(tmp_path / name, "--engine", name)
        assert result.exit_code == 2
        assert f"unknown engine {name!r}" in result.output


def test_seeds_cli_warns_about_each_guessable_seed(tmp_path):
    result = _seeds_cli(tmp_path, "--blind-seed", "5", "--blind-seed", "7", "--blind-seed", str(2**40))
    assert result.exit_code == 0, result.output
    assert "--blind-seed 5 is small and guessable" in result.output
    assert "--blind-seed 7 is small and guessable" in result.output
    assert f"--blind-seed {2**40} is small" not in result.output
    ids = json.loads((Path(result.output.strip().splitlines()[-1]).parent / "engine_ids.json").read_text())
    assert ids["seeds"] == [5, 7, 2**40]


def test_seeds_cli_warns_that_n_is_ignored_with_blind_seeds(tmp_path):
    result = _seeds_cli(tmp_path, "--n", "4", *_seed_args())
    assert result.exit_code == 0, result.output
    assert "--n is ignored" in result.output
    ids = json.loads((Path(result.output.strip().splitlines()[-1]).parent / "engine_ids.json").read_text())
    assert ids["seeds"] == SEEDS


def test_seeds_cli_warns_about_duplicate_seeds(tmp_path):
    a, b = SEEDS[:2]
    result = _seeds_cli(tmp_path, "--blind-seed", str(a), "--blind-seed", str(a), "--blind-seed", str(b))
    assert result.exit_code == 0, result.output
    assert f"--blind-seed {a} is given more than once" in result.output
    ids = json.loads((Path(result.output.strip().splitlines()[-1]).parent / "engine_ids.json").read_text())
    assert ids["seeds"] == [a, b]


def test_seeds_cli_matches_run_with_the_same_blind_seed(tmp_path):
    result = _seeds_cli(tmp_path, "--engine", ENGINE, *_seed_args())
    assert result.exit_code == 0, result.output
    spread = json.loads((Path(result.output.strip().splitlines()[-1]).parent / "summary.json").read_text())
    d = tmp_path / "scenarios"  # written by _seeds_cli
    for i, seed in enumerate(SEEDS):
        out = tmp_path / f"run-{i}"
        done = runner.invoke(
            app, ["run", "--scenarios", str(d), "--out", str(out), "--engine", ENGINE, "--blind-seed", str(seed)]
        )
        assert done.exit_code == 0, done.output
        (summary,) = out.glob("*/summary.json")
        metrics = json.loads(summary.read_text())["engines"]["E1"]["metrics"]
        for m in ("m1", "m2"):
            assert (spread["engines"]["E1"][m]["k"][i], spread["engines"]["E1"][m]["n"][i]) == (
                metrics[m]["k"],
                metrics[m]["n"],
            )
        assert spread["engines"]["E1"]["m3"]["values"][i] == metrics["m3"]


def test_seeds_cli_stops_when_a_later_seed_fails_the_self_check(tmp_path, monkeypatch):
    real = harness.cli.run_selfcheck
    calls = []

    def second_fails(*args, **kwargs):
        calls.append(1)
        checks, runs = real(*args, **kwargs)
        if len(calls) == 2:
            checks = [CheckResult(name="oracle_perfect", ok=False, detail="forced")]
        return checks, runs

    monkeypatch.setattr(harness.cli, "run_selfcheck", second_fails)
    result = _seeds_cli(tmp_path, *_seed_args())
    assert result.exit_code == 1
    assert "self-check failed with seed #2" in result.output
    assert not list((tmp_path / "out").glob("*-seeds"))


def test_seeds_report_names_engine_versions_without_the_candidate(tmp_path):
    result = _seeds_cli(tmp_path, "--engine", ENGINE, *_seed_args())
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    summary = json.loads((report.parent / "summary.json").read_text())
    assert summary["meta"]["engine_infos"]["E1"]["name"] == "E1"
    assert summary["meta"]["engine_infos"]["E1"]["version"] == "0.1"
    md = report.read_text(encoding="utf-8")
    assert "## 엔진" in md and "| E1 | 0.1 |" in md
    for text in (md, json.dumps(summary)):
        assert "LowestIdFirst" not in text and "dummy_engine" not in text and "latest" not in text
