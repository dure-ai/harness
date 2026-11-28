from pathlib import Path

import pytest

from harness.generate.config import GenConfig, load_gen_config
from harness.generate.traces import TraceMessage, read_parquet, usable
from harness.stress.noise import NoiseText, clean, trace_pool
from tests.gen_fixtures import make_parquet, make_trace

pytest.importorskip("pyarrow")

LONG = "I looked at the parser module and the failing test, then decided to change the tokenizer loop "


def reference_pool(paths: list[Path], gen: GenConfig, limit: int) -> list[NoiseText]:
    """What the pool was before it read columns itself: assistant messages through read_parquet."""
    out: list[NoiseText] = []
    seen: set[str] = set()
    for path in paths:
        for trace in read_parquet(path):
            if not usable(trace, gen):
                continue
            for m in trace.messages:
                text = clean(m.content) if m.role == "assistant" else None
                if text is not None and text not in seen:
                    seen.add(text)
                    out.append(NoiseText(trace.trajectory_id, text, trace.repo))
                    if len(out) >= limit:
                        return out
    return out


def messages(n: int) -> list[TraceMessage]:
    out = [TraceMessage(index=0, role="user", content="Fix the bug in parser.py, please, before the release.")]
    for i in range(1, n):
        role = "assistant" if i % 2 else "tool"
        out.append(TraceMessage(index=i, role=role, content=f"{LONG}step {i}." if i != 5 else ""))
    out.append(TraceMessage(index=n, role="assistant", content=f"{LONG}step 1."))  # a duplicate
    return out


def test_trace_pool_reads_what_read_parquet_gives(tmp_path):
    traces = [
        make_trace(trajectory_id="a", messages=messages(9)),
        make_trace(trajectory_id="b", license="GPL-3.0", messages=messages(5)),  # not allowed
        make_trace(trajectory_id="c", language="rust", messages=messages(5)),  # not python
        make_trace(trajectory_id="d", repo="other/repo", messages=messages(13)),
    ]
    path = tmp_path / "shard.parquet"
    make_parquet(path, traces)
    gen = load_gen_config(Path("config/gen-v0.1.yaml"))
    for limit in (1, 3, 100):
        assert trace_pool([path], gen, limit) == reference_pool([path], gen, limit)
    pool = trace_pool([path], gen, 100)
    assert {t.trace_id for t in pool} == {"a", "d"} and {t.repo for t in pool} == {"acme/widget", "other/repo"}
