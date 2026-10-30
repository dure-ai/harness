"""Fixtures for generation tests: trace builders and a small parquet in the Open-SWE-Traces layout."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from harness.generate.traces import Trace, TraceMessage

_TOOL_CALL = pa.struct(
    [
        ("function", pa.struct([("name", pa.string()), ("arguments", pa.string())])),
        ("id", pa.string()),
        ("type", pa.string()),
    ]
)
_MESSAGE = pa.struct(
    [
        ("role", pa.string()),
        ("content", pa.string()),
        ("reasoning_content", pa.string()),
        ("tool_calls", pa.list_(_TOOL_CALL)),
    ]
)
_PATCH = pa.struct(
    [
        ("patch", pa.string()),
        ("num_modified_files", pa.int32()),
        ("num_modified_lines", pa.int32()),
    ]
)
_SCHEMA = pa.schema(
    [
        ("instance_id", pa.string()),
        ("repo", pa.string()),
        ("license", pa.string()),
        ("language", pa.string()),
        ("trajectory_id", pa.string()),
        ("messages", pa.list_(_MESSAGE)),
        ("tools", pa.list_(pa.string())),
        ("resolved", pa.int32()),
        (
            "metadata",
            pa.struct(
                [
                    ("category", pa.string()),
                    (
                        "teacher_model",
                        pa.struct(
                            [
                                ("name", pa.string()),
                                ("enable_thinking", pa.bool_()),
                                ("reasoning_effort", pa.string()),
                            ]
                        ),
                    ),
                    ("reference_patch", _PATCH),
                    ("model_patch", _PATCH),
                ]
            ),
        ),
        ("hf_dataset_name", pa.string()),
    ]
)


def make_trace(**kw) -> Trace:
    """Build a `Trace`; keyword arguments override the defaults."""
    defaults = dict(
        trajectory_id="traj-1",
        instance_id="acme__widget-1",
        repo="acme/widget",
        license="MIT",
        language="python",
        model="model-a",
        messages=[
            TraceMessage(index=0, role="system", content="You are an engineer."),
            TraceMessage(index=1, role="user", content="Fix the bug in parser.py."),
            TraceMessage(
                index=2,
                role="assistant",
                content="Looking at it.",
                tool_calls=['bash({"cmd": "ls"})'],
            ),
            TraceMessage(index=3, role="tool", content="parser.py"),
        ],
    )
    return Trace(**{**defaults, **kw})


def _split_call(call: str) -> tuple[str, str]:
    name, _, rest = call.partition("(")
    return name, rest.removesuffix(")")


def make_parquet(path: Path, traces: list[Trace]) -> None:
    """Write `traces` as a parquet file with the Open-SWE-Traces column layout.

    Content and reasoning_content are nullable in the real data: empty content is written as null.
    """
    patch = {"patch": "", "num_modified_files": 0, "num_modified_lines": 0}
    rows = []
    for t in traces:
        messages = [
            {
                "role": m.role,
                "content": m.content or None,
                "reasoning_content": "hidden reasoning",
                "tool_calls": [
                    {
                        "function": dict(zip(("name", "arguments"), _split_call(c), strict=True)),
                        "id": f"call-{m.index}-{i}",
                        "type": "function",
                    }
                    for i, c in enumerate(m.tool_calls)
                ],
            }
            for m in t.messages
        ]
        rows.append(
            {
                "instance_id": t.instance_id,
                "repo": t.repo,
                "license": t.license,
                "language": t.language,
                "trajectory_id": t.trajectory_id,
                "messages": messages,
                "tools": ["bash"],
                "resolved": 1,
                "metadata": {
                    "category": "bugfix",
                    "teacher_model": {
                        "name": t.model,
                        "enable_thinking": True,
                        "reasoning_effort": "high",
                    },
                    "reference_patch": patch,
                    "model_patch": patch,
                },
                "hf_dataset_name": "nvidia/Open-SWE-Traces",
            }
        )
    pq.write_table(pa.Table.from_pylist(rows, schema=_SCHEMA), path, row_group_size=2)
