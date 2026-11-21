"""Noise texts for stress variants: unrelated agent messages from Open-SWE-Traces, or a text file."""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from harness.generate.config import GenConfig
from harness.scenario.model import Scenario
from harness.text import secret_forms, secret_needle

MIN_CHARS = 60
MAX_CHARS = 400  # dev event texts are at most about 400 characters (median about 200)
DEFAULT_POOL = 20000


@dataclass(frozen=True)
class NoiseText:
    """One candidate noise text and where it came from.

    Attributes:
        trace_id: Trajectory id, or `file:<line>` for a text file.
        text: The text, whitespace collapsed and at most `MAX_CHARS` long.
        repo: Repository of the trajectory ("owner/name"); None for a text file.
    """

    trace_id: str
    text: str
    repo: str | None = None


def clean(text: str) -> str | None:
    """`text` on one line and cut at a word boundary to `MAX_CHARS`; None when shorter than `MIN_CHARS`."""
    line = " ".join(text.split())
    if len(line) < MIN_CHARS:
        return None
    if len(line) > MAX_CHARS:
        cut = line[:MAX_CHARS]
        space = cut.rfind(" ")
        line = cut[:space] if space > MIN_CHARS else cut
    return line


def _same_repo(repo: str | None, repos: set[str]) -> bool:
    """Whether `repo` is one of `repos` (casefolded), compared as "owner/name" or by the short name."""
    if repo is None:
        return False
    folded = repo.casefold()
    return folded in repos or folded.rsplit("/", 1)[-1] in repos


class NoisePool:
    """Noise texts with the forms the per-scenario filter compares, computed once.

    Attributes:
        texts: The texts, in pool order.
        sha256: Fingerprint of the pool (texts, trajectories and repositories, in order),
            recorded in every variant drawn from it.
    """

    def __init__(self, texts: Sequence[NoiseText]) -> None:
        self.texts: tuple[NoiseText, ...] = tuple(texts)
        self._folded = [t.text.casefold() for t in self.texts]
        self._forms = [secret_forms(t.text) for t in self.texts]
        payload = json.dumps([[t.trace_id, t.repo, t.text] for t in self.texts], ensure_ascii=False)
        self.sha256: str = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._usable: dict[str, list[NoiseText]] = {}

    def __len__(self) -> int:
        return len(self.texts)

    def usable_for(self, s: Scenario) -> list[NoiseText]:
        """The texts that can be noise for `s`, in pool order (cached per scenario id).

        A usable text states none of the scenario's facts (by marker), holds none of its secrets
        (by the `contains_secret` rule), and comes neither from a trajectory the scenario was made
        from nor from one of its repositories: another model's trajectory of the same task can
        restate a fact without its marker strings.
        """
        if s.id in self._usable:
            return self._usable[s.id]
        markers = [m.casefold() for f in s.facts.values() for m in f.markers if m.strip()]
        needles = [n for e in s.events for m in e.secret_markers if (n := secret_needle(m)) is not None]
        own = set(s.provenance.source_traces)
        repos = {r.repo.casefold() for r in s.provenance.source_repos} | {e.repo.casefold() for e in s.events}
        repos |= {r.rsplit("/", 1)[-1] for r in repos}
        usable = [
            t
            for t, folded, (plain, normalized) in zip(self.texts, self._folded, self._forms, strict=True)
            if t.trace_id not in own
            and not _same_repo(t.repo, repos)
            and not any(m in folded for m in markers)
            and not any(needle in (plain if kind == "plain" else normalized) for kind, needle in needles)
        ]
        self._usable[s.id] = usable
        return usable


def trace_pool(paths: Iterable[Path], gen: GenConfig, limit: int = DEFAULT_POOL) -> list[NoiseText]:
    """Assistant messages of usable trajectories (python, allowed license), in file order.

    Needs `pyarrow` (extra `gen`). Duplicate texts are kept once.

    Args:
        paths: Open-SWE-Traces parquet shards.
        gen: Generation config holding the license allowlist, see `generate.traces.usable`.
        limit: Stop after this many texts.
    """
    from harness.generate.traces import read_parquet, usable

    out: list[NoiseText] = []
    seen: set[str] = set()
    for path in paths:
        for trace in read_parquet(path):
            if not usable(trace, gen):
                continue
            for message in trace.messages:
                text = clean(message.content) if message.role == "assistant" else None
                if text is None or text in seen:
                    continue
                seen.add(text)
                out.append(NoiseText(trace.trajectory_id, text, trace.repo))
                if len(out) >= limit:
                    return out
    return out


def file_pool(path: Path) -> list[NoiseText]:
    """One noise text per line of a UTF-8 file, for offline runs and tests; short lines are skipped."""
    out: list[NoiseText] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = clean(line)
        if text is not None:
            out.append(NoiseText(f"file:{number}", text))
    return out
