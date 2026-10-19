import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime

from harness.agent import Choice
from harness.contract import EngineInfo, Result
from harness.metrics import compute_metrics
from harness.replay import ProbeRecord, ScenarioRun
from harness.report import (
    RunMeta,
    _scrubber,
    blind_ids,
    harness_version,
    machine_info,
    write_report,
)
from harness.scoring import Leak
from harness.selection import EngineScore, RankEntry, Verdict
from harness.selfcheck import CheckResult
from harness.stats import Delay, rate

SECRET = "acme.mem:SecretEngine"
OTHER = "zeta.mem:OtherEngine"


def metrics(m1=0.2, m2=0.9, m3=0, **kw):
    empty = compute_metrics([], {})
    return replace(
        empty,
        m1=rate(round(m1 * 10), 10),
        m2=rate(round(m2 * 10), 10),
        m3=m3,
        promote_fn=rate(1, 10),
        by_family={"truth_change": {"m1": rate(1, 5), "m2": rate(4, 5), "m3": 0}},
        feedback_by_family={"echo": {"m1": rate(0, 3), "m2": rate(3, 3), "m3": 0}},
        by_author={"human": {"m1": rate(1, 5), "m2": rate(4, 5)}},
        retraction=[Delay("f_old", "sc_secret", 3600.0), Delay("f_x", "sc_other", None)],
        context_chars_mean=120.5,
        record_ms_p50=1.0,
        record_ms_p95=2.0,
        search_ms_p50=3.0,
        search_ms_p95=4.0,
        **kw,
    )


def score(name, **kw):
    m = metrics(**kw)
    v = Verdict(True, True, m.m3 == 0, None, m.m3 == 0, [] if m.m3 == 0 else ["M3 leaked"])
    return EngineScore(name, m, v, [m])


def record(probe_id="probe_secret", outcome="TP", leaks=()):
    return ProbeRecord(
        probe_id=probe_id,
        kind="promote",
        results=[Result(text="use vendor v2", sources=["e1"])],
        choice=Choice(0, frozenset({"f_b", "f_a"})),
        outcome=outcome,
        leaks=list(leaks),
        bases=[frozenset({"f_a"})],
        contaminated=False,
        reached=True,
        search_ms=1.0,
        context_chars=13,
    )


def run(sid="sc_secret", error=None, probes=None):
    probes = [record()] if probes is None else probes
    return ScenarioRun(sid, "truth_change", "read_only", probes=[] if error else probes, error=error)


def meta(sealed=False, infos=None):
    infos = infos if infos is not None else {
        SECRET: EngineInfo(name=SECRET, version="1.2", virtual_clock=True, sources=True, scopes=False),
        "none": EngineInfo(name="none", version="0"),
    }
    return RunMeta(
        run_id="r1",
        started_at=datetime(2026, 10, 19, 9, 30, tzinfo=UTC),
        harness_version="abc1234",
        config_hash="cfg-hash-xyz",
        scenario_set_hash="set-hash-123",
        n_scenarios=2,
        machine="TestOS / Python 3.14",
        engine_infos=infos,
        sealed=sealed,
    )


def fixture(sealed=False, errors=False):
    scores = [score("none", m1=0.6, m2=0.0), score("all_store", m1=0.6, m2=0.9),
              score("oracle", m1=0.0, m2=1.0), score("repeat_n", m1=0.4, m2=0.8),
              score(SECRET), score(OTHER, m3=1)]
    ranking = [RankEntry(SECRET, 1, False, None, None)]
    runs = {n: [run()] for n in ("none", "all_store", "oracle", "repeat_n", SECRET, OTHER)}
    if errors:
        runs[OTHER] = [run("sc_broken", error=f"RuntimeError: {OTHER} exploded | badly")]
    selfcheck = [CheckResult("oracle_perfect", True, "fine"),
                 CheckResult("no_errors", False, "errors in none:sc_secret")]
    return meta(sealed), scores, ranking, runs, selfcheck


def write(tmp_path, sealed=False, errors=False, seed=7):
    m, scores, ranking, runs, selfcheck = fixture(sealed, errors)
    out = tmp_path / "out"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=seed)
    return out


def test_report_files_written(tmp_path):
    out = write(tmp_path)
    for name in ("summary.json", "results.jsonl", "report.md", "engine_ids.json"):
        assert (out / name).is_file()
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["meta"]["config_hash"] == "cfg-hash-xyz"
    assert summary["meta"]["started_at"] == "2026-10-19T09:30:00+00:00"
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))
    assert sorted(ids.values()) == sorted([SECRET, OTHER])
    assert set(summary["engines"]) == {"none", "all_store", "repeat_n", "oracle", *ids}
    blind = next(i for i, n in ids.items() if n == SECRET)
    assert summary["engines"][blind]["metrics"]["m1"]["value"] == 0.2
    assert summary["engines"][blind]["metrics"]["m1"]["ci"][0] < 0.2
    assert summary["engines"][blind]["verdict"]["passed"] is True
    assert summary["ranking"][0]["engine_id"] == blind
    assert summary["selfcheck"][1]["name"] == "no_errors"
    assert summary["meta"]["engine_infos"][blind]["version"] == "1.2"


def test_results_jsonl_lines(tmp_path):
    out = write(tmp_path)
    lines = [json.loads(line) for line in (out / "results.jsonl").read_text("utf-8").splitlines()]
    assert len(lines) == 6
    first = next(line for line in lines if line["engine_id"] == "none")
    assert first["scenario_id"] == "sc_secret" and first["probe_id"] == "probe_secret"
    assert first["kind"] == "promote" and first["outcome"] == "TP"
    assert first["choice"] == {"index": 0, "basis": ["f_a", "f_b"]}
    assert first["results"] == [{"text": "use vendor v2", "sources": ["e1"]}]
    assert first["leaks"] == []


def test_results_jsonl_leaks_and_skips_errors(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture()
    runs["none"] = [run("sc_leak", probes=[record(leaks=[Leak(0, "ev9", "wrong_team")])]),
                    run("sc_broken", error="boom")]
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=1)
    lines = [json.loads(line) for line in (out / "results.jsonl").read_text("utf-8").splitlines()]
    none_lines = [line for line in lines if line["engine_id"] == "none"]
    assert [line["scenario_id"] for line in none_lines] == ["sc_leak"]
    assert none_lines[0]["leaks"] == [{"result_index": 0, "event_id": "ev9", "reason": "wrong_team"}]


def test_sealed_omits_details(tmp_path):
    out = write(tmp_path, sealed=True, errors=True)
    assert not (out / "results.jsonl").exists()
    report = (out / "report.md").read_text("utf-8")
    for secret in ("probe_secret", "sc_secret", "sc_other", "sc_broken", "f_old", "none:sc_secret"):
        assert secret not in report
    summary_text = (out / "summary.json").read_text("utf-8")
    for secret in ("probe_secret", "sc_secret", "sc_other", "sc_broken", "f_old"):
        assert secret not in summary_text
    assert "truth_change" in report  # family names are fine
    assert "0.200" in report  # metric values are fine


def test_blind_ids_deterministic():
    names = ["none", SECRET, OTHER, "oracle", "zzz:Third"]
    a = blind_ids(names, 42)
    assert a == blind_ids(list(reversed(names)), 42)
    assert a["none"] == "none" and a["oracle"] == "oracle"
    assert sorted(v for k, v in a.items() if k not in ("none", "oracle")) == ["E1", "E2", "E3"]
    assert any(blind_ids(names, s) != a for s in range(1, 20))


def test_names_not_in_outputs(tmp_path):
    out = write(tmp_path, errors=True)
    for name in ("report.md", "summary.json", "results.jsonl"):
        text = (out / name).read_text("utf-8")
        assert SECRET not in text and OTHER not in text
        assert "SecretEngine" not in text and "OtherEngine" not in text
    report = (out / "report.md").read_text("utf-8")
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))
    assert all(i in report for i in ids)
    for baseline in ("none", "all_store", "repeat_n", "oracle"):
        assert baseline in report


def test_same_seed_same_files(tmp_path):
    a = write(tmp_path / "a")
    b = write(tmp_path / "b")
    for name in ("summary.json", "report.md", "engine_ids.json", "results.jsonl"):
        assert (a / name).read_text("utf-8") == (b / name).read_text("utf-8")


def test_errors_listed_in_report(tmp_path):
    out = write(tmp_path, errors=True)
    report = (out / "report.md").read_text("utf-8")
    assert "sc_broken" in report and "RuntimeError" in report
    assert "exploded" in report


def test_sealed_errors_only_count(tmp_path):
    out = write(tmp_path, sealed=True, errors=True)
    report = (out / "report.md").read_text("utf-8")
    assert "오류 시나리오" in report
    assert report.split("## 오류 시나리오")[1].split("##")[0].strip() == "오류가 난 시나리오 수: 1"


def test_report_sections_and_formats(tmp_path):
    out = write(tmp_path)
    report = (out / "report.md").read_text("utf-8")
    titles = ["## 판정", "## 보조·관찰", "## 계열별", "## 되먹임", "## 출처별", "## 순위",
              "## 성능", "## 엔진 한계", "## 오류 시나리오"]
    positions = [report.index(t) for t in titles]
    assert positions == sorted(positions)
    assert report.startswith("# ")
    assert "cfg-hash-xyz" in report and "set-hash-123" in report and "abc1234" in report
    assert "0.200 (n=10) [" in report  # value, n and CI
    assert "n/a" in report  # missing values (e.g. no improvement ratio for non-candidates)
    assert "improvement_ratio" in report or "개선 달성률" in report
    assert "no_errors" in report


def test_relative_metrics_for_candidates(tmp_path):
    out = write(tmp_path)
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))
    blind = next(i for i, n in ids.items() if n == SECRET)
    rel = summary["engines"][blind]["relative"]
    # (0.6 - 0.2) / (0.6 - 0.0)
    assert abs(rel["improvement_ratio"] - 2 / 3) < 1e-9


def test_relative_metrics_skipped_without_baselines(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture()
    scores = [s for s in scores if s.engine_id != "oracle"]
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=1)
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert all(e["relative"] is None for e in summary["engines"].values())


def test_engine_limits_section(tmp_path):
    out = write(tmp_path)
    report = (out / "report.md").read_text("utf-8")
    limits = report.split("## 엔진 한계")[1].split("## 오류 시나리오")[0]
    assert "virtual_clock" in limits and "sources" in limits and "scopes" in limits
    assert "1.2" in limits


def test_harness_version_unknown_on_failure(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert harness_version() == "unknown"


def test_harness_version_returns_string():
    assert isinstance(harness_version(), str) and harness_version()


def test_machine_info():
    assert "Python" in machine_info()


def test_engine_info_scrubbed(tmp_path):
    info = EngineInfo(
        name=SECRET,
        version="SecretEngine-1",
        config={"module": SECRET, "note": "SecretEngine v2", "nested": [{"k": SECRET}],
                SECRET: 1, "SecretEngine_opt": 2},
        virtual_clock=True,
    )
    for sealed in (False, True):
        m, scores, ranking, runs, selfcheck = fixture(sealed)
        m.engine_infos = {SECRET: info}
        out = tmp_path / str(sealed)
        write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=3)
        for name in ("summary.json", "report.md"):
            text = (out / name).read_text("utf-8")
            assert SECRET not in text and "SecretEngine" not in text
        blind = next(i for i, n in json.loads((out / "engine_ids.json").read_text("utf-8")).items() if n == SECRET)
        shown = json.loads((out / "summary.json").read_text("utf-8"))["meta"]["engine_infos"][blind]
        assert shown["version"] == f"{blind}-1"
        assert shown["config"]["module"] == blind and shown["config"]["note"] == f"{blind} v2"
        assert "acme.mem" not in shown["name"]


def test_shared_class_name_not_mapped():
    ids = blind_ids(["a.x:Engine", "b.y:Engine", "none"], 5)
    scrub = _scrubber(ids)
    assert scrub("a.x:Engine and b.y:Engine") == f"{ids['a.x:Engine']} and {ids['b.y:Engine']}"
    assert ids["a.x:Engine"] != ids["b.y:Engine"]
    assert scrub("bare Engine stays") == "bare Engine stays"
    assert scrub("none") == "none"
