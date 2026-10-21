"""Tests for the LLM clients: cache, retry-once validation, fake provider and request shapes."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from harness.generate.config import GenConfig
from harness.generate.llm import (
    AnthropicClient,
    CachedClient,
    FakeClient,
    LLMError,
    OpenAIClient,
    make_client,
)


class Out(BaseModel):
    ok: bool


class Other(BaseModel):
    name: str


def _cfg(**over: Any) -> GenConfig:
    data: dict[str, Any] = {
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "temperature": 0.2,
        "prompt_version": "gen-v0.1",
        "license_allowlist": ["MIT"],
        "banned_cue_words": ["outdated"],
    }
    data.update(over)
    return GenConfig.model_validate(data)


def test_cache_hit_skips_inner(tmp_path: Path) -> None:
    inner = FakeClient(responses={"k": [{"ok": True}]})
    client = CachedClient(inner, tmp_path / "llm", "v1")
    first = client.complete_json("k", "sys", "usr", Out)
    second = client.complete_json("k", "sys", "usr", Out)
    assert first == second == Out(ok=True)
    assert len(inner.requests) == 1
    assert (client.calls, client.hits) == (1, 1)


def test_cache_key_changes_with_prompt_version(tmp_path: Path) -> None:
    inner = FakeClient(responders={"k": lambda s, u: {"ok": True}})
    CachedClient(inner, tmp_path, "v1").complete_json("k", "s", "u", Out)
    CachedClient(inner, tmp_path, "v2").complete_json("k", "s", "u", Out)
    assert len(inner.requests) == 2
    CachedClient(inner, tmp_path, "v2").complete_json("k", "s", "u", Out)
    assert len(inner.requests) == 2


def test_cache_key_changes_with_schema(tmp_path: Path) -> None:
    inner = FakeClient(responders={"k": lambda s, u: {"ok": True, "name": "x"}})
    client = CachedClient(inner, tmp_path, "v1")
    client.complete_json("k", "s", "u", Out)
    client.complete_json("k", "s", "u", Other)
    assert len(inner.requests) == 2


def test_cached_entry_failing_validate_counts_as_miss(tmp_path: Path) -> None:
    inner = FakeClient(responses={"k": [{"ok": False}, {"ok": True}]})
    client = CachedClient(inner, tmp_path, "v1")
    client.complete_json("k", "s", "u", Out)  # no validate: {"ok": False} is cached

    def need_ok(value: Out) -> None:
        if not value.ok:
            raise ValueError("ok must be true")

    result = client.complete_json("k", "s", "u", Out, need_ok)
    assert result.ok is True
    assert client.hits == 0
    assert client.calls == 2


def test_responses_failing_validate_are_not_cached(tmp_path: Path) -> None:
    inner = FakeClient(responses={"k": [{"ok": False}, {"ok": False}]})
    client = CachedClient(inner, tmp_path, "v1")

    def need_ok(value: Out) -> None:
        if not value.ok:
            raise ValueError("ok must be true")

    with pytest.raises(LLMError):
        client.complete_json("k", "s", "u", Out, need_ok)
    assert list(tmp_path.glob("*.json")) == []


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    inner = FakeClient(responders={"k": lambda s, u: {"ok": True}})
    client = CachedClient(inner, tmp_path, "v1")
    client.complete_json("k", "s", "u", Out)
    (entry,) = tmp_path.glob("*.json")
    entry.write_text("{not json", encoding="utf-8")
    assert client.complete_json("k", "s", "u", Out) == Out(ok=True)
    assert len(inner.requests) == 2


def test_invalid_json_retried_once_then_succeeds() -> None:
    client = FakeClient(responses={"k": ["{bad", {"ok": True}]})
    assert client.complete_json("k", "sys", "usr", Out) == Out(ok=True)
    assert len(client.requests) == 2
    assert client.requests[0] == ("k", "sys", "usr")
    assert client.requests[1][2].startswith("usr")
    assert "failed validation" in client.requests[1][2]


def test_validate_value_error_triggers_the_same_retry() -> None:
    client = FakeClient(responses={"k": [{"ok": False}, {"ok": True}]})

    def need_ok(value: Out) -> None:
        if not value.ok:
            raise ValueError("ok must be true")

    assert client.complete_json("k", "s", "u", Out, need_ok).ok is True
    assert "ok must be true" in client.requests[1][2]


def test_invalid_twice_raises_llm_error() -> None:
    client = FakeClient(responses={"k": ["{bad", '{"ok": 3}']})
    with pytest.raises(LLMError) as exc:
        client.complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert len(client.requests) == 2


def test_fake_falls_back_to_responder_after_responses() -> None:
    seen: list[tuple[str, str]] = []

    def responder(system: str, user: str) -> dict[str, Any]:
        seen.append((system, user))
        return {"ok": False}

    client = FakeClient(responses={"k": [{"ok": True}]}, responders={"k": responder})
    assert client.complete_json("k", "s", "u", Out).ok is True
    assert client.complete_json("k", "s2", "u2", Out).ok is False
    assert seen == [("s2", "u2")]


def test_fake_without_response_for_kind_raises() -> None:
    with pytest.raises(LLMError, match="nothing"):
        FakeClient().complete_json("nothing", "s", "u", Out)


def test_fake_does_not_consume_the_callers_lists() -> None:
    canned: dict[str, list[dict[str, Any] | str]] = {"k": [{"ok": True}]}
    FakeClient(responses=canned).complete_json("k", "s", "u", Out)
    assert canned == {"k": [{"ok": True}]}


class _Recorder:
    """Stands in for an SDK client: records create() kwargs and returns a canned reply."""

    def __init__(self, reply: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply = reply

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _anthropic_sdk(reply: Any, made: list[dict[str, Any]] | None = None) -> tuple[Any, _Recorder]:
    rec = _Recorder(reply)

    def factory(**kwargs: Any) -> Any:
        if made is not None:
            made.append(kwargs)
        return SimpleNamespace(beta=SimpleNamespace(messages=rec))

    return SimpleNamespace(Anthropic=factory), rec


def _anthropic_reply(text: str, stop_reason: str = "end_turn") -> Any:
    blocks = [
        SimpleNamespace(type="thinking", thinking="hmm"),
        SimpleNamespace(type="text", text=text),
    ]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def test_anthropic_request_shape() -> None:
    sdk, rec = _anthropic_sdk(_anthropic_reply('{"ok": true}'))
    client = AnthropicClient(_cfg(), sdk=sdk)
    assert (client.name, client.model) == ("anthropic", "claude-opus-5-5")
    assert client.complete_json("k", "SYS", "USR", Out) == Out(ok=True)
    (call,) = rec.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["system"] == "SYS"
    assert call["messages"] == [{"role": "user", "content": "USR"}]
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    sent = call["output_config"]["format"]["schema"]
    assert sent["properties"]["ok"]["type"] == "boolean"
    assert sent["additionalProperties"] is False
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "temperature" not in call
    assert "thinking" not in call


def test_anthropic_joins_only_text_blocks() -> None:
    reply = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text='{"ok":'),
            SimpleNamespace(type="thinking", thinking="ignored"),
            SimpleNamespace(type="text", text=" true}"),
        ],
        stop_reason="end_turn",
    )
    sdk, _ = _anthropic_sdk(reply)
    assert AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out).ok is True


def test_anthropic_refusal_raises() -> None:
    sdk, rec = _anthropic_sdk(_anthropic_reply("", stop_reason="refusal"))
    with pytest.raises(LLMError, match="refus") as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert len(rec.calls) == 1  # a refusal is not retried


def test_anthropic_invalid_output_is_retried_with_the_error() -> None:
    sdk, rec = _anthropic_sdk(_anthropic_reply("nope"))
    with pytest.raises(LLMError):
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert len(rec.calls) == 2
    assert "failed validation" in rec.calls[1]["messages"][0]["content"]


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("denied", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("cls", "status"),
    [(anthropic.AuthenticationError, 401), (anthropic.PermissionDeniedError, 403)],
)
def test_anthropic_auth_errors_are_fatal_and_name_the_key(
    cls: type[anthropic.APIStatusError], status: int
) -> None:
    sdk, _ = _anthropic_sdk(_status_error(cls, status))
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY") as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is True


def test_anthropic_missing_credentials_is_fatal() -> None:
    err = TypeError("Could not resolve authentication method. Expected one of api_key, auth_token")
    sdk, _ = _anthropic_sdk(err)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY") as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is True


def test_anthropic_other_api_errors_are_not_fatal() -> None:
    sdk, _ = _anthropic_sdk(_status_error(anthropic.InternalServerError, 500))
    with pytest.raises(LLMError) as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert "InternalServerError" in str(exc.value)


def _openai_sdk(reply: Any, made: list[dict[str, Any]]) -> tuple[Any, _Recorder]:
    rec = _Recorder(reply)

    def factory(**kwargs: Any) -> Any:
        made.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=rec))

    return SimpleNamespace(OpenAI=factory), rec


def _openai_reply(text: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_openai_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_LLM_KEY", "secret-value")
    made: list[dict[str, Any]] = []
    sdk, rec = _openai_sdk(_openai_reply('{"ok": true}'), made)
    cfg = _cfg(
        provider="openai",
        model="local-model",
        base_url="http://localhost:8000/v1",
        api_key_env="MY_LLM_KEY",
    )
    client = OpenAIClient(cfg, sdk=sdk)
    assert (client.name, client.model) == ("openai", "local-model")
    assert client.complete_json("k", "SYS", "USR", Out) == Out(ok=True)
    assert made == [{"base_url": "http://localhost:8000/v1", "api_key": "secret-value"}]
    (call,) = rec.calls
    assert call["model"] == "local-model"
    assert call["temperature"] == 0.2
    assert call["response_format"] == {"type": "json_object"}
    system, user = call["messages"]
    assert system["role"] == "system"
    assert system["content"].startswith("SYS")
    assert json.dumps(Out.model_json_schema()) in system["content"]
    assert user == {"role": "user", "content": "USR"}


def test_openai_default_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    made: list[dict[str, Any]] = []
    sdk, _ = _openai_sdk(_openai_reply('{"ok": true}'), made)
    OpenAIClient(_cfg(provider="openai"), sdk=sdk)
    assert made[0]["api_key"] == "k1"


def test_missing_key_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_LLM_KEY", raising=False)
    sdk, _ = _openai_sdk(_openai_reply("{}"), [])
    with pytest.raises(LLMError, match="OPENAI_API_KEY") as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk)
    assert exc.value.fatal is True
    with pytest.raises(LLMError, match="MY_LLM_KEY"):
        OpenAIClient(_cfg(provider="openai", api_key_env="MY_LLM_KEY"), sdk=sdk)


def test_openai_auth_error_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    request = httpx2.Request("POST", "http://x/v1/chat/completions")
    err = openai.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=request), body=None
    )
    sdk, _ = _openai_sdk(err, [])
    with pytest.raises(LLMError, match="OPENAI_API_KEY") as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is True


def test_make_client_fake_reads_file(tmp_path: Path) -> None:
    canned = tmp_path / "fake.json"
    canned.write_text(json.dumps({"k": [{"ok": True}, "{bad"]}), encoding="utf-8")
    cfg = _cfg(provider="fake", model="fake-1", fake_responses=canned)
    client = make_client(cfg, tmp_path / "llm")
    assert isinstance(client, CachedClient)
    assert client.name == "fake"
    assert client.model == "fake-1"
    assert client.complete_json("k", "s", "u", Out).ok is True


def test_make_client_fake_without_file_has_no_responses(tmp_path: Path) -> None:
    client = make_client(_cfg(provider="fake", model="fake-1"), tmp_path / "llm")
    with pytest.raises(LLMError):
        client.complete_json("k", "s", "u", Out)


def test_make_client_wraps_real_providers_in_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    client = make_client(_cfg(provider="openai", model="m"), tmp_path / "llm")
    assert isinstance(client, CachedClient)
    assert (client.name, client.model) == ("openai", "m")


def test_make_client_fake_repeat_is_a_cache_hit(tmp_path: Path) -> None:
    canned = tmp_path / "fake.json"
    canned.write_text(json.dumps({"k": [{"ok": True}]}), encoding="utf-8")
    client = make_client(_cfg(provider="fake", model="fake-1", fake_responses=canned), tmp_path / "llm")
    assert isinstance(client, CachedClient)
    first = client.complete_json("k", "s", "u", Out)
    second = client.complete_json("k", "s", "u", Out)
    assert first == second
    assert (client.calls, client.hits) == (1, 1)


def test_make_client_wraps_anthropic_in_cache(tmp_path: Path) -> None:
    client = make_client(_cfg(provider="anthropic"), tmp_path / "llm")  # builds the SDK client, no request
    assert isinstance(client, CachedClient)
    assert (client.name, client.model) == ("anthropic", "claude-opus-5-5")


# ---- errors that stop a run ---------------------------------------------------------------------


def _sdk_errors(sdk: Any, url: str) -> dict[str, BaseException]:
    """One instance of each error class that must stop a run, and of a timeout that must not."""
    request = httpx2.Request("POST", url)
    return {
        "NotFoundError": sdk.NotFoundError("no such model", response=httpx2.Response(404, request=request), body=None),
        "BadRequestError": sdk.BadRequestError("bad", response=httpx2.Response(400, request=request), body=None),
        "APIConnectionError": sdk.APIConnectionError(request=request),
        "APITimeoutError": sdk.APITimeoutError(request=request),
    }


FATAL = ["NotFoundError", "BadRequestError", "APIConnectionError"]


@pytest.mark.parametrize("name", FATAL)
def test_anthropic_wrong_model_bad_request_and_no_connection_are_fatal(name: str) -> None:
    sdk, _ = _anthropic_sdk(_sdk_errors(anthropic, "https://api.anthropic.com/v1/messages")[name])
    with pytest.raises(LLMError) as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is True
    assert name in str(exc.value)


@pytest.mark.parametrize("name", FATAL)
def test_openai_wrong_model_bad_request_and_no_connection_are_fatal(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openai

    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    sdk, _ = _openai_sdk(_sdk_errors(openai, "http://x/v1/chat/completions")[name], [])
    with pytest.raises(LLMError) as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is True
    assert name in str(exc.value)


def test_timeouts_are_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    sdk, _ = _anthropic_sdk(_sdk_errors(anthropic, "https://api.anthropic.com/v1/messages")["APITimeoutError"])
    with pytest.raises(LLMError) as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    sdk, _ = _openai_sdk(_sdk_errors(openai, "http://x/v1/chat/completions")["APITimeoutError"], [])
    with pytest.raises(LLMError) as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False


# ---- empty and truncated replies ----------------------------------------------------------------


def test_openai_empty_choices_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    sdk, rec = _openai_sdk(SimpleNamespace(choices=[], model="m"), [])
    with pytest.raises(LLMError, match="no choices") as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert len(rec.calls) == 1


def test_anthropic_max_tokens_is_truncation_without_retry() -> None:
    sdk, rec = _anthropic_sdk(_anthropic_reply('{"ok": tr', stop_reason="max_tokens"))
    with pytest.raises(LLMError, match="reply truncated at max_output_tokens") as exc:
        AnthropicClient(_cfg(), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert len(rec.calls) == 1


def test_openai_length_is_truncation_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    choice = SimpleNamespace(message=SimpleNamespace(content='{"ok": tr'), finish_reason="length")
    sdk, rec = _openai_sdk(SimpleNamespace(choices=[choice], model="m"), [])
    with pytest.raises(LLMError, match="reply truncated at max_output_tokens") as exc:
        OpenAIClient(_cfg(provider="openai"), sdk=sdk).complete_json("k", "s", "u", Out)
    assert exc.value.fatal is False
    assert len(rec.calls) == 1


# ---- the model that served the reply ------------------------------------------------------------


def test_anthropic_and_openai_report_the_served_model(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = _anthropic_reply('{"ok": true}')
    reply.model = "claude-fallback-1"
    sdk, _ = _anthropic_sdk(reply)
    client = AnthropicClient(_cfg(), sdk=sdk)
    assert client.last_model is None
    client.complete_json("k", "s", "u", Out)
    assert client.last_model == "claude-fallback-1"

    monkeypatch.setenv("OPENAI_API_KEY", "k1")
    oreply = _openai_reply('{"ok": true}')
    oreply.model = "gpt-served-2"
    sdk, _ = _openai_sdk(oreply, [])
    oclient = OpenAIClient(_cfg(provider="openai"), sdk=sdk)
    oclient.complete_json("k", "s", "u", Out)
    assert oclient.last_model == "gpt-served-2"


def test_cache_keeps_the_served_model(tmp_path: Path) -> None:
    inner = FakeClient(responders={"k": lambda s, u: {"ok": True}})
    inner.last_model = "served-1"
    first = CachedClient(inner, tmp_path, "v1")
    first.complete_json("k", "s", "u", Out)
    assert first.last_model == "served-1"
    inner.last_model = None
    again = CachedClient(inner, tmp_path, "v1")
    assert again.complete_json("k", "s", "u", Out) == Out(ok=True)
    assert again.hits == 1 and again.last_model == "served-1"
