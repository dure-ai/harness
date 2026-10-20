from pathlib import Path

from harness.generate.config import load_gen_config
from harness.generate.traces import (
    TraceMessage,
    bundles,
    compress,
    message_text,
    read_parquet,
    usable,
)
from tests.gen_fixtures import make_parquet, make_trace

CFG = load_gen_config(Path("config/gen-v0.1.yaml"))


def test_read_parquet_maps_fields(tmp_path):
    a = make_trace(trajectory_id="t1", model="model-a")
    b = make_trace(
        trajectory_id="t2",
        model="model-b",
        license=None,
        messages=[
            TraceMessage(index=0, role="user", content="hi"),
            TraceMessage(index=1, role="assistant", content="", tool_calls=['edit({"p": 1})']),
        ],
    )
    c = make_trace(trajectory_id="t3")
    path = tmp_path / "x.parquet"
    make_parquet(path, [a, b, c])
    got = list(read_parquet(path))
    assert [t.trajectory_id for t in got] == ["t1", "t2", "t3"]
    assert got[0] == a
    assert got[1].model == "model-b"
    assert got[1].license is None
    assert [m.index for m in got[0].messages] == [0, 1, 2, 3]
    assert got[1].messages[1].content == ""
    assert got[1].messages[1].tool_calls == ['edit({"p": 1})']


def test_usable_filters_language_and_license():
    assert usable(make_trace(), CFG)
    assert not usable(make_trace(language="go"), CFG)
    assert not usable(make_trace(license="GPL-3.0"), CFG)
    assert not usable(make_trace(license=None), CFG)


def test_bundles_group_by_instance_distinct_models():
    ts = [
        make_trace(trajectory_id="t1", instance_id="i2", model="a"),
        make_trace(trajectory_id="t2", instance_id="i1", model="a"),
        make_trace(trajectory_id="t3", instance_id="i1", model="b"),
        make_trace(trajectory_id="t4", instance_id="i1", model="a"),
    ]
    got = bundles(ts)
    assert list(got) == ["i1", "i2"]
    assert [t.trajectory_id for t in got["i1"]] == ["t2", "t3"]
    assert [t.trajectory_id for t in got["i2"]] == ["t1"]


def _long(role, index, content, **kw):
    return TraceMessage(index=index, role=role, content=content, **kw)


def test_compress_keeps_original_indices():
    t = make_trace(
        messages=[_long("user", 5, "go"), _long("assistant", 7, "ok"), _long("tool", 9, "out")]
    )
    out = compress(t)
    assert "[#5 user]" in out and "[#7 assistant]" in out and "[#9 tool]" in out


def test_compress_truncates_tool_output_middle():
    body = "A" * 300 + "M" * 1400 + "Z" * 300
    t = make_trace(messages=[_long("tool", 3, body)])
    out = compress(t)
    assert "A" * 300 + "…[1400 chars]…" + "Z" * 300 in out
    assert "M" not in out


def test_compress_truncates_first_system_user_and_tool_call_args():
    t = make_trace(
        messages=[
            _long("system", 0, "S" * 3000),
            _long("user", 1, "U" * 3000),
            _long("assistant", 2, "", tool_calls=["bash(" + "x" * 3000 + ")"]),
        ]
    )
    out = compress(t)
    assert "S" * 2000 in out and "S" * 2001 not in out
    assert "U" * 2000 in out and "U" * 2001 not in out
    assert "x" * 990 in out and "x" * 1001 not in out  # "bash(" counts toward the 1,000


def test_compress_excludes_reasoning(tmp_path):
    path = tmp_path / "x.parquet"
    make_parquet(path, [make_trace()])
    (t,) = read_parquet(path)
    assert "hidden reasoning" not in compress(t)


def test_compress_respects_budget():
    msgs = [_long("system", 0, "s"), _long("user", 1, "u")]
    for i in range(2, 200):
        msgs.append(_long("assistant" if i % 2 == 0 else "tool", i, f"m{i} " + "w" * 500))
    out = compress(make_trace(messages=msgs), budget=5000)
    assert len(out) <= 5000 + 200
    assert "[#0 system]" in out and "[#1 user]" in out and "[#199 tool]" in out
    assert "omitted" in out


def test_message_text_includes_tool_call_args():
    t = make_trace(
        messages=[_long("assistant", 4, "see", tool_calls=['bash({"cmd": "pytest -x"})'])]
    )
    text = message_text(t, 4)
    assert "see" in text and "pytest -x" in text
    assert message_text(t, 99) == ""
