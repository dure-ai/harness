"""Open-SWE-Traces trajectories: read, filter, bundle per task and compress for prompting.

Needs `pyarrow` (extra `gen`), imported inside `read_parquet` only.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness.generate.config import GenConfig

_HEAD_CHARS = 2000  # first system / first user / other free text of a message
_ARGS_CHARS = 1000  # all tool-call arguments of one message
_TOOL_KEEP = 300  # tool output: kept at each end
_BATCH_ROWS = 8

# Leaf columns only: skips `reasoning_content` and the (large) reference/model patches.
_COLUMNS = [
    "trajectory_id",
    "instance_id",
    "repo",
    "license",
    "language",
    "metadata.teacher_model.name",
    "messages.list.element.role",
    "messages.list.element.content",
    "messages.list.element.tool_calls",
]


class TraceMessage(BaseModel):
    """One message of a trajectory.

    Attributes:
        index: Position in the original trajectory, kept through compression.
        role: system, user, assistant or tool.
        content: Message text; empty when the source is null.
        tool_calls: One string per call, "name(arguments)".
    """

    model_config = ConfigDict(frozen=True)

    index: int
    role: str
    content: str = ""
    tool_calls: list[str] = []


class Trace(BaseModel):
    """One agent trajectory.

    Attributes:
        trajectory_id: Unique id of the trajectory.
        instance_id: Task the trajectory attempts; several models share one.
        repo: Source repository, "owner/name".
        license: SPDX id of the repository license, if known.
        language: Main language of the repository.
        model: Name of the model that produced the trajectory.
        messages: The conversation in order.
    """

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    instance_id: str
    repo: str
    license: str | None = None
    language: str | None = None
    model: str
    messages: list[TraceMessage]


def _call_text(call: dict[str, Any]) -> str:
    fn = call.get("function") or {}
    args = fn.get("arguments")
    if args is None:
        args = ""
    elif not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return f"{fn.get('name') or ''}({args})"


def _to_trace(row: dict[str, Any]) -> Trace:
    messages = [
        TraceMessage(
            index=i,
            role=m.get("role") or "",
            content=m.get("content") or "",
            tool_calls=[_call_text(c) for c in (m.get("tool_calls") or [])],
        )
        for i, m in enumerate(row.get("messages") or [])
    ]
    model = ((row.get("metadata") or {}).get("teacher_model") or {}).get("name") or ""
    return Trace(
        trajectory_id=row["trajectory_id"],
        instance_id=row["instance_id"],
        repo=row["repo"],
        license=row.get("license"),
        language=row.get("language"),
        model=model,
        messages=messages,
    )


def read_parquet(path: Path) -> Iterator[Trace]:
    """Read trajectories from a parquet file a few rows at a time.

    Args:
        path: Parquet file in the Open-SWE-Traces layout.

    Yields:
        One `Trace` per row, in file order. Null content becomes "".
    """
    import pyarrow.parquet as pq

    for batch in pq.ParquetFile(path).iter_batches(batch_size=_BATCH_ROWS, columns=_COLUMNS):
        for row in batch.to_pylist():
            yield _to_trace(row)


def usable(trace: Trace, cfg: GenConfig) -> bool:
    """Whether a trajectory may be used: a python repository with an allowed license.

    Args:
        trace: Trajectory to check.
        cfg: Generation config holding the license allowlist.

    Returns:
        True for python repos whose license is on the allowlist; a missing license is not allowed.
    """
    if (trace.language or "").lower() != "python":
        return False
    return trace.license is not None and trace.license in cfg.license_allowlist


def bundles(traces: Iterable[Trace]) -> dict[str, list[Trace]]:
    """Group trajectories by task, one per model.

    Args:
        traces: Trajectories in any order.

    Returns:
        `instance_id` -> trajectories from distinct models (the first of a repeated model wins),
        with keys and each list sorted by trajectory_id for a stable order.
    """
    out: dict[str, dict[str, Trace]] = {}
    for t in sorted(traces, key=lambda t: (t.instance_id, t.trajectory_id)):
        out.setdefault(t.instance_id, {}).setdefault(t.model, t)
    return {k: list(v.values()) for k, v in out.items()}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[{len(text) - limit} chars]…"


def _middle_cut(text: str) -> str:
    if len(text) <= 2 * _TOOL_KEEP:
        return text
    cut = len(text) - 2 * _TOOL_KEEP
    return text[:_TOOL_KEEP] + f"…[{cut} chars]…" + text[-_TOOL_KEEP:]


def _render(m: TraceMessage) -> str:
    body = _middle_cut(m.content) if m.role == "tool" else _clip(m.content, _HEAD_CHARS)
    parts = [f"[#{m.index} {m.role}] {body}".rstrip()]
    if m.tool_calls:
        parts.append(_clip("\n".join(m.tool_calls), _ARGS_CHARS))
    return "\n".join(parts)


def _omitted(messages: list[TraceMessage], dropped: list[int]) -> str:
    return f"[… #{messages[dropped[0]].index}-#{messages[dropped[-1]].index} omitted …]"


def _join(messages: list[TraceMessage], rendered: list[str], keep: list[bool]) -> str:
    parts: list[str] = []
    gap: list[int] = []
    for i, k in enumerate(keep):
        if k:
            if gap:
                parts.append(_omitted(messages, gap))
                gap = []
            parts.append(rendered[i])
        else:
            gap.append(i)
    if gap:
        parts.append(_omitted(messages, gap))
    return "\n\n".join(parts)


def compress(trace: Trace, budget: int = 15000) -> str:
    """Shrink a trajectory to fit a prompt.

    Text fields are clipped (2,000 chars per message, 1,000 chars of tool-call arguments per message,
    tool output keeps its first and last 300 chars). If the result is still over `budget`, messages
    are dropped from the middle outward, keeping the opening and the end. `reasoning_content` is not
    part of `Trace` and never appears. Every message starts with `[#<original index> <role>]`.

    Args:
        trace: Trajectory to compress.
        budget: Target length in characters; omission markers may exceed it slightly.

    Returns:
        The compressed text.
    """
    msgs = trace.messages
    rendered = [_render(m) for m in msgs]
    keep = [True] * len(msgs)
    # The first two messages (system prompt and task) are never dropped.
    remaining = list(range(2, len(msgs)))
    while remaining and len(_join(msgs, rendered, keep)) > budget:
        keep[remaining.pop(len(remaining) // 2)] = False
    return _join(msgs, rendered, keep)


def message_text(trace: Trace, index: int) -> str:
    """Original text of one message, for checking that a quote really appears in it.

    Args:
        trace: Trajectory holding the message.
        index: Original message number (`TraceMessage.index`).

    Returns:
        The content followed by each tool call string, or "" if there is no such message.
    """
    for m in trace.messages:
        if m.index == index:
            return "\n".join([m.content, *m.tool_calls]).strip()
    return ""
