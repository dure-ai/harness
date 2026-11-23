"""load_yaml parses with libyaml when it is installed, and gives exactly what the pure-Python
parser gives: approval hashes are computed from the parsed content, so any difference would
invalidate approved scenarios."""

import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from harness.yamlio import PurePythonUniqueKeyLoader, UniqueKeyLoader, load_yaml

ROOT = Path(__file__).resolve().parents[1]
# The tracked files only: a walk of the checkout would also pick up other checkouts or worktrees
# nested in it, and skip any directory that happens to be named `runs`.
FILES = sorted(
    ROOT / name
    for name in subprocess.run(
        ["git", "ls-files", "-z", "*.yaml", "*.yml"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    if name
)


def _typed(value):
    """The value with every leaf's type, so 1 and 1.0 and True do not compare equal."""
    if isinstance(value, dict):
        return {_typed(k): _typed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_typed(v) for v in value]
    return (type(value).__name__, value)


def test_the_repo_has_yaml_files_to_compare():
    assert len(FILES) > 40


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_yaml_file_parses_the_same_with_both_parsers(path):
    text = path.read_text(encoding="utf-8")
    fast = yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506
    pure = yaml.load(text, Loader=PurePythonUniqueKeyLoader)  # noqa: S506
    assert _typed(fast) == _typed(pure)


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\nb: 1.0\nc: true\nd: null\ne: ~\nf: '1'\ng: 0x1f\nh: 1_000\n",
        "t: 2026-10-01T13:00:00+09:00\nd: 2026-10-01\nq: '2026-10-01T13:00:00+09:00'\n",
        "s: |\n  line one\n  line two\nf: >\n  folded\n  text\n",
        "k: \"\\u00e9\\t\\x41\"\nko: 배포 서버를\nemoji: \"\\U0001F600\"\n",
        "list: [1, two, {x: y}]\nempty: {}\nnone: []\n",
        "base: &b {x: 1}\nmerged:\n  <<: *b\n  y: 2\n",
    ],
)
def test_tricky_scalars_parse_the_same(text):
    fast = yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506
    pure = yaml.load(text, Loader=PurePythonUniqueKeyLoader)  # noqa: S506
    assert _typed(fast) == _typed(pure)


def test_load_yaml_uses_libyaml_when_installed():
    if not getattr(yaml, "__with_libyaml__", False):
        pytest.skip("PyYAML was built without libyaml")
    assert issubclass(UniqueKeyLoader, yaml.CSafeLoader)


@pytest.mark.parametrize("loader", [UniqueKeyLoader, PurePythonUniqueKeyLoader])
def test_both_parsers_reject_a_repeated_key_with_its_line(loader):
    with pytest.raises(yaml.YAMLError, match=r"duplicate key 'k' \(line 3\)"):
        yaml.load("k: 1\nj: 2\nk: 3\n", Loader=loader)  # noqa: S506
    assert load_yaml("k: 1") == {"k": 1}
    assert isinstance(load_yaml("t: 2026-10-01T13:00:00+09:00")["t"], datetime)
    assert isinstance(load_yaml("d: 2026-10-01")["d"], date)


@pytest.mark.parametrize(
    ("text", "fast", "pure"),
    [
        ("a: b\tc\n", {"a": "b\tc"}, "ScannerError"),  # a tab inside a plain scalar
        ('a: "\\ud83d\\ude00"\n', "ScannerError", {"a": "\ud83d\ude00"}),  # an escaped surrogate pair
    ],
    ids=["tab-in-plain-scalar", "escaped-surrogate-pair"],
)
def test_the_parsers_differ_where_the_docstring_says(text, fast, pure):
    """Pins the differences UniqueKeyLoader's docstring lists, so a PyYAML or libyaml upgrade that
    changes them is noticed. No file in the repository uses either form."""
    if not getattr(yaml, "__with_libyaml__", False):
        pytest.skip("PyYAML was built without libyaml")
    for loader, expected in ((UniqueKeyLoader, fast), (PurePythonUniqueKeyLoader, pure)):
        if isinstance(expected, str):
            with pytest.raises(yaml.YAMLError) as caught:
                yaml.load(text, Loader=loader)  # noqa: S506
            assert type(caught.value).__name__ == expected
        else:
            assert yaml.load(text, Loader=loader) == expected  # noqa: S506


def test_only_the_pure_parser_shows_the_offending_line_in_errors():
    if not getattr(yaml, "__with_libyaml__", False):
        pytest.skip("PyYAML was built without libyaml")
    text = "a: [1, 2\nb: 3\n"
    messages = {}
    for loader in (UniqueKeyLoader, PurePythonUniqueKeyLoader):
        with pytest.raises(yaml.YAMLError) as caught:
            yaml.load(text, Loader=loader)  # noqa: S506
        messages[loader] = str(caught.value)
    assert "^" not in messages[UniqueKeyLoader]
    assert "^" in messages[PurePythonUniqueKeyLoader]
