import hashlib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from harness.bench import datasets
from harness.bench.dataset import FetchError, RemoteFile, bench_cache, fetch_file, is_cached
from harness.cli import app

runner = CliRunner()


def test_fetch_downloads_verifies_and_reuses(tmp_path: Path, served: Any) -> None:
    body = b'{"x": 1}'
    f = RemoteFile("d.json", served(body), len(body), hashlib.sha256(body).hexdigest())
    path = fetch_file(f, tmp_path)
    assert path == tmp_path / "d.json" and path.read_bytes() == body
    assert fetch_file(f, tmp_path) == path  # cached: no second request
    assert served.requests == 1


def test_fetch_rejects_a_bad_checksum_and_keeps_nothing(tmp_path: Path, served: Any) -> None:
    f = RemoteFile("d.json", served(b"other"), 5, "0" * 64)
    with pytest.raises(FetchError, match="sha256"):
        fetch_file(f, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_fetch_rejects_a_short_body(tmp_path: Path, served: Any) -> None:
    f = RemoteFile("d.json", served(b"abc"), 10, hashlib.sha256(b"abc").hexdigest())
    with pytest.raises(FetchError, match="size"):
        fetch_file(f, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bench_cache_follows_the_harness_cache_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path))
    assert bench_cache("longmemeval-s") == tmp_path / "bench" / "longmemeval-s"


class FakeDataset:
    name = "fake"
    license = "MIT"
    commit_ok = True
    converter_version = 3
    label_file = None

    def __init__(self, files: list[RemoteFile]) -> None:
        self._files = files

    def files(self) -> list[RemoteFile]:
        return self._files

    def convert(self, cache: Path, *, types: set[str] | None, limit: int | None) -> Any:
        return iter(())


def test_bench_list_and_fetch_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: Any) -> None:
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path))
    body = b"hello bench"
    fake = FakeDataset([RemoteFile("d.json", served(body), len(body), hashlib.sha256(body).hexdigest())])
    monkeypatch.setitem(datasets.DATASETS, "fake", fake)

    listed = runner.invoke(app, ["bench", "list"])
    assert listed.exit_code == 0
    assert "fake" in listed.output and "MIT" in listed.output and "MB" in listed.output
    assert "cached: no" in listed.output

    refused = runner.invoke(app, ["bench", "fetch", "--dataset", "fake"], input="n\n")
    assert refused.exit_code == 1
    assert served.requests == 0
    assert not is_cached(fake)

    unknown = runner.invoke(app, ["bench", "fetch", "--dataset", "nope", "--yes"])
    assert unknown.exit_code == 2 and "fake" in unknown.output

    done = runner.invoke(app, ["bench", "fetch", "--dataset", "fake", "--yes"])
    assert done.exit_code == 0
    assert str(bench_cache("fake")) in done.output
    assert (bench_cache("fake") / "d.json").read_bytes() == body

    again = runner.invoke(app, ["bench", "list"])
    assert "cached: yes" in again.output


def test_dataset_facts_come_from_the_dataset_itself() -> None:
    import hashlib
    from importlib import resources

    from harness.bench.datasets.longmemeval import LongMemEvalS
    from harness.bench.run import dataset_facts, scenario_facts

    assert dataset_facts(FakeDataset([]), {"a.json": "ab"}) == {
        "dataset": "fake", "license": "MIT", "file_sha256": {"a.json": "ab"}, "converter": "fake/3", "label_sha256": None,
    }
    lme = dataset_facts(LongMemEvalS(), {})
    label = (resources.files("harness.bench.datasets") / "longmemeval_ku_labels.yaml").read_bytes()
    assert lme["converter"] == "longmemeval-s/1" and lme["label_sha256"] == hashlib.sha256(label).hexdigest()
    assert scenario_facts("scenarios/dev") == {
        "dataset": "scenarios/dev", "license": "n/a", "file_sha256": {}, "converter": "n/a", "label_sha256": None,
    }
