import json
import re
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


def test_run_latest_wins_engine_passes(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:LatestWins", "--repeat", "1")
    verdict = summary_of(result)["engines"]["E1"]["verdict"]
    assert verdict["passed"] is True, verdict


def test_run_engine_erroring_on_subset_fails_verdict(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:FailOnTc", "--repeat", "1")
    engine = summary_of(result)["engines"]["E1"]
    assert engine["metrics"]["errors"] == ["tc"]
    assert engine["verdict"]["passed"] is False
    assert "판정 불가: 오류 시나리오 1개" in engine["verdict"]["reasons"]


def test_run_reports_flip_for_repeated_engine(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:Dummy", "--repeat", "2")
    summary = summary_of(result)
    assert summary["engines"]["E1"]["flip"]["k"] == 0 and summary["engines"]["E1"]["flip"]["n"] == 4
    assert summary["engines"]["oracle"]["flip"] is None


def test_run_single_repeat_has_no_flip(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:Dummy", "--repeat", "1")
    assert summary_of(result)["engines"]["E1"]["flip"] is None


def test_run_meta_records_drafts_roots_repeats(tmp_path):
    d = good_dir(tmp_path, status="draft")
    result, _ = run_cli(tmp_path, d, "--include-drafts", "--engine", "tests.dummy_engine:Dummy", "--repeat", "2")
    summary = summary_of(result)
    assert summary["meta"]["include_drafts"] is True
    assert summary["meta"]["scenario_roots"] == [str(d)]
    assert summary["meta"]["repeats"] == {"none": 1, "all_store": 1, "repeat_n": 1, "oracle": 1, "E1": 2}
    report = Path(result.output.strip().splitlines()[-1]).read_text(encoding="utf-8")
    assert "> ⚠️ 초안 포함 — 발표 수치로 쓰지 않음" in report.splitlines()[:3]


def test_run_sealed_meta_roots_count(tmp_path):
    d = good_dir(tmp_path)
    result, _ = run_cli(tmp_path, d, "--sealed")
    meta = summary_of(result)["meta"]
    assert meta["n_scenario_roots"] == 1 and "scenario_roots" not in meta


def invalid_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scenarios"
    d.mkdir()
    s = truth_change()
    bad = [s.events[0].model_copy(update={"fact": "nope"}), s.events[1].model_copy(update={"fact": "nope"})]
    write(d, s.model_copy(update={"events": [*bad, *s.events[2:]]}))
    write(d, truth_change(), name="dup.yaml")
    return d


def assert_no_scenario_content(output: str) -> None:
    for secret in ("tc", "sp", "p_block", "p_promote", "f_old", "f_new", "nope", "vendor"):
        assert secret not in output, (secret, output)


def test_run_sealed_validation_prints_counts_only(tmp_path):
    result, out = run_cli(tmp_path, invalid_dir(tmp_path), "--sealed")
    assert result.exit_code == 1
    assert "error unknown_fact: 2" in result.output
    assert "error duplicate_scenario_id: 1" in result.output
    assert_no_scenario_content(result.output)
    assert not out.exists()


def test_selfcheck_sealed_validation_prints_counts_only(tmp_path):
    result = runner.invoke(app, ["selfcheck", "--scenarios", str(invalid_dir(tmp_path)), "--sealed"])
    assert result.exit_code == 1
    assert "error unknown_fact: 2" in result.output
    assert_no_scenario_content(result.output)


def sealed_selfcheck_failure_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, too_easy())
    return d


def test_run_sealed_selfcheck_failure_names_only(tmp_path):
    result, out = run_cli(tmp_path, sealed_selfcheck_failure_dir(tmp_path), "--sealed")
    assert result.exit_code == 1
    lines = result.output.strip().splitlines()
    assert "FAIL  all_store_max_m1" in lines and "PASS  oracle_perfect" in lines
    assert "want" not in result.output and "m1=" not in result.output
    assert_no_scenario_content(result.output)
    assert not out.exists()


def test_selfcheck_sealed_names_only(tmp_path):
    (tmp_path / "x").mkdir()
    for d, code in ((good_dir(tmp_path), 0), (sealed_selfcheck_failure_dir(tmp_path / "x"), 1)):
        result = runner.invoke(app, ["selfcheck", "--scenarios", str(d), "--sealed"])
        assert result.exit_code == code, result.output
        assert all(re.fullmatch(r"(PASS|FAIL)  \w+", line) for line in result.output.strip().splitlines())
        assert "want" not in result.output and "skipped" not in result.output
        assert_no_scenario_content(result.output)


def test_run_engine_returning_ints_errors_cleanly(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:ReturnsInts", "--repeat", "1")
    engine = summary_of(result)["engines"]["E1"]
    assert engine["metrics"]["errors"] == ["sp", "tc"]
    assert engine["verdict"]["passed"] is False


def test_run_engine_returning_dicts_works(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:ReturnsDicts", "--repeat", "1")
    engine = summary_of(result)["engines"]["E1"]
    assert engine["metrics"]["errors"] == [] and engine["verdict"]["passed"] is True
