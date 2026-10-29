"""Run `harness generate` with `claude -p` as the LLM instead of the anthropic SDK.

Only `make_client` is swapped: prompts, schema checks, retries, cache and `check_draft` are the
harness's own code. The provenance records provider `claude-code`.

Usage (from the harness repo, after `uv sync --extra gen`):
    uv run python tools/gen_claude_p.py generate --family all --n 1 --max-traces 10 --seed 1
Every argument after the script name is passed to the harness CLI unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from harness.generate import llm
from harness.generate.config import GenConfig
from harness.generate.llm import CachedClient, LLMError, _ask_with_retry, _cache_settings

T = TypeVar("T", bound=BaseModel)

CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


class ClaudeCodeClient:
    """`claude -p --json-schema` as an `LLMClient`."""

    name = "claude-code"

    def __init__(self, cfg: GenConfig) -> None:
        self._cfg = cfg
        self.model = cfg.model
        self.last_model: str | None = None

    def _send(self, system: str, user: str, schema: dict) -> str:
        cmd = [
            CLAUDE, "-p",
            "--model", self.model,
            "--effort", self._cfg.effort,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system,
            "--tools", "",
        ]
        try:
            proc = subprocess.run(
                cmd, input=user, capture_output=True, text=True,
                timeout=self._cfg.timeout_s * (self._cfg.max_retries + 1),
                cwd=Path.home(),  # keep the repo's CLAUDE.md and settings out of the prompt
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError("claude-code: request timed out") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()[-800:]
            fatal = any(s in err.lower() for s in ("not logged in", "authentication", "login", "invalid model", "unknown model"))
            raise LLMError(f"claude-code: exit {proc.returncode}: {err}", fatal=fatal)
        try:
            reply = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude-code: output is not JSON: {proc.stdout[:300]}") from exc
        if reply.get("is_error"):
            raise LLMError(f"claude-code: {reply.get('result')}")
        served = reply.get("modelUsage") or {}
        self.last_model = next(iter(served), None)
        out = reply.get("structured_output")
        if out is None:
            dbg = Path(os.environ.get("T5_HARNESS_CACHE", Path.home() / ".cache" / "t5-harness")) / "claude-p-debug"
            dbg.mkdir(parents=True, exist_ok=True)
            n = len(list(dbg.glob("*.json")))
            (dbg / f"{n:03d}.json").write_text(json.dumps({"reply": reply, "system": system, "user": user}, ensure_ascii=False, indent=1))
            raise LLMError(
                f"claude-code: no structured_output (stop={reply.get('stop_reason')}, "
                f"terminal={reply.get('terminal_reason')}, result={str(reply.get('result'))[:200]!r}; saved {dbg / f'{n:03d}.json'})"
            )
        return json.dumps(out)

    def complete_json(
        self, kind: str, system: str, user: str, schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Ask claude -p for one JSON object that fits `schema`; retry once on invalid output."""
        # Not `anthropic.transform_schema`: it drops `additionalProperties` maps (BodySlots keys
        # text by fact/event id), and claude -p accepts the plain pydantic schema.
        api_schema = schema.model_json_schema()
        return _ask_with_retry(lambda u: self._send(system, u, api_schema), user, schema, validate)


def make_client(cfg: GenConfig, cache_dir: Path):
    """Replacement for `harness.generate.llm.make_client`: claude -p for every real provider."""
    if cfg.provider == "fake":
        return _orig_make_client(cfg, cache_dir)
    return CachedClient(ClaudeCodeClient(cfg), cache_dir, cfg.prompt_version, _cache_settings(cfg))


_orig_make_client = llm.make_client
llm.make_client = make_client

if __name__ == "__main__":
    from harness.cli import app

    sys.argv = ["harness", *sys.argv[1:]]
    app()
