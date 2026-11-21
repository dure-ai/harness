"""The `harness` command line: scenario check and new, selfcheck, run, review, traces and generate."""

import json
import os
import secrets
import tempfile
from collections import Counter
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from rich.console import Console

from harness.config import EvalConfig, config_hash, load_config
from harness.contract import BaseEngine, EngineInfo, EngineUnavailable
from harness.engines import BASELINES, OSS_ENGINES, make_engine
from harness.metrics import compute_metrics, flip_rate
from harness.replay import DEFAULT_BLIND_KEY, ScenarioRun, blind_key, error_kind_of, run_scenario
from harness.report import RunMeta, harness_version, jsonable, machine_info, write_report
from harness.scenario.load import (
    ScenarioLoadError,
    ScenarioSet,
    approval_issue,
    load_scenario,
    load_set,
    read_scenario,
)
from harness.scenario.model import EXT_FAMILIES, Scenario
from harness.scenario.validate import Issue, validate_scenario
from harness.selection import EngineScore, rank
from harness.selfcheck import CheckResult, check_discrimination, run_selfcheck

if TYPE_CHECKING:
    from harness.bench.config import BenchConfig
    from harness.bench.dataset import Converted
    from harness.bench.run import BenchRun, DatasetFacts
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
bench_app = typer.Typer(help="External benchmarks converted into scenarios.", no_args_is_help=True)
app.add_typer(bench_app, name="bench")

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
    """Validate scenarios (drafts included) and check that each one discriminates between baselines.

    An approved file is also checked against the hash recorded at approval: edited since is an
    error (`approved_content_changed`); no usable hash is an error for an LLM-authored file
    (`llm_approval_unhashed`) and a warning (`approval_unverified`) for a human-authored one.
    """
    cfg = _config(config)
    failed = False
    for root in paths:
        if not root.exists():
            typer.echo(f"{root}: error load no such file or directory")
            failed = True
            continue
        for path in _yaml_files(root):
            try:
                raw, scenario = read_scenario(path)
            except ScenarioLoadError as e:
                typer.echo(f"{path}: error load {e.message}")
                failed = True
                continue
            if scenario.review.status == "rejected":
                typer.echo(f"{path}: rejected (review status; checked anyway)")
            approval = approval_issue(raw, scenario)
            issues = [approval] if approval is not None else []
            issues += validate_scenario(scenario)
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


# What `--sealed` says about a load error: one fixed sentence per error code, never the path,
# a scenario id or the parser's text.
_SEALED_LOAD_ERRORS = {
    "load": "a scenario file failed to load",
    "approved_content_changed": "an approved scenario file was edited after its approval",
    "approved_hash_missing": "an approved scenario file was approved by hand, without a recorded approval hash",
    "llm_approval_unhashed": "an approved LLM-authored scenario file has no usable approval hash",
}


def _load(
    scenarios: list[Path], config: Path, include_drafts: bool, sealed: bool
) -> tuple[EvalConfig, ScenarioSet]:
    for root in scenarios:
        if not root.exists():
            raise _fail(f"error: scenario path does not exist: {root}", 2)
    cfg = _config(config)
    try:
        loaded = load_set(scenarios, include_drafts)
    except ScenarioLoadError as e:
        if sealed:  # the path and the parser message can reveal scenario content
            what = _SEALED_LOAD_ERRORS.get(e.code, _SEALED_LOAD_ERRORS["load"])
            raise _fail(f"error {e.code}: {what} (details hidden by --sealed)", 1) from e
        raise _fail(f"{e.path}: error {e.code} {e.message}", 1) from e
    if sealed and loaded.warnings:  # the lines name files
        typer.echo(
            f"warning: {len(loaded.warnings)} scenario file warning(s), such as approvals that "
            "cannot be verified (details hidden by --sealed)",
            err=True,
        )
    elif loaded.warnings:
        for line in loaded.warnings:
            typer.echo(line, err=True)
    return cfg, loaded


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


# An engine that was unavailable (no answer, or retryable errors after retries) for this many
# scenarios in a row is tripped: the rest of that repeat is recorded as skipped, so a dead server
# cannot stall the run (each unavailable scenario costs the HTTP client its retries, up to
# minutes). Only `unavailable` runs count; a scenario that fails fast (the server answered, with
# an error) resets the count and the engine keeps being measured. The count restarts every
# repeat, and a tripped engine is tried again with the first scenario of each later repeat.
MAX_CONSECUTIVE_ENGINE_ERRORS = 3


UNAVAILABLE = "unavailable (no answer, or retryable errors after retries)"


def _scenarios(n: int) -> str:
    return f"{n} scenario" if n == 1 else f"{n} scenarios"


def _run_extra(
    name: str,
    repeats: int,
    scenarios: list[Scenario],
    cfg: EvalConfig,
    run_id: str,
    infos: dict[str, EngineInfo],
    key: bytes,
    *,
    sealed: bool,
) -> list[list[ScenarioRun]]:
    """One list of scenario runs per repeat; every scenario gets a fresh engine; `key` blinds namespaces.

    After `MAX_CONSECUTIVE_ENGINE_ERRORS` `unavailable` runs in a row within a repeat the engine
    is tripped: the rest of that repeat is kept as `skipped` runs saying why, and a warning goes
    to stderr. Runs that are `ok` or `error` reset the count. Each later repeat runs its first
    scenario for real; if the engine answers (`ok` or `error`) it resumes, otherwise the rest of
    that repeat is skipped again. With `sealed` the warnings show only the kind of the last
    failure (`ScenarioRun.error_kind`), never engine error text, which can quote an engine reply
    and through it scenario content.

    A failure to build the engine is a result like any other (`unavailable` for
    `EngineUnavailable`, else `error`) and never ends the run; `run` already built every
    candidate once before starting, so a configuration problem is caught there.
    """
    all_runs: list[list[ScenarioRun]] = []
    tripped = False
    for r in range(1, repeats + 1):
        runs: list[ScenarioRun] = []
        streak = 0
        skip_reason: str | None = None
        skip_kind: str | None = None
        for i, s in enumerate(scenarios):
            if skip_reason is not None:
                runs.append(
                    ScenarioRun(s.id, s.family, s.mode, error=skip_reason, status="skipped", error_kind=skip_kind)
                )
                continue
            try:
                engine = make_engine(name, s, cfg)
                if name not in infos:  # safety net: `run` fills it in its pre-flight
                    infos[name] = _checked_info(engine)
            except Exception as e:
                run = ScenarioRun(
                    s.id,
                    s.family,
                    s.mode,
                    error=f"{type(e).__name__}: {e}",
                    error_kind=error_kind_of(e),
                    status="unavailable" if isinstance(e, EngineUnavailable) else "error",
                )
            else:
                run = run_scenario(engine, s, run_id=f"{run_id}-r{r}", cfg=cfg, blind_key=key)
            runs.append(run)
            streak = streak + 1 if run.status == "unavailable" else 0
            if tripped and i == 0:  # the check of a tripped engine
                if run.status == "unavailable":
                    last = f" (last: {run.error_kind})" if sealed else ""
                    typer.echo(f"warning: {name} still unavailable in repeat {r}{last}; skipping it", err=True)
                    skip_reason = f"skipped: {name} still unavailable in repeat {r}; last: {run.error}"
                    skip_kind = run.error_kind
                else:
                    tripped = False
                    typer.echo(f"warning: {name} answered again in repeat {r}; resuming", err=True)
            elif not tripped and streak >= MAX_CONSECUTIVE_ENGINE_ERRORS:
                tripped = True
                skip_reason = _skip_reason(name, streak, run.error)
                skip_kind = run.error_kind
                tail = (
                    "will check again next repeat"  # nothing left to skip in this repeat
                    if i == len(scenarios) - 1
                    else "skipping the rest of the repeat"
                )
                shown = f"{name} was {UNAVAILABLE} for {_scenarios(streak)} in a row"
                if sealed:
                    shown += f" (last: {run.error_kind}); {tail} (details hidden by --sealed)"
                elif i == len(scenarios) - 1:
                    shown += f"; last: {run.error}; {tail}"
                else:
                    shown = skip_reason
                typer.echo(f"warning: {shown}", err=True)
        all_runs.append(runs)
    return all_runs


def _preflight(
    names: list[str], scenario: Scenario, cfg: EvalConfig, infos: dict[str, EngineInfo], sealed: bool
) -> None:
    """Build each candidate once and record its `info()` in `infos`, before anything runs.

    The engines built here are discarded; every scenario still gets a fresh one.

    Raises:
        typer.Exit: With code 2 for the first engine that cannot be built or whose `info()` is
            unusable. With `sealed` the message has the kind of the failure, never its text.
    """
    for name in names:
        try:
            infos[name] = _checked_info(make_engine(name, scenario, cfg))
        except Exception as e:
            what = f"{error_kind_of(e)} (details hidden by --sealed)" if sealed else f"{type(e).__name__}: {e}"
            raise _fail(f"error: cannot build engine {name!r}: {what}", 2) from e


def _skip_reason(name: str, streak: int, last: str | None) -> str:
    return f"skipped: {name} was {UNAVAILABLE} for {_scenarios(streak)} in a row; last: {last}"


def _check_engine_specs(specs: list[str] | None) -> None:
    """Exit 2 on the first spec that is neither a baseline, an OSS engine nor 'package.module:Class'."""
    for name in specs or []:
        if name not in BASELINES and name not in OSS_ENGINES and ":" not in name:  # e.g. all_store_noacl
            raise _fail(
                f"error: unknown engine {name!r}; use {', '.join(map(repr, OSS_ENGINES))} or "
                f"'package.module:ClassName' (baselines {', '.join(BASELINES)} always run)",
                2,
            )


def _blind_seed(blind_seed: int | None) -> int:
    """The run's blind seed: the given one (with a warning when it is guessable), else a random one."""
    if blind_seed is None:
        return secrets.randbits(64)
    if blind_seed < 2**32:
        typer.echo(
            f"warning: --blind-seed {blind_seed} is small and guessable; the namespace key is "
            "derived from it, so use a high-entropy value (such as one recorded in engine_ids.json) "
            "unless you are only reproducing a run",
            err=True,
        )
    return blind_seed


def _dataset_types(types: list[str] | None) -> set[str] | None:
    """`--types` values, repeatable and comma separated, as a set; None when none is given."""
    return {t.strip() for arg in types or [] for t in arg.split(",") if t.strip()} or None


def _cannot_be_committed(path: Path) -> bool:
    """Whether `path` is git-ignored or outside every git work tree.

    Raises:
        typer.Exit: git is not installed, so this cannot be told (exit 2).
    """
    import subprocess

    path = path.resolve()
    anchor = next((p for p in (path, *path.parents) if p.is_dir()), Path.cwd())
    try:
        found = subprocess.run(["git", "-C", str(anchor), "check-ignore", "-q", str(path)], capture_output=True)
        if found.returncode in (0, 1):  # 0 ignored, 1 not ignored
            return found.returncode == 0
        inside = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
        )
    except FileNotFoundError as e:
        raise _fail("error: git is needed to check that --out cannot be committed, and it was not found", 2) from e
    return inside.returncode != 0 or inside.stdout.strip() != "true"


BenchConfigOpt = Annotated[
    Path, typer.Option("--config", help="Bench config YAML (default config/bench-v0.1.yaml).")
]
TypesOpt = Annotated[
    list[str] | None,
    typer.Option("--types", help="Only these dataset types (repeatable, or comma separated)."),
]


def _bench_config(config: Path) -> "BenchConfig":
    from harness.bench.config import load_bench_config

    try:
        return load_bench_config(config)
    except Exception as e:
        raise _fail(f"error: cannot load config {config}: {e}", 2) from e


@app.command()
def run(
    scenarios: ScenariosOpt,
    engine: Annotated[
        list[str] | None,
        typer.Option(
            "--engine",
            help="Extra engine: 'hindsight', 'mem0' or 'pkg.module:Class' (repeatable). Baselines always run; other bare names are rejected.",
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
    _check_engine_specs(engine)
    cfg, loaded = _load(scenarios, config, include_drafts, sealed)
    scenario_list = loaded.scenarios
    external = sum(s.family in EXT_FAMILIES for s in scenario_list)
    if external:  # the pre-registered verdict is not defined on external benchmark scenarios
        raise _fail(
            f"error: {external} scenario(s) of an external benchmark family ({', '.join(EXT_FAMILIES)}); "
            "harness run judges only the main families, so run them with harness bench run --scenarios",
            2,
        )
    candidates = list(dict.fromkeys(n for n in engine or [] if n not in BASELINES))
    infos: dict[str, EngineInfo] = {}
    if candidates and scenario_list:  # no scenarios: _selfcheck reports it
        _preflight(candidates, scenario_list[0], cfg, infos, sealed)
    seed = _blind_seed(blind_seed)
    key = blind_key(seed)  # one seed: candidate ids and namespaces, so engine_ids.json reproduces both
    checks, base_runs = _selfcheck(loaded, cfg, sealed, key)
    if not all(c.ok for c in checks):
        _echo_checks(checks, sealed)
        raise typer.Exit(1)

    started_at = datetime.now().astimezone()
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    by_id = {s.id: s for s in scenario_list}
    infos = {  # baselines first, as in the report; the candidates' infos come from the pre-flight
        **{name: make_engine(name, scenario_list[0], cfg).info() for name in BASELINES},
        **infos,
    }

    base_metrics = {name: compute_metrics(base_runs[name], by_id) for name in BASELINES}
    base = base_metrics["all_store"]
    scores = [
        EngineScore.from_repeats(name, [base_metrics[name]], base, cfg)
        for name in BASELINES
    ]
    report_runs = dict(base_runs)
    repeat_counts: dict[str, int] = dict.fromkeys(BASELINES, 1)

    candidate_scores: list[EngineScore] = []
    candidate_runs: dict[str, list[ScenarioRun]] = {}
    candidate_repeats: dict[str, list[list[ScenarioRun]]] = {}
    for name in candidates:
        repeats = repeat if repeat is not None else cfg.llm_repeats
        per_repeat = _run_extra(name, repeats, scenario_list, cfg, run_id, infos, key, sealed=sealed)
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
def _needs_gen() -> Generator[None]:
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
    timeout: Annotated[
        float,
        typer.Option(
            help="Seconds a server may stay silent before giving up (>0); applies to listing "
            "(whole call) and download (no answer or progress)."
        ),
    ] = 60.0,
) -> None:
    """Download one parquet shard of Open-SWE-Traces into the cache, after showing its size."""
    if not timeout > 0:
        raise _fail(f"error: --timeout must be greater than 0, got {timeout:g}", 2)
    with _needs_gen():
        from harness.generate import fetch
        from harness.generate.config import cache_root

        try:
            shards = fetch.list_shards(dataset_config, split, timeout=timeout)
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
            path = fetch.fetch_shard(info, dest, timeout=timeout)
        except ModuleNotFoundError:
            raise
        except Exception as e:
            raise _fail(f"error: download failed: {type(e).__name__}: {e}", 2) from e
        typer.echo(str(path))


@bench_app.command("list")
def bench_list() -> None:
    """Show the registered external datasets and whether they are in the cache."""
    from harness.bench.dataset import is_cached
    from harness.bench.datasets import DATASETS

    for name in sorted(DATASETS):
        ds = DATASETS[name]
        size = sum(f.size for f in ds.files())
        typer.echo(f"{name}  {ds.license}  {_mb(size)}  cached: {'yes' if is_cached(ds) else 'no'}")


@bench_app.command("fetch")
def bench_fetch(
    dataset: Annotated[str, typer.Option("--dataset", help="Dataset name; see harness bench list.")],
    yes: Annotated[bool, typer.Option("--yes", help="Download without asking.")] = False,
    timeout: Annotated[
        float, typer.Option(help="Seconds a server may stay silent before giving up (>0).")
    ] = 60.0,
) -> None:
    """Download a dataset's files into the cache, after showing their size."""
    from harness.bench.dataset import FetchError, bench_cache, fetch_file
    from harness.bench.datasets import DATASETS

    if not timeout > 0:
        raise _fail(f"error: --timeout must be greater than 0, got {timeout:g}", 2)
    ds = DATASETS.get(dataset)
    if ds is None:
        raise _fail(f"error: unknown dataset {dataset!r}; choose from: {', '.join(sorted(DATASETS))}", 2)
    files = ds.files()
    for f in files:
        typer.echo(f"{f.name}  {_mb(f.size)}")
    if not yes and not typer.confirm("받을까요?", default=False):
        raise _fail("취소했습니다. 아무것도 받지 않았습니다.", 1)
    dest = bench_cache(ds.name)
    try:
        for f in files:
            fetch_file(f, dest, timeout=timeout)
    except FetchError as e:
        raise _fail(f"error: {e}", 2) from e
    typer.echo(str(dest))


@bench_app.command("export")
def bench_export(
    dataset: Annotated[str, typer.Option("--dataset", help="Dataset name; see harness bench list.")],
    out: Annotated[Path, typer.Option("--out", help="Directory for the scenario files.")],
    types: TypesOpt = None,
    limit: Annotated[int | None, typer.Option(min=1, help="At most this many source items.")] = None,
    config: BenchConfigOpt = Path("config/bench-v0.1.yaml"),
) -> None:
    """Convert a cached dataset into scenario files, one per item that passes the same screening as bench run."""
    from harness.bench import check
    from harness.bench.convert import scenario_text
    from harness.bench.dataset import FetchError, bench_cache, is_cached, verify_cached
    from harness.bench.datasets import DATASETS

    ds = DATASETS.get(dataset)
    if ds is None:
        raise _fail(f"error: unknown dataset {dataset!r}; choose from: {', '.join(sorted(DATASETS))}", 2)
    if not ds.commit_ok and not _cannot_be_committed(out):
        raise _fail(f"error: {dataset} may not be committed; --out {out} must be git-ignored", 2)
    if not is_cached(ds):
        raise _fail(f"error: {dataset} is not in the cache; run harness bench fetch --dataset {dataset} first", 2)
    try:
        verify_cached(ds)
    except FetchError as e:
        raise _fail(f"error: {e}", 2) from e
    cfg = _bench_config(config)
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for c in check.screened(ds.convert(bench_cache(ds.name), types=_dataset_types(types), limit=limit), cfg.eval):
        if c.scenario is None:
            skipped += 1
            typer.echo(f"skipped {c.source_id}: {c.skipped}")
            continue
        (out / f"{c.scenario.id}.yaml").write_text(scenario_text(c.scenario), encoding="utf-8")
        written += 1
    typer.echo(f"wrote {written} scenarios to {out}, skipped {skipped}")


@bench_app.command("run")
def bench_run(
    dataset: Annotated[str | None, typer.Option("--dataset", help="Dataset name; see harness bench list.")] = None,
    scenarios_path: Annotated[
        Path | None,
        typer.Option("--scenarios", help="Scenario file or directory to run instead of a dataset (e.g. scenarios/dev)."),
    ] = None,
    engine: Annotated[
        list[str] | None,
        typer.Option(
            "--engine",
            help="Extra engine: 'hindsight', 'mem0' or 'pkg.module:Class' (repeatable). The baselines always run.",
        ),
    ] = None,
    types: TypesOpt = None,
    limit: Annotated[int | None, typer.Option(min=1, help="At most this many source items.")] = None,
    gold: Annotated[bool, typer.Option("--gold", help="Also run with only the events of facts a probe asks about.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Run without asking.")] = False,
    config: BenchConfigOpt = Path("config/bench-v0.1.yaml"),
    out: Annotated[Path, typer.Option("--out", help="Directory for bench reports.")] = Path("runs/bench"),
    blind_seed: Annotated[
        int | None,
        typer.Option(
            "--blind-seed",
            help="Reproduce a recorded run: the seed from its engine_ids.json. Random by default.",
        ),
    ] = None,
) -> None:
    """Run the baselines and extra engines on a converted external dataset and write its report."""
    import time

    from harness.bench.check import screened
    from harness.bench.config import bench_config_hash
    from harness.bench.report import write_bench_report
    from harness.bench.run import BenchMeta

    _check_engine_specs(engine)
    if (dataset is None) == (scenarios_path is None):
        raise _fail("error: give exactly one of --dataset and --scenarios", 2)
    if scenarios_path is not None and types:
        raise _fail("error: --types applies to --dataset only; it cannot be combined with --scenarios", 2)
    cfg = _bench_config(config)
    found, facts, folder = _bench_source(dataset, scenarios_path, types, limit)
    items = list(screened(found, cfg.eval))
    candidates = list(dict.fromkeys(n for n in engine or [] if n not in BASELINES))
    scenarios = _confirm_bench(items, candidates, gold=gold, yes=yes)

    infos: dict[str, EngineInfo] = {}
    _preflight(candidates, scenarios[0], cfg.eval, infos, False)
    seed = _blind_seed(blind_seed)
    started_at = datetime.now().astimezone()
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    t0 = time.monotonic()
    normal, gold_run = _bench_passes(
        items, [*BASELINES, *candidates], cfg, run_id=run_id, key=blind_key(seed), dataset=facts["dataset"], gold=gold
    )
    wall = time.monotonic() - t0
    for b in (normal, gold_run):
        if b is not None:
            b.infos.update(infos)  # the pre-flight checked these are writable
    meta = BenchMeta(
        **facts,
        run_id=run_id,
        started_at=started_at,
        harness_version=harness_version(),
        config_hash=bench_config_hash(cfg),
        machine=machine_info(),
        blind_seed=seed,
    )
    out_dir = out / folder / run_id
    write_bench_report(out_dir, meta, normal, gold_run)
    typer.echo(str(out_dir / "report.md"))
    typer.echo(f"wall time: {wall:.1f} s", err=True)


def _bench_source(
    dataset: str | None, scenarios_path: Path | None, types: list[str] | None, limit: int | None
) -> "tuple[Iterable[Converted], DatasetFacts, str]":
    """The items `bench run` converts, the facts its report records, and its report folder name.

    A dataset must be known and cached with the right sha256 (exit 2 otherwise); its items are
    converted lazily. Scenario files are loaded now, approved ones only.
    """
    from harness.bench.dataset import Converted, FetchError, bench_cache, is_cached, verify_cached
    from harness.bench.datasets import DATASETS
    from harness.bench.run import dataset_facts, scenario_facts

    if dataset is not None:
        ds = DATASETS.get(dataset)
        if ds is None:
            raise _fail(f"error: unknown dataset {dataset!r}; choose from: {', '.join(sorted(DATASETS))}", 2)
        if not is_cached(ds):
            raise _fail(f"error: run 'harness bench fetch --dataset {dataset}' first", 2)
        try:
            file_sha256 = verify_cached(ds)
        except FetchError as e:
            raise _fail(f"error: {e}", 2) from e
        found = ds.convert(bench_cache(ds.name), types=_dataset_types(types), limit=limit)
        return found, dataset_facts(ds, file_sha256), ds.name
    assert scenarios_path is not None
    try:
        loaded = load_set([scenarios_path], include_drafts=False).scenarios
    except Exception as e:
        raise _fail(f"error: cannot load scenarios {scenarios_path}: {e}", 2) from e
    found = [Converted(s, s.id, s.family, None) for s in loaded[:limit]]
    return found, scenario_facts(str(scenarios_path)), "scenarios"


def _confirm_bench(items: "list[Converted]", candidates: list[str], *, gold: bool, yes: bool) -> list[Scenario]:
    """Show what `bench run` will replay and its LLM-call estimate, ask unless `yes`, and return the scenarios.

    Exits 2 when no scenario survived conversion and screening, 1 when the user declines.
    """
    from harness.bench.gold import gold_copy

    scenarios = [c.scenario for c in items if c.scenario is not None]
    if not scenarios:
        raise _fail("error: no scenario was converted; check --types and --limit", 2)
    n_events = sum(len(s.events) for s in scenarios)
    n_probes = sum(len(s.probes) for s in scenarios)
    typer.echo(f"scenarios: {len(scenarios)} (skipped {len(items) - len(scenarios)}), events: {n_events}, probes: {n_probes}")
    n_calls = n_events + (sum(len(gold_copy(s).events) for s in scenarios) if gold else 0)
    per = "one per recorded event, both passes" if gold else "one per recorded event"
    for name in candidates:
        if name in OSS_ENGINES:
            typer.echo(f"{name}: about {n_calls} LLM calls ({per})")
    if not yes and not typer.confirm("실행할까요?", default=False):
        raise _fail("취소했습니다. 아무것도 실행하지 않았습니다.", 1)
    return scenarios


def _bench_passes(
    items: "list[Converted]", names: list[str], cfg: "BenchConfig", *, run_id: str, key: bytes, dataset: str, gold: bool
) -> "tuple[BenchRun, BenchRun | None]":
    """The normal pass and, with `gold`, the gold pass, warning about engines that were tripped."""
    from harness.bench.run import run_bench

    trip = MAX_CONSECUTIVE_ENGINE_ERRORS
    normal = run_bench(items, names, cfg, run_id=run_id, key=key, dataset=dataset, max_unavailable=trip)
    gold_run = None
    if gold:
        gold_run = run_bench(
            items, names, cfg, run_id=run_id + "-gold", key=key, gold=True, dataset=dataset,
            max_unavailable=trip, tripped=normal.tripped,
        )
    for b in (normal, gold_run):
        if b is not None:
            for name in sorted(b.tripped):
                typer.echo(f"warning: {name} stayed unavailable in the {b.mode} pass; its remaining scenarios were skipped", err=True)
    return normal, gold_run


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
    max_attempts: Annotated[
        int | None,
        typer.Option(min=1, help="Most body-writing attempts per family; default 3 x that family's target."),
    ] = None,
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
                    pipeline.generate(
                        families, n, seed, loaded, client, cfg, gen, out, Path(scratch), wanted, per_family, max_traces, max_attempts
                    )
                return
            client = make_client(gen, cache_root() / "llm")
            report = pipeline.generate(
                families, n, seed, loaded, client, cfg, gen, out, run_dir, wanted, per_family, max_traces, max_attempts
            )
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
        if report.first_llm_error is not None:
            typer.echo(f"  first LLM error: {report.first_llm_error}")
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
    from harness.generate.pipeline import mark_generated, save_draft

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
    # A leak skeleton's secret strings (a label) are filled in by hand, so no label fingerprint.
    mark_generated(s, fingerprint=not s.provenance.secret_events)
    save_draft(s, path)
    typer.echo(str(path))
    typer.echo("글을 채우려면: harness review edit " + s.id)
    if s.provenance.secret_events:  # `secret_markers` is not editable in `review edit`
        typer.echo("비밀 문자열(secret_markers)은 review edit로 고칠 수 없습니다. review edit 전에 파일을 직접 열어 비밀 기록의 secret_markers와 본문에 채우세요: " + str(path))


# ---- review -------------------------------------------------------------------------------------


def _review_root(root: Path) -> Path:
    if not root.exists():
        raise _fail(f"error: no such directory: {root}", 2)
    return root


def _draft_path(root: Path, scenario_id: str) -> Path:
    """The one file holding `scenario_id`; exit 1 when there is none or several."""
    from harness import review as review_mod

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
    from harness import review as review_mod

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
    from harness import review as review_mod

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
    from harness import review as review_mod

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
        if result.kept_copy is not None:
            typer.echo(f"your edit was kept at {result.kept_copy}")
        raise typer.Exit(1)
    else:
        typer.echo(f"saved {path} (provenance.reviewer_edited: true)")
        if result.reset_to_draft:
            typer.echo("review status reset to draft: re-approval is needed (harness review set)")
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
    from harness import review as review_mod

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
