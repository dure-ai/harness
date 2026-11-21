"""Noise texts for stress variants: unrelated agent messages from Open-SWE-Traces, or a text file."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from harness.generate.config import GenConfig

MIN_CHARS = 60
MAX_CHARS = 400  # dev event texts are at most about 400 characters (median about 200)


@dataclass(frozen=True)
class NoiseText:
    """One candidate noise text and the trajectory it came from.

    Attributes:
        trace_id: Trajectory id, or `file:<line>` for a text file.
        text: The text, whitespace collapsed and at most `MAX_CHARS` long.
    """

    trace_id: str
    text: str


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


def trace_pool(paths: Iterable[Path], gen: GenConfig, limit: int) -> list[NoiseText]:
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
                out.append(NoiseText(trace.trajectory_id, text))
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
