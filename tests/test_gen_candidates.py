"""Tests for fact candidate extraction, the quote check and dedupe."""

from typing import Any

from harness.generate.candidates import (
    CandidateList,
    FactCandidate,
    check_quotes,
    dedupe,
    extract_candidates,
    fake_candidates,
    load_prompt,
)
from harness.generate.config import GenConfig
from harness.generate.llm import FakeClient
from harness.generate.traces import TraceMessage
from tests.gen_fixtures import make_trace


def _cfg(**over: Any) -> GenConfig:
    data: dict[str, Any] = {
        "provider": "fake",
        "model": "fake",
        "prompt_version": "gen-v0.1",
        "license_allowlist": ["MIT"],
        "banned_cue_words": ["outdated"],
    }
    return GenConfig.model_validate({**data, **over})


def _raw(i: int, **over: Any) -> dict[str, Any]:
    return {
        "statement": f"Fact number {i} about the repo",
        "kind": "test",
        "quote": "Looking at it.",
        "message_index": 2,
        **over,
    }


def _cand(**over: Any) -> FactCandidate:
    return FactCandidate.model_validate(_raw(0, **over))


def test_extract_sends_compressed_trace_and_caps_count():
    client = FakeClient(responses={"candidates": [{"candidates": [_raw(i) for i in range(7)]}]})
    got = extract_candidates(client, make_trace(), _cfg())
    assert len(got) == 5
    assert [c.index for c in got] == [0, 1, 2, 3, 4]
    assert [c.statement for c in got] == [f"Fact number {i} about the repo" for i in range(5)]
    (kind, system, user), = client.requests
    assert kind == "candidates"
    assert "[#2 assistant]" in user
    assert "at most 5" in system
    assert "index" not in CandidateList.model_json_schema()["$defs"]["RawCandidate"]["properties"]


def test_extract_respects_configured_cap():
    client = FakeClient(responses={"candidates": [{"candidates": [_raw(i) for i in range(7)]}]})
    assert len(extract_candidates(client, make_trace(), _cfg(max_candidates_per_trace=2))) == 2


def test_quote_must_exist_in_message():
    good, bad = _cand(), _cand(quote="never said this")
    ok, rejected = check_quotes(make_trace(), [good, bad])
    assert ok == [good]
    assert [r.code for r in rejected] == ["quote_not_found"]
    assert rejected[0].stage == "candidates"
    assert rejected[0].trace_id == "traj-1"


def test_quote_can_come_from_a_tool_call_string():
    ok, rejected = check_quotes(make_trace(), [_cand(quote='bash({"cmd": "ls"})')])
    assert len(ok) == 1 and rejected == []


def test_quote_whitespace_insensitive():
    trace = make_trace(
        messages=[TraceMessage(index=0, role="assistant", content="Run  pytest\n  with   -x\tfor speed")]
    )
    ok, rejected = check_quotes(trace, [_cand(message_index=0, quote="pytest with -x for\nspeed")])
    assert len(ok) == 1 and rejected == []


def test_empty_quote_rejected():
    ok, rejected = check_quotes(make_trace(), [_cand(quote="  ")])
    assert ok == [] and [r.code for r in rejected] == ["quote_not_found"]


def test_quote_index_out_of_range_rejected():
    ok, rejected = check_quotes(make_trace(), [_cand(message_index=99), _cand(message_index=-1)])
    assert ok == []
    assert [r.code for r in rejected] == ["message_out_of_range"] * 2


def test_dedupe_near_identical_statements():
    a = _cand(statement="Run the tests with pytest -x from the repo root directory")
    b = _cand(statement="Run the tests with pytest -x from the repo root")
    c = _cand(statement="Deploys are owned by the platform team")
    assert dedupe([a, b, c]) == [a, c]


def test_fake_candidates_pass_quote_check():
    trace = make_trace()
    reply = fake_candidates("", extract_user(trace))
    cands = [FactCandidate(**c, index=i) for i, c in enumerate(reply["candidates"])]
    assert len(cands) == 3
    ok, rejected = check_quotes(trace, cands)
    assert rejected == [] and len(dedupe(ok)) == 3
    assert sum(c.kind == "test" for c in cands) == 1
    assert sum(c.fix == "workaround" for c in cands) == 1
    assert sum(c.condition == {"service": "api"} for c in cands) == 1


def test_fake_candidates_with_several_multiline_assistant_messages():
    trace = make_trace(
        messages=[
            TraceMessage(index=0, role="system", content="sys"),
            TraceMessage(index=1, role="user", content="task"),
            TraceMessage(index=4, role="assistant", content="First line of reasoning\n\nsecond paragraph here and more words"),
            TraceMessage(index=5, role="tool", content="output"),
            TraceMessage(index=6, role="assistant", content="", tool_calls=['bash({"cmd": "pytest -x"})']),
            TraceMessage(index=7, role="assistant", content="Done; the fix is in place and tests pass now."),
        ]
    )
    reply = fake_candidates("", extract_user(trace))
    assert [c["message_index"] for c in reply["candidates"]] == [4, 6, 7]
    cands = [FactCandidate(**c) for c in reply["candidates"]]
    assert check_quotes(trace, cands)[1] == []


def test_fake_candidates_without_assistant_message():
    trace = make_trace(messages=[TraceMessage(index=0, role="user", content="hi")])
    assert fake_candidates("", extract_user(trace)) == {"candidates": []}


def test_fake_provider_end_to_end_through_extract():
    client = FakeClient(responders={"candidates": fake_candidates})
    got = extract_candidates(client, make_trace(), _cfg())
    assert len(got) == 3
    assert check_quotes(make_trace(), got)[1] == []


def test_prompt_ships_with_package_and_is_english():
    text = load_prompt("candidates.md")
    assert "{max_candidates}" in text
    assert "message_index" in text and "quote" in text


def extract_user(trace) -> str:
    client = FakeClient(responses={"candidates": [{"candidates": []}]})
    extract_candidates(client, trace, _cfg())
    return client.requests[0][2]
