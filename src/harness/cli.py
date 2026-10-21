"""The `harness` command line: scenario check and new, selfcheck, run, review, traces and generate."""

import json
import secrets
import os
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from rich.console import Console

from harness import review as review_mod
from harness.config import EvalConfig, config_hash, load_config
from harness.contract import BaseEngine, EngineInfo
from harness.engines import BASELINES, make_engine
from harness.metrics import compute_metrics, flip_rate
from harness.replay import DEFAULT_BLIND_KEY, ScenarioRun, blind_key, run_scenario
from harness.report import RunMeta, harness_version, jsonable, machine_info, write_report
from harness.scenario.load import (
    ScenarioLoadError,
    ScenarioSet,
    load_scenario,
    load_set,
)
from harness.scenario.model import Scenario
from harness.scenario.validate import Issue, validate_scenario
from harness.selection import EngineScore, rank
from harness.selfcheck import CheckResult, check_discrimination, run_selfcheck

if TYPE_CHECKING:
    from harness.generate.config import GenConfig

NO_SCENARIOS = "승인된 시나리오가 없습니다. 초안을 포함하려면 --include-drafts"
GEN_MISSING = "uv sync --extra gen 이 필요합니다"
_GEN_DEPS = frozenset({"anthropic", "openai", "pyarrow", "huggingface_hub"})

app = typer.Typer(help="Memory evaluation harness.", no_args_is_help=True)
scenario_app = typer.Typer(help="Scenario files.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")
review_app = typer.Typer(help="Review drafts: list, show, edit, approve or reject.", no_args_is_help=True)
app.add_typer(review_app, name="review")
traces_app = typer.Typer(help="Source trajectories for scenario generation.", no_args_is_help=True)
app.add_typer(traces_app, name="traces")

ScenariosOpt = Annotated[
    list[Path],
    typer.Option("--scenarios", help="Scenario directory or file (repeatable)."),
]
ConfigOpt = Annotated[Path, typer.Option("--config", help="Evaluation config YAML.")]
GenConfigOpt = Annotated[Path, typer.Option("--gen-config", help="Generation config YAML.")]
RootOpt = Annotated[Path, typer.Option("--root", help="Directory (or file) with the drafts.")]
DraftsOpt = Annotated[
    bool, typer.Option("--include-drafts", help="Also evaluate draft scenarios.")
]
SealedOpt = Annotated[
    bool,
    typer.Option(
        "--sealed", help="Keep scenario content out of the terminal and the report."
    ),
]


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message)
    return typer.Exit(code)


def _echo_issue(where: str, issue: Issue) -> None:
    typer.echo(f"{where}: {issue.level} {issue.code} {issue.message}")


# ---- scenario check ---------------------------------------------------------------------------


def _yaml_files(path: Path) -> list[Path]:
    return sorted(path.rglob("*.yaml")) if path.is_dir() else [path]


@scenario_app.command("check")
def scenario_check(
    paths: Annotated[list[Path], typer.Argument(help="Scenario files or directories.")],
    include_drafts: Annotated[
        bool, typer.Option("--include-drafts", help="No-op: drafts are always checked.")
    ] = False,
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
) -> None:
    """Validate scenarios (drafts included) and check that each one discriminates between baselines."""
    cfg = _config(config)
    failed = False
    for root in paths:
        if not root.exists():
            typer.echo(f"{root}: error load no such file or directory")
            failed = True
            continue
        for path in _yaml_files(root):
            try:
                scenario = load_scenario(path)
            except ScenarioLoadError as e:
                typer.echo(f"{path}: error load {e.message}")
                failed = True
                continue
            if scenario.review.status == "rejected":
                typer.echo(f"{path}: rejected (review status; checked anyway)")
            issues = validate_scenario(scenario)
            if not any(
                i.level == "error" for i in issues
            ):  # engines may not cope with a broken scenario
                issues += check_discrimination(scenario, cfg)
            for issue in issues:
                _echo_issue(str(path), issue)
            failed = failed or any(i.level == "error" for i in issues)
    if failed:
        raise typer.Exit(1)


# ---- shared steps for selfcheck and run --------------------------------------------------------


def _config(config: Path) -> EvalConfig:
    try:
        return load_config(config)
    except Exception as e:
        raise _fail(f"error: cannot load config {config}: {e}", 2) from e


def _load(
    scenarios: list[Path], config: Path, include_drafts: bool, sealed: bool
) -> tuple[EvalConfig, ScenarioSet]:
    for root in scenarios:
        if not root.exists():
            raise _fail(f"error: scenario path does not exist: {root}", 2)
    cfg = _config(config)
    try:
        return cfg, load_set(scenarios, include_drafts)
    except ScenarioLoadError as e:
        if sealed:  # the path and the parser message can reveal scenario content
            raise _fail("error load: a scenario file failed to load (details hidden by --sealed)", 1) from e
        raise _fail(f"{e.path}: error load {e.message}", 1) from e


def _validate_all(scenarios: list[Scenario], sealed: bool) -> None:
    """Print every issue (sealed: only a count per issue code); exit 1 if any is an error."""
    found: list[tuple[str, Issue]] = []
    for dup, n in Counter(s.id for s in scenarios).items():
        if n > 1:
            found.append(
                (
                    dup,
                    Issue(
                        "error",
                        "duplicate_scenario_id",
                        f"scenario id appears {n} times",
                    ),
                )
            )
    for s in scenarios:
        found += [(s.id, issue) for issue in validate_scenario(s)]
    if sealed:
        for (level, code), n in Counter((i.level, i.code) for _, i in found).items():
            typer.echo(f"{level} {code}: {n}")
    else:
        for where, issue in found:
            _echo_issue(where, issue)
    if any(issue.level == "error" for _, issue in found):
        raise typer.Exit(1)


def _echo_checks(checks: list[CheckResult], sealed: bool) -> None:
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        typer.echo(
            f"{status}  {c.name}" if sealed else f"{status}  {c.name}  {c.detail}"
        )


def _selfcheck(
    scenarios: ScenarioSet, cfg: EvalConfig, sealed: bool, key: bytes = DEFAULT_BLIND_KEY
) -> tuple[list[CheckResult], dict[str, list[ScenarioRun]]]:
    if not scenarios.scenarios:
        raise _fail(NO_SCENARIOS, 2)
    _validate_all(scenarios.scenarios, sealed)
    return run_selfcheck(scenarios.scenarios, cfg, key)


@app.command()
def selfcheck(
    scenarios: ScenariosOpt,
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    include_drafts: DraftsOpt = False,
    sealed: SealedOpt = False,
) -> None:
    """Check that the four baselines order as designed on these scenarios."""
    cfg, loaded = _load(scenarios, config, include_drafts, sealed)
    checks, _ = _selfcheck(loaded, cfg, sealed)
    _echo_checks(checks, sealed)
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


# ---- run --------------------------------------------------------------------------------------


def _checked_info(engine: BaseEngine) -> EngineInfo:
    """Return `engine.info()` after checking that it is an `EngineInfo` the report can write.

    Raises:
        TypeError: If `info()` returns something else, or a config the report cannot write as
            JSON (checked with the report's own conversion, `report.jsonable`).
    """
    info = engine.info()
    if not isinstance(info, EngineInfo):
        raise TypeError(f"info() returned {type(info).__name__}, expected EngineInfo")
    try:
        json.dumps(jsonable(info))  # the same conversion the report uses
    except (TypeError, ValueError) as e:
        raise TypeError(f"info() is not JSON-serializable: {e}") from e
    return info


def _run_extra(
    name: str,
    repeats: int,
    scenarios: list[Scenario],
    cfg: EvalConfig,
    run_id: str,
    infos: dict[str, EngineInfo],
    key: bytes,
) -> list[list[ScenarioRun]]:
    """One list of scenario runs per repeat; every scenario gets a fresh engine; `key` blinds namespaces."""
    all_runs: list[list[ScenarioRun]] = []
    for r in range(1, repeats + 1):
        runs = []
        for i, s in enumerate(scenarios):
            try:
                engine = make_engine(name, s, cfg)
                if name not in infos:
                    infos[name] = _checked_info(engine)
            except Exception as e:
                if (
                    r == 1 and i == 0
                ):  # cannot even build the engine: a usage problem, not a result
                    raise _fail(
                        f"error: cannot build engine {name!r}: {type(e).__name__}: {e}",
                        2,
                    ) from e
                runs.append(
                    ScenarioRun(
                        s.id, s.family, s.mode, error=f"{type(e).__name__}: {e}"
                    )
                )
                continue
            runs.append(run_scenario(engine, s, run_id=f"{run_id}-r{r}", cfg=cfg, blind_key=key))
        all_runs.append(runs)
    return all_runs


@app.command()
def run(
    scenarios: ScenariosOpt,
    engine: Annotated[
        list[str] | None,
        typer.Option(
            "--engine",
            help="Extra engine: 'pkg.module:Class' (repeatable). Baselines always run; other bare names are rejected.",
        ),
    ] = None,
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    out: Annotated[
        Path, typer.Option("--out", help="Directory for run reports.")
    ] = Path("runs"),
    include_drafts: DraftsOpt = False,
    sealed: SealedOpt = False,
    repeat: Annotated[
        int | None, typer.Option("--repeat", min=1, help="Repeats per extra engine.")
    ] = None,
    blind_seed: Annotated[
        int | None,
        typer.Option(
            "--blind-seed",
            help="Reproduce a recorded run: the seed from its engine_ids.json. Sets the candidate ids (E1, E2, ...) and the namespace key; random by default, so use a high-entropy secret, not a small number.",
        ),
    ] = None,
) -> None:
    """Self-check the baselines, evaluate extra engines, judge, rank and write the report."""
    # First, so a typo does not pay for loading the scenarios and the baseline self-check.
    for name in engine or []:
        if name not in BASELINES and ":" not in name:  # internal names such as all_store_noacl
            raise _fail(
                f"error: unknown engine {name!r}; use 'package.module:ClassName' "
                f"(baselines {', '.join(BASELINES)} always run)",
                2,
            )
    cfg, loaded = _load(scenarios, config, include_drafts, sealed)
    seed = secrets.randbits(64) if blind_seed is None else blind_seed
    if blind_seed is not None and blind_seed < 2**32:
        typer.echo(
            f"warning: --blind-seed {blind_seed} is small and guessable; the namespace key is "
            "derived from it, so use a high-entropy value (such as one recorded in engine_ids.json) "
            "unless you are only reproducing a run",
            err=True,
        )
    key = blind_key(seed)  # one seed: candidate ids and namespaces, so engine_ids.json reproduces both
    checks, base_runs = _selfcheck(loaded, cfg, sealed, key)
    if not all(c.ok for c in checks):
        _echo_checks(checks, sealed)
        raise typer.Exit(1)

    started_at = datetime.now().astimezone()
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    scenario_list = loaded.scenarios
    by_id = {s.id: s for s in scenario_list}
    infos = {
        name: make_engine(name, scenario_list[0], cfg).info() for name in BASELINES
    }

    base_metrics = {name: compute_metrics(base_runs[name], by_id) for name in BASELINES}
    base = base_metrics["all_store"]
    scores = [
        EngineScore.from_repeats(name, [base_metrics[name]], base, cfg)
        for name in BASELINES
    ]
    report_runs = dict(base_runs)
    repeat_counts: dict[str, int] = dict.fromkeys(BASELINES, 1)

    candidates = list(dict.fromkeys(n for n in engine or [] if n not in BASELINES))
    candidate_scores: list[EngineScore] = []
    candidate_runs: dict[str, list[ScenarioRun]] = {}
    candidate_repeats: dict[str, list[list[ScenarioRun]]] = {}
    for name in candidates:
        repeats = repeat if repeat is not None else cfg.llm_repeats
        per_repeat = _run_extra(name, repeats, scenario_list, cfg, run_id, infos, key)
        metrics = [compute_metrics(runs, by_id) for runs in per_repeat]
        flip = flip_rate(per_repeat) if repeats > 1 else None
        candidate_scores.append(
            EngineScore.from_repeats(name, metrics, base, cfg, flip)
        )
        candidate_runs[name] = per_repeat[0]
        candidate_repeats[name] = per_repeat
        repeat_counts[name] = repeats

    ranking = rank(candidate_scores, candidate_runs, cfg)
    meta = RunMeta(
        run_id=run_id,
        started_at=started_at,
        harness_version=harness_version(),
        config_hash=config_hash(cfg),
        scenario_set_hash=loaded.hash,
        n_scenarios=len(scenario_list),
        machine=machine_info(),
        engine_infos=infos,
        sealed=sealed,
        include_drafts=include_drafts,
        scenario_roots=[str(root) for root in scenarios],
        repeats=repeat_counts,
    )
    out_dir = out / run_id
    write_report(
        out_dir,
        meta,
        [*scores, *candidate_scores],
        ranking,
        {**report_runs, **candidate_runs},
        checks,
        blind_seed=seed,
        repeat_runs={
            **{name: [r] for name, r in report_runs.items()},
            **candidate_repeats,
        },
    )
    typer.echo(str(out_dir / "report.md"))


# ---- traces and generate (need the `gen` extra; harness.generate is imported lazily) ------------


@contextmanager
def _needs_gen() -> Iterator[None]:
    """Turn a missing `gen` dependency into the install hint and exit code 2."""
    try:
        yield
    except ModuleNotFoundError as e:
        if (e.name or "").split(".")[0] not in _GEN_DEPS:
            raise
        raise _fail(GEN_MISSING, 2) from e


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


@traces_app.command("fetch")
def traces_fetch(
    dataset_config: Annotated[
        str, typer.Option("--config", help="Open-SWE-Traces version: v1.0, v1.1 or v1.2.")
    ],
    split: Annotated[str, typer.Option(help="Agent: openhands, sweagent or minisweagent.")],
    shard: Annotated[int, typer.Option(min=0, help="Index in the sorted list of files.")] = 0,
    yes: Annotated[bool, typer.Option("--yes", help="Download without asking.")] = False,
) -> None:
    """Download one parquet shard of Open-SWE-Traces into the cache, after showing its size."""
    with _needs_gen():
        from harness.generate import fetch
        from harness.generate.config import cache_root

        try:
            shards = fetch.list_shards(dataset_config, split)
        except ValueError as e:
            raise _fail(f"error: {e}", 2) from e
        except ModuleNotFoundError:
            raise  # a missing gen dependency: _needs_gen explains it
        except Exception as e:
            raise _fail(f"error: cannot list shards: {type(e).__name__}: {e}", 2) from e
        if shard >= len(shards):
            raise _fail(f"error: {dataset_config}/{split} has {len(shards)} shards; --shard {shard} does not exist", 2)
        info = shards[shard]
        typer.echo(f"{info.path}  {_mb(info.size)}")
        if not yes and not typer.confirm("받을까요?", default=False):
            raise _fail("취소했습니다. 아무것도 받지 않았습니다.", 1)
        dest = cache_root() / "traces" / dataset_config / split
        try:
            path = fetch.fetch_shard(info, dest)
        except ModuleNotFoundError:
            raise
        except Exception as e:
            raise _fail(f"error: download failed: {type(e).__name__}: {e}", 2) from e
        typer.echo(str(path))


@traces_app.command("list")
def traces_list() -> None:
    """Show the shards in the cache, trajectories per repository and how many tasks can be bundled."""
    with _needs_gen():
        from harness.generate import fetch
        from harness.generate.config import cache_root

        root = cache_root() / "traces"
        shards = fetch.local_shards(root)
        if not shards:
            typer.echo(f"받은 조각이 없습니다: {root} (harness traces fetch)")
            return
        stats = fetch.cache_stats(shards)
        typer.echo(f"조각 {len(shards)}개")
        for path in shards:
            typer.echo(f"  {path.relative_to(root)}  {_mb(path.stat().st_size)}")
        typer.echo("저장소별 궤적 수")
        for r in stats.repos:
            typer.echo(f"  {r.repo}  {r.license or 'unknown'}  {r.language or 'unknown'}  {r.trajectories}")
        typer.echo(f"궤적 {stats.trajectories}개, 작업 {stats.tasks}개, 묶음(모델 2개 이상이 푼 작업) {stats.bundles}개")


def _gen_config(path: Path) -> "GenConfig":
    from harness.generate.config import load_gen_config

    try:
        return load_gen_config(path)
    except Exception as e:
        raise _fail(f"error: cannot load gen config {path}: {e}", 2) from e


@app.command("generate")
def generate_cmd(
    family: Annotated[str, typer.Option(help="Family name, or 'all' for the ten families.")],
    n: Annotated[int, typer.Option(min=1, help="Drafts per family (normal_repeat: n // 2 with 'all').")] = 4,
    seed: Annotated[int, typer.Option(help="Seed of the trace order and the skeletons.")] = 20261020,
    repos: Annotated[str | None, typer.Option(help="Only these repositories, comma separated.")] = None,
    out: Annotated[Path, typer.Option(help="Directory for the drafts.")] = Path("scenarios/dev"),
    gen_config: Annotated[Path, typer.Option(help="Generation config YAML.")] = Path("config/gen-v0.1.yaml"),
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    traces: Annotated[Path | None, typer.Option(help="Trace directory; default: the cache.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the first LLM request and stop.")] = False,
    max_traces: Annotated[
        int, typer.Option(min=1, help="Most traces sent to candidate extraction in this run.")
    ] = 100,
) -> None:
    """Make draft scenarios from cached traces with an LLM; they are saved with status draft."""
    cfg = _config(config)
    gen = _gen_config(gen_config)
    with _needs_gen():
        from harness.generate import fetch, pipeline
        from harness.generate.config import cache_root
        from harness.generate.families import TEMPLATES
        from harness.generate.llm import LLMError, make_client
        from harness.generate.traces import usable

        if family != "all" and family not in TEMPLATES:
            raise _fail(f"error: unknown family {family!r}; choose all or one of {', '.join(TEMPLATES)}", 2)
        families = list(TEMPLATES) if family == "all" else [family]
        per_family = pipeline.all_family_counts(n) if family == "all" else None
        wanted = {r.strip() for r in repos.split(",") if r.strip()} if repos else None

        root = traces if traces is not None else cache_root() / "traces"
        shards = fetch.local_shards(root)
        if not shards:
            raise _fail(f"error: no trace files under {root}; run harness traces fetch first", 2)
        loaded = [
            t
            for t in pipeline.read_all(shards)
            if usable(t, gen) and (wanted is None or pipeline.matches_repos(t, wanted))
        ]
        if not loaded:
            raise _fail("error: no usable trajectory (python, allowed license, --repos) in the trace files", 2)

        run_dir = cache_root() / "runs" / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        try:
            if dry_run:
                with tempfile.TemporaryDirectory() as scratch:
                    client = pipeline.DryRunClient(gen.provider, gen.model)
                    pipeline.generate(families, n, seed, loaded, client, cfg, gen, out, Path(scratch), wanted, per_family, max_traces)
                return
            client = make_client(gen, cache_root() / "llm")
            report = pipeline.generate(families, n, seed, loaded, client, cfg, gen, out, run_dir, wanted, per_family, max_traces)
        except pipeline.DryRun as d:
            typer.echo(f"[{d.kind}] system:\n{d.system}\n\nuser:\n{d.user}")
            typer.echo("--dry-run: 모델을 부르지 않았고 아무것도 저장하지 않았습니다.")
            return
        except LLMError as e:
            raise _fail(f"error: {e}", 2) from e

        typer.echo(f"요청 {report.requested}개 중 {len(report.written)}개 생성")
        if report.skipped_existing:
            typer.echo(f"  이미 검토된 파일이라 건너뜀: {', '.join(report.skipped_existing)}")
        if report.rejected:
            typer.echo(f"  거절 {report.rejected}건: {run_dir / 'rejected.jsonl'}")
        for fam, reason in report.shortfall.items():
            typer.echo(f"  부족 {fam}: {reason}")
        if report.trace_cap_hit:
            typer.echo(f"  --max-traces {max_traces}에서 후보 뽑기를 멈췄습니다. 더 만들려면 값을 올리세요.")
        typer.echo(f"저장 위치: {out}")


# ---- scenario new -------------------------------------------------------------------------------


@scenario_app.command("new")
def scenario_new(
    family: Annotated[str, typer.Option(help="Family name.")],
    seed: Annotated[int, typer.Option(help="Seed of dates, users and the id.")],
    out: Annotated[Path, typer.Option(help="Directory for the skeleton.")] = Path("scenarios/dev"),
) -> None:
    """Write an empty skeleton of a family (labels set, all text empty) to fill in with `review edit`."""
    from harness.generate.candidates import FactCandidate
    from harness.generate.families import TEMPLATES
    from harness.generate.pipeline import save_draft

    if family not in TEMPLATES:
        raise _fail(f"error: unknown family {family!r}; choose one of {', '.join(TEMPLATES)}", 2)
    fix = "workaround" if family == "workaround_then_root" else None
    blank = FactCandidate(statement="", kind="other", quote="", message_index=0, fix=fix)
    skeleton = TEMPLATES[family](blank, [], seed)
    if skeleton is None:
        raise _fail(f"error: the {family} template cannot build a skeleton without a trace", 2)
    s = skeleton.scenario
    s.provenance.author = "human"
    path = out / family / f"{s.id}.yaml"
    if path.exists():
        raise _fail(f"error: {path} already exists; not overwriting it", 1)
    save_draft(s, path)
    typer.echo(str(path))
    typer.echo("글을 채우려면: harness review edit " + s.id)


# ---- review -------------------------------------------------------------------------------------


def _review_root(root: Path) -> Path:
    if not root.exists():
        raise _fail(f"error: no such directory: {root}", 2)
    return root


def _draft_path(root: Path, scenario_id: str) -> Path:
    """The one file holding `scenario_id`; exit 1 when there is none or several."""
    found = review_mod.find_draft(_review_root(root), scenario_id)
    if not found:
        raise _fail(f"error: no scenario with id {scenario_id!r} under {root}", 1)
    if len(found) > 1:
        raise _fail(f"error: id {scenario_id!r} is in several files: {', '.join(str(p) for p in found)}", 1)
    return found[0]


def _echo_findings(issues: list[Issue]) -> None:
    for issue in issues:
        typer.echo(f"  {issue.level} {issue.code} {issue.message}")


@review_app.command("list")
def review_list(
    root: Annotated[Path, typer.Argument(help="Directory (or file) with the drafts.")] = Path("scenarios/dev"),
) -> None:
    """List the scenario files: id, family, status, generation time, warnings and path."""
    rows = review_mod.list_drafts(_review_root(root))
    if not rows:
        typer.echo(f"no scenario files under {root}")
        return
    for r in rows:
        when = r.generated_at.astimezone().strftime("%Y-%m-%d %H:%M") if r.generated_at else "-"
        typer.echo(f"{r.id}  {r.family}  {r.status}  {when}  {','.join(r.warnings) or '-'}  {r.path}")


@review_app.command("show")
def review_show(
    scenario_id: Annotated[str, typer.Argument(metavar="ID", help="Scenario id.")],
    root: RootOpt = Path("scenarios/dev"),
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    gen_config: GenConfigOpt = Path("config/gen-v0.1.yaml"),
) -> None:
    """Show a draft: header, labels, timeline and the baselines' results."""
    path = _draft_path(root, scenario_id)
    cfg, gen = _config(config), _gen_config(gen_config)
    try:
        s = load_scenario(path)
    except ScenarioLoadError as e:
        raise _fail(f"{path}: error load {e.message}", 1) from e
    Console().print(review_mod.render_show(s, cfg, gen))


@review_app.command("edit")
def review_edit(
    scenario_id: Annotated[str, typer.Argument(metavar="ID", help="Scenario id.")],
    root: RootOpt = Path("scenarios/dev"),
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    gen_config: GenConfigOpt = Path("config/gen-v0.1.yaml"),
) -> None:
    """Edit a draft in $EDITOR (default vi). Text can change; labels cannot, and such edits are undone."""
    path = _draft_path(root, scenario_id)
    cfg, gen = _config(config), _gen_config(gen_config)
    try:
        result = review_mod.edit_draft(path, os.environ.get("EDITOR") or "vi", cfg, gen)
    except ScenarioLoadError as e:
        raise _fail(f"{path}: error load {e.message}", 1) from e
    if result.status == "unchanged":
        typer.echo("no changes")
    elif result.status == "reverted":
        typer.echo(f"reverted: {result.reason}")
        for name in result.changed_fields:
            typer.echo(f"  changed {name}")
        raise typer.Exit(1)
    else:
        typer.echo(f"saved {path} (provenance.reviewer_edited: true)")
        _echo_findings(result.issues)


@review_app.command("set")
def review_set(
    scenario_id: Annotated[str, typer.Argument(metavar="ID", help="Scenario id.")],
    status: Annotated[str, typer.Argument(help="approved or rejected.")],
    reviewer: Annotated[str, typer.Option(help="Your name, kept in review.reviewer.")],
    note: Annotated[str | None, typer.Option(help="Why; recommended for a rejection.")] = None,
    root: RootOpt = Path("scenarios/dev"),
    config: ConfigOpt = Path("config/eval-v0.1.yaml"),
    gen_config: GenConfigOpt = Path("config/gen-v0.1.yaml"),
) -> None:
    """Approve (only if every automatic check passes) or reject a draft."""
    if status not in ("approved", "rejected"):
        raise _fail(f"error: status must be approved or rejected, not {status!r}", 2)
    path = _draft_path(root, scenario_id)
    cfg, gen = _config(config), _gen_config(gen_config)
    try:
        issues = review_mod.set_status(path, status, reviewer, note, cfg, gen)
    except ScenarioLoadError as e:
        raise _fail(f"{path}: error load {e.message}", 1) from e
    if review_mod.blocking(issues):
        typer.echo(f"not approved: {path} is unchanged")
        _echo_findings(issues)
        raise typer.Exit(1)
    typer.echo(f"{status} {path}")
    _echo_findings(issues)
