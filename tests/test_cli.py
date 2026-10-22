import json
import re
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.scenario.model import Review, Scenario
from tests.test_selfcheck import poison, truth_change

runner = CliRunner()


def write(dir_: Path, s: Scenario, status: str = "approved", name: str | None = None, hashed: bool = True) -> Path:
    """Save `s` with `status`; an approval records its content hash unless `hashed` is False."""
    from harness.scenario.labels import content_hash

    s = s.model_copy(update={"review": Review(status=status)})
    if status == "approved" and hashed:
        s.review.approved_hash = content_hash(s.model_dump(mode="json"), "approval")
    path = dir_ / (name or f"{s.id}.yaml")
    path.write_text(yaml.safe_dump(s.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    return path


def good_dir(tmp_path: Path, status: str = "approved", hashed: bool = True) -> Path:
    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, truth_change(), status, hashed=hashed)
    write(d, poison(), status, hashed=hashed)
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


# ---- traces and generate -----------------------------------------------------------------------


def gen_yaml(tmp_path: Path, **over) -> Path:
    data = {
        "provider": "fake",
        "model": "fake-1",
        "prompt_version": "gen-test",
        "license_allowlist": ["MIT"],
        "banned_cue_words": ["outdated", "no longer", "rumor", "deprecated"],
        **over,
    }
    path = tmp_path / "gen.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def cached_traces(tmp_path: Path, monkeypatch, repos=("acme/widget", "acme/widget", "other/gadget")) -> Path:
    from tests.test_gen_pipeline import trace

    cache = tmp_path / "cache"
    monkeypatch.setenv("T5_HARNESS_CACHE", str(cache))
    shard_dir = cache / "traces" / "v1.2" / "minisweagent"
    shard_dir.mkdir(parents=True)
    from tests.gen_fixtures import make_parquet

    make_parquet(shard_dir / "a.parquet", [trace(i + 1, repo=r) for i, r in enumerate(repos)])
    return cache


def gen_args(tmp_path: Path, *extra: str, **over) -> list[str]:
    return ["generate", "--gen-config", str(gen_yaml(tmp_path, **over)), "--out", str(tmp_path / "out"), *extra]


def test_cli_fetch_declined_downloads_nothing(tmp_path, monkeypatch):
    from harness.generate import fetch

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    info = fetch.ShardInfo(path="data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet", size=250_000_000)
    monkeypatch.setattr(fetch, "list_shards", lambda config, split, api=None, timeout=60.0: [info])
    monkeypatch.setattr(fetch, "fetch_shard", lambda *a, **k: pytest.fail("must not download"))
    result = runner.invoke(app, ["traces", "fetch", "--config", "v1.2", "--split", "minisweagent", "--shard", "0"], input="n\n")
    assert result.exit_code == 1
    assert "250.0 MB" in result.output and info.path in result.output
    assert not (tmp_path / "cache").exists()


def test_cli_fetch_confirmed_downloads_into_the_cache(tmp_path, monkeypatch):
    from harness.generate import fetch

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    infos = [fetch.ShardInfo(path=f"data/sweagent/qwen38_27b/src/train-0000{i}-of-00002.parquet", size=10) for i in (0, 1)]
    monkeypatch.setattr(fetch, "list_shards", lambda config, split, api=None, timeout=60.0: infos)
    got = []
    monkeypatch.setattr(fetch, "fetch_shard", lambda info, dest, api=None, timeout=60.0: got.append((info, dest, timeout)) or dest / info.path)
    result = runner.invoke(app, ["traces", "fetch", "--config", "v1.1", "--split", "sweagent", "--shard", "1"], input="y\n")
    assert result.exit_code == 0, result.output
    assert got == [(infos[1], tmp_path / "cache" / "traces" / "v1.1" / "sweagent", 60.0)]
    got.clear()
    assert runner.invoke(app, ["traces", "fetch", "--config", "v1.1", "--split", "sweagent", "--yes"]).exit_code == 0
    assert got[0][0] == infos[0]
    got.clear()
    args = ["traces", "fetch", "--config", "v1.1", "--split", "sweagent", "--yes", "--timeout", "5"]
    assert runner.invoke(app, args).exit_code == 0
    assert got[0][2] == 5.0


def test_cli_fetch_rejects_a_non_positive_timeout(tmp_path, monkeypatch):
    from harness.generate import fetch

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(fetch, "list_shards", lambda *a, **k: pytest.fail("must not reach the network"))
    for bad in ("0", "-3"):
        args = ["traces", "fetch", "--config", "v1.2", "--split", "sweagent", "--yes", "--timeout", bad]
        result = runner.invoke(app, args)
        assert result.exit_code == 2 and "--timeout must be greater than 0" in result.output


def test_cli_fetch_reports_a_timeout_as_an_error(tmp_path, monkeypatch):
    from harness.generate import fetch

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))

    def silent(*a, **k):
        raise fetch.FetchError("timed out: no answer from the server for 1 s while listing")

    monkeypatch.setattr(fetch, "list_shards", silent)
    result = runner.invoke(app, ["traces", "fetch", "--config", "v1.2", "--split", "sweagent", "--yes"])
    assert result.exit_code == 2 and "timed out" in result.output


def test_cli_fetch_bad_arguments_exit2(tmp_path, monkeypatch):
    from harness.generate import fetch

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(fetch, "list_shards", lambda config, split, api=None, timeout=60.0: [])
    result = runner.invoke(app, ["traces", "fetch", "--config", "v1.2", "--split", "sweagent", "--yes"])
    assert result.exit_code == 2 and "has 0 shards" in result.output
    monkeypatch.undo()
    result = runner.invoke(app, ["traces", "fetch", "--config", "v9", "--split", "sweagent", "--yes"])
    assert result.exit_code == 2 and "unknown config" in result.output


def test_cli_traces_list(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, ["traces", "list"])
    assert result.exit_code == 0, result.output
    assert "v1.2/minisweagent/a.parquet" in result.output
    assert re.search(r"acme/widget\s+MIT\s+python\s+2", result.output)
    assert re.search(r"other/gadget\s+MIT\s+python\s+1", result.output)
    assert "궤적 3개, 작업 3개, 묶음(모델 2개 이상이 푼 작업) 0개" in result.output


def test_cli_traces_list_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    result = runner.invoke(app, ["traces", "list"])
    assert result.exit_code == 0 and "받은 조각이 없습니다" in result.output


def test_cli_generate_writes_drafts_and_reports(tmp_path, monkeypatch):
    cache = cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "2"))
    assert result.exit_code == 0, result.output
    assert "요청 2개 중 2개 생성" in result.output
    files = sorted((tmp_path / "out" / "truth_change").glob("*.yaml"))
    assert len(files) == 2
    assert all(yaml.safe_load(f.read_text(encoding="utf-8"))["review"]["status"] == "draft" for f in files)
    (run_dir,) = (cache / "runs").iterdir()
    assert (run_dir / "candidates.jsonl").is_file() and (run_dir / "rejected.jsonl").is_file()
    assert (cache / "llm").is_dir()


def test_cli_generate_shortfall_exits_0(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    canned = tmp_path / "canned.json"
    cand = {"statement": "Run the checks.", "kind": "test", "quote": "run the project checks", "message_index": 2}
    canned.write_text(json.dumps({"candidates": [{"candidates": [cand]}] * 3}), encoding="utf-8")
    args = gen_args(tmp_path, "--family", "workaround_then_root", "--n", "2", fake_responses=str(canned))
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "요청 2개 중 0개 생성" in result.output and "부족 workaround_then_root" in result.output


def test_cli_generate_all_halves_normal_repeat(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "all", "--n", "2"))
    assert result.exit_code == 0, result.output
    assert "요청 19개 중" in result.output  # 9 families x 2 + normal_repeat x 1


def test_cli_generate_repos_filter(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "2", "--repos", "other/gadget"))
    assert result.exit_code == 0, result.output
    names = [f.name for f in (tmp_path / "out" / "truth_change").glob("*.yaml")]
    assert names and all("-gadget-" in n for n in names)


def test_generate_missing_key_exit2(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    args = gen_args(tmp_path, "--family", "truth_change", provider="openai", api_key_env="NO_SUCH_KEY")
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "openai" in result.output and "NO_SUCH_KEY" in result.output
    assert not (tmp_path / "out").exists()


def test_generate_dry_run_makes_no_calls(tmp_path, monkeypatch):
    cache = cached_traces(tmp_path, monkeypatch)
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    args = gen_args(tmp_path, "--family", "truth_change", "--dry-run", provider="openai", api_key_env="NO_SUCH_KEY")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output  # no key needed: no client is built
    assert "Repository: " in result.output and "system:" in result.output
    assert not (tmp_path / "out").exists()
    assert not (cache / "llm").exists() and not (cache / "runs").exists()


def test_generate_unknown_family_and_no_traces_exit2(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    result = runner.invoke(app, gen_args(tmp_path, "--family", "nope"))
    assert result.exit_code == 2 and "unknown family" in result.output
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change"))
    assert result.exit_code == 2 and "traces fetch" in result.output
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--repos", "nowhere/zip"))
    assert result.exit_code == 2 and "no usable trajectory" in result.output


def test_gen_commands_without_gen_deps_exit2(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    for dep in ("pyarrow", "huggingface_hub"):
        monkeypatch.setitem(sys.modules, dep, None)
    for argv in (
        gen_args(tmp_path, "--family", "truth_change"),
        ["traces", "list"],
        ["traces", "fetch", "--config", "v1.2", "--split", "sweagent", "--yes"],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 2, (argv, result.output)
        assert "uv sync --extra gen 이 필요합니다" in result.output


def test_cli_generate_max_traces_is_reported(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "5", "--max-traces", "1"))
    assert result.exit_code == 0, result.output
    assert "stopped at --max-traces 1" in result.output and "--max-traces 1" in result.output.splitlines()[-2]
    assert runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--max-traces", "0")).exit_code == 2


def test_cli_generate_max_attempts_is_reported(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "2", "--max-attempts", "1"))
    assert result.exit_code == 0, result.output
    assert "요청 2개 중 1개 생성" in result.output and "stopped at --max-attempts 1" in result.output
    assert runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--max-attempts", "0")).exit_code == 2


def test_cli_generate_prints_the_first_llm_error(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    canned = tmp_path / "canned.json"
    canned.write_text(json.dumps({"bodies": ["{not json", "{still not json"]}), encoding="utf-8")
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "1", fake_responses=str(canned)))
    assert result.exit_code == 0, result.output
    assert "first LLM error: model output failed validation twice" in result.output
    clean = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "1"))
    assert clean.exit_code == 0 and "first LLM error" not in clean.output


def test_cli_generate_stops_after_three_llm_errors_in_a_row(tmp_path, monkeypatch):
    cached_traces(tmp_path, monkeypatch)
    canned = tmp_path / "canned.json"
    canned.write_text(json.dumps({"candidates": ["{bad"] * 6}), encoding="utf-8")
    result = runner.invoke(app, gen_args(tmp_path, "--family", "truth_change", "--n", "1", fake_responses=str(canned)))
    assert result.exit_code == 2
    assert "3 LLM errors in a row" in result.output and "first LLM error" in result.output


# ---- review and scenario new -------------------------------------------------------------------


def review_root(tmp_path: Path) -> Path:
    from harness.generate.pipeline import save_draft

    root = tmp_path / "dev"
    save_draft(truth_change("tc-a"), root / "truth_change" / "tc-a.yaml")
    save_draft(truth_change("tc-b"), root / "truth_change" / "tc-b.yaml")
    save_draft(too_easy().model_copy(update={"id": "tc-easy"}), root / "truth_change" / "tc-easy.yaml")
    return root


def test_cli_review_list_and_set(tmp_path):
    root = review_root(tmp_path)
    result = runner.invoke(app, ["review", "list", str(root)])
    assert result.exit_code == 0, result.output
    for sid in ("tc-a", "tc-b", "tc-easy"):
        assert sid in result.output
    assert result.output.count("draft") >= 3

    ok = runner.invoke(app, ["review", "set", "tc-a", "approved", "--reviewer", "kim", "--note", "fine", "--root", str(root)])
    assert ok.exit_code == 0, ok.output
    assert "approved" in ok.output
    rej = runner.invoke(app, ["review", "set", "tc-b", "rejected", "--reviewer", "kim", "--root", str(root)])
    assert rej.exit_code == 0, rej.output
    listing = runner.invoke(app, ["review", "list", str(root)]).output
    assert [line.split()[2] for line in listing.splitlines() if line.startswith("tc-")] == ["approved", "rejected", "draft"]


def test_cli_review_set_approved_blocked_exit1(tmp_path):
    root = review_root(tmp_path)
    path = root / "truth_change" / "tc-easy.yaml"
    before = path.read_bytes()
    result = runner.invoke(app, ["review", "set", "tc-easy", "approved", "--reviewer", "kim", "--root", str(root)])
    assert result.exit_code == 1
    assert "too_easy" in result.output
    assert path.read_bytes() == before


def test_cli_review_set_bad_status_exit2(tmp_path):
    root = review_root(tmp_path)
    result = runner.invoke(app, ["review", "set", "tc-a", "maybe", "--reviewer", "kim", "--root", str(root)])
    assert result.exit_code == 2


def test_cli_review_unknown_id_exit1(tmp_path):
    root = review_root(tmp_path)
    for argv in (
        ["show", "nope"],
        ["edit", "nope"],
        ["set", "nope", "rejected", "--reviewer", "kim"],
    ):
        result = runner.invoke(app, ["review", *argv, "--root", str(root)])
        assert result.exit_code == 1, (argv, result.output)
        assert "nope" in result.output


def test_cli_review_duplicate_id_exit1(tmp_path):
    root = review_root(tmp_path)
    (root / "other").mkdir()
    (root / "other" / "copy.yaml").write_bytes((root / "truth_change" / "tc-a.yaml").read_bytes())
    result = runner.invoke(app, ["review", "show", "tc-a", "--root", str(root)])
    assert result.exit_code == 1
    assert "copy.yaml" in result.output and "tc-a.yaml" in result.output


def test_cli_review_show(tmp_path):
    root = review_root(tmp_path)
    result = runner.invoke(app, ["review", "show", "tc-a", "--root", str(root)], terminal_width=200)
    assert result.exit_code == 0, result.output
    for word in ("tc-a", "truth_change", "valid_until", "oracle", "all_store", "p_block"):
        assert word in result.output


def test_cli_review_edit(tmp_path, monkeypatch):
    from tests.test_review import editor

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    root = review_root(tmp_path)
    path = root / "truth_change" / "tc-a.yaml"
    monkeypatch.setenv("EDITOR", editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))"))
    result = runner.invoke(app, ["review", "edit", "tc-a", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "reviewer_edited" in path.read_text(encoding="utf-8")
    assert "re-approval" not in result.output

    before = path.read_bytes()
    monkeypatch.setenv("EDITOR", editor(tmp_path, "path.write_text(text.replace('valid: true', 'valid: false', 1))"))
    result = runner.invoke(app, ["review", "edit", "tc-a", "--root", str(root)])
    assert result.exit_code == 1
    assert "facts.f_old.valid" in result.output and "reject the draft" in result.output
    assert path.read_bytes() == before
    kept = next((tmp_path / "cache" / "review").glob("tc-a-*.yaml"))
    assert f"your edit was kept at {kept}" in result.output


def test_cli_review_edit_defaults_to_vi(tmp_path, monkeypatch):
    from harness import review

    seen: list[str] = []
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(review, "edit_draft", lambda path, editor, cfg, gen: seen.append(editor) or review.EditResult("unchanged"))
    root = review_root(tmp_path)
    assert runner.invoke(app, ["review", "edit", "tc-a", "--root", str(root)]).exit_code == 0
    assert seen == ["vi"]


def test_cli_review_works_without_gen_deps(tmp_path, monkeypatch):
    for dep in ("anthropic", "openai", "pyarrow", "huggingface_hub"):
        monkeypatch.setitem(sys.modules, dep, None)
    root = review_root(tmp_path)
    assert runner.invoke(app, ["review", "list", str(root)]).exit_code == 0
    assert runner.invoke(app, ["review", "set", "tc-a", "approved", "--reviewer", "kim", "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, ["scenario", "new", "--family", "truth_change", "--seed", "1", "--out", str(tmp_path / "n")]).exit_code == 0


def test_scenario_new_writes_empty_skeleton(tmp_path):
    from harness.scenario.load import load_scenario

    out = tmp_path / "new"
    result = runner.invoke(app, ["scenario", "new", "--family", "truth_change", "--seed", "7", "--out", str(out)])
    assert result.exit_code == 0, result.output
    (path,) = list(out.rglob("*.yaml"))
    assert path.parent.name == "truth_change" and path.name == f"{path.stem}.yaml"
    assert str(path) in result.output
    s = load_scenario(path)
    assert path.stem == s.id and s.id.startswith("truth_change-manual-")
    assert s.provenance.author == "human" and s.review.status == "draft"
    assert s.provenance.source_traces == [] and s.provenance.source_repos == []
    assert all(f.text == "" and f.markers == [] for f in s.facts.values())
    assert all(e.text == "" and e.secret_markers == [] for e in s.events)
    assert all(p.query == "" for p in s.probes)
    assert any(f.valid_until is not None for f in s.facts.values())  # labels come from the template
    assert all(e.fact in s.facts for e in s.events if e.fact)


def test_scenario_new_every_family(tmp_path):
    from harness.generate.families import TEMPLATES

    for family in TEMPLATES:
        result = runner.invoke(app, ["scenario", "new", "--family", family, "--seed", "3", "--out", str(tmp_path)])
        assert result.exit_code == 0, (family, result.output)
        assert len(list((tmp_path / family).glob("*.yaml"))) == 1


def test_scenario_new_leak_family_says_the_secret_is_filled_in_by_hand(tmp_path):
    from harness.scenario.load import load_scenario

    out = runner.invoke(app, ["scenario", "new", "--family", "leak_personal", "--seed", "3", "--out", str(tmp_path)])
    assert out.exit_code == 0 and "secret_markers" in out.output and "review edit 전에" in out.output
    (path,) = list(tmp_path.rglob("*.yaml"))
    s = load_scenario(path)
    assert s.provenance.secret_events and all(e.secret_markers == [] for e in s.events)
    other = runner.invoke(app, ["scenario", "new", "--family", "truth_change", "--seed", "3", "--out", str(tmp_path)])
    assert "secret_markers" not in other.output


def test_scenario_new_workaround_family_sets_fix(tmp_path):
    from harness.scenario.load import load_scenario

    runner.invoke(app, ["scenario", "new", "--family", "workaround_then_root", "--seed", "3", "--out", str(tmp_path)])
    (path,) = list(tmp_path.rglob("*.yaml"))
    assert {f.fix for f in load_scenario(path).facts.values()} >= {"workaround", "root"}


def test_scenario_new_refuses_to_overwrite(tmp_path):
    argv = ["scenario", "new", "--family", "truth_change", "--seed", "7", "--out", str(tmp_path)]
    assert runner.invoke(app, argv).exit_code == 0
    (path,) = list(tmp_path.rglob("*.yaml"))
    path.write_text(path.read_text(encoding="utf-8") + "# my work\n", encoding="utf-8")
    again = runner.invoke(app, argv)
    assert again.exit_code == 1
    assert "# my work" in path.read_text(encoding="utf-8")


def test_scenario_new_unknown_family_exit2(tmp_path):
    result = runner.invoke(app, ["scenario", "new", "--family", "nope", "--seed", "1", "--out", str(tmp_path)])
    assert result.exit_code == 2
    assert "nope" in result.output


def test_cli_review_edit_of_approved_says_reapproval_needed(tmp_path, monkeypatch):
    from tests.test_review import editor

    root = review_root(tmp_path)
    assert runner.invoke(app, ["review", "set", "tc-a", "approved", "--reviewer", "kim", "--root", str(root)]).exit_code == 0
    monkeypatch.setenv("EDITOR", editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))"))
    result = runner.invoke(app, ["review", "edit", "tc-a", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "draft" in result.output and "re-approval" in result.output


# ---- approval integrity ---------------------------------------------------------------------------


def approved_dir(tmp_path: Path) -> Path:
    """Two approved scenarios: one approved through `review set`, one with its hash recorded directly."""
    from harness.generate.pipeline import mark_generated, save_draft

    d = tmp_path / "scenarios"
    s = truth_change()
    mark_generated(s)
    save_draft(s, d / f"{s.id}.yaml")
    assert runner.invoke(app, ["review", "set", s.id, "approved", "--reviewer", "kim", "--root", str(d)]).exit_code == 0
    write(d, poison())  # its bodies fail check_draft, so `review set` would not approve it
    return d


def _tamper(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["events"][0]["text"] += " and also the secret-repo-name"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_run_with_verified_approvals_prints_no_warning(tmp_path):
    result, _ = run_cli(tmp_path, approved_dir(tmp_path))
    assert result.exit_code == 0, result.output
    assert "approval_unverified" not in result.output and "warning" not in result.output


def test_run_refuses_an_approved_file_edited_after_approval(tmp_path):
    d = approved_dir(tmp_path)
    _tamper(d / "tc.yaml")
    result, out = run_cli(tmp_path, d)
    assert result.exit_code == 1
    assert f"{d / 'tc.yaml'}: error approved_content_changed" in result.output
    assert not out.exists()
    sealed, _ = run_cli(tmp_path, d, "--sealed")
    assert sealed.exit_code == 1 and "approved_content_changed" in sealed.output
    assert "tc.yaml" not in sealed.output and "secret" not in sealed.output


def test_unverified_approvals_are_warned_with_paths(tmp_path):
    d = good_dir(tmp_path, hashed=False)  # approved by hand, no approved_hash
    result, _ = run_cli(tmp_path, d)
    assert result.exit_code == 0, result.output
    assert result.output.count("approval_unverified") == 2 and str(d / "tc.yaml") in result.output
    sc = runner.invoke(app, ["selfcheck", "--scenarios", str(d)])
    assert sc.exit_code == 0 and sc.output.count("approval_unverified") == 2


def test_sealed_mode_prints_only_the_number_of_approval_warnings(tmp_path):
    d = good_dir(tmp_path, hashed=False)
    result, _ = run_cli(tmp_path, d, "--sealed")
    assert result.exit_code == 0, result.output
    assert "warning: 2 " in result.output
    body = "\n".join(result.output.strip().splitlines()[:-1])  # the last line is the report path
    for leak in ("tc.yaml", "sp.yaml", str(d), "approval_unverified"):
        assert leak not in body, leak
    sc = runner.invoke(app, ["selfcheck", "--scenarios", str(d), "--sealed"])
    assert sc.exit_code == 0 and "warning: 2 " in sc.output and ".yaml" not in sc.output


def test_check_reports_approval_state(tmp_path):
    d = approved_dir(tmp_path)
    assert runner.invoke(app, ["scenario", "check", str(d)]).exit_code == 0
    _tamper(d / "tc.yaml")
    result = runner.invoke(app, ["scenario", "check", str(d)])
    assert result.exit_code == 1
    assert f"{d / 'tc.yaml'}: error approved_content_changed" in result.output
    unverified = write(tmp_path, truth_change(), name="old.yaml", hashed=False)
    result = runner.invoke(app, ["scenario", "check", str(unverified)])
    assert result.exit_code == 0, result.output
    assert f"{unverified}: warning approval_unverified" in result.output


def test_scenario_new_records_the_generation_hashes(tmp_path):
    from harness.scenario.labels import content_hash, label_fingerprint
    from harness.scenario.load import load_scenario
    from harness.yamlio import load_yaml

    runner.invoke(app, ["scenario", "new", "--family", "truth_change", "--seed", "3", "--out", str(tmp_path / "a")])
    (path,) = list((tmp_path / "a").rglob("*.yaml"))
    s = load_scenario(path)
    assert s.provenance.label_fingerprint == label_fingerprint(s)
    assert s.provenance.generated_hash == content_hash(load_yaml(path.read_text(encoding="utf-8")), "generated")
    # Leak skeletons get their secret strings by hand (a label), so no fingerprint is recorded for them.
    runner.invoke(app, ["scenario", "new", "--family", "leak_personal", "--seed", "3", "--out", str(tmp_path / "b")])
    (leak,) = list((tmp_path / "b").rglob("*.yaml"))
    assert load_scenario(leak).provenance.label_fingerprint is None
