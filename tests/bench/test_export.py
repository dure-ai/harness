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


def test_export_needs_the_dataset_in_the_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "empty"))
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tmp_path / "o")])
    assert r.exit_code == 2 and "bench fetch" in r.output


def test_export_takes_repeated_types(cached: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = runner.invoke(
        app,
        ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out),
         "--types", "single-session-user", "--types", "multi-session"],
    )
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in out.iterdir()) == ["lme-m1.yaml", "lme-o1.yaml"]


def test_export_screens_with_the_bench_config(cached: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import harness.bench.check as check

    seen: list[int] = []
    real = check.screened

    def spy(items, cfg):  # type: ignore[no-untyped-def]
        seen.append(cfg.k)
        return real(items, cfg)

    monkeypatch.setattr(check, "screened", spy)
    config = tmp_path / "bench.yaml"
    config.write_text(Path("config/bench-v0.1.yaml").read_text(encoding="utf-8").replace("k: 5", "k: 7"), encoding="utf-8")
    r = runner.invoke(
        app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tmp_path / "out"), "--config", str(config)]
    )
    assert r.exit_code == 0, r.output
    assert seen == [7]
    bad = runner.invoke(
        app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tmp_path / "o2"), "--config", str(tmp_path / "nope.yaml")]
    )
    assert bad.exit_code == 2 and "cannot load config" in bad.output


def _not_committable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LongMemEvalS, "commit_ok", False)
    monkeypatch.setitem(DATASETS, "longmemeval-s", LongMemEvalS())


def test_export_of_a_non_committable_dataset_may_go_outside_any_repository(
    cached: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _not_committable(monkeypatch)
    out = tmp_path / "out"  # tmp_path is not inside a git work tree
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(out)])
    assert r.exit_code == 0, r.output


def test_export_of_a_non_committable_dataset_needs_an_ignored_directory_inside_a_repository(
    cached: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    _not_committable(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    tracked = repo / "tracked"
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tracked)])
    assert r.exit_code == 2 and "git-ignored" in r.output and not tracked.exists()
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(repo / "ignored" / "x")])
    assert r.exit_code == 0, r.output


def test_export_without_git_says_so(cached: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _not_committable(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    r = runner.invoke(app, ["bench", "export", "--dataset", "longmemeval-s", "--out", str(tmp_path / "out")])
    assert r.exit_code == 2 and "git" in r.output and not (tmp_path / "out").exists()
