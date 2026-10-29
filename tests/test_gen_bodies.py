"""Tests for body writing with an LLM client."""

from typing import Any

import pytest

from harness.generate.bodies import write_bodies
from harness.generate.candidates import FactCandidate
from harness.generate.config import GenConfig
from harness.generate.families import TEMPLATES
from harness.generate.llm import FakeClient, LLMError
from harness.generate.slots import Skeleton, fake_slots, slots_for, slots_prompt_block
from harness.generate.traces import Trace
from harness.scenario.labels import changed_label_fields
from harness.scenario.validate import validate_scenario
from tests.gen_fixtures import make_trace

SEED = 20261020
QUOTE = "pytest -x tests/unit"


def _gen(**over: Any) -> GenConfig:
    data: dict[str, Any] = {
        "provider": "fake",
        "model": "writer-1",
        "prompt_version": "gen-test",
        "license_allowlist": ["MIT"],
        "banned_cue_words": ["outdated", "no longer"],
    }
    return GenConfig.model_validate({**data, **over})


def _cand() -> FactCandidate:
    return FactCandidate(
        statement="Unit tests run with `pytest -x tests/unit`.",
        kind="test",
        quote=QUOTE,
        message_index=2,
        fix="workaround",
        index=1,
    )


def _skeleton(family: str = "truth_change", traces: list[Trace] | None = None) -> Skeleton:
    tr = [make_trace(), make_trace(trajectory_id="traj-2", model="model-b")] if traces is None else traces
    sk = TEMPLATES[family](_cand(), tr, SEED)
    assert sk is not None
    return sk


def _reply(sk: Skeleton) -> dict[str, Any]:
    return fake_slots("", slots_prompt_block(slots_for(sk, _cand())))


def _client(*replies: dict[str, Any]) -> FakeClient:
    return FakeClient(responses={"bodies": list(replies)})


def test_bodies_fill_only_text_fields() -> None:
    sk = _skeleton()
    s = write_bodies(_client(_reply(sk)), sk, _cand(), [make_trace()], _gen())
    assert all(f.text and f.markers for f in s.facts.values())
    assert all(e.text for e in s.events)
    assert all(p.query for p in s.probes)
    assert set(changed_label_fields(sk.scenario, s)) == {
        "provenance.author",
        "provenance.provider",
        "provenance.llm_model",
        "provenance.prompt_version",
        "provenance.generated_at",
        "provenance.evidence",
    }
    assert validate_scenario(s) == []


def test_evidence_and_provenance_written() -> None:
    sk = _skeleton()
    reply = _reply(sk)
    ids = list(reply["events"])
    reply["events"][ids[0]]["origin"] = "quoted"
    s = write_bodies(_client(reply), sk, _cand(), [make_trace(trajectory_id="traj-9")], _gen())
    assert len(s.provenance.evidence) == len(s.events)
    by_event = {ev.event_id: ev for ev in s.provenance.evidence}
    assert set(by_event) == {e.id for e in s.events}
    quoted = by_event[ids[0]]
    assert (quoted.origin, quoted.trace_id, quoted.message_index, quoted.quote) == ("quoted", "traj-9", 2, QUOTE)
    for eid in ids[1:]:
        w = by_event[eid]
        assert (w.origin, w.trace_id, w.message_index, w.quote) == ("written", None, None, None)
    p = s.provenance
    assert (p.author, p.provider, p.llm_model, p.prompt_version) == ("llm", "fake", "fake", "gen-test")
    assert p.generated_at is not None and p.generated_at.utcoffset() is not None
    assert p.source_traces == sk.scenario.provenance.source_traces
    assert p.source_repos == sk.scenario.provenance.source_repos
    assert p.fabricated_facts == sk.scenario.provenance.fabricated_facts


def test_provenance_records_the_model_that_served_the_reply() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    client.last_model = "fallback-model-7"
    s = write_bodies(client, sk, _cand(), [], _gen())
    assert s.provenance.llm_model == "fallback-model-7"
    plain = write_bodies(_client(_reply(sk)), sk, _cand(), [], _gen())
    assert plain.provenance.llm_model == "fake"  # nothing reported: the configured model


def test_quoted_evidence_without_traces_has_no_trace_id() -> None:
    sk = _skeleton(traces=[])
    reply = _reply(sk)
    first = next(iter(reply["events"]))
    reply["events"][first]["origin"] = "quoted"
    s = write_bodies(_client(reply), sk, _cand(), [], _gen())
    got = next(e for e in s.provenance.evidence if e.event_id == first)
    assert (got.trace_id, got.message_index, got.quote) == (None, 2, QUOTE)
    assert s.provenance.source_traces == []


def test_missing_slot_retried_then_error() -> None:
    sk = _skeleton()
    bad = _reply(sk)
    del bad["events"][next(iter(bad["events"]))]
    client = _client(bad, bad)
    with pytest.raises(LLMError, match="failed validation twice"):
        write_bodies(client, sk, _cand(), [make_trace()], _gen())
    assert len(client.requests) == 2
    assert "missing" in client.requests[1][2]


def test_missing_slot_fixed_by_retry() -> None:
    sk = _skeleton()
    bad = _reply(sk)
    del bad["probes"][next(iter(bad["probes"]))]
    s = write_bodies(_client(bad, _reply(sk)), sk, _cand(), [make_trace()], _gen())
    assert all(p.query for p in s.probes)


def test_prompt_contains_labels_and_quote() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [make_trace()], _gen())
    (kind, system, user), = client.requests
    assert kind == "bodies"
    assert "<<<SLOTS" in user and "valid_until" in user
    assert QUOTE in user and _cand().statement in user
    assert "Fix the bug in parser.py." in user  # the start of the trace
    assert "outdated, no longer" in system
    assert "{banned_cue_words}" not in system


def test_prompt_instructs_probe_terms_and_markers() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [], _gen())
    system, user = client.requests[0][1:]
    assert "not paraphrases" in system
    assert "2 or 3" in system
    assert "another fact" in system
    assert "Start of the source trace" not in user


def test_prompt_forbids_nested_markers_and_foreign_markers_in_feedback() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [], _gen())
    system = client.requests[0][1]
    assert "no marker may be a substring of another fact's marker" in system
    rule3 = next(line for line in system.splitlines() if line.startswith("3. "))
    assert "event" in rule3 and "feedback" in rule3


def test_prompt_calls_the_candidate_kind_category() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [], _gen())
    user = client.requests[0][2]
    head = user.split("Slots to fill", 1)[0]
    assert f"- category: {_cand().kind}" in head
    assert "- kind:" not in head


def test_trace_excerpt_is_capped() -> None:
    sk = _skeleton()
    long = make_trace()
    big = long.messages[2].model_copy(update={"index": 4, "content": "z" * 9000})
    long = long.model_copy(update={"messages": [*long.messages, big]})
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [long], _gen())
    excerpt = client.requests[0][2].split("for style only:\n", 1)[1]
    assert len(excerpt) <= 4000


def _rule(system: str, n: int) -> str:
    return next(line for line in system.splitlines() if line.startswith(f"{n}. "))


def test_prompt_rules_forbid_hinting_question_wording() -> None:
    sk = _skeleton()
    client = _client(_reply(sk))
    write_bodies(client, sk, _cand(), [], _gen())
    system = client.requests[0][1]
    rule9, rule10 = _rule(system, 9), _rule(system, 10)
    # Rule 9 no longer tells the writer what each probe kind is scored on (repro d14).
    for said in ("should not use", "should be recalled", "of kind `block`", "`promote` probe", "`leak` probe"):
        assert said not in rule9, said
    assert "not paraphrases" in rule9
    for need in ("same form and tone", "guess", "presuppose", "doubt"):
        assert need in rule10, need
    assert "{banned_cue_words}" not in system


def test_quoted_origin_on_fabricated_fact_fails_the_reply_check() -> None:
    sk = _skeleton()  # truth_change: f_new is fabricated
    bad = _reply(sk)
    fabricated = next(e.id for e in sk.scenario.events if e.fact == "f_new")
    bad["events"][fabricated]["origin"] = "quoted"
    client = _client(bad, bad)
    with pytest.raises(LLMError, match="failed validation twice"):
        write_bodies(client, sk, _cand(), [make_trace()], _gen())
    assert "quoted" in client.requests[1][2] and fabricated in client.requests[1][2]


def _marker_with_cue(sk: Skeleton) -> dict[str, Any]:
    """A reply whose first fact has a marker holding the banned cue word `outdated`."""
    bad = _reply(sk)
    fid = next(iter(bad["facts"]))
    bad["facts"][fid]["markers"] = ["the outdated option"]
    return bad


def test_cue_word_in_marker_is_retried_and_fixed() -> None:
    sk = _skeleton()
    client = _client(_marker_with_cue(sk), _reply(sk))
    s = write_bodies(client, sk, _cand(), [make_trace()], _gen())
    assert len(client.requests) == 2
    retry = client.requests[1][2]  # the retry names the marker and the cue word
    assert "failed validation" in retry and "the outdated option" in retry and "outdated" in retry
    assert all("outdated" not in m for f in s.facts.values() for m in f.markers)


def test_cue_word_in_marker_twice_is_an_error() -> None:
    sk = _skeleton()
    bad = _marker_with_cue(sk)
    client = _client(bad, bad)
    with pytest.raises(LLMError, match="failed validation twice"):
        write_bodies(client, sk, _cand(), [make_trace()], _gen())
    assert len(client.requests) == 2


def test_poison_repeats_too_different_are_retried_and_fixed() -> None:
    sk = _skeleton("single_source_poison")
    good = _reply(sk)
    bad = _reply(sk)
    false_ids = [e.id for e in sk.scenario.events if e.fact == "f_false"]
    marker = bad["facts"]["f_false"]["markers"][0]
    words = ["alpha beta", "gamma delta", "epsilon zeta", "eta theta"]
    for eid, w in zip(false_ids, words, strict=False):
        bad["events"][eid]["text"] = f"{w} {marker} {w[::-1]}"
    client = _client(bad, good)
    write_bodies(client, sk, _cand(), [make_trace()], _gen())
    assert len(client.requests) == 2
    assert "need 3 records" in client.requests[1][2]
    assert "lead-in" in client.requests[1][2]
