"""Opt-in live check: both open-source engines, real containers, real model calls."""

import json
import os

import pytest

from tests.test_oss_cli import EXAMPLES, any_errors, run_cli

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESS_LIVE_OSS") != "1",
    reason="set HARNESS_LIVE_OSS=1 with both containers up",
)


def test_live_examples_both_engines(tmp_path):
    result, out = run_cli(
        tmp_path, EXAMPLES, "--include-drafts",
        "--engine", "hindsight", "--engine", "mem0", "--repeat", "1",
    )
    assert result.exit_code == 0, result.output
    assert not any_errors(json.loads((next(out.iterdir()) / "summary.json").read_text()))
