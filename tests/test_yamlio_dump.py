from pathlib import Path

import yaml

from harness.yamlio import dump_yaml


def safe(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def test_dump_yaml_writes_what_safe_dump_writes_for_every_scenario_file():
    files = sorted(Path("scenarios").rglob("*.yaml"))
    assert files
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert dump_yaml(data) == safe(data), path


def test_dump_yaml_matches_safe_dump_on_awkward_text():
    for text in ("emoji 😀 here", "한국어 문장 \"따옴표\" 와 'apostrophe'", "C:\\Users\\path", "tab\there", "line\nbreak",
                 "trailing space ", "- list-like", "key: value", "x" * 500, "\u2028 separator", "null", "0123"):
        data = {"text": text, "items": [text, {"nested": text}]}
        assert dump_yaml(data) == safe(data), text
