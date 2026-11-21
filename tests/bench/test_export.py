import hashlib
import dataclasses
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.bench.dataset import bench_cache
from harness.bench.datasets import DATASETS
from harness.bench.datasets.longmemeval import LongMemEvalS
from harness.cli import app
from harness.scenario.load import load_scenario
from tests.bench.lme_fixture import lme_question, write_lme

runner = CliRunner()
QDATE = "2023/07/01 (Sat) 10:00"


@pytest.fixture
def cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A cache holding a small synthetic file under the dataset's name, whose declared size is adjusted to match."""
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = bench_cache("longmemeval-s") / "longmemeval_s_cleaned.json"
    path.parent.mkdir(parents=True)
    write_lme(
        path,
        [
            lme_question("m1", "multi-session", [("2023/05/01 (Mon) 09:00", [("user", "x")])], [0], QDATE),
            lme_question("m2_abs", "multi-session", [("2023/05/01 (Mon) 09:00", [("user", "y")])], [0], QDATE),
            lme_question("o1", "single-session-user", [("2023/05/01 (Mon) 09:00", [("user", "z")])], [0], QDATE),
        ],
    )
    real = LongMemEvalS.files
    size = path.stat().st_size
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        LongMemEvalS, "files", lambda self: [dataclasses.replace(f, size=size, sha256=sha) for f in real(self)]
    )
    return path


def test_export_writes_scenarios_and_reports_skips(cached: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in out.iterdir()) == ["lme-m1.yaml", "lme-o1.yaml"]
    assert "skipped m2_abs: abstention" in r.output
    raw = yaml.safe_load((out / "lme-m1.yaml").read_text(encoding="utf-8"))
    assert raw["family"] == "ext_recall" and raw["id"] == "lme-m1"
    assert load_scenario(out / "lme-m1.yaml").id == "lme-m1"


def test_export_filters_by_type_and_limit(cached: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = runner.invoke(
        app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out), "--types", "single-session-user"]
    )
    assert r.exit_code == 0 and [p.name for p in out.iterdir()] == ["lme-o1.yaml"]
    out2 = tmp_path / "out2"
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out2), "--limit", "1"])
    assert r.exit_code == 0 and [p.name for p in out2.iterdir()] == ["lme-m1.yaml"]


def test_export_refuses_a_dataset_that_may_not_be_committed_into_a_tracked_directory(
    cached: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LongMemEvalS, "commit_ok", False)
    monkeypatch.setitem(DATASETS, "longmemeval-s", LongMemEvalS())
    out = tmp_path / "out"  # not inside a git repository: check-ignore fails
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out)])
    assert r.exit_code == 2 and "git-ignored" in r.output and not out.exists()


def test_export_needs_the_dataset_in_the_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "empty"))
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tmp_path / "o")])
    assert r.exit_code == 2 and "bench fetch" in r.output
