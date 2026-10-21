"""End to end: generate with the fake provider, approve through the review CLI, then selfcheck and run."""

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.generate.traces import Trace, TraceMessage
from harness.scenario.load import load_scenario
from harness.scenario.model import FAMILIES
from tests.gen_fixtures import make_parquet, make_trace

runner = CliRunner()
SEED = 20261020
REPOS = ["acme/widget", "acme/gadget", "beta/engine", "gamma/parser"]
MODELS = ["model-a", "model-b"]


def e2e_trace(repo: str, task: int, model: str) -> Trace:
    """A trace of `repo`'s task solved by `model`, with distinct quotable assistant text."""
    tag = f"{repo.split('/')[1]}-{task}-{model}"
    messages = [
        TraceMessage(index=0, role="system", content="You are an engineer."),
        TraceMessage(index=1, role="user", content=f"Fix the bug in {tag}.py."),
    ]
    for i in range(4):
        messages.append(
            TraceMessage(
                index=2 + 2 * i,
                role="assistant",
                content=f"Step {i} of {tag}: run the project checks before editing.",
                tool_calls=['bash({"cmd": "make check"})'],
            )
        )
        messages.append(TraceMessage(index=3 + 2 * i, role="tool", content="ok"))
    return make_trace(
        trajectory_id=f"traj-{tag}",
        instance_id=f"{repo.replace('/', '__')}-{task}",
        repo=repo,
        model=model,
        messages=messages,
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A trace directory with a parquet, a fake-provider gen config, and an isolated cache."""
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    traces = tmp_path / "traces"
    traces.mkdir()
    pool = [e2e_trace(r, t, m) for r in REPOS for t in range(1, 4) for m in MODELS]
    make_parquet(traces / "shard-0.parquet", pool)
    cfg = yaml.safe_load(Path("config/gen-v0.1.yaml").read_text(encoding="utf-8"))
    cfg["provider"] = "fake"
    cfg["model"] = "fake-1"
    gen_config = tmp_path / "gen.yaml"
    gen_config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return traces, gen_config


def generate(traces: Path, gen_config: Path, out: Path):
    return runner.invoke(
        app,
        [
            "generate", "--family", "all", "--n", "1", "--seed", str(SEED),
            "--gen-config", str(gen_config), "--traces", str(traces), "--out", str(out),
        ],
    )  # fmt: skip


def snapshot(out: Path) -> dict[str, bytes]:
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*.yaml"))}


def test_end_to_end_fake_provider(tmp_path, workspace):
    traces, gen_config = workspace
    out = tmp_path / "scenarios"

    result = generate(traces, gen_config, out)
    assert result.exit_code == 0, result.output

    files = sorted(out.rglob("*.yaml"))
    drafts = [load_scenario(p) for p in files]
    assert sorted(s.family for s in drafts) == sorted(FAMILIES)
    assert len(files) == 10
    for path, s in zip(files, drafts, strict=True):
        assert path.parent.name == s.family
        assert s.review.status == "draft"
        assert s.provenance.author == "llm"

    for s in drafts:
        r = runner.invoke(app, ["review", "set", s.id, "approved", "--reviewer", "tester", "--root", str(out), "--gen-config", str(gen_config)])
        assert r.exit_code == 0, f"{s.id}: {r.output}"
        assert r.output.startswith("approved ")
    approved = [load_scenario(p) for p in files]
    assert {s.review.status for s in approved} == {"approved"}
    assert {s.review.reviewer for s in approved} == {"tester"}

    check = runner.invoke(app, ["selfcheck", "--scenarios", str(out)])
    assert check.exit_code == 0, check.output
    assert "FAIL" not in check.output
    assert len(re.findall(r"\bPASS\b", check.output)) >= 1

    runs = tmp_path / "runs"
    ran = runner.invoke(app, ["run", "--scenarios", str(out), "--out", str(runs)])
    assert ran.exit_code == 0, ran.output
    report = Path(ran.output.strip().splitlines()[-1])
    assert report.name == "report.md"
    assert report.parent.parent == runs
    assert report.is_file()
    assert report.read_text(encoding="utf-8").strip()

    before = snapshot(out)
    again = generate(traces, gen_config, out)
    assert again.exit_code == 0, again.output
    assert "이미 검토된 파일이라 건너뜀" in again.output
    for s in approved:
        assert s.id in again.output
    assert snapshot(out) == before
