import logging
from typing import Any

import pytest

from harness.config import LLM
from harness.oss.http import EngineHTTPError, JsonClient, check_local_url
from harness.oss.models import ENV_EMBEDDING, ENV_LLM, expected_models
from tests.oss_fakes import FakeServer, RawResponse, free_port


class Echo(FakeServer):
    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        return 200, body


@pytest.mark.parametrize("url", ["http://example.com:8888", "http://10.0.0.5:8888",
                                 "http://127.0.0.1:37777", "http://localhost:37701"])
def test_rejects_non_local_or_claude_mem(url):
    with pytest.raises(ValueError):
        check_local_url(url)


def test_accepts_local_and_strips_slash():
    assert check_local_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    assert check_local_url("http://localhost:8890") == "http://localhost:8890"
    assert check_local_url("http://[::1]:8888") == "http://[::1]:8888"


def test_korean_round_trip():
    with Echo() as s:
        assert JsonClient(s.url).request("POST", "/echo", body={"t": "벤더 모듈 수정"}) == {"t": "벤더 모듈 수정"}


def test_query_is_encoded_and_recorded():
    with Echo() as s:
        JsonClient(s.url).request("GET", "/q", query={"a": "벤더 x&y"})
    assert s.requests[0].query == {"a": "벤더 x&y"} and s.requests[0].body is None


def test_empty_body_is_none():
    class Empty(FakeServer):
        def handle(self, method, path, query, body):
            return 200, None

    with Empty() as s:
        assert JsonClient(s.url).request("DELETE", "/x") is None


def test_retries_429_then_succeeds(caplog):
    waits = []
    with Echo() as s, caplog.at_level(logging.WARNING, logger="harness.oss"):
        s.fail_next(429, 2)
        assert JsonClient(s.url, sleep=waits.append).request("POST", "/echo", body={"a": 1}) == {"a": 1}
    assert waits == [2.0, 8.0] and len(s.requests) == 3
    assert len([r for r in caplog.records if r.name == "harness.oss"]) == 2


def test_gives_up_after_three_retries():
    waits = []
    with Echo() as s:
        s.fail_next(503, 4)
        with pytest.raises(EngineHTTPError) as ei:
            JsonClient(s.url, sleep=waits.append).request("GET", "/x")
    assert ei.value.status == 503 and waits == [2.0, 8.0, 30.0]
    assert str(ei.value).startswith("GET /x -> 503: ")


def test_retry_false_fails_at_once():
    waits = []
    with Echo() as s:
        s.fail_next(503, 1)
        with pytest.raises(EngineHTTPError) as ei:
            JsonClient(s.url, sleep=waits.append).request("GET", "/x", retry=False)
        assert len(s.requests) == 1
    assert ei.value.status == 503 and waits == []


@pytest.mark.parametrize("status", [400, 404, 500])
def test_no_retry_for_other_errors(status):
    with Echo() as s:
        s.fail_next(status, 1)
        with pytest.raises(EngineHTTPError):
            JsonClient(s.url, sleep=lambda _: None).request("GET", "/x")
        assert len(s.requests) == 1


def test_ok_missing_404_is_none():
    with Echo() as s:
        s.fail_next(404, 1)
        assert JsonClient(s.url).request("DELETE", "/x", ok_missing=True) is None


def test_connection_refused_is_engine_error():
    port = free_port()  # 열려 있지 않은 포트
    waits = []
    with pytest.raises(EngineHTTPError) as ei:
        JsonClient(f"http://127.0.0.1:{port}", sleep=waits.append).request("GET", "/x")
    assert ei.value.status is None and waits == [2.0, 8.0, 30.0]


def test_client_rejects_non_local_url():
    with pytest.raises(ValueError):
        JsonClient("http://example.com:8888")


def test_models_null_in_config():
    with pytest.raises(ValueError, match="llm.model"):
        expected_models(LLM(provider="openai"), environ={})


def test_models_env_mismatch():
    llm = LLM(provider="openai", model="gpt-4.1-mini", embedding_model="text-embedding-3-small")
    with pytest.raises(ValueError, match="gpt-4o-mini"):
        expected_models(llm, environ={ENV_LLM: "gpt-4o-mini", ENV_EMBEDDING: "text-embedding-3-small"})
    assert expected_models(llm, environ={ENV_LLM: "gpt-4.1-mini", ENV_EMBEDDING: "text-embedding-3-small"}) \
        == ("gpt-4.1-mini", "text-embedding-3-small")


def test_models_env_missing():
    llm = LLM(provider="openai", model="gpt-4.1-mini", embedding_model="text-embedding-3-small")
    with pytest.raises(ValueError, match=ENV_LLM):
        expected_models(llm, environ={})


TRUNCATED = RawResponse(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"a\"")
GARBLED = RawResponse(b"not http at all\r\n\r\n")


class Broken(FakeServer):
    def __init__(self, reply: RawResponse) -> None:
        super().__init__()
        self.reply = reply

    def handle(self, method, path, query, body):
        return 200, self.reply


@pytest.mark.parametrize("reply", [TRUNCATED, GARBLED])
def test_dropped_or_garbled_reply_is_retried_engine_error(reply):
    waits = []
    with Broken(reply) as s:
        with pytest.raises(EngineHTTPError) as ei:
            JsonClient(s.url, sleep=waits.append).request("GET", "/x")
        assert len(s.requests) == 4
    assert ei.value.status is None and waits == [2.0, 8.0, 30.0]


def test_dropped_reply_then_recovers():
    class DropOnce(Echo):
        def handle(self, method, path, query, body):
            if len(self.requests) == 1:
                return 200, TRUNCATED
            return super().handle(method, path, query, body)

    waits = []
    with DropOnce() as s:
        assert JsonClient(s.url, sleep=waits.append).request("POST", "/e", body={"a": 1}) == {"a": 1}
    assert waits == [2.0] and len(s.requests) == 2


def test_truncated_error_body_keeps_status():
    reply = RawResponse(b"HTTP/1.1 500 Oops\r\nContent-Length: 100\r\n\r\nboom")
    with Broken(reply) as s:
        with pytest.raises(EngineHTTPError) as ei:
            JsonClient(s.url, sleep=lambda _: None).request("GET", "/x")
        assert len(s.requests) == 1
    assert ei.value.status == 500 and ei.value.body
