import json
import re

from pathlib import Path

import pytest

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
    assert got[0].messages[2].tool_calls == ["bash(cmd=ls)"]  # arguments are decoded on read
    assert got[0].model_copy(update={"messages": a.messages}) == a
    assert got[1].model == "model-b"
    assert got[1].license is None
    assert [m.index for m in got[0].messages] == [0, 1, 2, 3]
    assert got[1].messages[1].content == ""
    assert got[1].messages[1].tool_calls == ["edit(p=1)"]


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


def _numbered(n):
    msgs = [_long("system", 0, "s"), _long("user", 1, "u")]
    for i in range(2, n):
        msgs.append(_long("assistant" if i % 2 == 0 else "tool", i, f"m{i} " + "w" * 500))
    return msgs


def test_compress_keeps_early_messages_and_omits_the_rest_with_one_marker():
    out = compress(make_trace(messages=_numbered(200)), budget=5000)
    marker = re.findall(r"\[… #(\d+)-#(\d+) omitted …\]", out)
    assert len(marker) == 1
    a, b = map(int, marker[0])
    assert b == 199 and a > 2
    marker_text = f"[… #{a}-#{b} omitted …]"
    assert len(out) <= 5000 + 2 + len(marker_text)  # 2 = separator before the marker
    assert out.endswith(marker_text)
    assert "[#0 system]" in out and "[#1 user]" in out and "[#2 assistant]" in out
    assert "[#199 tool]" not in out and f"[#{a} " not in out
    assert f"[#{a - 1} " in out  # the message that did not fit is truncated, not dropped
    kept = [int(i) for i in re.findall(r"\[#(\d+) ", out)]
    assert kept == list(range(a))


def test_compress_has_no_marker_when_everything_fits():
    out = compress(make_trace(messages=_numbered(6)), budget=15000)
    assert "omitted" not in out and "[#5 tool]" in out


def test_compress_keeps_long_assistant_content_unclipped_within_budget():
    t = make_trace(
        messages=[
            _long("system", 0, "s"),
            _long("user", 1, "u"),
            _long("assistant", 2, "A" * 5000),
        ]
    )
    out = compress(t)
    assert "A" * 5000 in out and "omitted" not in out


def test_message_text_includes_tool_call_args():
    t = make_trace(
        messages=[_long("assistant", 4, "see", tool_calls=['bash({"cmd": "pytest -x"})'])]
    )
    text = message_text(t, 4)
    assert "see" in text and "pytest -x" in text
    assert message_text(t, 99) == ""


def _row(messages):
    return {"trajectory_id": "t", "instance_id": "i", "repo": "r/r", "messages": messages}


def _tool(content):
    return {"role": "tool", "content": content}


def test_tool_envelope_is_decoded_to_its_output():
    import json

    from harness.generate.traces import _to_trace

    env = json.dumps({"output": "line1\nit's \"x\"", "returncode": 0})
    t = _to_trace(_row([_tool(env)]))
    assert t.messages[0].content == "line1\nit's \"x\""


def test_tool_envelope_nonzero_returncode_is_appended_and_zero_omitted():
    import json

    from harness.generate.traces import _to_trace

    t = _to_trace(_row([_tool(json.dumps({"output": "boom", "returncode": 2}))]))
    assert t.messages[0].content == "boom\nreturncode: 2"


def test_tool_content_that_is_not_an_envelope_is_kept_raw():
    from harness.generate.traces import _to_trace

    for raw in ['{"output": "cut', "plain text", '{"other": 1}', "[1, 2]"]:
        assert _to_trace(_row([_tool(raw)])).messages[0].content == raw


def test_tool_call_arguments_render_as_name_key_value():
    import json

    from harness.generate.traces import _to_trace

    args = json.dumps({"command": "echo 'hi'\nls \"a\"", "timeout": 5})
    msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash", "arguments": args}}]}
    t = _to_trace(_row([msg]))
    assert t.messages[0].tool_calls == ["bash(command=echo 'hi'\nls \"a\", timeout=5)"]


def test_quote_with_real_newline_and_apostrophe_from_tool_output_passes_check_quotes():
    import json

    from harness.generate.candidates import check_quotes
    from harness.generate.traces import _to_trace
    from harness.generate.candidates import FactCandidate

    out = "error: can't open file\nNo such file: setup.py"
    trace = _to_trace(_row([_tool(json.dumps({"output": out, "returncode": 1}))]))
    cand = FactCandidate(
        statement="Opening setup.py failed.",
        kind="other",
        quote="can't open file\nNo such file: setup.py",
        message_index=0,
    )
    ok, rejected = check_quotes(trace, [cand])
    assert len(ok) == 1 and not rejected


@pytest.mark.parametrize("code", [0, "0", None])
def test_tool_envelope_with_a_zero_returncode_adds_no_line(code: object) -> None:
    from harness.generate.traces import _tool_content

    assert _tool_content(json.dumps({"output": "ok", "returncode": code})) == "ok"
