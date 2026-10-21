"""Shared pytest setup."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the generation cache at a temporary directory, so no test touches ~/.cache."""
    root = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("T5_HARNESS_CACHE", str(root))
    return root
