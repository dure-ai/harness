"""Download and inspect Open-SWE-Traces shards.

Needs `huggingface_hub` (extra `gen`) for the network calls; it is imported inside the
functions that use it, and only when no `api` is passed in.
"""

import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from harness.generate.traces import read_parquet

REPO_ID = "nvidia/Open-SWE-Traces"

# Model folders per dataset config and split (the agent), from the dataset card
# https://huggingface.co/datasets/nvidia/Open-SWE-Traces (section "configs").
# Files are `data/<split>/<model_dir>/<source>/train-XXXXX-of-YYYYY.parquet`.
CONFIG_MODELS: dict[str, dict[str, tuple[str, ...]]] = {
    "v1.2": {
        "openhands": ("qwen38_27b", "qwen38_flash_next"),
        "sweagent": ("qwen38_27b", "qwen38_flash_next"),
        "minisweagent": ("qwen38_27b", "qwen38_flash_next"),
    },
    "v1.1": {
        "openhands": ("qwen36_27b", "deepseek_v4_flash_0731"),
        "sweagent": ("qwen36_27b",),
        "minisweagent": ("qwen36_27b",),
    },
    "v1.0": {
        "openhands": ("minimax_m25", "qwen35_122b"),
        "sweagent": ("minimax_m25", "qwen35_122b"),
    },
}

_MODEL_DIR_PART = 2  # data / <split> / <model_dir> / ...
DEFAULT_TIMEOUT = 60.0  # seconds without an answer before `traces fetch` gives up


class FetchError(Exception):
    """A network step of `traces fetch` failed, e.g. the server did not answer in time."""


class ShardInfo(BaseModel):
    """One parquet file of the dataset.

    Attributes:
        path: Path inside the dataset repository.
        size: File size in bytes.
    """

    path: str
    size: int


class _Activity:
    """When the guarded call last showed a sign of life (monotonic seconds)."""

    def __init__(self) -> None:
        self.last = time.monotonic()

    def touch(self) -> None:
        self.last = time.monotonic()


def _guarded[T](call: Callable[[], T], timeout: float, activity: _Activity, what: str) -> T:
    """Run `call` in a daemon thread; give up after `timeout` seconds without activity.

    The HTTP layer of `huggingface_hub` has no overall time limit and retries timeouts
    several times, so a server that accepts the connection and never answers would hang
    the command. Waiting here is bounded instead: if `activity` has not been touched for
    `timeout` seconds the call is abandoned (its thread is a daemon and ends with the
    process) and a `FetchError` is raised.

    Args:
        call: The network operation.
        timeout: Seconds without activity before giving up.
        activity: Touched by the operation whenever it makes progress.
        what: What was being done, for the error message.

    Returns:
        What `call` returns.

    Raises:
        FetchError: If nothing happened for `timeout` seconds.
    """
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append((True, call()))
        except BaseException as e:  # handed to the caller below  # noqa: BLE001
            outcome.append((False, e))

    worker = threading.Thread(target=run, daemon=True)
    activity.touch()
    worker.start()
    while True:
        worker.join(min(0.05, timeout / 4))
        if outcome:
            ok, value = outcome[0]
            if ok:
                return value
            raise value
        if time.monotonic() - activity.last > timeout:
            raise FetchError(
                f"timed out: no answer or progress for {timeout:g} s while {what}; "
                "check the network or raise --timeout"
            )


def _check_timeout(timeout: float) -> None:
    if not timeout > 0:
        raise ValueError(f"timeout must be greater than 0 seconds, got {timeout!r}")


def _api(api: Any) -> Any:
    if api is not None:
        return api
    from huggingface_hub import HfApi

    return HfApi()


def list_shards(
    config: str, split: str, api: Any = None, *, timeout: float = DEFAULT_TIMEOUT
) -> list[ShardInfo]:
    """List the parquet files of one config and split, sorted by path.

    Args:
        config: Dataset version: `v1.0`, `v1.1` or `v1.2`.
        split: Agent folder: `openhands`, `sweagent` or `minisweagent`.
        api: Object with `HfApi.list_repo_tree`; defaults to a real `HfApi`.
        timeout: Seconds the whole listing may take before giving up; a server that
            accepts the connection and never answers fails after about this long.

    Returns:
        The files inside the model folders that belong to `config`.

    Raises:
        ValueError: If `config` is unknown or has no `split`, or `timeout` is not positive.
        FetchError: If the server does not answer within `timeout`.
    """
    _check_timeout(timeout)
    if config not in CONFIG_MODELS:
        raise ValueError(
            f"unknown config {config!r}; choose from {', '.join(CONFIG_MODELS)}"
        )
    models = CONFIG_MODELS[config].get(split)
    if models is None:
        raise ValueError(
            f"config {config} has no split {split!r}; choose from {', '.join(CONFIG_MODELS[config])}"
        )
    client = _api(api)
    entries = _guarded(
        lambda: list(
            client.list_repo_tree(
                repo_id=REPO_ID,
                repo_type="dataset",
                path_in_repo=f"data/{split}",
                recursive=True,
            )
        ),
        timeout,
        _Activity(),
        "listing the dataset files",
    )
    found = []
    for entry in entries:
        size = getattr(entry, "size", None)  # folders have none
        parts = entry.path.split("/")
        if (
            size is None
            or not entry.path.endswith(".parquet")
            or len(parts) <= _MODEL_DIR_PART
        ):
            continue
        if parts[_MODEL_DIR_PART] in models:
            found.append(ShardInfo(path=entry.path, size=size))
    return sorted(found, key=lambda s: s.path)


def fetch_shard(
    info: ShardInfo, dest: Path, api: Any = None, *, timeout: float = DEFAULT_TIMEOUT
) -> Path:
    """Download one shard unless it is already there.

    The limit is a stall limit, not a total one: a big file may take longer than
    `timeout` as long as bytes keep arriving (the progress bar of the real download
    counts as arriving). With an injected `api` there is no progress signal, so the
    limit then covers the whole call.

    Args:
        info: Shard to download.
        dest: Directory the repository path is recreated under.
        api: Object with `HfApi.hf_hub_download`; defaults to a real `HfApi`.
        timeout: Seconds without any answer or progress before giving up.

    Returns:
        Local path of the file, `dest/<info.path>`.

    Raises:
        ValueError: If `timeout` is not positive.
        FetchError: If the server stops answering for `timeout` seconds.
    """
    _check_timeout(timeout)
    target = dest / info.path
    if target.is_file() and target.stat().st_size == info.size:
        return target
    activity = _Activity()
    extra: dict[str, Any] = {}
    if api is None:
        from huggingface_hub.utils import tqdm as hf_tqdm

        class _Progress(hf_tqdm):  # type: ignore[misc]
            def update(self, n: float | None = 1) -> bool | None:
                activity.touch()
                return super().update(n)

        extra["tqdm_class"] = _Progress
    client = _api(api)
    got = _guarded(
        lambda: client.hf_hub_download(
            repo_id=REPO_ID, repo_type="dataset", filename=info.path, local_dir=dest, **extra
        ),
        timeout,
        activity,
        f"downloading {info.path}",
    )
    return Path(got)


def local_shards(root: Path) -> list[Path]:
    """Parquet files below `root`, sorted.

    Args:
        root: Directory to search, e.g. `cache_root() / "traces"`.

    Returns:
        The files; empty when `root` does not exist.
    """
    return sorted(root.rglob("*.parquet")) if root.is_dir() else []


class RepoCount(BaseModel):
    """Trajectories of one repository in the local cache.

    Attributes:
        repo: Repository, "owner/name".
        license: Its license as recorded in the data, if any.
        language: Its main language, if recorded.
        trajectories: Number of trajectories.
    """

    repo: str
    license: str | None
    language: str | None
    trajectories: int


class CacheStats(BaseModel):
    """What the local trace cache holds.

    Attributes:
        shards: Parquet files read.
        repos: Counts per repository, most trajectories first.
        trajectories: Total number of trajectories.
        tasks: Number of distinct `instance_id`.
        bundles: Tasks solved by two or more models, whose trajectories can be bundled.
    """

    shards: list[Path]
    repos: list[RepoCount]
    trajectories: int
    tasks: int
    bundles: int


def cache_stats(shards: list[Path]) -> CacheStats:
    """Count trajectories per repository and bundles, reading one trajectory at a time.

    Args:
        shards: Parquet files to read.

    Returns:
        The counts.
    """
    per_repo: Counter[tuple[str, str | None, str | None]] = Counter()
    models: dict[str, set[str]] = {}
    for path in shards:
        for t in read_parquet(path):
            per_repo[(t.repo, t.license, t.language)] += 1
            models.setdefault(t.instance_id, set()).add(t.model)
    repos = [
        RepoCount(repo=r, license=lic, language=lang, trajectories=n)
        for (r, lic, lang), n in sorted(
            per_repo.items(), key=lambda kv: (-kv[1], kv[0][0])
        )
    ]
    return CacheStats(
        shards=shards,
        repos=repos,
        trajectories=sum(per_repo.values()),
        tasks=len(models),
        bundles=sum(len(m) > 1 for m in models.values()),
    )
