import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.scenario.model import Review, Scenario
from tests.test_selfcheck import poison, truth_change

runner = CliRunner()


def write(dir_: Path, s: Scenario, status: str = "approved", name: str | None = None) -> Path:
    s = s.model_copy(update={"review": Review(status=status)})
    path = dir_ / (name or f"{s.id}.yaml")
    path.write_text(yaml.safe_dump(s.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    return path


def good_dir(tmp_path: Path, status: str = "approved") -> Path:
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, truth_change(), status)
    write(d, poison(), status)
    return d


def run_cli(tmp_path: Path, scenarios: Path, *extra: str):
    out = tmp_path / "out"
    return runner.invoke(app, ["run", "--scenarios", str(scenarios), "--out", str(out), *extra]), out


def test_check_reports_error_exit1(tmp_path):
    s = truth_change()
    bad = s.model_copy(update={"events": [s.events[0].model_copy(update={"fact": "nope"})]})
    path = write(tmp_path, bad)
    result = runner.invoke(app, ["scenario", "check", str(path)])
    assert result.exit_code == 1
    assert f"{path}: error unknown_fact" in result.output


def test_check_clean_directory_and_drafts(tmp_path):
    d = good_dir(tmp_path, status="draft")
    result = runner.invoke(app, ["scenario", "check", str(d)])
    assert result.exit_code == 0, result.output


def test_check_rejected_is_checked_and_labeled(tmp_path):
    path = write(tmp_path, truth_change(), "rejected")
    result = runner.invoke(app, ["scenario", "check", str(path)])
    assert result.exit_code == 0
    assert f"{path}: rejected" in result.output


def test_check_load_error(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("id: [unterminated", encoding="utf-8")
    result = runner.invoke(app, ["scenario", "check", str(tmp_path)])
    assert result.exit_code == 1
    assert f"{path}: error load" in result.output


def too_easy() -> Scenario:
    """Valid, but all_store can only ever reach the true fact, so nothing discriminates."""
    s = truth_change()
    return s.model_copy(update={"events": [e for e in s.events if e.fact == "f_new"]})


def test_check_runs_discrimination(tmp_path):
    path = write(tmp_path, too_easy())
    result = runner.invoke(app, ["scenario", "check", str(path)])
    assert result.exit_code == 0
    assert f"{path}: warning too_easy" in result.output


def test_run_no_approved_scenarios(tmp_path):
    d = good_dir(tmp_path, status="draft")
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 2
    assert "승인된 시나리오가 없습니다. 초안을 포함하려면 --include-drafts" in result.output
    assert not out.exists()


def test_run_include_drafts(tmp_path):
    d = good_dir(tmp_path, status="draft")
    result, _ = run_cli(tmp_path, d, "--include-drafts")
    assert result.exit_code == 0, result.output


def test_run_writes_report(tmp_path):
    d = good_dir(tmp_path)
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 0, result.output
    last = result.output.strip().splitlines()[-1]
    report = Path(last)
    assert report.name == "report.md" and report.exists()
    assert report.parent.parent == out
    assert len(report.parent.name) == 15 and report.parent.name[8] == "-"
    summary = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["engines"]) == {"none", "all_store", "repeat_n", "oracle"}


def test_run_stops_on_selfcheck_failure(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, too_easy())
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 1
    assert "all_store_max_m1" in result.output
    assert not out.exists()


def test_run_stops_on_validation_error(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    s = truth_change()
    write(d, s.model_copy(update={"events": [s.events[0].model_copy(update={"fact": "nope"})]}))
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 1
    assert "tc: error unknown_fact" in result.output
    assert not out.exists()


def test_run_extra_engine_import_path(tmp_path):
    d = good_dir(tmp_path)
    result, _ = run_cli(tmp_path, d, "--engine", "tests.dummy_engine:Dummy", "--repeat", "1")
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    summary = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["engines"]) == 5
    assert "E1" in summary["engines"]
    assert len(summary["engines"]["E1"]["repeats"]) == 1


def test_run_extra_engine_default_repeats_and_baseline_ignored(tmp_path):
    d = good_dir(tmp_path)
    result, _ = run_cli(tmp_path, d, "--engine", "tests.dummy_engine:Dummy", "--engine", "oracle")
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    summary = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["engines"]) == 5
    assert len(summary["engines"]["E1"]["repeats"]) == 3
    assert len(summary["engines"]["oracle"]["repeats"]) == 1


def test_run_unknown_engine_exit2(tmp_path):
    d = good_dir(tmp_path)
    result, out = run_cli(tmp_path, d, "--engine", "no.such.module:X")
    assert result.exit_code == 2
    assert "no.such.module:X" in result.output
    assert not out.exists()


def test_run_accepts_single_file(tmp_path):
    path = write(tmp_path, truth_change())
    result, _ = run_cli(tmp_path, path)
    assert result.exit_code == 0, result.output


def test_selfcheck_command(tmp_path):
    d = good_dir(tmp_path)
    result = runner.invoke(app, ["selfcheck", "--scenarios", str(d)])
    assert result.exit_code == 0, result.output
    assert "oracle_perfect" in result.output


def test_selfcheck_command_fails(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, too_easy())
    result = runner.invoke(app, ["selfcheck", "--scenarios", str(d)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_run_sealed_drops_results(tmp_path):
    d = good_dir(tmp_path)
    result, _ = run_cli(tmp_path, d, "--sealed")
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    assert not (report.parent / "results.jsonl").exists()
    assert json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))["meta"]["sealed"] is True


def summary_of(result) -> dict:
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    report = Path(result.output.strip().splitlines()[-1])
    assert report.name == "report.md" and report.exists()
    return json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))


def test_run_engine_build_fails_mid_run(tmp_path):
    from tests.dummy_engine import FailSecondBuild

    FailSecondBuild.built = 0
    result, _ = run_cli(tmp_path, good_dir(tmp_path),
                        "--engine", "tests.dummy_engine:FailSecondBuild", "--repeat", "1")
    metrics = summary_of(result)["engines"]["E1"]["metrics"]
    assert len(metrics["errors"]) == 1


def test_run_engine_record_raises(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path),
                        "--engine", "tests.dummy_engine:FailRecord", "--repeat", "1")
    metrics = summary_of(result)["engines"]["E1"]["metrics"]
    assert len(metrics["errors"]) == 2


def test_run_engine_record_raises_sealed_counts_errors(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path),
                        "--engine", "tests.dummy_engine:FailRecord", "--repeat", "1", "--sealed")
    assert summary_of(result)["engines"]["E1"]["metrics"]["n_errors"] == 2


def test_run_engine_first_build_fails_exit2(tmp_path):
    result, out = run_cli(tmp_path, good_dir(tmp_path),
                          "--engine", "tests.dummy_engine:FailInit", "--repeat", "1")
    assert result.exit_code == 2
    assert "cannot build engine" in result.output
    assert "cannot construct" in result.output
    assert "Traceback" not in result.output
    assert not out.exists()


def test_run_duplicate_scenario_id(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, truth_change(), name="a.yaml")
    write(d, truth_change(), name="b.yaml")
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 1
    assert "tc: error duplicate_scenario_id" in result.output
    assert not out.exists()


def test_run_missing_scenarios_path(tmp_path):
    result, out = run_cli(tmp_path, tmp_path / "nope")
    assert result.exit_code == 2
    assert "scenario path does not exist" in result.output
    assert str(tmp_path / "nope") in result.output
    assert not out.exists()


def test_run_bad_config_unknown_key(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("k: 5\nbogus_key: 1\n", encoding="utf-8")
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--config", str(cfg))
    assert result.exit_code == 2
    assert "cannot load config" in result.output
    assert "bogus_key" in result.output
    assert not out.exists()


def test_selfcheck_missing_config_file(tmp_path):
    result = runner.invoke(app, ["selfcheck", "--scenarios", str(good_dir(tmp_path)),
                                 "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2
    assert "cannot load config" in result.output


def test_check_accepts_include_drafts(tmp_path):
    d = good_dir(tmp_path, status="draft")
    result = runner.invoke(app, ["scenario", "check", str(d), "--include-drafts"])
    assert result.exit_code == 0, result.output
