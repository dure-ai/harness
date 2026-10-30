"""Open-SWE-Traces trajectories: read, filter, bundle per task and compress for prompting.

Needs `pyarrow` (extra `gen`), imported inside `read_parquet` only.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness.generate.config import GenConfig

_HEAD_CHARS = 2000  # first system / first user message
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
    elif isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            # String values are written as they are, so a command reads as the agent typed it.
            args = ", ".join(
                f"{k}={v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                for k, v in parsed.items()
            )
    else:
        args = json.dumps(args, ensure_ascii=False)
    return f"{fn.get('name') or ''}({args})"


def _tool_content(content: str) -> str:
    """Unwrap a tool message that is a JSON envelope; any other content is returned as it is."""
    try:
        env = json.loads(content)
    except ValueError:
        return content
    if not isinstance(env, dict) or not isinstance(env.get("output"), str):
        return content
    code = env.get("returncode")
    if code is None or (not isinstance(code, bool) and code in (0, "0")):
        return env["output"]
    return f"{env['output']}\nreturncode: {code}"


def _to_trace(row: dict[str, Any]) -> Trace:
    messages = [
        TraceMessage(
            index=i,
            role=m.get("role") or "",
            content=(
                _tool_content(m.get("content") or "")
                if m.get("role") == "tool"
                else m.get("content") or ""
            ),
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

    for batch in pq.ParquetFile(path).iter_batches(
        batch_size=_BATCH_ROWS, columns=_COLUMNS
    ):
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
    return (
        text if len(text) <= limit else text[:limit] + f"…[{len(text) - limit} chars]…"
    )


def _middle_cut(text: str) -> str:
    if len(text) <= 2 * _TOOL_KEEP:
        return text
    cut = len(text) - 2 * _TOOL_KEEP
    return text[:_TOOL_KEEP] + f"…[{cut} chars]…" + text[-_TOOL_KEEP:]


def _render(m: TraceMessage, clip_content: bool) -> str:
    if m.role == "tool":
        body = _middle_cut(m.content)
    else:
        body = _clip(m.content, _HEAD_CHARS) if clip_content else m.content
    parts = [f"[#{m.index} {m.role}] {body}".rstrip()]
    if m.tool_calls:
        parts.append(_clip("\n".join(m.tool_calls), _ARGS_CHARS))
    return "\n".join(parts)


def compress(trace: Trace, budget: int = 15000) -> str:
    """Shrink a trajectory to fit a prompt, keeping the beginning and cutting the end.

    The first system and the first user message are always kept, each clipped to 2,000 chars.
    The other messages are added in original order while they fit in `budget`: assistant content
    is kept whole, tool-call strings are clipped to 1,000 chars per message, and tool output keeps
    its first 300 and last 300 chars. The first message that does not fit is truncated to the
    remaining budget, and one marker `[… #a-#b omitted …]` stands for everything after it, so no
    later message is kept. The marker and its separator are not counted in `budget`. `reasoning_content` is not part
    of `Trace` and never appears. Every message starts with `[#<original index> <role>]`.

    Args:
        trace: Trajectory to compress.
        budget: Target length in characters, excluding the omission marker. The two always-kept
            messages are emitted even if they alone exceed it.

    Returns:
        The compressed text.
    """
    msgs = trace.messages
    head: dict[int, str] = {}
    for role in ("system", "user"):
        pos = next((i for i, m in enumerate(msgs) if m.role == role), None)
        if pos is not None:
            head[pos] = _render(msgs[pos], clip_content=True)
    used = sum(len(t) for t in head.values()) + 2 * max(len(head) - 1, 0)

    kept: dict[int, str] = dict(head)
    marker = ""
    rest = [i for i in range(len(msgs)) if i not in head]
    for n, i in enumerate(rest):
        text = _render(msgs[i], clip_content=False)
        cost = len(text) + (2 if kept else 0)
        if used + cost <= budget:
            kept[i] = text
            used += cost
            continue
        room = budget - used - (2 if kept else 0)
        if room > 0:
            kept[i] = text[:room]
            n += 1
        tail = rest[n:]
        if tail:
            marker = f"[… #{msgs[tail[0]].index}-#{msgs[tail[-1]].index} omitted …]"
        break
    parts = [kept[i] for i in sorted(kept)]
    if marker:
        parts.append(marker)
    return "\n\n".join(parts)


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
