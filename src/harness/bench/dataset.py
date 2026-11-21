"""Dataset interface and the download cache for external benchmarks."""

import hashlib
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness.generate.config import cache_root
from harness.scenario.model import Scenario

_CHUNK = 1 << 20


@dataclass(frozen=True)
class RemoteFile:
    """A file to download, with the size and sha256 it must have."""

    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Converted:
    """One converted source item: exactly one of `scenario` and `skipped` is set.

    `group` is the dataset's own type of the item, e.g. LongMemEval `question_type`.
    """

    scenario: Scenario | None
    source_id: str
    group: str
    skipped: str | None


class Dataset(Protocol):
    """An external benchmark that can be downloaded and converted into scenarios.

    Attributes:
        name: Dataset name, as the CLI takes it.
        license: License of the source data.
        commit_ok: Whether converted scenarios may be committed to the repository.
        converter_version: Version of the conversion; raise it when converted scenarios change.
        label_file: Package resource in `harness.bench.datasets` with hand-made labels the
            conversion reads, whose sha256 reports record; None when there is none.
    """

    name: str
    license: str
    commit_ok: bool
    converter_version: int
    label_file: str | None

    def files(self) -> list[RemoteFile]:
        """The files that make up the dataset."""
        ...

    def convert(self, cache: Path, *, types: set[str] | None, limit: int | None) -> Iterator[Converted]:
        """Convert cached files into scenarios, optionally only some types and at most `limit` items."""
        ...


class FetchError(Exception):
    """A download failed or did not match its expected size or checksum."""


def bench_cache(dataset: str) -> Path:
    """Directory holding the downloaded files of `dataset`."""
    return cache_root() / "bench" / dataset


def is_cached(dataset: Dataset) -> bool:
    """Whether every file is present with the right size; sha256 is checked at fetch."""
    root = bench_cache(dataset.name)
    for f in dataset.files():
        path = root / f.name
        if not path.is_file() or path.stat().st_size != f.size:
            return False
    return True


def verify_cached(dataset: Dataset) -> dict[str, str]:
    """Compute the sha256 of every cached file and check it against the expected one.

    Returns:
        File name to the sha256 computed from the cached bytes.

    Raises:
        FetchError: A file's content does not match, naming the file.
    """
    root = bench_cache(dataset.name)
    actual: dict[str, str] = {}
    for f in dataset.files():
        digest = hashlib.sha256()
        with (root / f.name).open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                digest.update(chunk)
        actual[f.name] = digest.hexdigest()
        if actual[f.name] != f.sha256:
            raise FetchError(
                f"{f.name}: cached sha256 {actual[f.name]} does not match the expected {f.sha256}; "
                f"delete {root / f.name} and run harness bench fetch --dataset {dataset.name} again"
            )
    return actual


def fetch_file(
    file: RemoteFile,
    dest_dir: Path,
    *,
    timeout: float = 60.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Download `file` into `dest_dir`, verifying size and sha256 before it appears under its name."""
    final = dest_dir / file.name
    if final.is_file() and final.stat().st_size == file.size:
        return final
    dest_dir.mkdir(parents=True, exist_ok=True)
    part = dest_dir / (file.name + ".part")
    try:
        digest = hashlib.sha256()
        total = 0
        with opener(file.url, timeout=timeout) as response, part.open("wb") as out:
            while chunk := response.read(_CHUNK):
                digest.update(chunk)
                total += len(chunk)
                out.write(chunk)
        if total != file.size:
            raise FetchError(f"{file.name}: size {total} does not match the expected {file.size}")
        if digest.hexdigest() != file.sha256:
            raise FetchError(f"{file.name}: sha256 {digest.hexdigest()} does not match the expected {file.sha256}")
        part.replace(final)
    except FetchError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise FetchError(f"{file.name}: download failed: {type(e).__name__}: {e}") from e
    return final
