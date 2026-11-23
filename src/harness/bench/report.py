"""Report of a bench run: report.md, summary.json, results.jsonl and engine_ids.json."""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from harness.bench.gold import CELLS, compare_modes, gold_only_wrong_examples, probe_correct
from harness.bench.run import BenchMeta, BenchRun
from harness.bench.timing import cost_rows
from harness.config import Stats
from harness.contract import EngineInfo
from harness.engines import BASELINES
from harness.metrics import m1_rate, m2_rate
from harness.replay import ScenarioRun
from harness.report import (
    DRAFTS_WARNING,
    blind_ids,
    ci_text,
    info_json,
    jsonable,
    markdown_table,
    order_key,
    rate_text,
    result_lines,
    scrubber,
)
from harness.scenario.model import EXT_FAMILIES
from harness.stats import CLUSTER_ITERS, MIN_CLUSTERS, Rate, cluster_ci, rate
from harness.stress.groups import group_sort_key

HEADER = "**외부 벤치마크 트랙. 사전 등록한 판정 기준이 아니며 `harness run` 결과와 섞지 않는다.**"
M2_TITLE = "근거 도달(M2)"
KU_NOTE = (
    "LongMemEval의 원래 판정 프롬프트는 옛 값과 새 값을 같이 말해도 새 값이 답이면 정답으로 본다. "
    "이 하네스는 옛 사실을 근거로 행동하면 `block` FP로 세므로, 결과 하나의 근거에 옛 사실과 새 사실이 "
    "함께 있으면 틀린 것으로 채점한다. 결과 여러 개에 나뉘어 있으면 에이전트가 따르는 첫 관련 결과가 판정을 정한다. "
    "이 차이는 의도한 것이다. 같은 문항의 점수를 LongMemEval 논문이나 다른 구현의 정확도와 직접 비교하지 않는다."
)


def _row(runs: list[ScenarioRun]) -> dict[str, Any]:
    """M1, M2, scenario count and error count of the runs that share a family or a group.

    M1 and M2 are `harness.metrics`' own, over the runs it uses for its headline rates:
    finished, read-only ones.
    """
    scored = [r for r in runs if r.error is None and r.mode == "read_only"]
    return {
        "m1": m1_rate(scored),
        "m2": m2_rate(scored),
        "n_scenarios": len(runs),
        "n_errors": sum(r.error is not None for r in runs),
    }


def _sourceless(runs: list[ScenarioRun]) -> Rate:
    """Share of all results of the finished runs that came back without `sources`, as `Metrics.sourceless`."""
    results = [res for r in runs if r.error is None for rec in r.probes for res in rec.results]
    return rate(sum(not res.sources for res in results), len(results))


def _split(runs: list[ScenarioRun], key: Callable[[ScenarioRun], str]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ScenarioRun]] = defaultdict(list)
    for r in runs:
        grouped[key(r)].append(r)
    return {k: _row(grouped[k]) for k in sorted(grouped, key=group_sort_key)}


def _tables(b: BenchRun, ids: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Per engine (blind id): its rows by family and by dataset group, and its errored scenarios."""
    order = order_key(ids)
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(b.runs, key=order):
        runs = b.runs[name]
        out[ids[name]] = {
            "by_family": _split(runs, lambda r: r.family),
            "by_group": _split(runs, lambda r: b.groups[r.scenario_id]),
            "sourceless": _sourceless(runs),
            "errors": sorted(r.scenario_id for r in runs if r.error is not None),
        }
    return out


def _skipped(b: BenchRun) -> dict[str, Any]:
    reasons = Counter(reason for _, reason in b.skipped)
    return {
        "count": len(b.skipped),
        "reasons": dict(sorted(reasons.items())),
        "ids": sorted(source for source, _ in b.skipped),
    }


def _family_table(tables: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Rows per engine and family: the external families first, then any other family by name."""
    rows = [
        [eid, fam, row["n_scenarios"], rate_text(row["m2"]), rate_text(row["m1"]), row["n_errors"]]
        for eid, t in tables.items()
        for fam in sorted(t["by_family"], key=lambda f: (f not in EXT_FAMILIES, f))
        if (row := t["by_family"].get(fam)) is not None
    ]
    return markdown_table(["엔진", "계열", "문항 수", "M2", "M1", "오류"], rows)


def _sourceless_table(tables: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return markdown_table(["엔진", "출처 없는 결과"], [[eid, rate_text(t["sourceless"])] for eid, t in tables.items()])


def _group_table(tables: Mapping[str, Mapping[str, Any]]) -> list[str]:
    rows = [
        [eid, group, row["n_scenarios"], rate_text(row["m2"]), row["n_errors"]]
        for eid, t in tables.items()
        for group, row in t["by_group"].items()
    ]
    return markdown_table(["엔진", "유형", "문항 수", "M2", "오류"], rows)


# Reference points, never compared: the oracle answers every probe and `none` answers none.
_NOT_COMPARED = ("oracle", "none")
# Every baseline. With two or more candidate engines only they are compared: `repeat_n` often
# answers like `none`, so a baseline in the comparison would make most probes split trivially.
_BASELINES = (*BASELINES, "all_store_noacl")
# The eval config's default bootstrap seed; every cell with the same base count shares resamples.
_CLUSTER_SEED = Stats().seed


def _scored(r: ScenarioRun) -> bool:
    """The runs tables 1, 2 and 6 rate: finished and read-only, as `harness.metrics`."""
    return r.error is None and r.mode == "read_only"


def _curve(b: BenchRun, engines: list[str], ids: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Per engine (blind id) and group: base count, M1 and M2, each with a base-resampling interval."""
    curve: dict[str, dict[str, Any]] = {}
    for name in engines:
        by_group: dict[str, dict[str, list[ScenarioRun]]] = defaultdict(lambda: defaultdict(list))
        for r in b.runs[name]:
            if _scored(r):
                by_group[b.groups[r.scenario_id]][b.bases[r.scenario_id]].append(r)
        rows: dict[str, Any] = {}
        for group in sorted(by_group, key=group_sort_key):
            per_base = list(by_group[group].values())
            m1 = [m1_rate(runs) for runs in per_base]
            m2 = [m2_rate(runs) for runs in per_base]
            rows[group] = {
                "n_bases": len(per_base),
                "m1": rate(sum(x.k for x in m1), sum(x.n for x in m1)),
                "m1_base_ci": cluster_ci([(x.k, x.n) for x in m1], _CLUSTER_SEED),
                "m2": rate(sum(x.k for x in m2), sum(x.n for x in m2)),
                "m2_base_ci": cluster_ci([(x.k, x.n) for x in m2], _CLUSTER_SEED),
            }
        curve[ids[name]] = rows
    return curve


def _verdict(values: set[bool]) -> str:
    if len(values) == 2:
        return "split"
    return "all_right" if values == {True} else "all_wrong"


def _discrimination(b: BenchRun, compared: list[str]) -> dict[str, dict[str, int]]:
    """Per group: judged probes of scored runs that every compared engine answered, by verdict."""
    if len(compared) < 2:
        return {}
    verdicts: dict[tuple[str, str], dict[str, bool]] = defaultdict(dict)
    for name in compared:
        for r in b.runs[name]:
            if _scored(r):
                for rec in r.probes:
                    if rec.kind in ("block", "promote"):
                        verdicts[(r.scenario_id, rec.probe_id)][name] = probe_correct(rec.kind, rec.outcome)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"probes": 0, "split": 0, "all_right": 0, "all_wrong": 0})
    for (sid, _), by_engine in verdicts.items():
        if len(by_engine) == len(compared):
            row = counts[b.groups[sid]]
            row["probes"] += 1
            row[_verdict(set(by_engine.values()))] += 1
    return {g: counts[g] for g in sorted(counts, key=group_sort_key)}


def _stress(b: BenchRun, ids: Mapping[str, str]) -> dict[str, Any] | None:
    """Difficulty curves and per-probe discrimination of a run on stress variants; None otherwise.

    curve: per engine (blind id) and group ("<family>/<knob>=<value>"), the number of base
    scenarios and M1/M2 over finished read-only runs, each with a 95% interval that resamples
    base scenarios (`cluster_ci`; n/a with fewer than three).

    discrimination: per group, the judged probes of finished read-only runs that every compared
    engine answered, and how many of them split the compared engines, all got right or all got
    wrong (`probe_correct`). With two or more candidate engines only they are compared
    (`compared_kind` "candidates"); otherwise the baselines other than the oracle and `none`.
    """
    if all(b.bases.get(sid, sid) == sid for sid in b.groups):
        return None
    engines = sorted(b.runs, key=order_key(ids))
    compared = [name for name in engines if name not in _BASELINES]
    candidates = len(compared) >= 2
    if not candidates:
        compared = [name for name in engines if name not in _NOT_COMPARED]
    return {
        "compared": [ids[name] for name in compared],
        "compared_kind": "candidates" if candidates else "baselines",
        "curve": _curve(b, engines, ids),
        "discrimination": _discrimination(b, compared),
    }


def _stress_lines(stress: Mapping[str, Any]) -> list[str]:
    curve_rows = [
        [eid, group, row["n_bases"], rate_text(row["m1"]), ci_text(row["m1_base_ci"]),
         rate_text(row["m2"]), ci_text(row["m2_base_ci"])]
        for eid, rows in stress["curve"].items()
        for group, row in rows.items()
    ]  # fmt: skip
    split_rows = [
        [group, row["probes"], row["split"], row["all_right"], row["all_wrong"]]
        for group, row in stress["discrimination"].items()
    ]
    compared = ", ".join(stress["compared"]) or "-"
    lines = [
        "## 표 6. 난이도 곡선 (스트레스 변형)",
        "",
        "계열마다 손잡이를 하나씩 돌린 변형의 M1·M2다(끝난 read_only 실행). 칸마다 원본 시나리오가 변형 하나씩 들어 있으므로, "
        f"원본 단위 95% 구간은 원본 시나리오를 {CLUSTER_ITERS:,}회 다시 뽑은 부트스트랩이다(원본이 {MIN_CLUSTERS}개 미만이면 n/a). "
        "같은 원본이 모든 단계에 나와 단계끼리 서로 독립이 아니므로, 단계 사이의 차이는 구간이 겹치는지로 판단하지 않는다. "
        "이 표는 어느 손잡이에서 어느 엔진이 무너지는지 보는 진단이고, 엔진 순위의 근거로 쓰지 않는다. "
        "M1(막아야 할 프로브 중 잘못 쓴 비율)은 낮을수록 좋고, 노이즈에서 내려갈 수도 있다: 노이즈가 검색 점수의 단어 통계를 바꿔 "
        "원본 이벤트의 순위가 달라지고, 노이즈 자체가 상위 결과에 들어오기도 한다. "
        "원본 단위 구간이 한 점(예: [1.000, 1.000])이면 모든 원본에서 같은 값이 나왔다는 뜻이고, 원본이 적을 때도 생긴다.",
        "",
        *markdown_table(["엔진", "계열/손잡이", "원본 수", "M1", "M1 원본 단위 95%", "M2", "M2 원본 단위 95%"], curve_rows),
        "## 표 7. 문항 변별력",
        "",
        f"표 6과 같은 실행의 판정 프로브(block·promote) 중 비교한 엔진({compared})의 결과가 갈린 수다. "
        + (
            "후보 엔진이 둘 이상이라 후보끼리만 비교했다(기준 엔진은 뺐다). "
            if stress.get("compared_kind") == "candidates"
            else "후보 엔진이 둘 미만이라 oracle과 none을 뺀 기준 엔진끼리 비교했다. repeat_n은 none처럼 답하는 일이 많아 갈림이 쉽게 생기므로, 후보 엔진의 변별력으로 읽지 않는다. "
        )
        + "모두 맞히거나 모두 틀린 프로브는 이 엔진들을 가르지 못한다.",
        "",
    ]
    if len(stress["compared"]) < 2:
        return [*lines, "비교할 엔진이 둘 미만이라 세지 않았다.", ""]
    return [*lines, *markdown_table(["계열/손잡이", "판정 프로브", "엔진 사이 갈림", "모두 맞음", "모두 틀림"], split_rows)]


def _comparison(normal: BenchRun, gold: BenchRun, ids: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Per engine (blind id): the four mode-comparison counts and up to 5 `gold_only_wrong` probes."""
    order = order_key(ids)
    return {
        ids[name]: {
            "counts": compare_modes(normal.runs[name], gold.runs[name]),
            "gold_only_wrong_examples": [list(k) for k in gold_only_wrong_examples(normal.runs[name], gold.runs[name])],
        }
        for name in sorted(normal.runs.keys() & gold.runs.keys(), key=order)
    }


def _comparison_table(comparison: Mapping[str, Mapping[str, Any]]) -> list[str]:
    rows = [[eid, *(c["counts"][cell] for cell in CELLS)] for eid, c in comparison.items()]
    out = markdown_table(["엔진", "둘 다 맞음", "검색·잡음", "판단·사용", "골드에서만 틀림"], rows)
    for eid, c in comparison.items():
        examples = ", ".join(f"{s}/{p}" for s, p in c["gold_only_wrong_examples"])
        if examples:
            out += [f"- {eid} 골드에서만 틀린 예: {examples}"]
    return [*out, ""] if any(c["gold_only_wrong_examples"] for c in comparison.values()) else out


def _markdown(
    meta: BenchMeta,
    normal: BenchRun,
    gold: BenchRun | None,
    tables: Mapping[str, Mapping[str, Any]],
    gold_tables: Mapping[str, Mapping[str, Any]] | None,
    comparison: Mapping[str, Mapping[str, Any]] | None,
    ids: Mapping[str, str],
    scrub: Callable[[str], str],
    stress: Mapping[str, Any] | None = None,
) -> str:
    skipped = _skipped(normal)
    reasons = ", ".join(f"{reason} {n}" for reason, n in skipped["reasons"].items()) or "-"
    files = ", ".join(f"{name} {sha}" for name, sha in meta["file_sha256"].items())
    infos = [
        [ids[name], scrub(info.name), scrub(info.version)]
        for name, info in sorted(normal.infos.items(), key=lambda kv: order_key(ids)(kv[0]))
    ]
    lines = [
        HEADER,
        "",
        "# 외부 벤치마크 리포트",
        "",
        *([DRAFTS_WARNING, ""] if meta["include_drafts"] else []),
        f"- 실행: {meta['run_id']}",
        f"- 실행 일시: {meta['started_at']}",
        f"- 하네스 버전: {meta['harness_version']}",
        f"- 벤치 설정 해시: {meta['config_hash']}",
        f"- 데이터셋: {meta['dataset']} (라이선스 {meta['license']})",
        f"- 데이터 파일 sha256: {files}",
        f"- 변환기: {meta['converter']}",
        f"- 라벨 파일 sha256: {meta['label_sha256'] or '-'}",
        f"- 실행 모드: {'보통 + 골드' if gold is not None else '보통'}",
        f"- 변환된 문항 수: {len(next(iter(normal.runs.values()), []))}",
        f"- 건너뜀: {skipped['count']} ({reasons})",
        f"- 건너뛴 id: {', '.join(skipped['ids']) or '-'}",
        f"- 머신: {meta['machine']}",
        "",
        *markdown_table(["엔진", "이름", "버전"], infos),
        "## 표 1. 계열별 M2·M1",
        "",
        *_family_table(tables),
        "## 표 1-1. 출처 없는 결과 수",
        "",
        "엔진이 돌려준 결과 중 `sources`가 빈 것의 비율이다. 이 트랙은 표지(marker)가 없어 출처가 없으면 근거로 인정받지 못하므로 "
        "M2가 0이 된다. 출처를 채우지 않는 엔진의 낮은 점수는 이 표와 함께 읽는다.",
        "",
        *_sourceless_table(tables),
        f"## 표 2. {M2_TITLE}",
        "",
        *_group_table(tables),
    ]
    if gold_tables is not None:
        lines += ["## 표 1 (골드 모드). 계열별 M2·M1", "", *_family_table(gold_tables)]
    if comparison is not None:
        lines += [
            "## 표 3. 골드 모드 비교",
            "",
            "같은 문항을 전체 이벤트(보통)와 프로브가 묻는 사실의 이벤트만(골드)으로 돌려 프로브별로 맞고 틀림을 센다. "
            "block은 `FP`가 아니면, promote는 `TP`일 때 맞다. 검색·잡음은 보통에서만 틀림, "
            "판단·사용은 둘 다 틀림, 골드에서만 틀림은 보통에서만 맞음이다.",
            "",
            *_comparison_table(comparison),
        ]
    lines += [
        "## 표 4. 비용",
        "",
        "시간은 호출 한 번의 벽시계 ms(문항 전체를 모아 p50/p95), 컨텍스트는 프로브당 평균 글자 수다. "
        "사용량은 엔진이 `usage()`로 알린 값을 문항 전체에서 합한 것이고, 알리지 않으면 알 수 없음이다.",
        "",
        *markdown_table(
            ["엔진", "record p50", "record p95", "flush p50", "flush p95", "search p50", "search p95",
             "컨텍스트 글자", "LLM 호출", "입력 토큰", "출력 토큰", "임베딩 호출"],
            [[ids[r[0]], *r[1:]] for r in cost_rows(normal.timed, normal.runs)],
        ),
    ]
    if any("knowledge-update" in t["by_group"] for t in tables.values()):
        lines += ["## 표 5. knowledge-update 판정 차이", "", KU_NOTE, ""]
    if stress is not None:
        lines += _stress_lines(stress)
    return "\n".join(lines)


def write_bench_report(out_dir: Path, meta: BenchMeta, normal: BenchRun, gold: BenchRun | None) -> None:
    """Write report.md, summary.json, results.jsonl and engine_ids.json to `out_dir`.

    Engine names other than the baselines become blind ids everywhere except engine_ids.json.
    A gold run adds `gold` to summary.json, a table to the report and results-gold.jsonl.

    Args:
        out_dir: Directory to create and write into.
        meta: Run-level facts; its `blind_seed` goes to engine_ids.json only.
        normal: The run on the full event stream.
        gold: The run on gold events, if there was one.
    """
    seed = meta["blind_seed"]
    facts = {k: v for k, v in meta.items() if k != "blind_seed"}
    names = {*normal.runs, *normal.infos, *(gold.runs if gold is not None else [])}
    ids = blind_ids(sorted(names), seed)
    infos: dict[str, EngineInfo] = normal.infos
    scrub = scrubber(ids, infos)
    key = order_key(ids)
    tables = _tables(normal, ids)
    gold_tables = _tables(gold, ids) if gold is not None else None
    comparison = _comparison(normal, gold, ids) if gold is not None else None
    stress = _stress(normal, ids)

    out_dir.mkdir(parents=True, exist_ok=True)
    blind = {v: k for k, v in sorted(ids.items(), key=lambda kv: key(kv[0])) if v != k}
    (out_dir / "engine_ids.json").write_text(
        json.dumps({"seed": seed, "ids": blind}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary: dict[str, Any] = {
        "meta": {
            **jsonable(facts),
            "mode": normal.mode,
            "engine_infos": {
                ids[name]: info_json(name, info, ids, scrub)
                for name, info in sorted(infos.items(), key=lambda kv: key(kv[0]))
            },
        },
        "skipped": _skipped(normal),
        "engines": jsonable(tables),
        "gold": None if gold_tables is None else {"engines": jsonable(gold_tables), "comparison": comparison},
        "stress": None if stress is None else jsonable(stress),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run, name in ((normal, "results.jsonl"), (gold, "results-gold.jsonl")):
        if run is not None:
            lines = result_lines(run.runs, sorted(run.runs, key=key), ids)
            (out_dir / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(meta, normal, gold, tables, gold_tables, comparison, ids, scrub, stress) + "\n", encoding="utf-8")

