"""Read scenario YAML files from disk and select the set to evaluate."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from harness.scenario.labels import approval_state
from harness.scenario.model import Scenario
from harness.scenario.validate import Issue
from harness.yamlio import load_yaml

APPROVAL_CHANGED = "approved_content_changed"
APPROVAL_UNVERIFIED = "approval_unverified"
APPROVAL_MISSING = "approved_hash_missing"


class ScenarioLoadError(Exception):
    """A scenario file could not be read or validated.

    Attributes:
        path: The offending file; its name prefixes the message.
        message: What went wrong.
        code: `load` for a file that does not read or validate, `approved_content_changed` for
            an approved file edited after its approval, `approved_hash_missing` for an approved
            file made by the current tooling that has no approval hash (`load_set`).
    """

    def __init__(self, path: Path, message: str, code: str = "load"):
        super().__init__(f"{path.name}: {message}")
        self.path = path
        self.message = message
        self.code = code


@dataclass
class ScenarioSet:
    """The scenarios selected for one run.

    Attributes:
        scenarios: Loaded scenarios in file order.
        paths: Their files, in the same order.
        hash: SHA-256 over (relative path, content hash) of the included files, recorded in
            reports so a result can be tied to the exact scenario set.
        warnings: One line per included file that needs attention but does not stop a run,
            `"<path>: warning <code> <message>"`; for now only `approval_unverified`. They name
            files, so a sealed run prints only their number.
    """

    scenarios: list[Scenario]
    paths: list[Path]
    hash: str
    warnings: list[str] = field(default_factory=list)


def read_scenario(path: Path) -> tuple[Mapping[str, Any], Scenario]:
    """Read and validate one scenario file, keeping the parsed YAML as well.

    Args:
        path: YAML file to read.

    Returns:
        The parsed document (what `content_hash` hashes) and the validated scenario.

    Raises:
        ScenarioLoadError: As `load_scenario`.
    """
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ScenarioLoadError(path, str(e)) from e
    try:
        s = Scenario.model_validate(data)
    except ValidationError as e:
        raise ScenarioLoadError(path, str(e)) from e
    return data, s


def load_scenario(path: Path) -> Scenario:
    """Read and validate one scenario file.

    Args:
        path: YAML file to read.

    Returns:
        The validated scenario.

    Raises:
        ScenarioLoadError: If the file cannot be read, is not valid YAML (including a repeated key), or fails validation
            (including unknown keys and times without a timezone).
    """
    return read_scenario(path)[1]


def approval_issue(raw: Mapping[str, Any], s: Scenario) -> Issue | None:
    """Check that an approved scenario is still the content that was approved.

    The hash is in the file, so this catches an edit made after approval by mistake, not one
    that rewrites the hash on purpose; git history is the record for that.

    Args:
        raw: The parsed file (`read_scenario`).
        s: The scenario.

    Returns:
        None for a scenario that is not approved or whose `review.approved_hash` matches; an
        error `approved_content_changed` when the content changed after approval; a warning
        `approval_unverified` when there is no hash in an older file (approved before it existed)
        or its version is unknown; an error `approved_hash_missing` when there is none in a file
        that carries `provenance.generated_hash` or `provenance.label_fingerprint` (written by
        the current tooling, which records a hash on every approval: the status was set by hand).
    """
    if s.review.status != "approved":
        return None
    state = approval_state(raw, s)
    if state == "changed":
        return Issue(
            "error",
            APPROVAL_CHANGED,
            "the file was edited after it was approved; review it and approve it again (harness review set)",
        )
    if state == "missing":
        return Issue(
            "error",
            APPROVAL_MISSING,
            "marked approved by hand: the file was generated with the review tooling but has no "
            "review.approved_hash; review it and approve it with harness review set",
        )
    if state == "unverified":
        return Issue(
            "warning",
            APPROVAL_UNVERIFIED,
            "approved without a recorded content hash, so later edits cannot be detected; "
            "approve it again with harness review set to record one",
        )
    return None


def load_set(roots: list[Path], include_drafts: bool) -> ScenarioSet:
    """Load every scenario under `roots` that is eligible for evaluation.

    Rejected scenarios are always skipped; drafts only with `include_drafts`. An approved file is
    checked against the hash recorded at approval (`approval_issue`).

    Args:
        roots: Directories (searched recursively for *.yaml) or single files.
        include_drafts: Whether draft scenarios are included.

    Returns:
        The selected scenarios with their set hash, and an `approval_unverified` warning for each
        older approved file without a usable hash.

    Raises:
        ScenarioLoadError: If any file fails to load (code `load`), an approved file changed
            after its approval (code `approved_content_changed`), or an approved file made by
            the current tooling has no approval hash (code `approved_hash_missing`).
    """
    included = {"approved", "draft"} if include_drafts else {"approved"}
    scenarios: list[Scenario] = []
    paths: list[Path] = []
    entries: list[tuple[str, str]] = []
    warnings: list[str] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.yaml"))
        for path in files:
            raw, scenario = read_scenario(path)
            if scenario.review.status not in included:
                continue
            issue = approval_issue(raw, scenario)
            if issue is not None and issue.level == "error":
                raise ScenarioLoadError(path, issue.message, code=issue.code)
            if issue is not None:
                warnings.append(f"{path}: {issue.level} {issue.code} {issue.message}")
            scenarios.append(scenario)
            paths.append(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((path.name if root.is_file() else path.relative_to(root).as_posix(), digest))
    joined = "".join(rel + digest for rel, digest in sorted(entries))
    return ScenarioSet(scenarios, paths, hashlib.sha256(joined.encode()).hexdigest(), warnings)
