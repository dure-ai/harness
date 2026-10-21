"""Shared pytest setup."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the generation cache at a temporary directory, so no test touches ~/.cache."""
    root = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("T5_HARNESS_CACHE", str(root))
    return root


@pytest.fixture(autouse=True)
def _forget_verified_engine_urls() -> None:
    """Start every test with no engine address marked as already verified."""
    from harness.oss import hindsight, mem0

    hindsight._verified_urls.clear()  # pyright: ignore[reportPrivateUsage]
    mem0._verified_urls.clear()  # pyright: ignore[reportPrivateUsage]
