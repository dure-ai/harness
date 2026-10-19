"""Run report: summary.json, results.jsonl, report.md and engine_ids.json, with blind engine ids."""

import dataclasses
import json
import platform
import random
import secrets
import statistics
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from harness.contract import EngineInfo
from harness.engines import BASELINES
from harness.metrics import Metrics
from harness.replay import ScenarioRun
from harness.selection import EngineScore, RankEntry, relative_metrics
from harness.selfcheck import CheckResult
from harness.stats import Delay, Rate

NA = "n/a"
UNRETRACTED = "미철회"
UNREFLECTED = "미반영"
DRAFTS_WARNING = "> ⚠️ 초안 포함 — 발표 수치로 쓰지 않음"


@dataclass
class RunMeta:
    run_id: str
    started_at: datetime
    harness_version: str
    config_hash: str
    scenario_set_hash: str
    n_scenarios: int
    machine: str
    engine_infos: dict[str, EngineInfo]
    sealed: bool
    include_drafts: bool
    scenario_roots: list[str]
    repeats: dict[str, int]  # engine name -> runs per scenario


def harness_version() -> str:
    """Short git commit of the harness checkout, or "unknown" if git cannot say."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return done.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def machine_info() -> str:
    return f"{platform.platform()} / Python {platform.python_version()}"


def blind_ids(names: list[str], seed: int) -> dict[str, str]:
    """Name -> id. Baselines keep their names; the others get E1, E2, ... in a seeded shuffle."""
    candidates = sorted({n for n in names if n not in BASELINES})
    random.Random(seed).shuffle(candidates)
    mapping = {n: n for n in names if n in BASELINES}
    mapping.update({name: f"E{i}" for i, name in enumerate(candidates, 1)})
    return mapping


# ---- JSON ------------------------------------------------------------------------------------


def _plain(obj: Any) -> Any:
    """Recursively turn dataclasses, pydantic models, datetimes and sets into JSON-able values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, BaseModel):
        return _plain(obj.model_dump())
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(_plain(v) for v in obj)
    if isinstance(obj, Mapping):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _metrics_json(m: Metrics, sealed: bool) -> dict[str, Any]:
    d = _plain(m)
    if sealed:  # no scenario ids or fact ids
        d.pop("errors")
        d["n_errors"] = len(m.errors)
        for key in ("retraction", "reflection"):
            d[key] = [{"seconds": x["seconds"]} for x in d[key]]
    return d


def _scrubber(ids: Mapping[str, str]) -> Callable[[str], str]:
    """Replace each candidate's full name and its class-name part (after ":") with its blind id."""
    pairs: dict[str, str] = {}
    classes: dict[str, list[str]] = {}
    for name, blind in ids.items():
        if blind == name:
            continue
        pairs[name] = blind
        _, sep, cls = name.partition(":")
        if sep and cls:
            classes.setdefault(cls, []).append(blind)
    for cls, blinds in classes.items():
        if len(blinds) == 1 and cls not in pairs:  # a class name shared by two candidates is ambiguous
            pairs[cls] = blinds[0]
    ordered = sorted(pairs, key=len, reverse=True)

    def scrub(text: str) -> str:
        for name in ordered:
            text = text.replace(name, pairs[name])
        return text

    return scrub


def _scrub_value(value: Any, scrub: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {(scrub(k) if isinstance(k, str) else k): _scrub_value(v, scrub) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v, scrub) for v in value]
    return value


def _info_json(
    name: str, info: EngineInfo, ids: Mapping[str, str], scrub: Callable[[str], str]
) -> dict[str, Any]:
    """EngineInfo as JSON with every string value scrubbed; `name` is the blind id."""
    return {**_scrub_value(_plain(info), scrub), "name": ids[name]}


def _order_key(ids: Mapping[str, str]) -> Callable[[str], tuple[int, int]]:
    def key(name: str) -> tuple[int, int]:
        if name in BASELINES:
            return 0, BASELINES.index(name)
        return 1, int(ids[name][1:])

    return key


def _relative(
    score: EngineScore, by_name: Mapping[str, EngineScore]
) -> dict[str, float | None] | None:
    if score.engine_id in BASELINES or not all(b in by_name for b in ("none", "all_store", "oracle")):
        return None
    return relative_metrics(
        score.metrics, by_name["none"].metrics, by_name["all_store"].metrics, by_name["oracle"].metrics
    )


def _ordered_repeats(meta: RunMeta, ids: Mapping[str, str]) -> list[tuple[str, int]]:
    return sorted(meta.repeats.items(), key=lambda kv: _order_key(ids)(kv[0]))


def _summary(
    meta: RunMeta,
    ordered: list[EngineScore],
    ranking: list[RankEntry],
    selfcheck: list[CheckResult],
    ids: Mapping[str, str],
    scrub: Callable[[str], str],
) -> dict[str, Any]:
    by_name = {s.engine_id: s for s in ordered}
    meta_json = _plain(meta)
    meta_json["engine_infos"] = {
        ids[name]: _info_json(name, info, ids, scrub)
        for name, info in sorted(meta.engine_infos.items(), key=lambda kv: _order_key(ids)(kv[0]))
    }
    meta_json["repeats"] = {ids[name]: n for name, n in _ordered_repeats(meta, ids)}
    if meta.sealed:  # paths can name the sealed set
        meta_json["n_scenario_roots"] = len(meta_json.pop("scenario_roots"))
    return {
        "meta": meta_json,
        "engines": {
            ids[s.engine_id]: {
                "metrics": _metrics_json(s.metrics, meta.sealed),
                "verdict": _plain(s.verdict),
                "repeats": [_metrics_json(r, meta.sealed) for r in s.repeats],
                "flip": _plain(s.flip),
                "relative": _relative(s, by_name),
            }
            for s in ordered
        },
        "ranking": [{**_plain(e), "engine_id": ids[e.engine_id]} for e in ranking],
        "selfcheck": [
            {"name": c.name, "ok": c.ok, **({} if meta.sealed else {"detail": scrub(c.detail)})}
            for c in selfcheck
        ],
    }


def _result_lines(
    runs: Mapping[str, list[ScenarioRun]], names: Iterable[str], ids: Mapping[str, str]
) -> list[str]:
    lines = []
    for name in names:
        for run in sorted(runs.get(name, []), key=lambda r: r.scenario_id):
            if run.error is not None:
                continue
            for rec in run.probes:
                line = {
                    "engine_id": ids[name],
                    "scenario_id": run.scenario_id,
                    "probe_id": rec.probe_id,
                    "kind": rec.kind,
                    "outcome": rec.outcome,
                    "choice": {"index": rec.choice.index, "basis": sorted(rec.choice.basis)},
                    "results": [{"text": r.text, "sources": list(r.sources)} for r in rec.results],
                    "leaks": [_plain(leak) for leak in rec.leaks],
                }
                lines.append(json.dumps(line, ensure_ascii=False, separators=(",", ":")))
    return lines


# ---- Markdown --------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return [*out, ""]


def _num(x: float | None, digits: int = 3) -> str:
    return NA if x is None else f"{x:.{digits}f}"


def _rate(r: Rate | None) -> str:
    if r is None or r.value is None:
        return NA
    ci = NA if r.ci is None else f"[{r.ci[0]:.3f}, {r.ci[1]:.3f}]"
    return f"{r.value:.3f} (n={r.n}) {ci}"


def _delay(delays: list[Delay], never: str) -> str:
    """Median delay and the share of facts for which it never happened (labelled `never`)."""
    if not delays:
        return NA
    seen = [d.seconds for d in delays if d.seconds is not None]
    median = f"{statistics.median(seen):.0f}s" if seen else "-"
    return f"중앙값 {median}, {never} {len(delays) - len(seen)}/{len(delays)}"


def _worse_than(m: Metrics, repeat_n: Metrics | None) -> str:
    if repeat_n is None:
        return NA
    worse = sorted(
        f
        for f, t in m.by_family.items()
        if t["m1"].value is not None
        and (rn := repeat_n.by_family.get(f)) is not None
        and rn["m1"].value is not None
        and t["m1"].value > rn["m1"].value
    )
    return ", ".join(worse) or "-"


def _family_rows(
    ordered: list[EngineScore], ids: Mapping[str, str], table: Callable[[Metrics], Mapping]
) -> list[list[Any]]:
    rows = []
    for s in ordered:
        for family, t in table(s.metrics).items():
            m3 = t.get("m3")
            rows.append([ids[s.engine_id], family, _rate(t["m1"]), _rate(t["m2"]), NA if m3 is None else m3])
    return rows


def _section_judgment(ordered: list[EngineScore], ids: Mapping[str, str]) -> list[str]:
    rows = [
        [
            ids[s.engine_id],
            _rate(s.metrics.m1),
            _rate(s.metrics.m2),
            s.metrics.m3,
            "통과" if s.verdict.passed else "탈락",
            "; ".join(s.verdict.reasons) or "-",
        ]
        for s in ordered
    ]
    out = ["## 판정", "", *_table(["엔진", "M1", "M2", "M3", "결과", "근거"], rows)]
    repeated = [s for s in ordered if len(s.repeats) > 1]
    if repeated:
        out += ["### 실행별 값", "", "판정의 M1·M2는 실행별 값의 평균이고, 신뢰구간은 첫 실행 기준이다.", ""]
        runs = [
            [ids[s.engine_id], i, _rate(r.m1), _rate(r.m2), r.m3]
            for s in repeated
            for i, r in enumerate(s.repeats, 1)
        ]
        out += _table(["엔진", "실행", "M1", "M2", "M3"], runs)
    return out


def _section_aux(
    ordered: list[EngineScore], ids: Mapping[str, str], by_name: Mapping[str, EngineScore]
) -> list[str]:
    repeat_n = by_name["repeat_n"].metrics if "repeat_n" in by_name else None
    rows = []
    for s in ordered:
        m, rel = s.metrics, _relative(s, by_name) or {}
        worst = NA if m.worst_family is None else f"{m.worst_family[0]} ({m.worst_family[1]:.3f})"
        rows.append(
            [
                ids[s.engine_id],
                _rate(m.shallow_fix),
                _delay(m.retraction, UNRETRACTED),
                _rate(m.reach),
                _delay(m.reflection, UNREFLECTED),
                _rate(m.contamination),
                _rate(m.sourceless),
                _rate(m.promote_fn),
                _num(rel.get("improvement_ratio")),
                _num(rel.get("repeat_mistake_reduction")),
                worst,
                "-" if s.engine_id == "repeat_n" else _worse_than(m, repeat_n),
                _rate(s.flip),
            ]
        )
    headers = ["엔진", "얕은 지식 고착률", "철회 지연", "유효 지식 도달률", "반영 지연", "오염 노출률",
               "출처 없는 결과", "promote FN", "개선 달성률", "반복 실수 감소율", "최악 계열",
               "repeat_n보다 나쁜 계열", "재실행 뒤집힘률"]
    return ["## 보조·관찰", "", *_table(headers, rows)]


def _section_ranking(ranking: list[RankEntry], ids: Mapping[str, str]) -> list[str]:
    out = ["## 순위", ""]
    if not ranking:
        return [*out, "통과한 엔진이 없다.", ""]
    rows = [
        [
            e.position,
            ids[e.engine_id],
            "동률" if e.tied_with_previous else "-",
            e.tiebreak or "-",
            NA if e.diff_ci is None else f"[{e.diff_ci[0]:.3f}, {e.diff_ci[1]:.3f}]",
        ]
        for e in ranking
    ]
    out.append("차이는 바로 앞 순위와의 promote TP 비율 차이(부트스트랩 95% 구간)다.")
    out.append("")
    return [*out, *_table(["순위", "엔진", "앞 순위와", "동률 판정 기준", "차이 95% 구간"], rows)]


def _section_performance(ordered: list[EngineScore], ids: Mapping[str, str]) -> list[str]:
    rows = [
        [
            ids[s.engine_id],
            _num(m.record_ms_p50, 2),
            _num(m.record_ms_p95, 2),
            _num(m.search_ms_p50, 2),
            _num(m.search_ms_p95, 2),
            _num(m.context_chars_mean, 1),
        ]
        for s in ordered
        for m in [s.metrics]
    ]
    headers = ["엔진", "record p50 (ms)", "record p95 (ms)", "search p50 (ms)", "search p95 (ms)",
               "질문당 컨텍스트 (문자)"]
    return ["## 성능", "", *_table(headers, rows)]


def _section_limits(meta: RunMeta, ids: Mapping[str, str], scrub: Callable[[str], str]) -> list[str]:
    rows = []
    for name, info in sorted(meta.engine_infos.items(), key=lambda kv: _order_key(ids)(kv[0])):
        notes = []
        if not info.virtual_clock:
            notes.append("가상 시계 미지원: 진실 변경·철회 지연 결과에 주석 필요")
        if not info.sources:
            notes.append("출처 미반환: 출처 관련 결과에 주석 필요")
        if not info.scopes:
            notes.append("범위 미지원")
        shown = _info_json(name, info, ids, scrub)
        config = json.dumps(shown["config"], ensure_ascii=False, sort_keys=True)
        rows.append([ids[name], shown["version"], info.virtual_clock, info.sources, info.scopes,
                     config, "; ".join(notes) or "-"])
    headers = ["엔진", "버전", "virtual_clock", "sources", "scopes", "설정", "비고"]
    return ["## 엔진 한계", "", *_table(headers, rows)]


def _section_errors(
    meta: RunMeta,
    runs: Mapping[str, list[ScenarioRun]],
    ids: Mapping[str, str],
    scrub: Callable[[str], str],
) -> list[str]:
    failed = [
        (name, run)
        for name in sorted(runs, key=_order_key(ids))
        for run in sorted(runs[name], key=lambda r: r.scenario_id)
        if run.error is not None
    ]
    out = ["## 오류 시나리오", ""]
    if meta.sealed:
        return [*out, f"오류가 난 시나리오 수: {len(failed)}", ""]
    if not failed:
        return [*out, "없음", ""]
    rows = [[ids[name], run.scenario_id, scrub(run.error or "")] for name, run in failed]
    return [*out, *_table(["엔진", "시나리오", "오류"], rows)]


def _markdown(
    meta: RunMeta,
    ordered: list[EngineScore],
    ranking: list[RankEntry],
    runs: Mapping[str, list[ScenarioRun]],
    selfcheck: list[CheckResult],
    ids: Mapping[str, str],
    scrub: Callable[[str], str],
) -> str:
    by_name = {s.engine_id: s for s in ordered}
    roots = (
        f"- 시나리오 경로 수: {len(meta.scenario_roots)}"
        if meta.sealed
        else f"- 시나리오 경로: {', '.join(meta.scenario_roots) or '-'}"
    )
    repeats = ", ".join(f"{ids[name]} {n}" for name, n in _ordered_repeats(meta, ids)) or "-"
    head = [
        "# 메모리 평가 리포트",
        "",
        *([DRAFTS_WARNING, ""] if meta.include_drafts else []),
        f"- 실행: {meta.run_id}",
        f"- 실행 일시: {meta.started_at.isoformat()}",
        f"- 하네스 버전: {meta.harness_version}",
        f"- 설정 해시: {meta.config_hash}",
        f"- 시나리오 세트 해시: {meta.scenario_set_hash}",
        f"- 시나리오 수: {meta.n_scenarios}",
        f"- 머신: {meta.machine}",
        f"- 봉인 모드: {'예' if meta.sealed else '아니오'}",
        f"- 초안 포함: {'예' if meta.include_drafts else '아니오'}",
        roots,
        f"- 반복 실행: {repeats}",
        "",
    ]
    check_rows = [
        [c.name, "통과" if c.ok else "실패", "-" if meta.sealed else scrub(c.detail)] for c in selfcheck
    ]
    lines = [
        *head,
        *_section_judgment(ordered, ids),
        *_section_aux(ordered, ids, by_name),
        "## 계열별",
        "",
        *_table(["엔진", "계열", "M1", "M2", "M3"], _family_rows(ordered, ids, lambda m: m.by_family)),
        "## 되먹임",
        "",
        *_table(
            ["엔진", "계열", "M1", "M2", "M3"],
            _family_rows(ordered, ids, lambda m: m.feedback_by_family),
        ),
        "## 출처별",
        "",
        *_table(
            ["엔진", "출처", "M1", "M2"],
            [
                [ids[s.engine_id], author, _rate(t["m1"]), _rate(t["m2"])]
                for s in ordered
                for author, t in s.metrics.by_author.items()
            ],
        ),
        *_section_ranking(ranking, ids),
        *_section_performance(ordered, ids),
        *_section_limits(meta, ids, scrub),
        *_section_errors(meta, runs, ids, scrub),
        "## 자가 검증",
        "",
        *_table(["검사", "결과", "상세"], check_rows),
    ]
    return "\n".join(lines)


# ---- entry point -----------------------------------------------------------------------------


def write_report(
    out_dir: Path,
    meta: RunMeta,
    scores: list[EngineScore],
    ranking: list[RankEntry],
    runs: Mapping[str, list[ScenarioRun]],
    selfcheck: list[CheckResult],
    blind_seed: int | None = None,
) -> None:
    """Write the four report files. Engine names other than the baselines become blind ids
    (seeded by `blind_seed`, default a fresh random seed) everywhere but engine_ids.json, which
    alone records the seed."""
    if blind_seed is None:
        blind_seed = secrets.randbits(64)
    names = {
        *(s.engine_id for s in scores),
        *(e.engine_id for e in ranking),
        *runs,
        *meta.engine_infos,
        *meta.repeats,
    }
    ids = blind_ids(sorted(names), blind_seed)
    scrub = _scrubber(ids)
    key = _order_key(ids)
    ordered = sorted(scores, key=lambda s: key(s.engine_id))

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = _summary(meta, ordered, ranking, selfcheck, ids, scrub)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    results = out_dir / "results.jsonl"
    if meta.sealed:
        results.unlink(missing_ok=True)
    else:
        lines = _result_lines(runs, sorted(runs, key=key), ids)
        results.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    (out_dir / "report.md").write_text(
        _markdown(meta, ordered, ranking, runs, selfcheck, ids, scrub) + "\n", encoding="utf-8"
    )
    blind = {v: k for k, v in sorted(ids.items(), key=lambda kv: key(kv[0])) if v != k}
    (out_dir / "engine_ids.json").write_text(
        json.dumps({"seed": blind_seed, "ids": blind}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
