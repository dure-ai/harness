"""LLM clients for scenario generation: Claude, OpenAI-compatible servers, a canned fake, and a response cache.

SDK imports happen inside functions so `harness run` works without the `gen` extra.
"""

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from harness.generate.config import GenConfig

T = TypeVar("T", bound=BaseModel)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_AUTH_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError"})


class LLMError(Exception):
    """A generation call failed; the message is one line meant for the user.

    Attributes:
        fatal: True when retrying other items is pointless (missing key, auth or permission error).
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        """Create the error.

        Args:
            message: One-line explanation shown to the user.
            fatal: Whether the whole run should stop.
        """
        super().__init__(message)
        self.fatal = fatal


class LLMClient(Protocol):
    """Asks a model for JSON that fits a pydantic schema.

    Attributes:
        name: Provider name (`anthropic`, `openai`, `fake`).
        model: Model name.
    """

    name: str
    model: str

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Ask for one JSON object and validate it.

        The reply must parse as `schema`. If it does not, or `validate` raises `ValueError`,
        the model is asked once more with the error appended.

        Args:
            kind: Request label (`candidates`, `bodies`, ...), used by fakes and the cache key.
            system: System prompt.
            user: User prompt.
            schema: Pydantic model the reply must fit.
            validate: Extra check on the parsed reply; raises `ValueError` to reject it.

        Returns:
            The validated reply.

        Raises:
            LLMError: If the call fails or the reply is still invalid after one retry.
        """
        ...


def _summarize(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()[:5]
        ]
        return "; ".join(parts)
    return " ".join(str(exc).split())


def _ask_with_retry(
    ask: Callable[[str], str],
    user: str,
    schema: type[T],
    validate: Callable[[T], None] | None,
) -> T:
    """Call `ask(user)`, validate the text, and ask once more with the error if it fails."""
    prompt = user
    last = ""
    for attempt in range(2):
        text = ask(prompt)
        try:
            value = schema.model_validate_json(text)
            if validate is not None:
                validate(value)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            last = _summarize(exc)
            prompt = f"{user}\n\nyour previous output failed validation: {last}"
            continue
        return value
    raise LLMError(f"model output failed validation twice: {last}")


class FakeClient:
    """Replays canned responses, for tests and offline dry runs.

    Attributes:
        name: Always `fake`.
        model: Always `fake`.
        requests: Every `(kind, system, user)` asked, retries included.
    """

    name = "fake"
    model = "fake"

    def __init__(
        self,
        responses: Mapping[str, list[dict[str, Any] | str]] | None = None,
        responders: Mapping[str, Callable[[str, str], dict[str, Any]]] | None = None,
    ) -> None:
        """Create the fake.

        Args:
            responses: Per kind, replies handed out in order. A `str` is returned verbatim
                (to test broken JSON); a `dict` is serialized.
            responders: Per kind, called as `(system, user)` once `responses` runs out.
        """
        self._queues = {kind: list(items) for kind, items in (responses or {}).items()}
        self._responders = dict(responders or {})
        self.requests: list[tuple[str, str, str]] = []

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Return the next canned reply for `kind`, validated like a real client would.

        Args:
            kind: Request label.
            system: System prompt.
            user: User prompt.
            schema: Pydantic model the reply must fit.
            validate: Extra check; raises `ValueError` to reject.

        Returns:
            The validated reply.

        Raises:
            LLMError: If no reply is available for `kind` or the replies are invalid.
        """

        def ask(prompt: str) -> str:
            self.requests.append((kind, system, prompt))
            queue = self._queues.get(kind)
            if queue:
                reply = queue.pop(0)
            elif kind in self._responders:
                reply = self._responders[kind](system, prompt)
            else:
                raise LLMError(f"fake: no canned response for kind {kind!r}")
            return reply if isinstance(reply, str) else json.dumps(reply)

        return _ask_with_retry(ask, user, schema, validate)


class CachedClient:
    """Stores validated replies on disk so a repeated request is free.

    Only replies that passed the schema and `validate` are stored. A stored reply that
    fails the current `validate` counts as a miss.

    Attributes:
        name: Provider name of the wrapped client.
        model: Model name of the wrapped client.
        calls: Requests forwarded to the wrapped client.
        hits: Requests answered from disk.
    """

    def __init__(self, inner: LLMClient, cache_dir: Path, prompt_version: str) -> None:
        """Wrap a client.

        Args:
            inner: Client that answers cache misses.
            cache_dir: Directory for one JSON file per cached reply.
            prompt_version: Prompt revision; part of the key so prompt changes never reuse replies.
        """
        self._inner = inner
        self._dir = Path(cache_dir)
        self._prompt_version = prompt_version
        self.name = inner.name
        self.model = inner.model
        self.calls = 0
        self.hits = 0

    def _key(self, kind: str, system: str, user: str, schema: type[BaseModel]) -> str:
        parts = [
            self.name,
            self.model,
            self._prompt_version,
            kind,
            system,
            user,
            schema.model_json_schema(),
        ]
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Answer from disk when possible, else ask the wrapped client and store the reply.

        Args:
            kind: Request label.
            system: System prompt.
            user: User prompt.
            schema: Pydantic model the reply must fit.
            validate: Extra check; raises `ValueError` to reject.

        Returns:
            The validated reply.

        Raises:
            LLMError: Whatever the wrapped client raises.
        """
        path = self._dir / f"{self._key(kind, system, user, schema)}.json"
        if path.is_file():
            try:
                cached = schema.model_validate_json(path.read_text(encoding="utf-8"))
                if validate is not None:
                    validate(cached)
            except (ValueError, OSError):
                pass  # unreadable, stale or no longer valid: ask again
            else:
                self.hits += 1
                return cached
        self.calls += 1
        value = self._inner.complete_json(kind, system, user, schema, validate)
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(value.model_dump_json(), encoding="utf-8")
        tmp.replace(path)
        return value


def _is_auth_error(exc: BaseException) -> bool:
    return any(cls.__name__ in _AUTH_ERRORS for cls in type(exc).__mro__)


class AnthropicClient:
    """Claude through the `anthropic` SDK with structured output and server-side fallback.

    Temperature and thinking settings are never sent: the default model rejects them.

    Attributes:
        name: Always `anthropic`.
        model: Model name from the config.
    """

    name = "anthropic"
    _KEY_HINT = "ANTHROPIC_API_KEY (or ant auth login)"

    def __init__(self, cfg: GenConfig, sdk: Any = None) -> None:
        """Create the client.

        Args:
            cfg: Generation config.
            sdk: Object with an `Anthropic(**kwargs)` factory, replacing the `anthropic`
                module. Tests inject a fake.
        """
        if sdk is None:
            import anthropic as sdk
        self._cfg = cfg
        self.model = cfg.model
        self._client = sdk.Anthropic(**({"base_url": cfg.base_url} if cfg.base_url else {}))

    def _send(self, system: str, user: str, schema: dict[str, Any]) -> str:
        try:
            reply = self._client.beta.messages.create(
                model=self.model,
                max_tokens=self._cfg.max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self._cfg.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            if _is_auth_error(exc) or (
                isinstance(exc, TypeError) and "authentication method" in str(exc)
            ):
                raise LLMError(
                    f"anthropic: authentication failed; set {self._KEY_HINT}", fatal=True
                ) from exc
            raise LLMError(f"anthropic: {type(exc).__name__}: {' '.join(str(exc).split())}") from exc
        if reply.stop_reason == "refusal":
            raise LLMError("anthropic: the model refused this request")
        return "".join(block.text for block in reply.content if block.type == "text")

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Ask Claude for one JSON object that fits `schema`.

        Args:
            kind: Request label (unused by this provider).
            system: System prompt.
            user: User prompt.
            schema: Pydantic model the reply must fit.
            validate: Extra check; raises `ValueError` to reject.

        Returns:
            The validated reply.

        Raises:
            LLMError: On refusal, API failure, missing credentials (fatal), or invalid output.
        """
        import anthropic

        api_schema = anthropic.transform_schema(schema)
        return _ask_with_retry(lambda u: self._send(system, u, api_schema), user, schema, validate)


class OpenAIClient:
    """An OpenAI-compatible chat endpoint (OpenAI itself, vLLM, Ollama, ...).

    Attributes:
        name: Always `openai`.
        model: Model name from the config.
    """

    name = "openai"

    def __init__(self, cfg: GenConfig, sdk: Any = None) -> None:
        """Create the client.

        Args:
            cfg: Generation config.
            sdk: Object with an `OpenAI(base_url=, api_key=)` factory, replacing the `openai`
                module. Tests inject a fake.

        Raises:
            LLMError: Fatal, if the API key environment variable is not set.
        """
        self._env = cfg.api_key_env or "OPENAI_API_KEY"
        key = os.environ.get(self._env)
        if not key:
            raise LLMError(f"openai: {self._env} is not set", fatal=True)
        if sdk is None:
            import openai as sdk
        self._cfg = cfg
        self.model = cfg.model
        self._client = sdk.OpenAI(base_url=cfg.base_url, api_key=key)

    def _send(self, system: str, user: str) -> str:
        try:
            reply = self._client.chat.completions.create(
                model=self.model,
                temperature=self._cfg.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:
            if _is_auth_error(exc):
                raise LLMError(
                    f"openai: authentication failed; check {self._env}", fatal=True
                ) from exc
            raise LLMError(f"openai: {type(exc).__name__}: {' '.join(str(exc).split())}") from exc
        return reply.choices[0].message.content or ""

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        schema: type[T],
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Ask the endpoint for one JSON object that fits `schema`.

        The schema is appended to the system prompt because `json_object` mode does not
        enforce one.

        Args:
            kind: Request label (unused by this provider).
            system: System prompt.
            user: User prompt.
            schema: Pydantic model the reply must fit.
            validate: Extra check; raises `ValueError` to reject.

        Returns:
            The validated reply.

        Raises:
            LLMError: On API failure, auth failure (fatal), or invalid output.
        """
        full_system = (
            f"{system}\n\nReply with a single JSON object matching this JSON schema:\n"
            f"{json.dumps(schema.model_json_schema())}"
        )
        return _ask_with_retry(lambda u: self._send(full_system, u), user, schema, validate)


def make_client(cfg: GenConfig, cache_dir: Path) -> LLMClient:
    """Build the client for `cfg.provider`.

    Real providers are wrapped in a `CachedClient`. The fake is not: its replies come from
    a file that may change between runs, and caching would hide the change.

    Args:
        cfg: Generation config.
        cache_dir: Directory for cached replies of real providers.

    Returns:
        A ready client.

    Raises:
        LLMError: Fatal, if a required API key is missing.
    """
    if cfg.provider == "fake":
        canned: dict[str, list[dict[str, Any] | str]] = {}
        if cfg.fake_responses is not None and Path(cfg.fake_responses).is_file():
            canned = json.loads(Path(cfg.fake_responses).read_text(encoding="utf-8"))
        fake = FakeClient(responses=canned)
        fake.model = cfg.model
        return fake
    inner: LLMClient = AnthropicClient(cfg) if cfg.provider == "anthropic" else OpenAIClient(cfg)
    return CachedClient(inner, cache_dir, cfg.prompt_version)
