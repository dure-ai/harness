"""A small JSON-over-HTTP client that only talks to local engines and retries briefly."""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_DELAYS = (2.0, 8.0, 30.0)

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_CLAUDE_MEM_PORTS = frozenset({37701, 37777})
_BODY_LIMIT = 500

_log = logging.getLogger("harness.oss")
# Never route local engine traffic through a proxy taken from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class EngineHTTPError(RuntimeError):
    """An engine answered with an error, or could not be reached.

    Attributes:
        method: HTTP method of the failed request.
        path: Request path.
        status: HTTP status, or None when the connection failed or timed out.
        body: First 500 characters of the response body (or the failure reason).
    """

    def __init__(self, method: str, path: str, status: int | None, body: str) -> None:
        super().__init__(f"{method} {path} -> {status}: {body}")
        self.method: str = method
        self.path: str = path
        self.status: int | None = status
        self.body: str = body


def check_local_url(url: str) -> str:
    """Accept only a local engine address that is not the user's claude-mem.

    Args:
        url: Base address of the engine.

    Returns:
        The address without a trailing slash.

    Raises:
        ValueError: If the host is not localhost, 127.0.0.1 or [::1], the scheme is not
            http(s), or the port is 37701 or 37777 (claude-mem).
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in _LOCAL_HOSTS:
        raise ValueError(f"engine url must be a local http address (localhost, 127.0.0.1, [::1]): {url}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"engine url has an invalid port: {url}") from exc
    if port in _CLAUDE_MEM_PORTS:
        raise ValueError(f"engine url must not use the claude-mem ports 37701 or 37777: {url}")
    return url.rstrip("/")


class JsonClient:
    """Sends JSON requests to one local engine and parses the JSON replies."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a client.

        Args:
            base_url: Engine address; it must pass `check_local_url`.
            timeout: Seconds to wait for each request.
            retry_delays: Seconds to wait before each successive retry; its length is the
                maximum number of retries.
            sleep: Function used to wait; replaced in tests.

        Raises:
            ValueError: If `base_url` is not an allowed local address.
        """
        self.base_url: str = check_local_url(base_url)
        self._timeout: float = timeout
        self._delays: tuple[float, ...] = tuple(retry_delays)
        self._sleep: Callable[[float], None] = sleep

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Mapping[str, str] | None = None,
        ok_missing: bool = False,
        retry: bool = True,
    ) -> Any:
        """Send one request and return the parsed JSON reply.

        Args:
            method: HTTP method.
            path: Path beginning with "/".
            body: JSON-able request body, or None for no body.
            query: Query parameters, URL-encoded.
            ok_missing: Return None instead of raising on a 404.
            retry: Retry 429/502/503/504, connection failures and timeouts after the
                configured waits; when False they raise at once.

        Returns:
            The parsed JSON, or None for an empty body (or a tolerated 404).

        Raises:
            EngineHTTPError: On any other error status, an unparseable reply, or when
                retries run out.
        """
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        attempt = 0
        while True:
            try:
                return self._send(method, path, url, data, ok_missing)
            except EngineHTTPError as err:
                retryable = err.status is None or err.status in RETRY_STATUSES
                if not (retry and retryable) or attempt >= len(self._delays):
                    raise
                delay = self._delays[attempt]
                attempt += 1
                _log.warning("%s %s -> %s; retrying in %ss", method, path, err.status, delay)
                self._sleep(delay)

    def _send(self, method: str, path: str, url: str, data: bytes | None, ok_missing: bool) -> Any:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with _opener.open(req, timeout=self._timeout) as resp:
                raw: bytes = resp.read()  # pyright: ignore[reportAny]
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", errors="replace")
            if err.code == 404 and ok_missing:
                return None
            raise EngineHTTPError(method, path, err.code, text[:_BODY_LIMIT]) from err
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            raise EngineHTTPError(method, path, None, str(err)[:_BODY_LIMIT]) from err
        if not raw.strip():
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except ValueError as err:
            raise EngineHTTPError(method, path, 200, text[:_BODY_LIMIT]) from err
