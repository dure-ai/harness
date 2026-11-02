"""The hindsight and mem0 engines through the whole CLI, against the local fake servers."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from harness.scenario.load import load_set
from harness.scenario.model import Scenario
from tests.oss_fakes import FakeHindsight, FakeMem0, Recorded, free_port
from tests.test_cli import run_cli

EXAMPLES = Path(__file__).resolve().parent.parent / "scenarios" / "examples"
STRUCTURED_KEYS = ("tags", "document_id", "run_id", "user_id", "metadata", "filters")


def set_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the model variables the adapters compare with the eval config."""
    monkeypatch.setenv("HARNESS_LLM_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("HARNESS_EMBEDDING_MODEL", "text-embedding-3-small")


@pytest.fixture
def hindsight_fake() -> Iterator[FakeHindsight]:
    fake = FakeHindsight()
    with fake:
        yield fake


@pytest.fixture
def mem0_fake() -> Iterator[FakeMem0]:
    fake = FakeMem0()
    with fake:
        yield fake


@pytest.fixture
def fakes_env(
    monkeypatch: pytest.MonkeyPatch, hindsight_fake: FakeHindsight, mem0_fake: FakeMem0
) -> None:
    monkeypatch.setenv("HARNESS_HINDSIGHT_URL", hindsight_fake.url)
    monkeypatch.setenv("HARNESS_MEM0_URL", mem0_fake.url)
    set_model_env(monkeypatch)


def load_examples() -> list[Scenario]:
    return load_set([EXAMPLES], include_drafts=True).scenarios


def summary_engine_ids(summary: dict[str, Any]) -> list[str]:
    """Engine ids the report's summary lists, baselines and blind candidates alike."""
    return list(summary["engines"])


def any_errors(summary: dict[str, Any]) -> bool:
    """Whether any engine had a scenario that errored."""
    return any(e["metrics"]["errors"] for e in summary["engines"].values())


def run_both(tmp_path: Path, *extra: str):
    return run_cli(
        tmp_path, EXAMPLES, "--include-drafts", "--engine", "hindsight", "--engine", "mem0",
        "--repeat", "1", *extra,
    )


def structured_fields(requests: list[Recorded]) -> str:
    """Everything the engines were sent except free text: paths, queries and id-like body fields."""
    found: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in STRUCTURED_KEYS:
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for r in requests:
        found += [r.path, r.query]
        walk(r.body)
    return json.dumps(found, ensure_ascii=False)


def leaked_labels(scenarios: list[Scenario], requests: list[Recorded]) -> list[str]:
    """Hidden labels found in what was sent to the engines, as readable findings."""
    structured = structured_fields(requests).lower()
    everything = json.dumps(
        [[r.method, r.path, r.query, r.body] for r in requests], ensure_ascii=False
    ).lower()
    hits = []
    for s in scenarios:
        for label in (s.id, s.family, *(p.id for p in s.probes), *(p.kind for p in s.probes)):
            if label.lower() in structured:
                hits.append(f"{s.id}: {label!r} in a structured field")
        observed = " ".join([*(e.text for e in s.events), *(p.query for p in s.probes)]).lower()
        markers = [m for e in s.events for m in e.secret_markers]
        markers += [m for f in s.facts.values() for m in f.markers]
        for marker in markers:
            if marker.lower() not in observed and marker.lower() in everything:
                hits.append(f"{s.id}: marker {marker!r} sent")
    return hits


def test_run_with_both_fake_engines(tmp_path, fakes_env):
    result, out = run_both(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((next(out.iterdir()) / "summary.json").read_text(encoding="utf-8"))
    assert {"E1", "E2"} <= set(summary_engine_ids(summary))
    assert not any_errors(summary)


def test_engine_down_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HINDSIGHT_URL", f"http://127.0.0.1:{free_port()}")
    set_model_env(monkeypatch)
    result, _ = run_cli(
        tmp_path, EXAMPLES, "--include-drafts", "--engine", "hindsight", "--repeat", "1"
    )
    assert result.exit_code == 2 and "cannot build engine 'hindsight'" in result.output
    assert "EngineHTTPError" in result.output
    assert "unknown engine" not in result.output


def test_config_without_models_exits_2(tmp_path, fakes_env):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("llm: {provider: openai}\n", encoding="utf-8")
    result, _ = run_cli(
        tmp_path, EXAMPLES, "--include-drafts", "--config", str(cfg), "--engine", "mem0", "--repeat", "1"
    )
    assert result.exit_code == 2 and "llm.model" in result.output


def test_no_hidden_labels_reach_engines(tmp_path, fakes_env, hindsight_fake, mem0_fake):
    result, out = run_both(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((next(out.iterdir()) / "summary.json").read_text(encoding="utf-8"))
    assert not any_errors(summary)
    # The check below is only worth anything if both engines were really used.
    assert any(r.method == "POST" and r.path.endswith("/memories") for r in hindsight_fake.requests)
    assert any(r.method == "POST" and r.path.endswith("/memories/recall") for r in hindsight_fake.requests)
    assert any(r.method == "POST" and r.path == "/memories" for r in mem0_fake.requests)
    assert any(r.method == "POST" and r.path == "/search" for r in mem0_fake.requests)
    requests = hindsight_fake.requests + mem0_fake.requests
    assert leaked_labels(load_examples(), requests) == []


def test_label_check_flags_planted_labels():
    scenarios = load_examples()
    first = scenarios[0]
    planted = [
        Recorded("GET", f"/v1/default/banks/{first.id}/operations", {}, None),
        Recorded("POST", "/memories", {}, {"run_id": "x", "metadata": {"f": first.family}}),
    ]
    hits = leaked_labels(scenarios, planted)
    assert any(f"{first.id!r}" in h for h in hits)
    assert any(f"{first.family!r}" in h for h in hits)


def synthetic_with_markers() -> Scenario:
    """A scenario with known markers: two hidden ones, and two that its own event text contains."""
    base = load_examples()[0]
    event = base.events[0].model_copy(
        update={"text": "Note mentioning Exempt-Secret and exempt-fact.",
                "secret_markers": ["hidden-secret-9", "exempt-secret"]}
    )
    fact_id, fact = next(iter(base.facts.items()))
    fact = fact.model_copy(update={"markers": ["hidden-fact-7", "exempt-fact"]})
    probe = base.probes[0].model_copy(update={"query": "plain question"})
    return base.model_copy(
        update={"id": "synthetic-1", "events": [event], "facts": {fact_id: fact}, "probes": [probe]}
    )


def post(content: str) -> Recorded:
    return Recorded("POST", "/memories", {}, {"messages": [{"role": "user", "content": content}]})


def test_label_check_flags_planted_markers_and_keeps_exemption():
    s = synthetic_with_markers()
    assert leaked_labels([s], [post("an ordinary note")]) == []
    assert leaked_labels([s], [post("see HIDDEN-SECRET-9 here")]) == [
        "synthetic-1: marker 'hidden-secret-9' sent"
    ]
    assert leaked_labels([s], [post("see hidden-fact-7 here")]) == [
        "synthetic-1: marker 'hidden-fact-7' sent"
    ]
    exempt = [post("exempt-secret and EXEMPT-FACT, as in the event text")]
    assert leaked_labels([s], exempt) == []


def test_same_blind_seed_gives_identical_engine_ids_and_results_with_both_engines(
    tmp_path, fakes_env, hindsight_fake, mem0_fake
):
    runs = []
    for name in ("one", "two"):
        result, out = run_both(tmp_path / name, "--blind-seed", "12345")
        assert result.exit_code == 0, result.output
        runs.append(next(out.iterdir()))
    for file in ("engine_ids.json", "results.jsonl"):
        assert (runs[0] / file).read_bytes() == (runs[1] / file).read_bytes(), file
    ids = json.loads((runs[0] / "engine_ids.json").read_text(encoding="utf-8"))
    assert ids == {"seed": 12345, "ids": {"E1": "hindsight", "E2": "mem0"}}
    # Both runs reached the servers; the namespaces they used differ only by the run id.
    assert len(hindsight_fake.requests) > 0 and len(mem0_fake.requests) > 0
