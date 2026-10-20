"""The `harness` command line: scenario check, selfcheck and run."""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from harness.config import EvalConfig, config_hash, load_config
from harness.contract import EngineInfo
from harness.engines import BASELINES, make_engine
from harness.metrics import compute_metrics, flip_rate
from harness.replay import ScenarioRun, run_scenario
from harness.report import RunMeta, harness_version, machine_info, write_report
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

NO_SCENARIOS = "승인된 시나리오가 없습니다. 초안을 포함하려면 --include-drafts"

app = typer.Typer(help="Memory evaluation harness.", no_args_is_help=True)
scenario_app = typer.Typer(help="Scenario files.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")

ScenariosOpt = Annotated[
    list[Path],
    typer.Option("--scenarios", help="Scenario directory or file (repeatable)."),
]
ConfigOpt = Annotated[Path, typer.Option("--config", help="Evaluation config YAML.")]
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
    scenarios: ScenarioSet, cfg: EvalConfig, sealed: bool
) -> tuple[list[CheckResult], dict[str, list[ScenarioRun]]]:
    if not scenarios.scenarios:
        raise _fail(NO_SCENARIOS, 2)
    _validate_all(scenarios.scenarios, sealed)
    return run_selfcheck(scenarios.scenarios, cfg)


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


def _run_extra(
    name: str,
    repeats: int,
    scenarios: list[Scenario],
    cfg: EvalConfig,
    run_id: str,
    infos: dict[str, EngineInfo],
) -> list[list[ScenarioRun]]:
    """One list of scenario runs per repeat; every scenario gets a fresh engine."""
    all_runs: list[list[ScenarioRun]] = []
    for r in range(1, repeats + 1):
        runs = []
        for i, s in enumerate(scenarios):
            try:
                engine = make_engine(name, s, cfg)
                if name not in infos:
                    infos[name] = engine.info()
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
            runs.append(run_scenario(engine, s, run_id=f"{run_id}-r{r}", cfg=cfg))
        all_runs.append(runs)
    return all_runs


@app.command()
def run(
    scenarios: ScenariosOpt,
    engine: Annotated[
        list[str] | None,
        typer.Option(
            "--engine",
            help="Extra engine: 'pkg.module:Class' (repeatable). Baselines always run.",
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
) -> None:
    """Self-check the baselines, evaluate extra engines, judge, rank and write the report."""
    cfg, loaded = _load(scenarios, config, include_drafts, sealed)
    checks, base_runs = _selfcheck(loaded, cfg, sealed)
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
    for name in candidates:
        repeats = repeat if repeat is not None else cfg.llm_repeats
        per_repeat = _run_extra(name, repeats, scenario_list, cfg, run_id, infos)
        metrics = [compute_metrics(runs, by_id) for runs in per_repeat]
        flip = flip_rate(per_repeat) if repeats > 1 else None
        candidate_scores.append(
            EngineScore.from_repeats(name, metrics, base, cfg, flip)
        )
        candidate_runs[name] = per_repeat[0]
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
    )
    typer.echo(str(out_dir / "report.md"))
