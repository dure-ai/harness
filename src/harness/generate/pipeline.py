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
from harness.generate.checks import blocking, check_draft
from harness.generate.config import GenConfig
from harness.generate.families import TEMPLATES, why_not
from harness.generate.families.base import repo_name
from harness.generate.llm import LLMClient, LLMError
from harness.generate.traces import Trace, bundles, read_parquet, usable
from harness.scenario.labels import content_hash, label_fingerprint
from harness.scenario.load import ScenarioLoadError, read_scenario
from harness.scenario.model import Scenario

T = TypeVar("T", bound=BaseModel)

_ERRORS_IN_A_ROW = 3
"""Non-fatal LLM errors in a row that stop a run: the provider is most likely down."""


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
        skipped_existing: Ids whose file a person already worked on (status not draft, edited by
            a reviewer or by hand since generation, or not written by the LLM) or that cannot be
            read; left untouched. They count toward the target.
        trace_cap_hit: True when extraction stopped at `max_traces` while a family still needed
            drafts.
        rejected: Number of rejections logged: bad quotes, near-duplicate candidates, failed
            LLM calls and drafts that failed the checks.
        shortfall: Family -> why fewer than requested were made.
        first_llm_error: Message of the first non-fatal LLM error of the run, if there was one.
    """

    requested: int
    written: list[str] = []
    skipped_existing: list[str] = []
    rejected: int = 0
    shortfall: dict[str, str] = {}
    trace_cap_hit: bool = False
    first_llm_error: str | None = None


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
        last_model: Always None.
    """

    def __init__(self, name: str, model: str) -> None:
        """Create the client.

        Args:
            name: Provider name.
            model: Model name.
        """
        self.name = name
        self.model = model
        self.last_model: str | None = None

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
    text = yaml.safe_dump(
        s.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def mark_generated(s: Scenario, fingerprint: bool = True) -> None:
    """Record, in `s`, the hashes a new draft carries: its labels and its content as generated.

    `provenance.label_fingerprint` lets `review set approved` refuse labels changed outside
    `review edit`; `provenance.generated_hash` lets a regeneration recognise a hand-edited draft.
    `save_draft` writes the model dump, whose parsed YAML is the same data, so the hash taken
    here is the hash of the saved file.

    Args:
        s: The scenario, changed in place; call it just before the first `save_draft`.
        fingerprint: False to leave `label_fingerprint` unset, for a skeleton whose labels a
            person still fills in by hand (the secret strings of a `scenario new` leak family).
    """
    s.provenance.label_fingerprint = label_fingerprint(s) if fingerprint else None
    s.provenance.generated_hash = content_hash(s.model_dump(mode="json"), "generated")


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
    capped: bool = False
    attempts: int = 0
    max_attempts: int = 0
    attempts_capped: bool = False

    def reason(self, target: int, max_traces: int) -> str:
        parts = [f"{self.written + self.skipped} of {target} made"]
        parts.append(f"looked at {self.traces} traces and {self.candidates} candidates")
        if self.no_fit:
            parts.append(f"{self.no_fit} did not fit ({self.first_no_fit})")
        if self.rejected:
            parts.append(f"{self.rejected} drafts rejected")
        if self.capped:
            parts.append(f"stopped at --max-traces {max_traces}")
        if self.attempts_capped:
            parts.append(f"stopped at --max-attempts {self.max_attempts}")
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
    max_traces: int
    cache: dict[str, list[FactCandidate]] = field(default_factory=dict)
    rejected: int = 0
    first_llm_error: str | None = None
    errors_in_a_row: int = 0

    def reject(self, r: Rejection) -> None:
        self.rejected += 1
        self.rejected_log.write(r.model_dump_json() + "\n")
        self.rejected_log.flush()

    def llm_ok(self) -> None:
        """A model call worked: the run of consecutive errors is over."""
        self.errors_in_a_row = 0

    def llm_failed(self, e: LLMError) -> None:
        """Count a non-fatal model error; re-raise a fatal one.

        Raises:
            LLMError: `e` itself when fatal, or a fatal error after `_ERRORS_IN_A_ROW` errors in a
                row, naming the last and the first error of the run.
        """
        if e.fatal:
            raise e
        self.first_llm_error = self.first_llm_error or str(e)
        self.errors_in_a_row += 1
        if self.errors_in_a_row >= _ERRORS_IN_A_ROW:
            raise LLMError(
                f"stopped after {_ERRORS_IN_A_ROW} LLM errors in a row; last: {e}; "
                f"first LLM error: {self.first_llm_error}",
                fatal=True,
            ) from e

    def can_extract(self, trace: Trace) -> bool:
        """Whether the trace's candidates are known or the trace cap still allows extracting them."""
        return trace.trajectory_id in self.cache or len(self.cache) < self.max_traces

    def candidates(self, trace: Trace) -> list[FactCandidate]:
        """Candidates of a trace, extracted at most once per run."""
        if trace.trajectory_id in self.cache:
            return self.cache[trace.trajectory_id]
        try:
            extracted = extract_candidates(self.client, trace, self.gen)
        except LLMError as e:
            if e.fatal:
                raise
            self.reject(
                Rejection(
                    stage="candidates",
                    code="llm_error",
                    message=str(e),
                    trace_id=trace.trajectory_id,
                )
            )
            self.cache[trace.trajectory_id] = []
            self.llm_failed(e)
            return []
        self.llm_ok()
        checked, bad = check_quotes(trace, extracted, self.gen.min_quote_chars)
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
            line = {
                "trajectory_id": trace.trajectory_id,
                "repo": trace.repo,
                **c.model_dump(),
            }
            self.candidates_log.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.candidates_log.flush()
        self.cache[trace.trajectory_id] = kept
        return kept

    def bundle_of(self, trace: Trace) -> list[Trace]:
        """The trace first, then the other models' trajectories of the same task."""
        others = [
            t
            for t in self.by_task.get(trace.instance_id, [])
            if t.trajectory_id != trace.trajectory_id
        ]
        return [trace, *others]


def _is_reviewed(path: Path) -> bool:
    """Whether the file at `path` belongs to a person and must not be replaced by a new draft.

    That is the case when its review status is not draft, a reviewer edited it
    (`provenance.reviewer_edited`), a person wrote it (`provenance.author` is not `llm`), its
    content no longer matches `provenance.generated_hash` (edited by hand outside `review edit`;
    a draft without that hash, from an older version, is judged by the other rules only), or it
    cannot be loaded. An absent file, or an untouched LLM draft, may be replaced.
    """
    if not path.exists():
        return False
    try:
        raw, s = read_scenario(path)
    except (ScenarioLoadError, UnicodeDecodeError, OSError, ValueError):
        return True
    generated = s.provenance.generated_hash
    return (
        s.review.status != "draft"
        or s.provenance.reviewer_edited
        or s.provenance.author != "llm"
        or (generated is not None and generated != content_hash(raw, "generated"))
    )


def _one(
    run: _Run, family: str, trace: Trace, cand: FactCandidate, tally: _Tally
) -> tuple[str, str] | None:
    """Try to make one draft.

    Returns:
        ("written" | "skipped", id), ("capped", id) when the family has used up its body
        attempts, or None when the draft did not work out.
    """
    bundle = run.bundle_of(trace)
    sk = TEMPLATES[family](cand, bundle, run.seed)
    if sk is None:
        tally.no_fit += 1
        tally.first_no_fit = tally.first_no_fit or "the template returned no skeleton"
        return None
    sid = sk.scenario.id
    path = run.out_dir / family / f"{sid}.yaml"
    if _is_reviewed(path):
        return "skipped", sid
    if tally.attempts >= tally.max_attempts:
        tally.attempts_capped = True
        return "capped", sid
    tally.attempts += 1

    def reject(stage: str, code: str, message: str) -> None:
        tally.rejected += 1
        run.reject(
            Rejection(
                stage=stage,
                code=code,
                message=message,
                trace_id=trace.trajectory_id,
                family=family,
            )
        )

    try:
        s = write_bodies(run.client, sk, cand, bundle, run.gen)
    except LLMError as e:
        if e.fatal:
            raise
        reject("bodies", "llm_error", str(e))
        run.llm_failed(e)
        return None
    run.llm_ok()
    stop = blocking(check_draft(s, run.cfg, run.gen))
    if stop:
        reject(
            "checks", stop[0].code, "; ".join(f"{i.code}: {i.message}" for i in stop)
        )
        return None
    mark_generated(s)
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
    max_traces: int = 100,
    max_attempts: int | None = None,
) -> GenerateReport:
    """Make draft scenarios, `n` per family, from real traces.

    Usable traces (python, allowed license; `repos` if given) are sorted by trajectory id and
    shuffled with `random.Random(seed)`. Each family walks that order. The candidates of a trace
    are extracted at most once and shared by all families; a family uses each of them once. A
    draft is saved to `out_dir/<family>/<id>.yaml` only if `check_draft` finds no error and no
    `too_easy` warning; otherwise a `Rejection` goes to `run_dir/rejected.jsonl`. A saved draft
    records its label fingerprint and content hash (`mark_generated`). A file with the same id is
    replaced only when it is an untouched LLM draft (status draft, author `llm`, not
    reviewer-edited, content still matching its `generated_hash`); any other file is never
    touched.

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
        max_traces: Upper bound on the distinct traces sent to candidate extraction in the whole
            run (the candidates are shared by the families). Traces already extracted stay usable.
        max_attempts: Most body-writing attempts per family; the family stops there. None for
            three times that family's target.

    Returns:
        What was written, skipped and rejected, why a family fell short, and the first LLM
        error.

    Raises:
        LLMError: A fatal one (missing key, authentication, wrong model, bad request, no
            connection), or after three non-fatal LLM errors in a row. Other failures reject the
            candidate or draft they hit.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    allowed = [t for t in traces if usable(t, gen)]
    pool = allowed if repos is None else [t for t in allowed if matches_repos(t, repos)]
    unique: dict[str, Trace] = {}
    for t in pool:
        unique.setdefault(
            t.trajectory_id, t
        )  # the first occurrence of a trajectory wins
    pool = list(unique.values())
    order = sorted(pool, key=lambda t: t.trajectory_id)
    random.Random(seed).shuffle(order)
    targets = {f: (per_family or {}).get(f, n) for f in families}
    report = GenerateReport(requested=sum(targets.values()))
    with (
        (run_dir / "candidates.jsonl").open("w", encoding="utf-8") as cand_log,
        (run_dir / "rejected.jsonl").open("w", encoding="utf-8") as rej_log,
    ):
        run = _Run(
            client,
            cfg,
            gen,
            seed,
            out_dir,
            cand_log,
            rej_log,
            bundles(pool),
            max_traces,
        )
        for family in families:
            target = targets[family]
            tally = _Tally(
                max_attempts=3 * target if max_attempts is None else max_attempts
            )
            for trace in order:
                if tally.written + tally.skipped >= target or tally.attempts_capped:
                    break
                if not run.can_extract(trace):
                    tally.capped = report.trace_cap_hit = True
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
                    if made[0] == "capped":
                        break
                    if made[0] == "written":
                        tally.written += 1
                        report.written.append(made[1])
                    else:
                        tally.skipped += 1
                        report.skipped_existing.append(made[1])
            if tally.written + tally.skipped < target:
                report.shortfall[family] = (
                    tally.reason(target, max_traces)
                    if pool
                    else _empty_pool_reason(len(traces), len(allowed), repos)
                )
        report.rejected = run.rejected
        report.first_llm_error = run.first_llm_error
    return report


def _empty_pool_reason(given: int, allowed: int, repos: set[str] | None) -> str:
    if given == 0:
        return "no traces were given"
    if allowed == 0:
        return (
            f"none of the {given} traces is a python repository with an allowed license"
        )
    return f"none of the {allowed} usable traces is from the repositories {sorted(repos or [])}"
