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
        promote_fp=rate(2, 10),
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


def score(name, flip=None, **kw):
    m = metrics(**kw)
    v = Verdict(True, True, m.m3 == 0, None, m.m3 == 0, [] if m.m3 == 0 else ["M3 leaked"])
    return EngineScore(name, m, v, [m], flip)


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


def meta(sealed=False, infos=None, include_drafts=False):
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
        include_drafts=include_drafts,
        scenario_roots=["secret/roots/dev", "secret/roots/extra.yaml"],
        repeats={"none": 1, "all_store": 1, "oracle": 1, "repeat_n": 1, SECRET: 3, OTHER: 1},
    )


def fixture(sealed=False, errors=False):
    scores = [score("none", m1=0.6, m2=0.0), score("all_store", m1=0.6, m2=0.9),
              score("oracle", m1=0.0, m2=1.0), score("repeat_n", m1=0.4, m2=0.8),
              score(SECRET, flip=rate(1, 8)), score(OTHER, m3=1)]
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
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))["ids"]
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
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))["ids"]
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
    ids = json.loads((out / "engine_ids.json").read_text("utf-8"))["ids"]
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


def test_engine_info_config_written_in_json_mode(tmp_path):
    from pathlib import Path

    from harness.report import jsonable

    info = EngineInfo(name="x", version="1", config={"v": Path("/data"), "s": {"b", "a"}})
    assert jsonable(info)["config"] == {"v": "/data", "s": ["a", "b"]}
    m, scores, ranking, runs, selfcheck = fixture()
    m.engine_infos = {SECRET: info}
    out = tmp_path / "out"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=3)
    shown = json.loads((out / "summary.json").read_text("utf-8"))["meta"]["engine_infos"]
    assert [i["config"]["v"] for i in shown.values()] == ["/data"]


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
        blind = next(i for i, n in json.loads((out / "engine_ids.json").read_text("utf-8"))["ids"].items() if n == SECRET)
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


def test_scrubber_hides_dotted_module_path_and_declared_name():
    # Reproduces review finding C10c: module path and the engine's own name stayed in the report.
    a, b = "acme.secretsauce:Engine", "zeta.mem:Other"
    ids = blind_ids([a, b, "none", "all_store"], 5)
    infos = {
        a: EngineInfo(name="SecretSauce", version="1"),
        b: EngineInfo(name="mem", version="1"),  # under 4 characters: too common to replace
        "all_store": EngineInfo(name="all_store", version="0"),
    }
    scrub = _scrubber(ids, infos)
    ea, eb = ids[a], ids[b]
    assert scrub(f"{a} failed") == f"{ea} failed"
    assert scrub("TimeoutError at acme.secretsauce.client") == f"TimeoutError at {ea}.client"
    assert scrub("zeta.mem broke") == f"{eb} broke"
    assert scrub("[SecretSauce] hello") == f"[{ea}] hello"
    assert scrub("mem leak in acme and zeta") == "mem leak in acme and zeta"  # single components stay
    assert scrub("all_store m1=0.5, none") == "all_store m1=0.5, none"


def test_scrubber_skips_ambiguous_and_baseline_like_names():
    a, b = "x.one:A", "y.two:B"
    ids = blind_ids([a, b, "all_store", "none"], 1)
    infos = {
        a: EngineInfo(name="shared", version="1"),
        b: EngineInfo(name="shared", version="1"),  # two candidates claim it: ambiguous
    }
    assert _scrubber(ids, infos)("shared thing") == "shared thing"
    infos = {a: EngineInfo(name="store", version="1"), b: EngineInfo(name="oracle", version="1")}
    scrub = _scrubber(ids, infos)
    assert scrub("all_store beats oracle") == "all_store beats oracle"  # would garble baseline names
    assert _scrubber(ids)("x.one failed") == f"{ids[a]} failed"  # infos are optional


def test_report_hides_module_path_and_declared_name(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture(errors=True)
    m.engine_infos = {
        SECRET: EngineInfo(name="SecretEngine", version="acme.mem 1.2", config={"impl": "acme.mem"}),
        OTHER: EngineInfo(name="Othello", version="2", config={"label": "Othello v2"}),
    }
    runs[OTHER] = [run("sc_broken", error="RuntimeError: zeta.mem.client: Othello timed out")]
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=2)
    for name in ("summary.json", "report.md"):
        text = (out / name).read_text("utf-8")
        for hidden in ("acme.mem", "zeta.mem", "SecretEngine", "Othello"):
            assert hidden not in text, (name, hidden)


def engine_ids(out):
    return json.loads((out / "engine_ids.json").read_text("utf-8"))


def test_flip_rate_reported(tmp_path):
    out = write(tmp_path)
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    blind = next(i for i, n in engine_ids(out)["ids"].items() if n == SECRET)
    assert summary["engines"][blind]["flip"]["k"] == 1 and summary["engines"][blind]["flip"]["n"] == 8
    assert summary["engines"]["none"]["flip"] is None
    report = (out / "report.md").read_text("utf-8")
    aux = report.split("## 보조·관찰")[1].split("## 계열별")[0]
    assert "재실행 뒤집힘률" in aux and "0.125 (n=8)" in aux


def test_run_meta_drafts_roots_repeats(tmp_path):
    out = write(tmp_path)
    meta_json = json.loads((out / "summary.json").read_text("utf-8"))["meta"]
    blind = next(i for i, n in engine_ids(out)["ids"].items() if n == SECRET)
    assert meta_json["include_drafts"] is False
    assert meta_json["scenario_roots"] == ["secret/roots/dev", "secret/roots/extra.yaml"]
    assert meta_json["repeats"][blind] == 3 and meta_json["repeats"]["none"] == 1
    assert SECRET not in json.dumps(meta_json["repeats"])
    report = (out / "report.md").read_text("utf-8")
    head = report.split("## 판정")[0]
    assert "초안 포함: 아니오" in head
    assert "secret/roots/dev" in head
    assert f"{blind} 3" in head
    assert "⚠️" not in report


def test_drafts_warning_at_top(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture()
    m.include_drafts = True
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=1)
    report = (out / "report.md").read_text("utf-8")
    assert "> ⚠️ 초안 포함 — 발표 수치로 쓰지 않음" in report.splitlines()[:3]
    assert "초안 포함: 예" in report
    assert json.loads((out / "summary.json").read_text("utf-8"))["meta"]["include_drafts"] is True


def test_sealed_roots_as_count(tmp_path):
    out = write(tmp_path, sealed=True)
    for name in ("summary.json", "report.md"):
        assert "secret/roots" not in (out / name).read_text("utf-8")
    meta_json = json.loads((out / "summary.json").read_text("utf-8"))["meta"]
    assert "scenario_roots" not in meta_json and meta_json["n_scenario_roots"] == 2
    assert "시나리오 경로 수: 2" in (out / "report.md").read_text("utf-8")


def test_default_blind_seed_is_random_and_only_in_engine_ids(tmp_path, monkeypatch):
    import secrets

    m, scores, ranking, runs, selfcheck = fixture()
    monkeypatch.setattr(secrets, "randbits", lambda bits: 987654321987 if bits == 64 else 0)
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck)
    assert engine_ids(out)["seed"] == 987654321987
    expected = blind_ids(sorted({*(s.engine_id for s in scores), *runs, *m.engine_infos}), 987654321987)
    assert engine_ids(out)["ids"] == {v: k for k, v in expected.items() if v != k}
    for name in ("summary.json", "report.md", "results.jsonl"):
        assert "987654321987" not in (out / name).read_text("utf-8")


def test_explicit_blind_seed_recorded(tmp_path):
    out = write(tmp_path, seed=7)
    assert engine_ids(out)["seed"] == 7


def test_delay_labels(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture()
    scores[0] = score("none", m1=0.6, m2=0.0, reflection=[Delay("f_new", "sc_secret", None)])
    out = tmp_path / "o"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=1)
    row = next(line for line in (out / "report.md").read_text("utf-8").split("## 보조·관찰")[1].splitlines()
               if line.startswith("| none "))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[2] == "중앙값 3600s, 미철회 1/2"  # retraction
    assert cells[4] == "중앙값 -, 미반영 1/1"  # reflection


def test_engine_ids_written_even_if_report_fails(tmp_path, monkeypatch):
    import pytest

    import harness.report as report

    def boom(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(report, "_markdown", boom)
    with pytest.raises(RuntimeError):
        write(tmp_path)
    ids = json.loads((tmp_path / "out" / "engine_ids.json").read_text("utf-8"))["ids"]
    assert sorted(ids.values()) == sorted([SECRET, OTHER])


def test_report_shows_promote_fp_column(tmp_path):
    out = write(tmp_path)
    report = (out / "report.md").read_text("utf-8")
    aux = report.split("## 보조·관찰")[1].split("## 계열별")[0]
    header = next(line for line in aux.splitlines() if line.startswith("| 엔진"))
    cols = [c.strip() for c in header.strip("|").split("|")]
    assert "promote FP" in cols and cols.index("promote FP") == cols.index("promote FN") + 1
    row = next(line for line in aux.splitlines() if line.startswith("| none"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[cols.index("promote FP")].startswith("0.200 (n=10)")
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["engines"]["none"]["metrics"]["promote_fp"]["k"] == 2


def test_report_header_says_which_numbers_use_the_first_repeat(tmp_path):
    out = write(tmp_path)
    head = (out / "report.md").read_text("utf-8").split("## 판정")[0]
    assert "M1·M2 구간과 순위 부트스트랩은 첫 회차 기준, 나머지 표는 회차 합산" in head


def test_aggregation_header_only_when_some_engine_repeats(tmp_path):
    m, scores, ranking, runs, selfcheck = fixture()
    m.repeats = dict.fromkeys(m.repeats, 1)
    out = tmp_path / "once"
    write_report(out, m, scores, ranking, runs, selfcheck, blind_seed=7)
    report = (out / "report.md").read_text("utf-8")
    assert "- 집계:" not in report
    assert "독립인 표본" not in report
    assert "- 반복 실행:" in report


def test_family_section_warns_that_pooled_intervals_are_optimistic(tmp_path):
    report = (write(tmp_path) / "report.md").read_text("utf-8")
    family = report.split("## 계열별")[1].split("## 되먹임")[0]
    assert "독립인 표본" in family and "참고용" in family
