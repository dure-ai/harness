import json
import re
from pathlib import Path

import pytest
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


def test_run_unknown_engine_exits_before_any_baseline_run(tmp_path, monkeypatch):
    import harness.cli

    def boom(*args, **kwargs):
        raise AssertionError("baseline selfcheck must not run before the engine name check")

    monkeypatch.setattr(harness.cli, "_selfcheck", boom)
    monkeypatch.setattr(harness.cli, "run_scenario", boom)
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--engine", "typo")
    assert result.exit_code == 2
    assert "typo" in result.output
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


def test_check_uses_config(tmp_path, monkeypatch):
    import harness.cli

    seen = []
    real = harness.cli.check_discrimination
    monkeypatch.setattr(harness.cli, "check_discrimination", lambda sc, cfg: seen.append(cfg) or real(sc, cfg))
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("k: 7\n", encoding="utf-8")
    path = write(tmp_path, truth_change())
    result = runner.invoke(app, ["scenario", "check", str(path), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert [c.k for c in seen] == [7]

    seen.clear()
    assert runner.invoke(app, ["scenario", "check", str(path)]).exit_code == 0
    assert [c.k for c in seen] == [5]  # config/eval-v0.1.yaml


def test_check_bad_config_exit2(tmp_path):
    path = write(tmp_path, truth_change())
    result = runner.invoke(app, ["scenario", "check", str(path), "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2
    assert "cannot load config" in result.output


def test_run_sealed_load_error_hides_path_and_content(tmp_path):
    d = good_dir(tmp_path)
    (d / "secret-repo-name.yaml").write_text("id: secret-scenario\nfamily: [", encoding="utf-8")
    result, _ = run_cli(tmp_path, d, "--sealed")
    assert result.exit_code == 1
    assert "secret" not in result.output
    assert "error load" in result.output


def _errors_section(result) -> str:
    report = Path(result.output.strip().splitlines()[-1]).read_text(encoding="utf-8")
    return report.split("## 오류 시나리오", 1)[1].split("\n## ", 1)[0]


def test_run_reports_errors_from_later_repeats(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path),
                        "--engine", "tests.dummy_engine:FailOnSecondRepeat", "--repeat", "2")
    engine = summary_of(result)["engines"]["E1"]
    assert engine["metrics"]["errors"] == ["sp", "tc"]
    assert engine["verdict"]["passed"] is False
    errors = _errors_section(result)
    assert "second repeat fails" in errors
    rows = [line for line in errors.splitlines() if "second repeat fails" in line]
    assert len(rows) == 2 and all("| 2 |" in row for row in rows)  # repeat 2, one row per scenario


def test_run_sealed_counts_errors_from_every_repeat(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--sealed",
                        "--engine", "tests.dummy_engine:FailOnSecondRepeat", "--repeat", "2")
    errors = _errors_section(result)
    assert "오류가 난 시나리오 수: 2" in errors
    assert "second repeat fails" not in errors


def test_info_returning_dict_exits_2(tmp_path):
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:DictInfo", "--repeat", "1")
    assert result.exit_code == 2
    assert "cannot build engine" in result.output and "EngineInfo" in result.output
    assert "Traceback" not in result.output
    assert not out.exists()


def test_non_json_config_exits_2_before_evaluation(tmp_path):
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:BadConfig", "--repeat", "1")
    assert result.exit_code == 2
    assert "cannot build engine" in result.output
    assert not isinstance(result.exception, TypeError)
    assert not out.exists()


def test_config_json_only_in_json_mode_is_written_as_strings(tmp_path):
    # Review finding: a Path/Decimal/bytes config passed the info check and then crashed the
    # report after the whole evaluation.
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:PathConfig", "--repeat", "1")
    assert result.exit_code == 0, result.output
    report = Path(result.output.strip().splitlines()[-1])
    infos = json.loads((report.parent / "summary.json").read_text("utf-8"))["meta"]["engine_infos"]
    shown = next(i for i in infos.values() if i["config"].get("v") == "/data")
    assert shown["config"] == {"v": "/data", "rate": "1.5", "raw": "x"}
    assert "/data" in report.read_text("utf-8")


def test_info_check_accepts_what_the_report_writes_and_names_the_bad_key():
    from harness.cli import _checked_info
    from tests.dummy_engine import BadConfig, Dummy

    class Odd(Dummy):
        def info(self):
            from harness.contract import EngineInfo

            return EngineInfo(name="odd", version="1", config={"nan": float("nan"), "mixed": {1, "a"}})

    assert _checked_info(Odd()).config["mixed"] == {1, "a"}
    with pytest.raises(TypeError) as e:
        _checked_info(BadConfig())
    assert "config.client" in str(e.value)


def test_all_store_noacl_is_not_a_candidate(tmp_path):
    result, out = run_cli(tmp_path, good_dir(tmp_path), "--engine", "all_store_noacl", "--repeat", "1")
    assert result.exit_code == 2
    assert "all_store_noacl" in result.output
    assert not out.exists()


# ---- blinding: --blind-seed, namespaces, engine_ids.json --------------------------------------


def _blind_run(tmp_path: Path, name: str, *extra: str):
    from tests.dummy_engine import NamespaceLog

    NamespaceLog.seen.clear()
    out = tmp_path / name
    result = runner.invoke(app, ["run", "--scenarios", str(tmp_path / "scenarios"), "--out", str(out),
                                 "--repeat", "1", "--engine", "tests.dummy_engine:NamespaceLog",
                                 "--engine", "tests.dummy_engine:NamespaceLogTwin", *extra])
    summary_of(result)
    run_dir = next(out.iterdir())
    namespaces = [ns.split(":", 1)[1] for ns in NamespaceLog.seen]  # drop the run id (a timestamp)
    return run_dir, namespaces


def test_same_blind_seed_gives_identical_engine_ids_and_results_jsonl(tmp_path):
    good_dir(tmp_path)
    one, ns_one = _blind_run(tmp_path, "one", "--blind-seed", "12345")
    two, ns_two = _blind_run(tmp_path, "two", "--blind-seed", "12345")
    for name in ("engine_ids.json", "results.jsonl"):
        assert (one / name).read_bytes() == (two / name).read_bytes(), name
    assert ns_one == ns_two and len(ns_one) == 4  # 2 candidates x 2 scenarios
    assert json.loads((one / "engine_ids.json").read_text("utf-8"))["seed"] == 12345


def _outcomes(run_dir: Path) -> list[tuple]:
    lines = [json.loads(line) for line in (run_dir / "results.jsonl").read_text("utf-8").splitlines()]
    return sorted((x["scenario_id"], x["probe_id"], x["outcome"], json.dumps(x["results"])) for x in lines)


def test_different_blind_seed_changes_namespaces_not_outcomes(tmp_path):
    good_dir(tmp_path)
    one, ns_one = _blind_run(tmp_path, "one", "--blind-seed", "1")
    two, ns_two = _blind_run(tmp_path, "two", "--blind-seed", "2")
    assert not set(ns_one) & set(ns_two)
    assert _outcomes(one) == _outcomes(two)
    for ns in ns_one + ns_two:
        assert re.fullmatch(r"[0-9a-f]{16}", ns)
        assert "tc" not in ns and "sp" not in ns


def test_engine_ids_json_records_seed(tmp_path):
    import hashlib
    import hmac

    from harness.replay import blind_key

    good_dir(tmp_path)
    run_dir, namespaces = _blind_run(tmp_path, "random")  # no --blind-seed: drawn at random
    seed = json.loads((run_dir / "engine_ids.json").read_text("utf-8"))["seed"]
    assert isinstance(seed, int)
    # the recorded seed alone reproduces the namespaces of the run
    expected = {hmac.new(blind_key(seed), f"ns:{sid}".encode(), hashlib.sha256).hexdigest()[:16]
                for sid in ("tc", "sp")}
    assert set(namespaces) == expected
    for name in ("summary.json", "report.md", "results.jsonl"):
        assert str(seed) not in (run_dir / name).read_text("utf-8")


def test_run_passes_the_blind_key_to_baselines_and_candidates(tmp_path, monkeypatch):
    import harness.cli
    import harness.selfcheck
    from harness.replay import DEFAULT_BLIND_KEY, blind_key, run_scenario

    keys = []

    def spy(*args, **kwargs):
        keys.append(kwargs.get("blind_key", DEFAULT_BLIND_KEY))
        return run_scenario(*args, **kwargs)

    monkeypatch.setattr(harness.cli, "run_scenario", spy)
    monkeypatch.setattr(harness.selfcheck, "run_scenario", spy)
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--engine", "tests.dummy_engine:Dummy",
                        "--repeat", "1", "--blind-seed", "77")
    summary_of(result)
    assert len(keys) == 4 * 2 + 2  # baselines and the candidate, two scenarios each
    assert set(keys) == {blind_key(77)}


@pytest.mark.parametrize(("seed", "warned"), [("7", True), (str(2**32 - 1), True), (str(2**32), False)])
def test_run_warns_about_a_small_blind_seed(tmp_path, seed, warned):
    result, _ = run_cli(tmp_path, good_dir(tmp_path), "--blind-seed", seed)
    assert result.exit_code == 0, result.output
    assert ("--blind-seed" in result.stderr and "small" in result.stderr) is warned


def test_run_without_a_blind_seed_does_not_warn(tmp_path):
    result, _ = run_cli(tmp_path, good_dir(tmp_path))
    assert result.exit_code == 0, result.output
    assert "warning" not in result.stderr
