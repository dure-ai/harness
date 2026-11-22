"""Seed spread: run every engine once per blind seed and report the mean and min-max of each metric.

An engine sees every event id as an opaque keyed id (`replay.engine_event_id`), and the key
comes from the run's blind seed. An engine that breaks score ties on ids (core's knowledge
search orders ties by id) therefore resolves the same tie differently under another seed, and a
probe that rests on such a tie can flip between outcomes. One seed's numbers are one draw of
that tie order; this report shows the draws side by side so that a comparison can tell a real
change from a different draw. It does not judge or rank: `harness run` does that for one seed.
"""

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from harness.engines import BASELINES
from harness.metrics import FamilyRow, Metrics
from harness.replay import ScenarioRun
from harness.report import DRAFTS_WARNING, NA, RunMeta, blind_ids, jsonable, scrubbed_infos
from harness.stats import Rate


def _span(values: Sequence[float | None]) -> dict[str, float | None]:
    """Mean, min and max of the values that exist (a rate with no sample is None)."""
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None}
    return {"mean": statistics.fmean(present), "min": min(present), "max": max(present)}


def _rate_spread(rates: Sequence[Rate | None]) -> dict[str, Any]:
    """Spread of one `Rate` over the seeds: mean/min/max of the value, and k and n per seed.

    A seed without the rate (None) keeps its place in the k and n lists as None.
    """
    return {
        **_span([r.value if r else None for r in rates]),
        "k": [r.k if r else None for r in rates],
        "n": [r.n if r else None for r in rates],
    }


def _family_spread(tables: Sequence[Mapping[str, FamilyRow]]) -> dict[str, dict[str, Any]]:
    """Per family, the spread of M1, M2 and M3, in seed order.

    A family missing in a seed (its scenarios errored) is None at that seed's place, so every
    list stays aligned with the seed list.
    """
    families = sorted({f for t in tables for f in t})
    spread: dict[str, dict[str, Any]] = {}
    for f in families:
        rows = [t.get(f) for t in tables]
        m3 = [row["m3"] if row else None for row in rows]
        spread[f] = {
            "m1": _rate_spread([row["m1"] if row else None for row in rows]),
            "m2": _rate_spread([row["m2"] if row else None for row in rows]),
            "m3": {**_span(m3), "values": m3},
        }
    return spread


def metric_spread(per_seed: Sequence[Metrics]) -> dict[str, Any]:
    """Summarize one engine's metrics over the seeds, in seed order.

    Args:
        per_seed: The engine's metrics, one per seed.

    Returns:
        "m1"/"m2": mean, min, max of the rate and its k and n per seed; "m3": mean, min, max and
        the value per seed; "by_family"/"feedback_by_family": the same per family, with None
        for a seed where the family is missing; and "n_error_runs" per seed.
    """
    return {
        "m1": _rate_spread([m.m1 for m in per_seed]),
        "m2": _rate_spread([m.m2 for m in per_seed]),
        "m3": {**_span([m.m3 for m in per_seed]), "values": [m.m3 for m in per_seed]},
        "by_family": _family_spread([m.by_family for m in per_seed]),
        "feedback_by_family": _family_spread([m.feedback_by_family for m in per_seed]),
        "n_error_runs": [m.n_error_runs for m in per_seed],
    }


def unstable_probes(per_seed: Sequence[Sequence[ScenarioRun]]) -> list[dict[str, Any]]:
    """Probes whose outcome is not the same under every seed.

    A probe missing under some seed (its scenario errored first) is compared on the seeds it ran.

    Args:
        per_seed: One engine's scenario runs, one list per seed.

    Returns:
        Sorted by scenario and probe id: {"scenario", "probe", "kind", "outcomes"}, where
        "outcomes" counts the seeds per outcome.
    """
    outcomes: dict[tuple[str, str], list[str]] = {}
    kinds: dict[tuple[str, str], str] = {}
    for runs in per_seed:
        for run in runs:
            for rec in run.probes:
                outcomes.setdefault((run.scenario_id, rec.probe_id), []).append(rec.outcome)
                kinds[(run.scenario_id, rec.probe_id)] = rec.kind
    return [
        {"scenario": sid, "probe": pid, "kind": kinds[(sid, pid)], "outcomes": dict(sorted(Counter(seen).items()))}
        for (sid, pid), seen in sorted(outcomes.items())
        if len(set(seen)) > 1
    ]


def _cell(spread: Mapping[str, Any]) -> str:
    """`mean [min, max] (k/n)`; just `value (k/n)` when every seed agrees."""
    if spread["mean"] is None:
        return NA
    ks = [k for k in spread["k"] if k is not None]
    ns = [n for n in spread["n"] if n is not None]
    k = f"{min(ks)}" if min(ks) == max(ks) else f"{min(ks)}–{max(ks)}"
    n = f"{min(ns)}" if min(ns) == max(ns) else f"{min(ns)}–{max(ns)}"
    if spread["min"] == spread["max"]:
        return f"{spread['mean']:.3f} ({k}/{n})"
    return f"{spread['mean']:.3f} [{spread['min']:.3f}, {spread['max']:.3f}] ({k}/{n})"


def _count_cell(spread: Mapping[str, Any]) -> str:
    if spread["mean"] is None:
        return NA
    if spread["min"] == spread["max"]:
        return f"{spread['min']:g}"
    return f"{spread['mean']:.2f} [{spread['min']:g}, {spread['max']:g}]"


def _family_section(
    title: str, key: str, names: list[str], spreads: Mapping[str, Mapping[str, Any]], ids: Mapping[str, str]
) -> list[str]:
    rows = [
        f"| {ids[name]} | {family} | {_cell(row['m1'])} | {_cell(row['m2'])} | {_count_cell(row['m3'])} |"
        for name in names
        for family, row in spreads[name][key].items()
    ]
    if not rows:
        return []
    return ["", f"## {title}", "", "| 엔진 | 계열 | M1 | M2 | M3 |", "|---|---|---|---|---|", *rows]


def _markdown(
    meta: RunMeta,
    seeds: Sequence[int],
    names: list[str],
    spreads: Mapping[str, Mapping[str, Any]],
    unstable: Mapping[str, list[dict[str, Any]]],
    ids: Mapping[str, str],
    infos: Mapping[str, Mapping[str, Any]],
) -> str:
    lines = [f"# 시드 분산 리포트 {meta.run_id}", ""]
    if meta.include_drafts:
        lines += [DRAFTS_WARNING, ""]
    lines += [
        f"- 하네스 {meta.harness_version} · 설정 해시 `{meta.config_hash}` · "
        f"시나리오 세트 해시 `{meta.scenario_set_hash}` · 시나리오 {meta.n_scenarios}개",
        f"- 블라인드 시드 {len(seeds)}개(engine_ids.json), 엔진마다 시드당 한 번 실행",
        "",
        "값은 `평균 [최소, 최대] (k/n)`이고, 모든 시드에서 같으면 `값 (k/n)`만 적는다. "
        "시드는 엔진이 보는 이벤트 id를 바꾸므로, 점수가 같은 결과를 id 순으로 고르는 엔진은 "
        "시드마다 다른 결과를 고를 수 있다. 한 시드의 수치는 이 분포에서 뽑은 하나이고, "
        "두 실행을 비교할 때 차이가 [최소, 최대] 안이면 동점 순서의 차이일 수 있다. "
        "판정·순위는 `harness run`이 한 시드로 낸다.",
        "",
        "## 지표",
        "",
        "| 엔진 | M1 | M2 | M3 | 시드마다 바뀐 문항 | 오류 실행 |",
        "|---|---|---|---|---|---|",
    ]
    for name in names:
        s = spreads[name]
        lines.append(
            f"| {ids[name]} | {_cell(s['m1'])} | {_cell(s['m2'])} | {_count_cell(s['m3'])} | "
            f"{len(unstable[name])} | {max(s['n_error_runs'])} |"
        )
    if infos:
        lines += ["", "## 엔진", "", "| 엔진 | 버전 | 설정 |", "|---|---|---|"]
        for name in names:
            if name in infos:
                info = infos[name]
                config = json.dumps(info["config"], ensure_ascii=False, sort_keys=True)
                lines.append(f"| {ids[name]} | {info['version']} | {config} |")
    lines += _family_section("계열별", "by_family", names, spreads, ids)
    lines += _family_section("되먹임 계열별", "feedback_by_family", names, spreads, ids)
    if not meta.sealed and any(unstable.values()):
        lines += ["", "## 시드마다 결과가 바뀐 문항", "", "| 엔진 | 문항 | 종류 | 결과(시드 수) |", "|---|---|---|---|"]
        for name in names:
            for u in unstable[name]:
                counts = ", ".join(f"{o} {n}" for o, n in u["outcomes"].items())
                lines.append(f"| {ids[name]} | {u['scenario']}/{u['probe']} | {u['kind']} | {counts} |")
    return "\n".join(lines)


def write_seed_report(
    out_dir: Path,
    meta: RunMeta,
    seeds: Sequence[int],
    metrics: Mapping[str, Sequence[Metrics]],
    runs: Mapping[str, Sequence[Sequence[ScenarioRun]]],
) -> None:
    """Write report.md, summary.json and engine_ids.json for a seed-spread run.

    Candidates get blind ids from the first seed, as `harness run` would give them for it, so
    reordering the seeds or changing the candidates can relabel E1, E2, ... . Engine infos are
    written scrubbed the way `harness run` writes them (`report.scrubbed_infos`), so a before/after pair can show which
    version produced each; error texts are not written. With `meta.sealed` the report also
    leaves out scenario and probe ids.

    Args:
        out_dir: Directory to create and write into.
        meta: Run-level facts; its `repeats` is not written (always 1 per seed).
        seeds: The blind seeds, in run order.
        metrics: Engine name to its metrics, one per seed.
        runs: Engine name to its scenario runs, one list per seed.
    """
    names_unsorted = list(metrics)
    ids = blind_ids(names_unsorted, seeds[0])
    # Baselines first in their usual order, then E1, E2, ... as in `harness run`'s report.
    names = sorted(
        names_unsorted,
        key=lambda n: (0, BASELINES.index(n)) if n in BASELINES else (1, int(ids[n][1:])),
    )
    infos = scrubbed_infos(ids, meta.engine_infos)
    spreads = {name: metric_spread(metrics[name]) for name in names}
    unstable = {name: unstable_probes(runs[name]) for name in names}

    out_dir.mkdir(parents=True, exist_ok=True)
    blind = {ids[n]: n for n in names if ids[n] != n}
    (out_dir / "engine_ids.json").write_text(
        json.dumps({"seeds": list(seeds), "ids": blind}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    meta_json = {k: v for k, v in jsonable(meta).items() if k not in ("engine_infos", "repeats")}
    meta_json["engine_infos"] = {ids[name]: info for name, info in infos.items()}
    meta_json["n_seeds"] = len(seeds)
    if meta.sealed:
        meta_json["n_scenario_roots"] = len(meta_json.pop("scenario_roots"))
    summary = {
        "meta": meta_json,
        "engines": {
            ids[name]: {
                **spreads[name],
                "unstable": len(unstable[name]) if meta.sealed else unstable[name],
            }
            for name in names
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(meta, seeds, names, spreads, unstable, ids, infos) + "\n", encoding="utf-8")
