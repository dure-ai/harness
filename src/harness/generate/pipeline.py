"""The generation pipeline: candidates, quote check, family skeleton, bodies, automatic checks, save."""

import json
import random
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO, TypeVar

import yaml
from pydantic import BaseModel

from harness.config import EvalConfig
from harness.generate.bodies import write_bodies
from harness.generate.candidates import (
    FactCandidate,
    Rejection,
    check_quotes,
    dedupe,
    extract_candidates,
)
from harness.generate.checks import check_draft
from harness.generate.config import GenConfig
from harness.generate.families import TEMPLATES, why_not
from harness.generate.families.base import repo_name
from harness.generate.llm import LLMClient, LLMError
from harness.generate.traces import Trace, bundles, read_parquet, usable
from harness.scenario.load import ScenarioLoadError, load_scenario
from harness.scenario.model import Scenario

T = TypeVar("T", bound=BaseModel)


def all_family_counts(n: int) -> dict[str, int]:
    """Drafts per family for `--family all`: `n` each, but `normal_repeat` has no pair.

    Args:
        n: Drafts wanted per family.

    Returns:
        Family -> count, with `normal_repeat` reduced to `max(1, n // 2)` (generation design 1).
    """
    return {f: max(1, n // 2) if f == "normal_repeat" else n for f in TEMPLATES}


class GenerateReport(BaseModel):
    """What one `generate` call did.

    Attributes:
        requested: Drafts wanted, summed over the families.
        written: Ids of the drafts saved (an earlier draft with the same id is replaced).
        skipped_existing: Ids that already exist as a reviewed (not draft) file; left untouched.
            They count toward the target.
        rejected: Number of rejections logged: bad quotes, near-duplicate candidates, failed
            LLM calls and drafts that failed the checks.
        shortfall: Family -> why fewer than requested were made.
    """

    requested: int
    written: list[str] = []
    skipped_existing: list[str] = []
    rejected: int = 0
    shortfall: dict[str, str] = {}


class DryRun(Exception):
    """Raised by `DryRunClient` on the first request, carrying it.

    Attributes:
        kind: Request label.
        system: System prompt.
        user: User prompt.
    """

    def __init__(self, kind: str, system: str, user: str) -> None:
        """Create the signal.

        Args:
            kind: Request label.
            system: System prompt.
            user: User prompt.
        """
        super().__init__(f"dry run: first {kind} request")
        self.kind = kind
        self.system = system
        self.user = user


class DryRunClient:
    """An `LLMClient` that never calls a model: its first request raises `DryRun`.

    Attributes:
        name: Provider name shown for the run.
        model: Model name shown for the run.
    """

    def __init__(self, name: str, model: str) -> None:
        """Create the client.

        Args:
            name: Provider name.
            model: Model name.
        """
        self.name = name
        self.model = model

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Stop the run with the request that would have been sent.

        Args:
            kind: Request label.
            system: System prompt.
            user: User prompt.
            schema: Unused.
            validate: Unused.

        Raises:
            DryRun: Always.
        """
        raise DryRun(kind, system, user)


def read_all(shards: Sequence[Path]) -> Iterator[Trace]:
    """Every trajectory of the given parquet files, in file order.

    Args:
        shards: Parquet files in the Open-SWE-Traces layout.

    Yields:
        One `Trace` per row.
    """
    for path in shards:
        yield from read_parquet(path)


def save_draft(s: Scenario, path: Path) -> None:
    """Write a scenario as YAML that `load_scenario` reads back.

    Args:
        s: Scenario to write.
        path: Target file; parent directories are created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(s.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def matches_repos(trace: Trace, repos: set[str]) -> bool:
    """Whether the trace's repository is one of `repos`, given as "owner/name" or the short name."""
    wanted = {r.casefold() for r in repos}
    return trace.repo.casefold() in wanted or repo_name(trace.repo).casefold() in wanted


@dataclass
class _Tally:
    """What one family looked at, to explain a shortfall."""

    traces: int = 0
    candidates: int = 0
    no_fit: int = 0
    first_no_fit: str = ""
    rejected: int = 0
    written: int = 0
    skipped: int = 0

    def reason(self, target: int) -> str:
        parts = [f"{self.written + self.skipped} of {target} made"]
        parts.append(f"looked at {self.traces} traces and {self.candidates} candidates")
        if self.no_fit:
            parts.append(f"{self.no_fit} did not fit ({self.first_no_fit})")
        if self.rejected:
            parts.append(f"{self.rejected} drafts rejected")
        return "; ".join(parts)


@dataclass
class _Run:
    """State shared by the families of one run."""

    client: LLMClient
    cfg: EvalConfig
    gen: GenConfig
    seed: int
    out_dir: Path
    candidates_log: TextIO
    rejected_log: TextIO
    by_task: dict[str, list[Trace]]
    cache: dict[str, list[FactCandidate]] = field(default_factory=dict)
    rejected: int = 0

    def reject(self, r: Rejection) -> None:
        self.rejected += 1
        self.rejected_log.write(r.model_dump_json() + "\n")
        self.rejected_log.flush()

    def candidates(self, trace: Trace) -> list[FactCandidate]:
        """Candidates of a trace, extracted at most once per run."""
        if trace.trajectory_id in self.cache:
            return self.cache[trace.trajectory_id]
        try:
            extracted = extract_candidates(self.client, trace, self.gen)
        except LLMError as e:
            if e.fatal:
                raise
            self.reject(Rejection(stage="candidates", code="llm_error", message=str(e), trace_id=trace.trajectory_id))
            self.cache[trace.trajectory_id] = []
            return []
        checked, bad = check_quotes(trace, extracted)
        kept = dedupe(checked)
        kept_ids = {c.index for c in kept}
        for r in bad:
            self.reject(r)
        for c in checked:
            if c.index not in kept_ids:
                self.reject(
                    Rejection(
                        stage="candidates",
                        code="duplicate",
                        message=f"candidate {c.index} repeats an earlier candidate",
                        trace_id=trace.trajectory_id,
                    )
                )
        for c in kept:
            line = {"trajectory_id": trace.trajectory_id, "repo": trace.repo, **c.model_dump()}
            self.candidates_log.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.candidates_log.flush()
        self.cache[trace.trajectory_id] = kept
        return kept

    def bundle_of(self, trace: Trace) -> list[Trace]:
        """The trace first, then the other models' trajectories of the same task."""
        others = [t for t in self.by_task.get(trace.instance_id, []) if t.trajectory_id != trace.trajectory_id]
        return [trace, *others]


def _existing_status(path: Path) -> str | None:
    """Review status of the file at `path`; "unreadable" when it cannot be loaded, None when absent."""
    if not path.exists():
        return None
    try:
        return load_scenario(path).review.status
    except ScenarioLoadError:
        return "unreadable"


def _one(run: _Run, family: str, trace: Trace, cand: FactCandidate, tally: _Tally) -> tuple[str, str] | None:
    """Try to make one draft; returns ("written" | "skipped", id) or None when it did not work out."""
    bundle = run.bundle_of(trace)
    sk = TEMPLATES[family](cand, bundle, run.seed)
    if sk is None:
        tally.no_fit += 1
        tally.first_no_fit = tally.first_no_fit or "the template returned no skeleton"
        return None
    sid = sk.scenario.id
    path = run.out_dir / family / f"{sid}.yaml"
    status = _existing_status(path)
    if status is not None and status != "draft":
        return "skipped", sid

    def reject(stage: str, code: str, message: str) -> None:
        tally.rejected += 1
        run.reject(Rejection(stage=stage, code=code, message=message, trace_id=trace.trajectory_id, family=family))

    try:
        s = write_bodies(run.client, sk, cand, bundle, run.gen)
    except LLMError as e:
        if e.fatal:
            raise
        reject("bodies", "llm_error", str(e))
        return None
    blocking = [
        i
        for i in check_draft(s, run.cfg, run.gen, sk.secret_event_ids)
        if i.level == "error" or i.code == "too_easy"
    ]
    if blocking:
        reject("checks", blocking[0].code, "; ".join(f"{i.code}: {i.message}" for i in blocking))
        return None
    save_draft(s, path)
    return "written", sid


def generate(
    families: Sequence[str],
    n: int,
    seed: int,
    traces: Sequence[Trace],
    client: LLMClient,
    cfg: EvalConfig,
    gen: GenConfig,
    out_dir: Path,
    run_dir: Path,
    repos: set[str] | None = None,
    per_family: Mapping[str, int] | None = None,
) -> GenerateReport:
    """Make draft scenarios, `n` per family, from real traces.

    Usable traces (python, allowed license; `repos` if given) are sorted by trajectory id and
    shuffled with `random.Random(seed)`. Each family walks that order. The candidates of a trace
    are extracted at most once and shared by all families; a family uses each of them once. A
    draft is saved to `out_dir/<family>/<id>.yaml` only if `check_draft` finds no error and no
    `too_easy` warning; otherwise a `Rejection` goes to `run_dir/rejected.jsonl`. A file with the
    same id whose review status is not draft is never touched.

    Args:
        families: Families to make, each a key of `TEMPLATES`.
        n: Drafts wanted per family.
        seed: Seed of the trace order and of every skeleton.
        traces: Candidate trajectories; unusable ones are ignored.
        client: LLM client.
        cfg: Eval config for the discrimination check.
        gen: Generation config.
        out_dir: Root of the saved drafts.
        run_dir: Directory for `candidates.jsonl` and `rejected.jsonl`; created.
        repos: Only traces of these repositories ("owner/name" or the short name); None for all.
        per_family: Per-family override of `n`.

    Returns:
        What was written, skipped and rejected, and why a family fell short.

    Raises:
        LLMError: Only a fatal one (missing key, authentication); other failures reject the
            candidate or draft they hit.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    allowed = [t for t in traces if usable(t, gen)]
    pool = allowed if repos is None else [t for t in allowed if matches_repos(t, repos)]
    order = sorted(pool, key=lambda t: t.trajectory_id)
    random.Random(seed).shuffle(order)
    targets = {f: (per_family or {}).get(f, n) for f in families}
    report = GenerateReport(requested=sum(targets.values()))
    with (
        (run_dir / "candidates.jsonl").open("w", encoding="utf-8") as cand_log,
        (run_dir / "rejected.jsonl").open("w", encoding="utf-8") as rej_log,
    ):
        run = _Run(client, cfg, gen, seed, out_dir, cand_log, rej_log, bundles(pool))
        for family in families:
            target, tally = targets[family], _Tally()
            for trace in order:
                if tally.written + tally.skipped >= target:
                    break
                tally.traces += 1
                for cand in run.candidates(trace):
                    if tally.written + tally.skipped >= target:
                        break
                    tally.candidates += 1
                    problem = why_not(family, cand)
                    if problem is not None:
                        tally.no_fit += 1
                        tally.first_no_fit = tally.first_no_fit or problem
                        continue
                    made = _one(run, family, trace, cand, tally)
                    if made is None:
                        continue
                    if made[0] == "written":
                        tally.written += 1
                        report.written.append(made[1])
                    else:
                        tally.skipped += 1
                        report.skipped_existing.append(made[1])
            if tally.written + tally.skipped < target:
                report.shortfall[family] = (
                    tally.reason(target) if pool else _empty_pool_reason(len(traces), len(allowed), repos)
                )
        report.rejected = run.rejected
    return report


def _empty_pool_reason(given: int, allowed: int, repos: set[str] | None) -> str:
    if given == 0:
        return "no traces were given"
    if allowed == 0:
        return f"none of the {given} traces is a python repository with an allowed license"
    return f"none of the {allowed} usable traces is from the repositories {sorted(repos or [])}"
