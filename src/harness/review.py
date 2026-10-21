"""Review of generated drafts: list, show, edit with label protection, and approve or reject.

Everything here works from the scenario YAML alone, so a teammate can review on their own
machine with only the repository checked out (generation design 7). It needs no `gen` extra.
"""

import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from harness.config import EvalConfig
from harness.engines import make_engine
from harness.generate.checks import blocking, check_draft, secret_events
from harness.generate.config import GenConfig
from harness.generate.pipeline import save_draft
from harness.replay import ScenarioRun, run_scenario
from harness.scenario.labels import changed_label_fields
from harness.scenario.load import ScenarioLoadError, load_scenario
from harness.scenario.model import Review, Scenario
from harness.scenario.validate import Issue, validate_scenario
from harness.selfcheck import check_discrimination
from harness.world import probe_ctx, true_at

__all__ = [
    "DraftRow",
    "EditResult",
    "blocking",
    "edit_draft",
    "find_draft",
    "list_drafts",
    "render_show",
    "set_status",
]

_RUN_ID = "review"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class DraftRow:
    """One scenario file in the review list.

    Attributes:
        id: Scenario id (the file stem when the file cannot be read).
        family: Scenario family ("?" when the file cannot be read).
        path: The file.
        status: Review status, or "unreadable".
        generated_at: When the draft was generated, if recorded.
        warnings: Codes of the structural findings (`validate_scenario`); `load_error` for an
            unreadable file.
    """

    id: str
    family: str
    path: Path
    status: str
    generated_at: datetime | None
    warnings: list[str]


@dataclass
class EditResult:
    """What `edit_draft` did.

    Attributes:
        status: "unchanged" (nothing to save), "saved", or "reverted" (the file is as before).
        issues: Findings of `check_draft` on the saved draft; empty unless saved.
        changed_fields: Label fields the edit moved, when it was reverted for that.
        reason: Why the edit was reverted.
        reset_to_draft: True when the file was approved or rejected and the saved edit put it
            back to draft, so it needs approval again.
    """

    status: Literal["unchanged", "saved", "reverted"]
    issues: list[Issue] = field(default_factory=list)
    changed_fields: list[str] = field(default_factory=list)
    reason: str = ""
    reset_to_draft: bool = False


# ---- list and find ------------------------------------------------------------------------------


def _yaml_files(root: Path) -> list[Path]:
    return [root] if root.is_file() else sorted(root.rglob("*.yaml"))


def list_drafts(root: Path) -> list[DraftRow]:
    """Describe every scenario file under `root`.

    Args:
        root: A directory (searched recursively for *.yaml) or one file.

    Returns:
        One row per file in path order. A file that cannot be loaded gets status "unreadable".
    """
    rows = []
    for path in _yaml_files(root):
        try:
            s = load_scenario(path)
        except (ScenarioLoadError, UnicodeDecodeError, OSError):
            rows.append(DraftRow(path.stem, "?", path, UNREADABLE, None, ["load_error"]))
            continue
        warnings = [i.code for i in validate_scenario(s)]
        rows.append(DraftRow(s.id, s.family, path, s.review.status, s.provenance.generated_at, warnings))
    return rows


def find_draft(root: Path, scenario_id: str) -> list[Path]:
    """Find the files under `root` that hold the scenario `scenario_id`.

    Args:
        root: A directory or one file.
        scenario_id: The id inside the file (not the file name).

    Returns:
        The matching files; unreadable files are skipped. Empty when there is none, and more than
        one when the id is duplicated.
    """
    return [r.path for r in list_drafts(root) if r.id == scenario_id and r.status != UNREADABLE]


# ---- edit and set -------------------------------------------------------------------------------


def edit_draft(path: Path, editor: str, cfg: EvalConfig, gen: GenConfig) -> EditResult:
    """Let the reviewer edit a draft in `editor`; keep the edit only if it leaves the labels alone.

    The editor works on a copy of the file, so the original stays untouched unless the edit is
    accepted. The edit is thrown away when the editor fails, when the result is not a valid
    scenario, when a ground-truth label moved (`changed_label_fields`; the review status and
    reviewer only change through `set_status`). Otherwise the draft is saved with
    `provenance.reviewer_edited` set and checked again. A saved edit of an approved or rejected
    file puts it back to draft (reviewer cleared, notes kept): the approval was for the old
    content.

    Args:
        path: The scenario file; it must load.
        editor: Editor command line, split like a shell would; the file path is appended.
        cfg: Eval config for the discrimination check.
        gen: Generation config for the body checks.

    Returns:
        What happened. After "saved", `issues` holds every finding, including errors: a saved
        draft is not necessarily approvable.

    Raises:
        ScenarioLoadError: If `path` does not load before the edit.
    """
    original = load_scenario(path)
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / path.name
        copy.write_bytes(path.read_bytes())
        try:
            done = subprocess.run([*shlex.split(editor), str(copy)], check=False)
        except (OSError, ValueError) as e:
            return EditResult("reverted", reason=f"cannot run editor {editor!r}: {e}")
        if done.returncode != 0:
            return EditResult("reverted", reason=f"editor exited with code {done.returncode}")
        try:
            edited = load_scenario(copy)
        except ScenarioLoadError as e:
            return EditResult("reverted", reason=f"the file no longer loads: {e.message}")
    if edited.model_dump(mode="json") == original.model_dump(mode="json"):
        return EditResult("unchanged")
    if (edited.review.status, edited.review.reviewer) != (original.review.status, original.review.reviewer):
        return EditResult("reverted", reason="review status and reviewer change only with `harness review set`")
    if changed := changed_label_fields(original, edited):
        return EditResult(
            "reverted",
            changed_fields=changed,
            reason="ground-truth fields cannot be edited; if the labels are wrong, reject the draft",
        )
    edited.provenance.reviewer_edited = True
    reset = edited.review.status != "draft"
    if reset:
        edited.review = Review(status="draft", reviewer=None, notes=edited.review.notes)
    save_draft(edited, path)
    return EditResult("saved", issues=check_draft(edited, cfg, gen, secret_events(edited)), reset_to_draft=reset)


def set_status(
    path: Path,
    status: Literal["approved", "rejected"],
    reviewer: str,
    note: str | None,
    cfg: EvalConfig,
    gen: GenConfig,
) -> list[Issue]:
    """Approve or reject a draft.

    Approval needs `check_draft` to find no error and no `too_easy` warning; otherwise the file
    is left as it is. Rejection always works.

    Args:
        path: The scenario file.
        status: The new review status.
        reviewer: Name recorded in `review.reviewer`.
        note: Text for `review.notes`; None keeps the existing notes.
        cfg: Eval config for the discrimination check.
        gen: Generation config for the body checks.

    Returns:
        The findings of `check_draft` for an approval (when `blocking` finds any, nothing was
        saved), empty for a rejection.

    Raises:
        ScenarioLoadError: If `path` does not load.
    """
    s = load_scenario(path)
    issues: list[Issue] = []
    if status == "approved":
        issues = check_draft(s, cfg, gen, secret_events(s))
        if blocking(issues):
            return issues
    s.review = Review(status=status, reviewer=reviewer, notes=s.review.notes if note is None else note)
    save_draft(s, path)
    return issues


# ---- show ---------------------------------------------------------------------------------------

_LEVEL_STYLE = {"error": "bold red", "warning": "yellow"}
_OUTCOME_STYLE = {"TP": "green", "FP": "red", "BLOCKED": "cyan", "LEAK_ONLY": "magenta"}


def _clock(t: datetime | None, tz: ZoneInfo | None) -> str:
    if t is None:
        return "-"
    return (t.astimezone(tz) if tz else t).strftime("%Y-%m-%d %H:%M")


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return None


def _header(s: Scenario, issues: Sequence[Issue], tz: ZoneInfo | None) -> RenderableType:
    p = s.provenance
    repos = ", ".join(f"{r.repo} ({r.license})" for r in p.source_repos) or "-"
    model = " / ".join(x for x in (p.provider, p.llm_model) if x) or "-"
    reviewer = f" by {s.review.reviewer}" if s.review.reviewer else ""
    rows = [
        ("id", s.id),
        ("family", f"{s.family}  mode={s.mode}  language={s.language}"),
        ("status", f"{s.review.status}{reviewer}" + (f"  notes: {s.review.notes}" if s.review.notes else "")),
        ("author", f"{p.author}  reviewer_edited={p.reviewer_edited}"),
        ("source repos", repos),
        ("source traces", ", ".join(p.source_traces) or "-"),
        ("model", f"{model}  prompt={p.prompt_version or '-'}  generated={_clock(p.generated_at, tz)}"),
    ]
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(Text(key, style="bold"), Text(value))
    parts: list[RenderableType] = [table]
    if issues:
        found = Table.grid(padding=(0, 2))
        for i in issues:
            found.add_row(Text(i.level, style=_LEVEL_STYLE[i.level]), Text(i.code), Text(i.message))
        parts += [Text("automatic checks", style="bold"), found]
    else:
        parts.append(Text("automatic checks: no findings", style="green"))
    return Panel(Group(*parts), title="draft", title_align="left")


def _label_table(s: Scenario, tz: ZoneInfo | None) -> RenderableType:
    table = Table(title="labels (facts)", title_justify="left", show_lines=True)
    for name in ("fact", "valid_from", "valid_until", "conditions", "valid", "fix", "source"):
        table.add_column(name, no_wrap=True)
    table.add_column("markers")
    table.add_column("text")
    for fid, f in s.facts.items():
        table.add_row(
            Text(fid),
            Text(_clock(f.valid_from, tz)),
            Text(_clock(f.valid_until, tz)),
            Text(", ".join(f"{k}={v}" for k, v in f.conditions.items()) or "-"),
            Text(str(f.valid).lower(), style="" if f.valid else "red"),
            Text(f.fix or "-"),
            Text("fabricated" if fid in s.provenance.fabricated_facts else "-"),
            Text(", ".join(f.markers) or "-"),
            Text(f.text or "(empty)"),
        )
    return table


def _timeline(s: Scenario, tz: ZoneInfo | None) -> RenderableType:
    table = Table(title="timeline (events and probes in time order)", title_justify="left", show_lines=True)
    table.add_column("time", no_wrap=True)
    table.add_column("id", no_wrap=True)
    table.add_column("user", no_wrap=True)
    table.add_column("what")
    table.add_column("fact / about")
    table.add_column("refs / true now")
    table.add_column("text")
    evidence = {ev.event_id: ev for ev in s.provenance.evidence}
    # Events come before probes at the same time, as in the replay.
    rows = [(e.t, 0, n) for n, e in enumerate(s.events)] + [(p.t, 1, n) for n, p in enumerate(s.probes)]
    for t, is_probe, n in sorted(rows):
        if not is_probe:
            e = s.events[n]
            body = Text(e.text or "(empty)")
            if e.secret_markers:
                body.append(f"\nsecret: {', '.join(e.secret_markers)}", style="magenta")
            if (ev := evidence.get(e.id)) is not None:
                body.append(f"\n[{ev.origin}]", style="dim")
                if ev.quote:
                    body.append(f" {ev.quote}", style="dim")
            table.add_row(
                Text(_clock(t, tz)),
                Text(e.id),
                Text(e.user),
                Text(f"{e.scope} {e.signal}/{e.kind}/{e.outcome}"),
                Text(e.fact or "-"),
                Text(", ".join(e.refs) or "-"),
                body,
            )
        else:
            p = s.probes[n]
            true_now = [fid for fid, f in s.facts.items() if true_at(f, p.t, probe_ctx(p))]
            table.add_row(
                Text(_clock(t, tz), style="bold"),
                Text(p.id, style="bold"),
                Text(p.user, style="bold"),
                Text(f"PROBE {p.kind}", style="bold"),
                Text(", ".join(p.about) or "-"),
                Text("true: " + (", ".join(true_now) or "-")),
                Text(p.query or "(empty)", style="bold"),
            )
    return table


def _cell(run: ScenarioRun, probe_id: str) -> Text:
    if run.error is not None:
        return Text("error", style="red")
    rec = next((r for r in run.probes if r.probe_id == probe_id), None)
    if rec is None:
        return Text("-")
    cell = Text(rec.outcome, style=_OUTCOME_STYLE.get(rec.outcome, ""))
    cell.append(f"  leaks {len(rec.leaks)}", style="red" if rec.leaks else "dim")
    return cell


def _baselines(s: Scenario, cfg: EvalConfig) -> RenderableType:
    names = ["oracle", "all_store", *(["all_store_noacl"] if any(p.kind == "leak" for p in s.probes) else [])]
    runs = {n: run_scenario(make_engine(n, s, cfg), s, run_id=_RUN_ID, cfg=cfg) for n in names}
    table = Table(title="baseline preview (outcome and leak count per probe)", title_justify="left")
    table.add_column("probe", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    for n in names:
        table.add_column(n, no_wrap=True)
    for p in s.probes:
        table.add_row(Text(p.id), Text(p.kind), *(_cell(runs[n], p.id) for n in names))
    notes = [Text(f"{n}: {r.error}", style="red") for n, r in runs.items() if r.error is not None]
    return Group(table, *notes)


def render_show(s: Scenario, cfg: EvalConfig, gen: GenConfig | None = None) -> RenderableType:
    """Draw one scenario for review: header, label table, timeline and baseline preview.

    Needs nothing but the scenario itself (no trace cache, no rejection log).

    Args:
        s: The scenario.
        cfg: Eval config for the baselines.
        gen: Generation config; with it the header lists every automatic finding of
            `check_draft`, without it only the structural and discrimination findings.

    Returns:
        A rich renderable. A scenario with structural errors shows the findings instead of the
        baseline preview.
    """
    tz = _zone(s.world.timezone)
    broken = any(i.level == "error" for i in validate_scenario(s))
    if gen is not None:
        issues = check_draft(s, cfg, gen, secret_events(s))
    else:
        issues = validate_scenario(s)
        if not broken:
            issues += check_discrimination(s, cfg)
    parts = [_header(s, issues, tz), _label_table(s, tz), _timeline(s, tz)]
    parts.append(Text("baseline preview skipped: fix the errors above", style="red") if broken else _baselines(s, cfg))
    return Group(*parts)

