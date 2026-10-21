"""Tests for listing, downloading and counting Open-SWE-Traces shards (no network)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from harness.generate.fetch import (
    CONFIG_MODELS,
    ShardInfo,
    cache_stats,
    fetch_shard,
    list_shards,
    local_shards,
)
from tests.gen_fixtures import make_parquet, make_trace


def _file(path: str, size: int = 10) -> Any:
    return SimpleNamespace(path=path, size=size)


class FakeApi:
    """Stands in for `HfApi`: serves a fixed tree and writes fake downloads."""

    def __init__(self, tree: list[Any] | None = None) -> None:
        self.tree = tree or []
        self.listed: list[dict[str, Any]] = []
        self.downloads: list[str] = []

    def list_repo_tree(self, **kw: Any) -> list[Any]:
        self.listed.append(kw)
        return self.tree

    def hf_hub_download(self, repo_id: str, filename: str, **kw: Any) -> str:
        self.downloads.append(filename)
        target = Path(kw["local_dir"]) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 10)
        return str(target)


TREE = [
    SimpleNamespace(path="data/minisweagent/qwen38_27b"),  # a folder: no size
    _file("data/minisweagent/qwen38_27b/src/train-00001-of-00002.parquet", 300),
    _file("data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet", 200),
    _file("data/minisweagent/qwen38_flash_next/src/train-00000-of-00001.parquet", 100),
    _file("data/minisweagent/qwen36_27b/src/train-00000-of-00001.parquet", 400),  # v1.1 model
    _file("data/minisweagent/qwen38_27b/src/README.md", 5),
]


def test_list_shards_filters_config_models() -> None:
    api = FakeApi(TREE)
    shards = list_shards("v1.2", "minisweagent", api)
    assert [s.path for s in shards] == [
        "data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet",
        "data/minisweagent/qwen38_27b/src/train-00001-of-00002.parquet",
        "data/minisweagent/qwen38_flash_next/src/train-00000-of-00001.parquet",
    ]
    assert [s.size for s in shards] == [200, 300, 100]
    assert api.listed == [
        {
            "repo_id": "nvidia/Open-SWE-Traces",
            "repo_type": "dataset",
            "path_in_repo": "data/minisweagent",
            "recursive": True,
        }
    ]
    assert [s.size for s in list_shards("v1.1", "minisweagent", FakeApi(TREE))] == [400]


def test_list_shards_rejects_unknown_config_and_split() -> None:
    with pytest.raises(ValueError, match="unknown config 'v9'"):
        list_shards("v9", "minisweagent", FakeApi())
    with pytest.raises(ValueError, match="no split 'minisweagent'"):
        list_shards("v1.0", "minisweagent", FakeApi())


def test_config_models_follow_the_dataset_card() -> None:
    assert CONFIG_MODELS["v1.2"]["sweagent"] == ("qwen38_27b", "qwen38_flash_next")
    assert CONFIG_MODELS["v1.1"]["openhands"] == ("qwen36_27b", "deepseek_v4_flash_0731")
    assert set(CONFIG_MODELS["v1.0"]) == {"openhands", "sweagent"}


def test_fetch_downloads_into_dest(tmp_path: Path) -> None:
    info = ShardInfo(path="data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet", size=10)
    api = FakeApi()
    got = fetch_shard(info, tmp_path, api)
    assert got == tmp_path / info.path and got.read_bytes() == b"x" * 10
    assert api.downloads == [info.path]


def test_fetch_skips_existing(tmp_path: Path) -> None:
    info = ShardInfo(path="data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet", size=10)
    target = tmp_path / info.path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"y" * 10)
    api = FakeApi()
    assert fetch_shard(info, tmp_path, api) == target
    assert api.downloads == []
    assert target.read_bytes() == b"y" * 10


def test_fetch_redownloads_a_file_of_the_wrong_size(tmp_path: Path) -> None:
    info = ShardInfo(path="data/minisweagent/qwen38_27b/src/train-00000-of-00002.parquet", size=10)
    target = tmp_path / info.path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"partial")
    api = FakeApi()
    fetch_shard(info, tmp_path, api)
    assert api.downloads == [info.path]


def test_cache_stats_counts_repos_tasks_and_bundles(tmp_path: Path) -> None:
    traces = [
        make_trace(trajectory_id="t1", instance_id="acme__widget-1", model="a"),
        make_trace(trajectory_id="t2", instance_id="acme__widget-1", model="b"),
        make_trace(trajectory_id="t3", instance_id="acme__widget-2", model="a"),
        make_trace(trajectory_id="t4", instance_id="zed__tool-1", repo="zed/tool", license="Apache-2.0", model="a"),
    ]
    (tmp_path / "x").mkdir()
    make_parquet(tmp_path / "x" / "one.parquet", traces[:2])
    make_parquet(tmp_path / "x" / "two.parquet", traces[2:])
    shards = local_shards(tmp_path)
    assert [p.name for p in shards] == ["one.parquet", "two.parquet"]
    stats = cache_stats(shards)
    assert (stats.trajectories, stats.tasks, stats.bundles) == (4, 3, 1)
    assert [(r.repo, r.license, r.trajectories) for r in stats.repos] == [
        ("acme/widget", "MIT", 3),
        ("zed/tool", "Apache-2.0", 1),
    ]


def test_local_shards_of_a_missing_dir_is_empty(tmp_path: Path) -> None:
    assert local_shards(tmp_path / "nope") == []
