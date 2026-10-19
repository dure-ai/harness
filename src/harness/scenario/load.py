"""Read scenario YAML files from disk and select the set to evaluate."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from harness.scenario.model import Scenario


class ScenarioLoadError(Exception):
    def __init__(self, path: Path, message: str):
        super().__init__(f"{path.name}: {message}")
        self.path = path
        self.message = message


@dataclass
class ScenarioSet:
    scenarios: list[Scenario]
    paths: list[Path]
    hash: str


def load_scenario(path: Path) -> Scenario:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ScenarioLoadError(path, str(e)) from e
    try:
        return Scenario.model_validate(data)
    except ValidationError as e:
        raise ScenarioLoadError(path, str(e)) from e


def load_set(roots: list[Path], include_drafts: bool) -> ScenarioSet:
    included = {"approved", "draft"} if include_drafts else {"approved"}
    scenarios: list[Scenario] = []
    paths: list[Path] = []
    entries: list[tuple[str, str]] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.yaml"))
        for path in files:
            scenario = load_scenario(path)
            if scenario.review.status not in included:
                continue
            scenarios.append(scenario)
            paths.append(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((path.name if root.is_file() else path.relative_to(root).as_posix(), digest))
    joined = "".join(rel + digest for rel, digest in sorted(entries))
    return ScenarioSet(scenarios, paths, hashlib.sha256(joined.encode()).hexdigest())
