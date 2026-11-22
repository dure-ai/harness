import json
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from harness.cli import app
from harness.scenario.load import load_set
from harness.stress.groups import VALUES

runner = CliRunner()
COINCIDENCE = Path("scenarios/dev/coincidence")  # one team per scenario: every knob builds


@pytest.fixture
def noise_file(tmp_path: Path) -> Path:
    path = tmp_path / "noise.txt"
    lines = [f"ran the full test suite for module_{i} and read the stack trace of failure number {i}" for i in range(400)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build(tmp_path: Path, noise_file: Path, *extra: str, scenarios: Path = COINCIDENCE, out: Path | None = None) -> tuple[Result, Path]:
    out = out or tmp_path / "stress"
    args = ["stress", "build", "--scenarios", str(scenarios), "--out", str(out), "--noise-file", str(noise_file), "--seed", "7"]
    return runner.invoke(app, [*args, *extra]), out


def bench(variants: Path, out: Path, *extra: str) -> Result:
    args = ["bench", "run", "--scenarios", str(variants), "--include-drafts", "--yes", "--out", str(out), "--blind-seed", "123456789012345"]
    return runner.invoke(app, [*args, *extra])


def test_build_writes_screened_draft_variants_for_every_knob_value(tmp_path, noise_file):
    r, out = build(tmp_path, noise_file)
    assert r.exit_code == 0, r.output
    bases = load_set([COINCIDENCE], include_drafts=False).scenarios
    variants = load_set([out], include_drafts=True).scenarios
    assert len(variants) == len(bases) * sum(map(len, VALUES.values()))
    assert all(v.review.status == "draft" and v.provenance.variant is not None for v in variants)
    assert {v.id.split("--", 1)[1] for v in variants} >= {"noise-300", "position-late", "gap-30d"}
    assert f"wrote {len(variants)} variants" in r.output and "--include-drafts" in r.output and "sha256" in r.output


def test_build_is_deterministic_and_dedupes_knobs(tmp_path, noise_file):
    r, out = build(tmp_path, noise_file, "--knob", "noise", "--knob", "noise")
    assert r.exit_code == 0, r.output
    first = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    assert len(first) == 4 * len(load_set([COINCIDENCE], include_drafts=False).scenarios)
    assert f"(noise {len(first)})" in r.output
    r, again = build(tmp_path, noise_file, "--knob", "noise", "--force", out=tmp_path / "again")
    assert {p.name: p.read_text(encoding="utf-8") for p in again.iterdir()} == first


def test_build_refuses_a_full_out_unless_forced(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "noise")
    r, _ = build(tmp_path, noise_file, "--knob", "gap")
    assert r.exit_code == 2 and "--force" in r.output
    r, _ = build(tmp_path, noise_file, "--knob", "gap", "--force")
    assert r.exit_code == 0, r.output
    assert all("--gap-" in p.name for p in out.iterdir())  # the noise variants of the first run are gone


def test_build_rejects_bad_input(tmp_path, noise_file):
    r, _ = build(tmp_path, noise_file, "--knob", "colour")
    assert r.exit_code == 2 and "colour" in r.output
    r, _ = build(tmp_path, noise_file, out=Path("scenarios/stress-tmp"))
    assert r.exit_code == 2 and "git-ignored" in r.output
    assert not Path("scenarios/stress-tmp").exists()
    r, _ = build(tmp_path, tmp_path / "missing.txt")
    assert r.exit_code == 2 and "cannot read --noise-file" in r.output


def test_build_refuses_variants_as_input(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    for p in out.iterdir():  # pretend someone approved them by hand
        p.write_text(p.read_text(encoding="utf-8").replace("status: draft", "status: approved"), encoding="utf-8")
    r, _ = build(tmp_path, noise_file, scenarios=out, out=tmp_path / "again")
    assert r.exit_code == 2


def test_leak_cross_team_noise_variants_are_skipped_with_the_reason(tmp_path, noise_file):
    r, out = build(tmp_path, noise_file, "--knob", "noise", scenarios=Path("scenarios/dev/leak_cross_team"))
    assert r.exit_code == 0, r.output
    assert "teams; noise must be visible to every asker" in r.output


def test_bench_run_reports_curves_and_discrimination_for_variants(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "noise")
    r = bench(out, tmp_path / "reports")
    assert r.exit_code == 0, r.output
    (run_dir,) = list((tmp_path / "reports" / "scenarios").iterdir())
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "표 6. 난이도 곡선" in report and "표 7. 문항 변별력" in report
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    groups = ["coincidence/noise=0", "coincidence/noise=20", "coincidence/noise=100", "coincidence/noise=300"]
    curve = summary["stress"]["curve"]["all_store"]
    assert list(curve) == groups
    assert curve["coincidence/noise=0"]["n_bases"] == 4 and curve["coincidence/noise=0"]["m2_base_ci"] is not None
    assert list(summary["engines"]["all_store"]["by_group"]) == groups  # tables 2 and 6 read in knob order
    assert summary["stress"]["compared"] == ["all_store", "repeat_n"]
    # all_store follows every coincidence block probe and repeat_n none, so each of them splits the two.
    discrimination = summary["stress"]["discrimination"]["coincidence/noise=0"]
    assert discrimination["probes"] == discrimination["split"] + discrimination["all_right"] + discrimination["all_wrong"]
    assert discrimination["probes"] >= 4


def test_bench_run_on_ordinary_scenarios_has_no_stress_tables(tmp_path):
    r = runner.invoke(app, ["bench", "run", "--scenarios", str(COINCIDENCE), "--yes", "--out", str(tmp_path / "r")])
    assert r.exit_code == 0, r.output
    (run_dir,) = list((tmp_path / "r" / "scenarios").iterdir())
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["stress"] is None
    assert "표 6" not in (run_dir / "report.md").read_text(encoding="utf-8")


def test_bench_run_guards_for_variants(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    r = runner.invoke(app, ["bench", "run", "--scenarios", str(out), "--yes", "--out", str(tmp_path / "r")])
    assert r.exit_code == 2 and "are drafts" in r.output and "--include-drafts" in r.output
    r = bench(out, Path("scenarios/bench-tmp"))
    assert r.exit_code == 2 and "git-ignored" in r.output
    assert not Path("scenarios/bench-tmp").exists()
    r = runner.invoke(app, ["bench", "run", "--dataset", "longmemeval-s", "--include-drafts", "--yes"])
    assert r.exit_code == 2 and "--include-drafts" in r.output


def test_harness_run_refuses_stress_variants(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    r = runner.invoke(app, ["run", "--scenarios", str(out), "--include-drafts", "--out", str(tmp_path / "runs")])
    assert r.exit_code == 2 and "bench run" in r.output
    assert not (tmp_path / "runs").exists()


def test_build_help_names_every_knob_value():
    r = runner.invoke(app, ["stress", "build", "--help"], terminal_width=200)
    text = " ".join(r.output.split())
    assert all(value in text for values in VALUES.values() for value in values)
