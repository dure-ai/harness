import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cortex_mem  # noqa: F401  (imported so its module entry can be patched)
import pytest
from cortex_mem.embed import HashEmbedder
from extbench import embedding, memory

T0 = datetime(2023, 5, 8, 13, 56, tzinfo=UTC)
PROMOTE_ALL = "cortex_mem.engines.baseline:PromoteAllEngine"
LOOPBACK = "http://127.0.0.1:18081/v1"


class FakeTransport:
    """Stands in for QwenEmbedder: deterministic vectors, a call log, and a settable failure."""

    def __init__(self) -> None:
        self.model = "fake-embed"
        self.failure: Exception | None = None
        self.calls: list[tuple[list[str], str]] = []
        self._hash = HashEmbedder(dim=32)

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        self.calls.append((list(texts), kind))
        return self._hash.embed(texts, "passage")


def records() -> list[memory.Record]:
    lines = [
        "1:56 pm on 8 May, 2023 | Caroline: I went to a LGBTQ support group yesterday",
        "1:56 pm on 8 May, 2023 | Melanie: That sounds lovely, how did it go?",
        "1:56 pm on 8 May, 2023 | Caroline: I am thinking about a career in counseling",
    ]
    return [memory.Record(f"r{i}", text, text.split("| ")[1].split(":")[0], T0 + timedelta(seconds=i))
            for i, text in enumerate(lines)]


@pytest.fixture
def caches() -> Iterator[Callable[..., embedding.CachedEmbedder]]:
    """Make CachedEmbedders that are all closed when the test ends (Python 3.13 warns on an open one)."""
    made: list[embedding.CachedEmbedder] = []

    def make(inner: FakeTransport, path: Path) -> embedding.CachedEmbedder:
        made.append(embedding.CachedEmbedder(inner, path))
        return made[-1]

    yield make
    for cache in made:
        cache.close()


def sent_texts(transport: FakeTransport) -> list[str]:
    return [t for texts, _ in transport.calls for t in texts]


def test_cache_serves_repeated_texts_without_calling_the_transport(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    transport = FakeTransport()
    path = tmp_path / "embeddings.sqlite3"
    first = caches(transport, path)

    # Act
    a = first.embed(["alpha", "beta"], "passage")
    b = first.embed(["beta", "alpha", "gamma"], "passage")
    again = caches(transport, path).embed(["gamma", "alpha"], "passage")

    # Assert
    assert sent_texts(transport) == ["alpha", "beta", "gamma"]
    assert b[:2] == [a[1], a[0]]
    assert again == [b[2], a[0]]
    assert first.dim == 32


def test_cache_keys_vectors_by_model(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    path = tmp_path / "embeddings.sqlite3"
    one, other = FakeTransport(), FakeTransport()
    other.model = "another-embed"

    # Act
    _ = caches(one, path).embed(["alpha"], "passage")
    _ = caches(other, path).embed(["alpha"], "passage")

    # Assert
    assert sent_texts(other) == ["alpha"]


def test_queries_carry_the_conversation_instruction_once(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    transport = FakeTransport()
    cached = caches(transport, tmp_path / "e.sqlite3")

    # Act
    _ = cached.embed(["what did Caroline research?"], "query")

    # Assert
    assert transport.calls == [
        ([f"Instruct: {embedding.QUERY_INSTRUCTION}\nQuery: what did Caroline research?"], "passage")
    ]


def test_engine_arm_with_an_embedder_builds_an_index_and_embeds_every_record(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    transport = FakeTransport()
    cached = caches(transport, tmp_path / "e.sqlite3")
    settings = {"group_threshold": 0.8}

    # Act
    mem = memory.make(f"engine:{PROMOTE_ALL}", records(), "ns", tmp_path, cached, settings)
    handed = mem.recall("career in counseling", 2)

    # Assert
    assert mem.rt.index is not None
    assert {r.text for r in records()} <= set(sent_texts(transport))
    assert any("counseling" in text for text in handed)


def test_engine_arm_without_an_embedder_runs_with_no_index(tmp_path: Path) -> None:
    # Act
    mem = memory.make(f"engine:{PROMOTE_ALL}", records(), "ns", tmp_path)

    # Assert
    assert mem.rt.index is None
    assert mem.recall("support group", 3)


def test_engine_arm_fails_once_the_embedder_has_failed(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    transport = FakeTransport()
    cached = caches(transport, tmp_path / "e.sqlite3")
    mem = memory.make(f"engine:{PROMOTE_ALL}", records(), "ns", tmp_path, cached, {"group_threshold": 0.8})
    transport.failure = RuntimeError("HTTP 503")

    # Act / Assert
    with pytest.raises(RuntimeError, match="embedding failed"):
        _ = mem.recall("support group", 3)


def test_engine_arm_rejects_an_embedder_without_a_threshold(tmp_path: Path, caches: Callable[..., embedding.CachedEmbedder]) -> None:
    # Arrange
    cached = caches(FakeTransport(), tmp_path / "e.sqlite3")

    # Act / Assert
    with pytest.raises(ValueError, match="group threshold"):
        _ = memory.EngineMemory(records(), PROMOTE_ALL, cached)


def test_settings_are_none_without_an_embedding_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.delenv("EXTBENCH_EMBED_URL", raising=False)

    # Act / Assert
    assert embedding.settings_from_env() is None


def test_settings_come_from_the_environment_and_record_the_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("EXTBENCH_EMBED_URL", LOOPBACK + "/")
    monkeypatch.setenv("EXTBENCH_EMBED_MODEL", "Qwen3-Embedding-4B-Q4_K_M.gguf")
    monkeypatch.setenv("EXTBENCH_GROUP_THRESHOLD", "0.75")

    # Act
    settings = embedding.settings_from_env()

    # Assert
    assert settings == {
        "base_url": LOOPBACK,
        "model": "Qwen3-Embedding-4B-Q4_K_M.gguf",
        "group_threshold": 0.75,
        "query_instruction": embedding.QUERY_INSTRUCTION,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [("EXTBENCH_EMBED_URL", "https://api.example.com/v1"), ("EXTBENCH_GROUP_THRESHOLD", "1.5")],
)
def test_invalid_settings_stop_the_run(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    # Arrange
    monkeypatch.setenv("EXTBENCH_EMBED_URL", LOOPBACK)
    monkeypatch.setenv(name, value)

    # Act / Assert
    with pytest.raises(SystemExit, match="invalid embedding settings"):
        _ = embedding.settings_from_env()


def test_core_facts_report_the_pin_of_an_installed_core_not_the_surrounding_repository() -> None:
    # Arrange: the installed package lives in the harness's .venv, inside the harness repository
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text()
    pinned = re.search(r'cortex-mem = \{ git = "[^"]+", rev = "([0-9a-f]+)" \}', pyproject)
    assert pinned is not None

    # Act
    facts = memory.core_facts()

    # Assert
    assert Path(facts["path"]).name == "cortex_mem"
    assert facts["commit"] == pinned.group(1)
    assert facts["uncommitted_changes"] is None


def test_core_facts_report_a_core_checkout_by_its_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a git repository laid out like core, whose src/cortex_mem is the imported package
    package = tmp_path / "core" / "src" / "cortex_mem"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("")
    git = ["git", "-C", str(tmp_path / "core"), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "core"]):
        _ = subprocess.run([*git, *args], check=True, capture_output=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    _ = (package / "extra.py").write_text("")
    monkeypatch.setattr(sys.modules["cortex_mem"], "__file__", str(package / "__init__.py"))

    # Act
    facts = memory.core_facts()

    # Assert
    assert facts == {"path": str(package.resolve()), "commit": head, "uncommitted_changes": True}


def test_a_cache_used_as_a_context_manager_is_closed_after(tmp_path: Path) -> None:
    # Act
    with embedding.CachedEmbedder(FakeTransport(), tmp_path / "e.sqlite3") as cached:
        _ = cached.embed(["alpha"], "passage")

    # Assert
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        _ = cached.embed(["beta"], "passage")
