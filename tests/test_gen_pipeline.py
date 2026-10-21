"""Tests for the generation pipeline with the fake LLM provider."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.config import EvalConfig
from harness.generate import pipeline
from harness.generate.candidates import fake_candidates
from harness.generate.config import GenConfig
from harness.generate.llm import CachedClient, FakeClient, LLMError, make_client
from harness.generate.pipeline import GenerateReport, all_family_counts, generate, save_draft
from harness.generate.slots import fake_slots
from harness.generate.traces import Trace, TraceMessage
from harness.scenario.load import load_scenario
from harness.scenario.model import FAMILIES
from harness.scenario.validate import Issue
from tests.gen_fixtures import make_trace

SEED = 20261020
CFG = EvalConfig()
GEN = GenConfig(
    provider="fake",
    model="fake-1",
    prompt_version="gen-test",
    license_allowlist=["MIT"],
    banned_cue_words=["outdated", "no longer", "rumor", "deprecated", "fake"],
)


def trace(n: int, **kw: Any) -> Trace:
    """A trace whose assistant messages carry distinct, quotable text."""
    messages = [
        TraceMessage(index=0, role="system", content="You are an engineer."),
        TraceMessage(index=1, role="user", content="Fix the bug in parser.py."),
    ]
    for i in range(3):
        messages.append(
            TraceMessage(
                index=2 + 2 * i,
                role="assistant",
                content=f"Step {i} of trace {n}: run the project checks before editing.",
                tool_calls=['bash({"cmd": "make check"})'],
            )
        )
        messages.append(TraceMessage(index=3 + 2 * i, role="tool", content="ok"))
    defaults: dict[str, Any] = {
        "trajectory_id": f"traj-{n}",
        "instance_id": f"acme__widget-{n}",
        "messages": messages,
    }
    return make_trace(**{**defaults, **kw})


def traces(k: int = 3) -> list[Trace]:
    return [trace(i) for i in range(1, k + 1)]


def fake_client(tmp_path: Path) -> CachedClient:
    client = make_client(GEN, tmp_path / "llm")
    assert isinstance(client, CachedClient)
    return client


def plain_fake() -> FakeClient:
    return FakeClient(responders={"candidates": fake_candidates, "bodies": fake_slots})


def run(
    tmp_path: Path,
    client: Any,
    families: list[str],
    n: int,
    pool: list[Trace] | None = None,
    **kw: Any,
) -> GenerateReport:
    return generate(
        families, n, SEED, traces() if pool is None else pool, client, CFG, GEN, tmp_path / "out", tmp_path / "run", **kw
    )


def rejections(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "run" / "rejected.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_all_family_counts_halve_normal_repeat() -> None:
    assert set(all_family_counts(4)) == set(FAMILIES)
    assert all_family_counts(4)["normal_repeat"] == 2
    assert all_family_counts(1)["normal_repeat"] == 1
    assert all_family_counts(5)["truth_change"] == 5


def test_generate_writes_drafts_that_pass_checks(tmp_path: Path) -> None:
    from harness.generate.checks import check_draft

    report = run(tmp_path, fake_client(tmp_path), ["truth_change"], 2)
    assert (report.requested, len(report.written), report.shortfall) == (2, 2, {})
    assert len(set(report.written)) == 2
    for sid in report.written:
        path = tmp_path / "out" / "truth_change" / f"{sid}.yaml"
        s = load_scenario(path)
        assert s.id == sid and s.review.status == "draft" and s.provenance.author == "llm"
        assert not [i for i in check_draft(s, CFG, GEN) if i.level == "error"]
    lines = (tmp_path / "run" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and {"trajectory_id", "repo", "statement", "quote", "index"} <= set(json.loads(lines[0]))


def test_generate_rerun_uses_cache_and_same_ids(tmp_path: Path) -> None:
    client = fake_client(tmp_path)
    first = run(tmp_path, client, ["truth_change"], 2)
    calls = client.calls
    again = run(tmp_path, client, ["truth_change"], 2)
    assert client.calls == calls and client.hits > 0
    assert again.written == first.written


def test_generate_does_not_overwrite_reviewed(tmp_path: Path) -> None:
    client = fake_client(tmp_path)
    first = run(tmp_path, client, ["truth_change"], 2)
    kept = tmp_path / "out" / "truth_change" / f"{first.written[0]}.yaml"
    data = yaml.safe_load(kept.read_text(encoding="utf-8"))
    data["review"] = {"status": "approved", "reviewer": "jin", "notes": "hand edited"}
    kept.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    before = kept.read_bytes()

    again = run(tmp_path, client, ["truth_change"], 2)
    assert again.skipped_existing == [first.written[0]]
    assert again.written == [first.written[1]]
    assert kept.read_bytes() == before


def test_generate_overwrites_an_older_draft(tmp_path: Path) -> None:
    client = fake_client(tmp_path)
    first = run(tmp_path, client, ["truth_change"], 1)
    path = tmp_path / "out" / "truth_change" / f"{first.written[0]}.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# stale\n", encoding="utf-8")
    run(tmp_path, client, ["truth_change"], 1)
    assert "# stale" not in path.read_text(encoding="utf-8")


def test_generate_does_not_touch_an_unreadable_file(tmp_path: Path) -> None:
    client = fake_client(tmp_path)
    first = run(tmp_path, client, ["truth_change"], 1)
    path = tmp_path / "out" / "truth_change" / f"{first.written[0]}.yaml"
    for content in (b"id: [", b"\xff\xfe"):
        path.write_bytes(content)
        again = run(tmp_path, client, ["truth_change"], 1)
        assert again.skipped_existing == [first.written[0]]
        assert path.read_bytes() == content


def candidate(quote: str, **over: Any) -> dict[str, Any]:
    return {
        "statement": "Checks run through the project make target.",
        "kind": "test",
        "quote": quote,
        "message_index": 2,
        **over,
    }


def test_generate_shortfall_reported(tmp_path: Path) -> None:
    quote = "run the project checks before editing"
    canned = {"candidates": [{"candidates": [candidate(quote)]} for _ in range(3)]}
    client = FakeClient(responses=canned, responders={"bodies": fake_slots})
    report = run(tmp_path, client, ["workaround_then_root"], 2)
    assert report.written == [] and report.requested == 2
    reason = report.shortfall["workaround_then_root"]
    assert "0 of 2 made" in reason and "3 traces" in reason and "fix" in reason
    assert not (tmp_path / "out").exists()


def test_generate_shortfall_when_no_trace_is_usable(tmp_path: Path) -> None:
    pool = [trace(1, language="rust"), trace(2, license="GPL-3.0"), trace(3, license=None)]
    report = run(tmp_path, plain_fake(), ["truth_change"], 1, pool)
    assert report.written == []
    assert "none of the 3 traces" in report.shortfall["truth_change"]
    assert run(tmp_path, plain_fake(), ["truth_change"], 1, []).shortfall["truth_change"] == "no traces were given"


def test_generate_bad_llm_output_is_rejected_not_fatal(tmp_path: Path) -> None:
    client = FakeClient(
        responses={"bodies": ["{not json", "{still not json"]},
        responders={"candidates": fake_candidates, "bodies": fake_slots},
    )
    report = run(tmp_path, client, ["truth_change"], 1)
    assert len(report.written) == 1 and report.rejected == 1
    (rej,) = rejections(tmp_path)
    assert (rej["stage"], rej["code"], rej["family"]) == ("bodies", "llm_error", "truth_change")
    assert rej["trace_id"].startswith("traj-")


def test_generate_bad_candidate_reply_rejects_only_that_trace(tmp_path: Path) -> None:
    client = FakeClient(
        responses={"candidates": ["{bad", "{bad"]},
        responders={"candidates": fake_candidates, "bodies": fake_slots},
    )
    report = run(tmp_path, client, ["truth_change"], 2)
    assert len(report.written) == 2
    assert [r["stage"] for r in rejections(tmp_path)] == ["candidates"]


class FatalClient:
    name = "fake"
    model = "fake"

    def complete_json(self, *args: Any, **kw: Any) -> Any:
        raise LLMError("anthropic: authentication failed; set ANTHROPIC_API_KEY", fatal=True)


def test_generate_fatal_error_stops_the_run(tmp_path: Path) -> None:
    with pytest.raises(LLMError) as e:
        run(tmp_path, FatalClient(), ["truth_change"], 2)
    assert e.value.fatal and "ANTHROPIC_API_KEY" in str(e.value)


def test_generate_fatal_error_in_bodies_stops_the_run(tmp_path: Path) -> None:
    class BodiesFatal(FakeClient):
        def complete_json(self, kind: str, *args: Any, **kw: Any) -> Any:
            if kind == "bodies":
                raise LLMError("openai: KEY is not set", fatal=True)
            return super().complete_json(kind, *args, **kw)

    client = BodiesFatal(responders={"candidates": fake_candidates, "bodies": fake_slots})
    with pytest.raises(LLMError):
        run(tmp_path, client, ["truth_change"], 2)


def test_generate_repos_filter(tmp_path: Path) -> None:
    pool = [trace(1), trace(2, repo="other/gadget"), trace(3, repo="other/gadget")]
    full = run(tmp_path, plain_fake(), ["truth_change"], 4, pool, repos={"other/gadget"})
    assert full.written and all("-gadget-" in sid for sid in full.written)
    short = run(tmp_path, plain_fake(), ["truth_change"], 2, pool, repos={"Widget"})
    assert short.written and all("-widget-" in sid for sid in short.written)
    none = run(tmp_path, plain_fake(), ["truth_change"], 1, pool, repos={"nowhere/zip"})
    assert none.written == [] and "usable traces is from the repositories" in none.shortfall["truth_change"]


def test_candidates_are_extracted_once_per_trace_and_shared(tmp_path: Path) -> None:
    client = plain_fake()
    report = run(tmp_path, client, ["truth_change", "coincidence"], 4, traces(2))
    asked = [u for k, _, u in client.requests if k == "candidates"]
    assert len(asked) == len(set(asked)) == 2
    assert len(report.written) == 8 and len(set(report.written)) == 8
    lines = (tmp_path / "run" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6  # 3 per trace, written once


def test_generate_orders_traces_by_seed(tmp_path: Path) -> None:
    pool = traces(6)
    first = run(tmp_path, plain_fake(), ["truth_change"], 1, pool)
    again = run(tmp_path, plain_fake(), ["truth_change"], 1, list(reversed(pool)))
    assert first.written == again.written
    other = generate(["truth_change"], 1, SEED + 1, pool, plain_fake(), CFG, GEN, tmp_path / "o2", tmp_path / "r2")
    assert other.written != first.written


def test_generate_rejects_too_easy_and_errors_but_saves_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdicts = [
        [Issue("warning", "too_easy", "all_store never follows a false fact")],
        [Issue("error", "cue_word", "event e1: text contains cue word 'fake'")],
        [Issue("warning", "markers_missing", "something minor")],
    ]
    monkeypatch.setattr(pipeline, "check_draft", lambda *a, **kw: verdicts.pop(0))
    report = run(tmp_path, plain_fake(), ["truth_change"], 1)
    assert len(report.written) == 1 and report.rejected == 2
    codes = [r["code"] for r in rejections(tmp_path)]
    assert codes == ["too_easy", "cue_word"]
    assert len(list((tmp_path / "out" / "truth_change").glob("*.yaml"))) == 1


def test_generate_passes_secret_events_to_the_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    def spy(s: Any, cfg: Any, gen: Any, secret_event_ids: Any = ()) -> list[Issue]:
        seen.append(tuple(secret_event_ids))
        return []

    monkeypatch.setattr(pipeline, "check_draft", spy)
    run(tmp_path, plain_fake(), ["leak_personal"], 1)
    assert seen and seen[0]


def test_generate_all_families_end_to_end(tmp_path: Path) -> None:
    client = fake_client(tmp_path)
    fams = list(all_family_counts(1))
    report = run(tmp_path, client, fams, 1, traces(4), per_family=all_family_counts(1))
    assert report.requested == 10 and report.shortfall == {}
    assert sorted(sid.split("-", 1)[0] for sid in report.written) == sorted(FAMILIES)
    for sid in report.written:
        fam = sid.split("-", 1)[0]
        assert load_scenario(tmp_path / "out" / fam / f"{sid}.yaml").family == fam


def test_save_draft_round_trips_with_aware_datetimes(tmp_path: Path) -> None:
    report = run(tmp_path, fake_client(tmp_path), ["truth_change"], 1)
    path = tmp_path / "out" / "truth_change" / f"{report.written[0]}.yaml"
    s = load_scenario(path)
    assert all(e.t.utcoffset() is not None for e in s.events)
    copy = tmp_path / "copy" / "x.yaml"
    save_draft(s, copy)
    assert load_scenario(copy) == s
    keys = list(yaml.safe_load(copy.read_text(encoding="utf-8")))
    assert keys[:3] == ["id", "family", "mode"] and keys.index("facts") < keys.index("events")


def test_dry_run_client_stops_at_the_first_request(tmp_path: Path) -> None:
    client = pipeline.DryRunClient("fake", "m")
    with pytest.raises(pipeline.DryRun) as d:
        run(tmp_path, client, ["truth_change"], 1)
    assert d.value.kind == "candidates" and "Repository: acme/widget" in d.value.user


def test_max_traces_stops_extraction_and_is_reported(tmp_path: Path) -> None:
    client = plain_fake()
    report = run(tmp_path, client, ["truth_change"], 5, traces(3), max_traces=1)
    assert len([1 for k, _, _ in client.requests if k == "candidates"]) == 1
    assert report.trace_cap_hit is True and len(report.written) == 3
    assert "stopped at --max-traces 1" in report.shortfall["truth_change"]


def test_max_traces_counts_traces_across_families(tmp_path: Path) -> None:
    client = plain_fake()
    report = run(tmp_path, client, ["truth_change", "coincidence"], 4, traces(3), max_traces=2)
    assert len([1 for k, _, _ in client.requests if k == "candidates"]) == 2  # shared, not 2 per family
    assert len(report.written) == 8 and report.shortfall == {}
    assert report.trace_cap_hit is False  # the cap was never what stopped a family

    capped = plain_fake()
    report = run(tmp_path, capped, ["truth_change", "coincidence"], 4, traces(3), max_traces=1)
    assert len([1 for k, _, _ in capped.requests if k == "candidates"]) == 1
    assert report.trace_cap_hit is True and set(report.shortfall) == {"truth_change", "coincidence"}
    assert all("stopped at --max-traces 1" in r for r in report.shortfall.values())


def test_max_traces_not_reached_is_false(tmp_path: Path) -> None:
    report = run(tmp_path, plain_fake(), ["truth_change"], 1, traces(3), max_traces=2)
    assert report.trace_cap_hit is False and report.shortfall == {}
    default = run(tmp_path, plain_fake(), ["truth_change"], 5, traces(3))
    assert default.trace_cap_hit is False and "max-traces" not in default.shortfall.get("truth_change", "")


def test_same_trajectory_twice_is_used_once(tmp_path: Path) -> None:
    client = plain_fake()
    report = run(tmp_path, client, ["truth_change"], 4, [trace(1), trace(1)])
    assert len(report.written) == 3 and len(set(report.written)) == 3  # 3 candidates, not 4 from a repeat
    assert report.shortfall["truth_change"].startswith("3 of 4 made; looked at 1 traces")
    assert len([1 for k, _, _ in client.requests if k == "candidates"]) == 1
