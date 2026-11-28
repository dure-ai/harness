"""Run every engine over the dev set, LoCoMo and LongMemEval-S from one config (harness #69).

This only chains commands that already exist and reads what they write. The dev set goes through
`harness run`; LoCoMo and LongMemEval-S go through `tools/extbench`, where a model answers and a
model judges. Those are not the pre-registered measurement, so their outputs stay in their own
run directories and the summary names them as extbench results.

Each step is one subprocess. A step whose `summary.json` exists is done and is skipped; a step
stopped part-way is run again with `--resume` on the answers it had written. A failed step is
recorded and the others still run.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from harness.config import StrictModel
from harness.yamlio import load_yaml

ENGINE_ARM = "engine:"
OSS_ARM = "oss:"
StepKind = Literal["dev", "locomo", "locomo-judge", "longmemeval"]


class EngineSpec(StrictModel):
    """One engine of the comparison.

    Attributes:
        id: Short name used in output directories and the summary.
        arm: The extbench arm: `engine:<module>:<Class>`, `oss:mem0`, `oss:hindsight`, `none` or `full`.
        core: A core checkout's `src` directory to import cortex-mem from (`EXTBENCH_CORE`, and
            `PYTHONPATH` for the dev run); None for the core the harness pins.
    """

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    arm: str = Field(pattern=r"^(engine:[\w.]+:\w+|oss:(mem0|hindsight)|none|full)$")
    core: Path | None = None

    @property
    def dev_engine(self) -> str | None:
        """The `harness run --engine` value, or None for the bounds, which the dev set has no use for."""
        if self.arm.startswith(ENGINE_ARM):
            return self.arm[len(ENGINE_ARM) :]
        if self.arm.startswith(OSS_ARM):
            return self.arm[len(OSS_ARM) :]
        return None


class DevSpec(StrictModel):
    """The dev-set run: `harness run` with the engines as extra engines."""

    scenarios: list[Path] = Field(min_length=1)
    config: Path = Path("config/eval-v0.1.yaml")
    blind_seed: int | None = None


class LocomoSpec(StrictModel):
    """LoCoMo through `run.sh locomo`, then `run.sh judge` on its answers."""

    data: Path
    categories: str = Field(default="1,2,3,4", pattern=r"^[1-5](,[1-5])*$")
    only: str | None = None
    per_category: int | None = Field(default=None, ge=1)


class LongMemEvalSpec(StrictModel):
    """LongMemEval-S through `run.sh longmemeval`."""

    data: Path | None = None
    questions: int | None = Field(default=None, ge=1)
    types: str | None = None


class ExtbenchRunConfig(StrictModel):
    """Everything one comparison needs; see `tools/extbench/all-engines.example.yaml`.

    Attributes:
        name: Prefix of every output directory of this comparison.
        root: The extbench directory (`EXTBENCH_ROOT`: cache, runs, stored-history markers).
        reader: The reader model spec, e.g. `openai:qwen`.
        judge: The judge model spec; a different family from the reader avoids self-grading.
        k: Texts each memory hands over per question.
        price: Reader and judge USD per million input,output tokens (`0,0` for local models).
        per_record: Estimated USD a tool spends storing one record (0 for local models).
        budget: Total estimated USD for the extbench steps; None for no limit.
    """

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    root: Path
    reader: str = Field(min_length=1)
    judge: str = Field(min_length=1)
    k: int = Field(default=20, ge=1)
    price: str = Field(default="0,0", pattern=r"^\d+(\.\d+)?,\d+(\.\d+)?$")
    per_record: float = Field(default=0.0, ge=0)
    budget: float | None = Field(default=None, ge=0)
    engines: list[EngineSpec] = Field(min_length=1)
    dev: DevSpec | None = None
    locomo: LocomoSpec | None = None
    longmemeval: LongMemEvalSpec | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> "ExtbenchRunConfig":
        ids = [e.id for e in self.engines]
        if len(ids) != len(set(ids)):
            raise ValueError(f"engine ids must be unique: {sorted(ids)}")
        return self


def load_run_config(path: Path) -> ExtbenchRunConfig:
    """Read and validate a comparison config.

    Raises:
        pydantic.ValidationError: If a key is unknown or a value is out of range.
        yaml.YAMLError: If the YAML is invalid or a key appears twice.
    """
    return ExtbenchRunConfig.model_validate(load_yaml(path.read_text(encoding="utf-8")) or {})


@dataclass(frozen=True)
class Step:
    """One subprocess of the comparison.

    Attributes:
        key: `<kind>/<engine ids>`, unique within a comparison.
        kind: Which command this is.
        argv: The command line.
        env: Variables set on top of the current environment.
        out: The directory the step writes; `summary.json` there (for dev: in a subdirectory) marks it done.
        engines: The engine ids the step covers.
        budgeted: Whether the step takes `--budget` (the remaining amount at the time it starts).
        after: The key of a step that must be done first.
    """

    key: str
    kind: StepKind
    argv: tuple[str, ...]
    env: Mapping[str, str]
    out: Path
    engines: tuple[str, ...]
    budgeted: bool = False
    after: str | None = None


def _run_dir(cfg: ExtbenchRunConfig, kind: str, suffix: str) -> tuple[str, Path]:
    name = f"{cfg.name}-{kind}-{suffix}"
    return name, cfg.root / "runs" / name


def _engine_env(cfg: ExtbenchRunConfig, engine: EngineSpec) -> dict[str, str]:
    env = {"EXTBENCH_ROOT": str(cfg.root)}
    if engine.core is not None:
        env["EXTBENCH_CORE"] = str(engine.core)
    return env


def _dev_steps(cfg: ExtbenchRunConfig, harness: Path) -> list[Step]:
    if cfg.dev is None:
        return []
    groups: dict[Path | None, list[EngineSpec]] = {}
    for engine in cfg.engines:
        if engine.dev_engine is not None:
            groups.setdefault(engine.core, []).append(engine)
    steps: list[Step] = []
    for n, (core, engines) in enumerate(groups.items()):
        _, out = _run_dir(cfg, "dev", str(n + 1))
        argv = [str(harness), "run", "--config", str(cfg.dev.config), "--out", str(out)]
        for scenarios in cfg.dev.scenarios:
            argv += ["--scenarios", str(scenarios)]
        for engine in engines:
            argv += ["--engine", engine.dev_engine or ""]
        if cfg.dev.blind_seed is not None:  # one seed for every group, so the ids and namespaces match
            argv += ["--blind-seed", str(cfg.dev.blind_seed)]
        env = {} if core is None else {"PYTHONPATH": str(core)}
        ids = tuple(e.id for e in engines)
        steps.append(Step(f"dev/{'+'.join(ids)}", "dev", tuple(argv), env, out, ids))
    return steps


def _locomo_steps(cfg: ExtbenchRunConfig, run_sh: Path, engine: EngineSpec) -> list[Step]:
    if cfg.locomo is None:
        return []
    spec = cfg.locomo
    name, out = _run_dir(cfg, "locomo", engine.id)
    argv = [str(run_sh), "locomo", "--data", str(spec.data), "--model", cfg.reader, "--arm", engine.arm,
            "--k", str(cfg.k), "--categories", spec.categories, "--name", name, "--price", cfg.price,
            "--per-record", str(cfg.per_record)]
    if spec.only:
        argv += ["--only", spec.only]
    if spec.per_category:
        argv += ["--per-category", str(spec.per_category)]
    env = _engine_env(cfg, engine)
    run = Step(f"locomo/{engine.id}", "locomo", tuple(argv), env, out, (engine.id,), budgeted=True)
    judged_name, judged_out = _run_dir(cfg, "locomo", f"{engine.id}-judged")
    judge_argv = (str(run_sh), "judge", "--model", cfg.judge, "--name", judged_name,
                  "--answers", str(out / "answers.jsonl"))
    judge = Step(f"locomo-judge/{engine.id}", "locomo-judge", judge_argv, env, judged_out, (engine.id,),
                 after=run.key)
    return [run, judge]


def _longmemeval_steps(cfg: ExtbenchRunConfig, run_sh: Path, engine: EngineSpec) -> list[Step]:
    if cfg.longmemeval is None:
        return []
    spec = cfg.longmemeval
    name, out = _run_dir(cfg, "longmemeval", engine.id)
    argv = [str(run_sh), "longmemeval", "--model", cfg.reader, "--judge-model", cfg.judge, "--arm", engine.arm,
            "--k", str(cfg.k), "--name", name, "--price", cfg.price, "--per-record", str(cfg.per_record)]
    if spec.data is not None:
        argv += ["--data", str(spec.data)]
    if spec.questions is not None:
        argv += ["--questions", str(spec.questions)]
    if spec.types:
        argv += ["--types", spec.types]
    return [Step(f"longmemeval/{engine.id}", "longmemeval", tuple(argv), _engine_env(cfg, engine), out,
                 (engine.id,), budgeted=True)]


def absolute_paths(cfg: ExtbenchRunConfig, base: Path) -> ExtbenchRunConfig:
    """The config with every path made absolute against `base`.

    `run.sh` changes into `tools/extbench` before it runs, so a relative path in the config would
    name a different file there than where the comparison was started.
    """
    def fix(path: Path) -> Path:
        return path if path.is_absolute() else base / path

    update: dict[str, object] = {
        "root": fix(cfg.root),
        "engines": [e.model_copy(update={"core": None if e.core is None else fix(e.core)}) for e in cfg.engines],
    }
    if cfg.dev is not None:
        update["dev"] = cfg.dev.model_copy(update={
            "scenarios": [fix(p) for p in cfg.dev.scenarios], "config": fix(cfg.dev.config),
        })
    if cfg.locomo is not None:
        update["locomo"] = cfg.locomo.model_copy(update={"data": fix(cfg.locomo.data)})
    if cfg.longmemeval is not None and cfg.longmemeval.data is not None:
        update["longmemeval"] = cfg.longmemeval.model_copy(update={"data": fix(cfg.longmemeval.data)})
    return cfg.model_copy(update=update)


def plan(cfg: ExtbenchRunConfig, run_sh: Path, harness: Path, base: Path | None = None) -> list[Step]:
    """Every step of the comparison, in the order they run: dev first, then per engine.

    Relative paths in `cfg` are taken against `base` (default: the current directory).
    """
    cfg = absolute_paths(cfg, Path.cwd() if base is None else base)
    steps = _dev_steps(cfg, harness)
    for engine in cfg.engines:
        steps += _locomo_steps(cfg, run_sh, engine)
        steps += _longmemeval_steps(cfg, run_sh, engine)
    return steps


def is_done(step: Step) -> bool:
    """Whether the step's output is complete."""
    if step.kind == "dev":
        return step.out.is_dir() and any(step.out.glob("*/summary.json"))
    return (step.out / "summary.json").is_file()


class _Lax(BaseModel):
    """Reads only the fields it names from a summary another command wrote."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class _Spent(_Lax):
    estimated_usd: float = 0.0


def spent(steps: Sequence[Step]) -> float:
    """Estimated USD of the finished budgeted steps, from their summaries."""
    total = 0.0
    for step in steps:
        if step.budgeted and is_done(step):
            total += _Spent.model_validate_json((step.out / "summary.json").read_text(encoding="utf-8")).estimated_usd
    return total


@dataclass
class Outcome:
    """What happened to one step."""

    key: str
    status: Literal["done", "skipped", "failed", "blocked"]
    detail: str = ""


Runner = Callable[[Sequence[str], Mapping[str, str]], int]


def run_subprocess(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """Run one step with `env` on top of this process's environment; return its exit code."""
    return subprocess.run(list(argv), env={**os.environ, **env}, check=False).returncode


@dataclass
class Execution:
    """Steps run in order; `outcomes` says what became of each."""

    steps: Sequence[Step]
    budget: float | None
    runner: Runner = run_subprocess
    rerun: bool = False
    outcomes: list[Outcome] = field(default_factory=list)

    def run(self) -> list[Outcome]:
        """Run every step that is not done, continuing past failures."""
        failed: set[str] = set()
        for step in self.steps:
            if step.after in failed:
                self.outcomes.append(Outcome(step.key, "blocked", f"{step.after} failed"))
                failed.add(step.key)
                continue
            if is_done(step) and not self.rerun:
                self.outcomes.append(Outcome(step.key, "skipped", "already done"))
                continue
            argv = list(step.argv) + self._resume_args(step) + self._budget_args(step)
            code = self.runner(argv, step.env)
            if code == 0 and is_done(step):
                self.outcomes.append(Outcome(step.key, "done"))
            else:
                failed.add(step.key)
                self.outcomes.append(Outcome(step.key, "failed", f"exit code {code}"))
        return self.outcomes

    def _resume_args(self, step: Step) -> list[str]:
        answers = step.out / "answers.jsonl"
        if step.kind not in ("locomo", "longmemeval") or self.rerun or not answers.is_file():
            return []
        if answers.stat().st_size == 0:
            return []
        # The runner empties answers.jsonl when it starts, so the earlier answers move aside first.
        earlier = step.out / "answers.earlier.jsonl"
        _ = answers.replace(earlier)
        return ["--resume", str(earlier)]

    def _budget_args(self, step: Step) -> list[str]:
        if not step.budgeted or self.budget is None:
            return []
        return ["--budget", f"{max(0.0, self.budget - spent(self.steps)):.4f}"]


def default_paths() -> tuple[Path, Path]:
    """`tools/extbench/run.sh` of this checkout and the `harness` command of this environment.

    Raises:
        FileNotFoundError: If either is missing (e.g. an installed harness without `tools/`).
    """
    run_sh = Path(__file__).resolve().parents[2] / "tools" / "extbench" / "run.sh"
    harness = Path(sys.executable).parent / "harness"
    for path in (run_sh, harness):
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found; run from a harness checkout with `uv run`")
    return run_sh, harness


# Summary ---------------------------------------------------------------------------------------


class _Rate(_Lax):
    k: int
    n: int
    value: float | None


class _DevMetrics(_Lax):
    m1: _Rate
    m2: _Rate
    m3: int


class _DevVerdict(_Lax):
    passed: bool


class _DevEngine(_Lax):
    metrics: _DevMetrics
    verdict: _DevVerdict | None = None


class _DevSummary(_Lax):
    engines: dict[str, _DevEngine]


class _EngineIds(_Lax):
    ids: dict[str, str]


class _LocomoArm(_Lax):
    n: int
    f1: float | None
    evidence_recall: float | None = None


class _LocomoSummary(_Lax):
    arms: dict[str, _LocomoArm]
    failed: list[list[str]] = Field(default_factory=list)


class _JudgedArm(_Lax):
    judged_correct: float | None


class _JudgedSummary(_Lax):
    arms: dict[str, _JudgedArm]


class _LmeRate(_Lax):
    n: int
    accuracy: float | None


class _LmeArm(_LmeRate):
    abstention: _LmeRate
    evidence_recall: float | None = None


class _LmeSummary(_Lax):
    arms: dict[str, _LmeArm]
    failed: list[list[str]] = Field(default_factory=list)


class Row(StrictModel):
    """One engine's line in the comparison table; None where a step was not configured or not done."""

    id: str
    arm: str
    core: str | None
    dev_m1: float | None = None
    dev_m2: float | None = None
    dev_m3: int | None = None
    dev_passed: bool | None = None
    locomo_f1: float | None = None
    locomo_judged: float | None = None
    locomo_evidence: float | None = None
    locomo_failed: int | None = None
    lme_accuracy: float | None = None
    lme_abstention: float | None = None
    lme_evidence: float | None = None
    lme_failed: int | None = None


def _read[T: BaseModel](model: type[T], path: Path) -> T | None:
    """The summary at `path`, or None when it is missing or not in the shape expected."""
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        return None


def _dev_row(row: Row, step: Step, name: str) -> Row:
    latest = sorted(step.out.glob("*/summary.json"))[-1].parent
    summary = _read(_DevSummary, latest / "summary.json")
    ids = _read(_EngineIds, latest / "engine_ids.json")
    if summary is None:
        return row
    blind = {engine: blind_id for blind_id, engine in (ids.ids if ids else {}).items()}
    scored = summary.engines.get(blind.get(name, name))
    if scored is None:
        return row
    return row.model_copy(update={
        "dev_m1": scored.metrics.m1.value, "dev_m2": scored.metrics.m2.value, "dev_m3": scored.metrics.m3,
        "dev_passed": None if scored.verdict is None else scored.verdict.passed,
    })


def _bench_row(row: Row, step: Step) -> Row:
    path, arm = step.out / "summary.json", row.arm
    if step.kind == "locomo" and (s := _read(_LocomoSummary, path)) and arm in s.arms:
        return row.model_copy(update={
            "locomo_f1": s.arms[arm].f1, "locomo_evidence": s.arms[arm].evidence_recall, "locomo_failed": len(s.failed),
        })
    if step.kind == "locomo-judge" and (j := _read(_JudgedSummary, path)) and arm in j.arms:
        return row.model_copy(update={"locomo_judged": j.arms[arm].judged_correct})
    if step.kind == "longmemeval" and (m := _read(_LmeSummary, path)) and arm in m.arms:
        return row.model_copy(update={
            "lme_accuracy": m.arms[arm].accuracy, "lme_abstention": m.arms[arm].abstention.accuracy,
            "lme_evidence": m.arms[arm].evidence_recall, "lme_failed": len(m.failed),
        })
    return row


def collect(cfg: ExtbenchRunConfig, steps: Sequence[Step]) -> list[Row]:
    """One row per engine, in config order, from whatever the steps have written so far."""
    engines = {e.id: e for e in cfg.engines}
    rows = {e.id: Row(id=e.id, arm=e.arm, core=None if e.core is None else str(e.core)) for e in cfg.engines}
    for step in steps:
        if not is_done(step):
            continue
        for engine_id in step.engines:
            if step.kind == "dev":
                rows[engine_id] = _dev_row(rows[engine_id], step, engines[engine_id].dev_engine or "")
            else:
                rows[engine_id] = _bench_row(rows[engine_id], step)
    return [rows[e.id] for e in cfg.engines]


_COLUMNS: tuple[tuple[str, Callable[[Row], object]], ...] = (
    ("engine", lambda r: r.id),
    ("dev M1", lambda r: r.dev_m1),
    ("dev M2", lambda r: r.dev_m2),
    ("dev M3", lambda r: r.dev_m3),
    ("dev pass", lambda r: r.dev_passed),
    ("LoCoMo F1", lambda r: r.locomo_f1),
    ("LoCoMo judged", lambda r: r.locomo_judged),
    ("LoCoMo evidence", lambda r: r.locomo_evidence),
    ("LME accuracy", lambda r: r.lme_accuracy),
    ("LME abstention", lambda r: r.lme_abstention),
    ("LME evidence", lambda r: r.lme_evidence),
)
_NOTE = (
    "LoCoMo and LongMemEval-S columns are extbench results (a model answers and a model judges); "
    + "they are not the pre-registered measurement. Dev columns come from `harness run`."
)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def summary_markdown(cfg: ExtbenchRunConfig, rows: Sequence[Row], outcomes: Sequence[Outcome]) -> str:
    """The comparison table, the settings it was made with, and what happened to each step."""
    lines = [
        f"# {cfg.name}",
        "",
        _NOTE,
        "",
        f"Reader `{cfg.reader}`, judge `{cfg.judge}`, k = {cfg.k}.",
        "",
        "| " + " | ".join(title for title, _ in _COLUMNS) + " |",
        "| " + " | ".join("---" for _ in _COLUMNS) + " |",
    ]
    for row in rows:
        cells = [_cell(value(row)) for _, value in _COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    if outcomes:
        lines += ["", "| step | status | detail |", "| --- | --- | --- |"]
        lines += [f"| {o.key} | {o.status} | {o.detail} |" for o in outcomes]
    return "\n".join(lines) + "\n"


def write_summary(cfg: ExtbenchRunConfig, steps: Sequence[Step], outcomes: Sequence[Outcome]) -> Path:
    """Write `<name>-summary.md` and `.json` under the runs directory; return the markdown path."""
    rows = collect(cfg, steps)
    out = cfg.root / "runs"
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"{cfg.name}-summary.md"
    _ = md.write_text(summary_markdown(cfg, rows, outcomes), encoding="utf-8")
    payload = {
        "name": cfg.name, "reader": cfg.reader, "judge": cfg.judge, "k": cfg.k,
        "rows": [r.model_dump(mode="json") for r in rows],
        "steps": [{"key": o.key, "status": o.status, "detail": o.detail} for o in outcomes],
    }
    _ = (out / f"{cfg.name}-summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return md
