"""Model backends. A runner calls `llm.ask(prompt)` and does not know which one it got.

- ClaudeCli: the local Claude Code CLI as a plain model (no tools, no MCP, no settings,
  no session files), run from an empty directory. The CLI takes no temperature or
  max_tokens, and adds a small context of its own to every call; both are deviations
  from the benchmarks' own model calls and are recorded in each run's meta.
- OpenAICompat: any OpenAI-compatible chat-completions endpoint (e.g. Upstage Solar).

Answers are cached on disk by (backend, model, system, prompt), so a rerun or a second
metric over the same answers costs nothing.
"""

import hashlib
import json
import os
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SYSTEM = "You are a helpful assistant."


class Cache:
    """Model replies on disk, one file per key."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        """Return the stored reply for a key, or None."""
        p = self._path(key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, key: str, value: dict) -> None:
        """Store a reply, replacing the file in one step."""
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp%d" % os.getpid())
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(p)


class Backend:
    """A model behind `ask`, with a disk cache, retries and usage counts."""

    name = "?"

    def __init__(self, model: str, cache: Cache, system: str = SYSTEM, workers: int = 4) -> None:
        self.model, self.cache, self.system, self.workers = model, cache, system, workers
        self.calls = self.hits = 0
        self.usage = {"input": 0, "output": 0, "seconds": 0.0}

    def describe(self) -> dict:
        """Say what a run needs recorded about this backend."""
        return {"backend": self.name, "model": self.model, "system": self.system}

    def limits(self) -> list:
        """Give the settings that change an answer; they are part of the cache key."""
        return []

    def _call(self, prompt: str) -> dict:  # {"text", "input", "output"}
        raise NotImplementedError

    def ask(self, prompt: str) -> str:
        """Answer one prompt, from the cache when the same call was made before."""
        key = hashlib.sha256(json.dumps([self.name, self.model, self.system, prompt, self.limits()]).encode()).hexdigest()
        self.calls += 1
        if (hit := self.cache.get(key)) is not None:
            self.hits += 1
            return hit["text"]
        started = time.time()
        last: Exception | None = None
        for attempt in range(10):
            try:
                out = self._call(prompt)
                break
            except Exception as exc:  # rate limits, transient CLI or network failures
                last = exc
                time.sleep(min(5 * 2**attempt, 90))  # a rate limit clears within a minute or two
        else:
            raise RuntimeError(f"model call failed 10 times: {last}")
        out["seconds"] = round(time.time() - started, 2)
        self.cache.put(key, out)
        for k in ("input", "output", "seconds"):
            self.usage[k] += out.get(k) or 0
        return out["text"]

    def ask_many(self, prompts: list[str]) -> list[str]:
        """Answer prompts `workers` at a time, in the order given."""
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(self.ask, prompts))


class ClaudeCli(Backend):
    """The local Claude Code CLI as a plain model, run from an empty directory."""

    name = "claude-cli"

    def __init__(self, model: str, cache: Cache, cwd: Path, **kw) -> None:
        super().__init__(model, cache, **kw)
        self.cwd = cwd
        cwd.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict:
        """Say what a run needs recorded about this backend."""
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
        return {**super().describe(), "cli": version,
                "note": "temperature and max_tokens are not settable; the CLI adds its own short context"}

    def _call(self, prompt: str) -> dict:
        cmd = ["claude", "-p", "--model", self.model, "--system-prompt", self.system, "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome",
               "--disable-slash-commands", "--setting-sources", "", "--no-session-persistence",
               "--output-format", "json"]
        done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=self.cwd, timeout=600)
        out = json.loads(done.stdout)
        if out.get("is_error") or "result" not in out:
            raise RuntimeError(str(out)[:300])
        u = out.get("usage") or {}
        return {"text": out["result"], "output": u.get("output_tokens", 0),
                "input": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)}


class OpenAICompat(Backend):
    """An OpenAI-compatible chat-completions endpoint."""

    name = "openai-compat"

    def __init__(self, model: str, cache: Cache, base_url: str, api_key: str, temperature: float = 0.0,
                 max_tokens: int | None = None, **kw) -> None:
        super().__init__(model, cache, **kw)
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self.temperature, self.max_tokens = temperature, max_tokens

    def describe(self) -> dict:
        """Say what a run needs recorded about this backend."""
        return {**super().describe(), "base_url": self.base_url, "temperature": self.temperature,
                "max_tokens": self.max_tokens}

    def limits(self) -> list:
        """Give the settings that change an answer; they are part of the cache key."""
        return [self.temperature, self.max_tokens]

    def _call(self, prompt: str) -> dict:
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}]}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        req = urllib.request.Request(self.base_url + "/chat/completions", json.dumps(body).encode(),
                                     {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            out = json.load(resp)
        u = out.get("usage") or {}
        return {"text": out["choices"][0]["message"]["content"] or "", "input": u.get("prompt_tokens", 0),
                "output": u.get("completion_tokens", 0)}


def make(spec: str, root: Path, workers: int = 4, max_tokens: int | None = None) -> Backend:
    """spec: 'claude-cli:<model>' or 'openai:<model>' (endpoint from EXTBENCH_BASE_URL / EXTBENCH_API_KEY)."""
    kind, _, model = spec.partition(":")
    cache = Cache(root / "cache")
    if kind == "claude-cli":
        return ClaudeCli(model, cache, cwd=root / "cwd", workers=workers)
    if kind == "openai":
        return OpenAICompat(model, cache, os.environ["EXTBENCH_BASE_URL"], os.environ["EXTBENCH_API_KEY"],
                            workers=workers, max_tokens=max_tokens)
    raise SystemExit(f"unknown backend {spec!r}")
