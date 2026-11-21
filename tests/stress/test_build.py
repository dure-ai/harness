import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.bench.cluster import cluster_ci
from harness.cli import app
from harness.scenario.load import load_set
from harness.stress.variant import group_sort_key

runner = CliRunner()


@pytest.fixture
def noise_file(tmp_path: Path) -> Path:
    path = tmp_path / "noise.txt"
    lines = [f"ran the full test suite for module_{i} and read the stack trace of failure number {i}" for i in range(400)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build(tmp_path: Path, noise_file: Path, *extra: str):
    out = tmp_path / "stress"
    r = runner.invoke(
        app,
        ["stress", "build", "--scenarios", "scenarios/dev/coincidence", "--out", str(out),
         "--noise-file", str(noise_file), "--seed", "7", *extra],
    )  # fmt: skip
    return r, out


def test_build_writes_screened_draft_variants_for_every_knob_value(tmp_path, noise_file):
    r, out = build(tmp_path, noise_file)
    assert r.exit_code == 0, r.output
    bases = load_set([Path("scenarios/dev/coincidence")], include_drafts=False).scenarios
    variants = load_set([out], include_drafts=True).scenarios
    assert len(variants) == len(bases) * (4 + 3 + 3)
    assert all(v.review.status == "draft" and v.provenance.variant is not None for v in variants)
    assert {v.id.split("--", 1)[1] for v in variants} >= {"noise-300", "position-late", "gap-30d"}
    assert f"wrote {len(variants)} variants" in r.output and "--include-drafts" in r.output


def test_build_is_deterministic(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "noise")
    first = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    _, again = build(tmp_path, noise_file, "--knob", "noise")
    assert {p.name: p.read_text(encoding="utf-8") for p in again.iterdir()} == first


def test_build_rejects_an_unknown_knob_and_a_committable_out(tmp_path, noise_file):
    r, _ = build(tmp_path, noise_file, "--knob", "colour")
    assert r.exit_code == 2 and "colour" in r.output
    r = runner.invoke(
        app, ["stress", "build", "--scenarios", "scenarios/dev/coincidence", "--out", "scenarios/stress-tmp",
              "--noise-file", str(noise_file)],
    )  # fmt: skip
    assert r.exit_code == 2 and "git-ignored" in r.output
    assert not Path("scenarios/stress-tmp").exists()


def test_build_refuses_variants_as_input(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    for p in out.iterdir():  # pretend someone approved them by hand
        p.write_text(p.read_text(encoding="utf-8").replace("status: draft", "status: approved"), encoding="utf-8")
    r = runner.invoke(app, ["stress", "build", "--scenarios", str(out), "--out", str(tmp_path / "again"),
                            "--noise-file", str(noise_file)])  # fmt: skip
    assert r.exit_code == 2


def test_bench_run_reports_curves_and_discrimination_for_variants(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "noise")
    reports = tmp_path / "reports"
    r = runner.invoke(
        app,
        ["bench", "run", "--scenarios", str(out), "--include-drafts", "--yes", "--out", str(reports),
         "--blind-seed", "123456789012345"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    (run_dir,) = list((reports / "scenarios").iterdir())
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "표 6. 난이도 곡선" in report and "표 7. 문항 변별력" in report
    assert "coincidence/noise=0" in report and "coincidence/noise=300" in report
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    curve = summary["stress"]["curve"]["all_store"]
    assert list(curve) == ["coincidence/noise=0", "coincidence/noise=20", "coincidence/noise=100", "coincidence/noise=300"]
    assert curve["coincidence/noise=0"]["n_bases"] == 4
    assert summary["stress"]["compared"] == ["all_store", "repeat_n"]
    assert set(summary["stress"]["discrimination"]) == set(curve)


def test_bench_run_skips_drafts_without_the_flag(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    r = runner.invoke(app, ["bench", "run", "--scenarios", str(out), "--yes", "--out", str(tmp_path / "r")])
    assert r.exit_code == 2 and "no scenario" in r.output


def test_harness_run_refuses_stress_variants(tmp_path, noise_file):
    _, out = build(tmp_path, noise_file, "--knob", "gap")
    r = runner.invoke(app, ["run", "--scenarios", str(out), "--include-drafts", "--out", str(tmp_path / "runs")])
    assert r.exit_code == 2 and "bench run" in r.output
    assert not (tmp_path / "runs").exists()


def test_cluster_ci_resamples_clusters():
    assert cluster_ci([]) is None and cluster_ci([(0, 0)]) is None
    assert cluster_ci([(3, 3), (3, 3)]) == (1.0, 1.0)
    low, high = cluster_ci([(5, 5), (0, 5), (5, 5), (0, 5)])
    assert 0.0 <= low < 0.5 < high <= 1.0
    # One cluster counted many times is still one cluster: the interval does not shrink.
    assert cluster_ci([(50, 50), (0, 50)]) == cluster_ci([(1, 1), (0, 1)])


def test_groups_sort_by_knob_value_order():
    groups = ["f/noise=300", "f/noise=20", "f/gap=30d", "f/position=late", "f/noise=0", "f/position=early", "f/gap=1h"]
    assert sorted(groups, key=group_sort_key) == [
        "f/noise=0", "f/noise=20", "f/noise=300", "f/position=early", "f/position=late", "f/gap=1h", "f/gap=30d",
    ]
