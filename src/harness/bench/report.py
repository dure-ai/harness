"""Report of a bench run: report.md, summary.json, results.jsonl and engine_ids.json."""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from harness.bench.run import BenchRun
from harness.contract import EngineInfo
from harness.metrics import _m1, _m2
from harness.replay import ScenarioRun
from harness.report import _info_json, _order_key, _rate, _result_lines, _scrubber, _table, blind_ids, jsonable

HEADER = "**외부 벤치마크 트랙. 사전 등록한 판정 기준이 아니며 `harness run` 결과와 섞지 않는다.**"
M2_TITLE = "근거 도달(M2)"
FAMILIES = ("ext_recall", "ext_update")
KU_NOTE = (
    "LongMemEval의 원래 판정 프롬프트는 옛 값과 새 값을 같이 말해도 새 값이 답이면 정답으로 본다. "
    "이 하네스는 옛 사실을 근거로 행동하면 `block` FP로 세므로, 섞인 근거는 틀린 것으로 채점한다. "
    "이 차이는 의도한 것이다. 같은 문항의 점수를 LongMemEval 논문이나 다른 구현의 정확도와 직접 비교하지 않는다."
)


def _row(runs: list[ScenarioRun]) -> dict[str, Any]:
    """M1, M2, scenario count and error count of the runs that share a family or a group.

    M1 and M2 are `harness.metrics`' own, over the runs it uses for its headline rates:
    finished, read-only ones.
    """
    scored = [r for r in runs if r.error is None and r.mode == "read_only"]
    return {
        "m1": _m1(scored),
        "m2": _m2(scored),
        "n_scenarios": len(runs),
        "n_errors": sum(r.error is not None for r in runs),
    }


def _split(runs: list[ScenarioRun], key: Callable[[ScenarioRun], str]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ScenarioRun]] = defaultdict(list)
    for r in runs:
        grouped[key(r)].append(r)
    return {k: _row(v) for k, v in sorted(grouped.items())}


def _tables(b: BenchRun, ids: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Per engine (blind id): its rows by family and by dataset group, and its errored scenarios."""
    order = _order_key(ids)
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(b.runs, key=order):
        runs = b.runs[name]
        out[ids[name]] = {
            "by_family": _split(runs, lambda r: r.family),
            "by_group": _split(runs, lambda r: b.groups[r.scenario_id]),
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
    rows = [
        [eid, fam, row["n_scenarios"], _rate(row["m2"]), _rate(row["m1"]), row["n_errors"]]
        for eid, t in tables.items()
        for fam in FAMILIES
        if (row := t["by_family"].get(fam)) is not None
    ]
    return _table(["엔진", "계열", "문항 수", "M2", "M1", "오류"], rows)


def _group_table(tables: Mapping[str, Mapping[str, Any]]) -> list[str]:
    rows = [
        [eid, group, row["n_scenarios"], _rate(row["m2"]), row["n_errors"]]
        for eid, t in tables.items()
        for group, row in t["by_group"].items()
    ]
    return _table(["엔진", "유형", "문항 수", "M2", "오류"], rows)


def _markdown(
    meta: Mapping[str, Any],
    normal: BenchRun,
    gold: BenchRun | None,
    tables: Mapping[str, Mapping[str, Any]],
    gold_tables: Mapping[str, Mapping[str, Any]] | None,
    ids: Mapping[str, str],
    scrub: Callable[[str], str],
) -> str:
    skipped = _skipped(normal)
    reasons = ", ".join(f"{reason} {n}" for reason, n in skipped["reasons"].items()) or "-"
    files = ", ".join(f"{name} {sha}" for name, sha in meta["file_sha256"].items())
    infos = [
        [ids[name], scrub(info.name), scrub(info.version)]
        for name, info in sorted(normal.infos.items(), key=lambda kv: _order_key(ids)(kv[0]))
    ]
    lines = [
        HEADER,
        "",
        "# 외부 벤치마크 리포트",
        "",
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
        *_table(["엔진", "이름", "버전"], infos),
        "## 표 1. 계열별 M2·M1",
        "",
        *_family_table(tables),
        f"## 표 2. {M2_TITLE}",
        "",
        *_group_table(tables),
    ]
    if gold_tables is not None:
        lines += ["## 표 1 (골드 모드). 계열별 M2·M1", "", *_family_table(gold_tables)]
    lines += ["## 표 5. knowledge-update 판정 차이", "", KU_NOTE, ""]
    return "\n".join(lines)


def write_bench_report(out_dir: Path, meta: dict[str, Any], normal: BenchRun, gold: BenchRun | None) -> None:
    """Write report.md, summary.json, results.jsonl and engine_ids.json to `out_dir`.

    Engine names other than the baselines become blind ids everywhere except engine_ids.json.
    A gold run adds `gold` to summary.json, a table to the report and results-gold.jsonl.

    Args:
        out_dir: Directory to create and write into.
        meta: Run-level facts: `run_id`, `started_at`, `harness_version`, `config_hash`,
            `machine`, `dataset`, `license`, `file_sha256` (name to sha256), `converter`,
            `label_sha256` (or None) and `blind_seed`; the seed goes to engine_ids.json only.
        normal: The run on the full event stream.
        gold: The run on gold events, if there was one.
    """
    meta = dict(meta)
    seed: int = meta.pop("blind_seed")
    names = {*normal.runs, *normal.infos, *(gold.runs if gold is not None else [])}
    ids = blind_ids(sorted(names), seed)
    infos: dict[str, EngineInfo] = normal.infos
    scrub = _scrubber(ids, infos)
    key = _order_key(ids)
    tables = _tables(normal, ids)
    gold_tables = _tables(gold, ids) if gold is not None else None

    out_dir.mkdir(parents=True, exist_ok=True)
    blind = {v: k for k, v in sorted(ids.items(), key=lambda kv: key(kv[0])) if v != k}
    (out_dir / "engine_ids.json").write_text(
        json.dumps({"seed": seed, "ids": blind}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary: dict[str, Any] = {
        "meta": {
            **jsonable(meta),
            "mode": normal.mode,
            "engine_infos": {
                ids[name]: _info_json(name, info, ids, scrub)
                for name, info in sorted(infos.items(), key=lambda kv: key(kv[0]))
            },
        },
        "skipped": _skipped(normal),
        "engines": jsonable(tables),
        "gold": None if gold_tables is None else {"engines": jsonable(gold_tables)},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run, name in ((normal, "results.jsonl"), (gold, "results-gold.jsonl")):
        if run is not None:
            lines = _result_lines(run.runs, sorted(run.runs, key=key), ids)
            (out_dir / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(meta, normal, gold, tables, gold_tables, ids, scrub) + "\n", encoding="utf-8")

