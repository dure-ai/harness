"""Fake local HTTP engine used to test the OSS adapters without a real network."""

import json
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit


@dataclass
class RawResponse:
    """Bytes a fake handler writes to the socket verbatim before closing the connection."""

    data: bytes


@dataclass
class Recorded:
    """One request the fake server received."""

    method: str
    path: str
    query: dict[str, str]
    body: Any


def free_port() -> int:
    """Return a local port nobody is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeServer:
    """A JSON HTTP server on 127.0.0.1 that records requests; subclasses implement `handle`."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self._failures: list[int] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf-8")) if raw else None
                parts = urlsplit(self.path)
                query = dict(parse_qsl(parts.query))
                with outer._lock:
                    outer.requests.append(Recorded(self.command, parts.path, query, body))
                    failure = outer._failures.pop(0) if outer._failures else None
                if failure is not None:
                    status, payload = failure, {"error": f"forced {failure}"}
                else:
                    status, payload = outer.handle(self.command, parts.path, query, body)
                if isinstance(payload, RawResponse):
                    self.wfile.write(payload.data)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Base address of the running server."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        """Answer one request; override in subclasses.

        Returns:
            A (status, JSON-able payload) pair. A None payload means an empty body; a `RawResponse` is written to the socket as is.
        """
        return 404, {"error": "not found"}

    def fail_next(self, status: int, times: int) -> None:
        """Answer the next `times` requests with `status` instead of calling `handle`."""
        with self._lock:
            self._failures.extend([status] * times)

    def __enter__(self) -> "FakeServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
