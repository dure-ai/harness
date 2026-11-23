import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class Served:
    """Local HTTP server: `served(body)` returns a URL that serves those bytes."""

    def __init__(self) -> None:
        self.requests = 0
        self.bodies: dict[str, bytes] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests += 1
                body = outer.bodies.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def __call__(self, body: bytes) -> str:
        path = f"/f{len(self.bodies)}"
        self.bodies[path] = body
        return f"http://127.0.0.1:{self.server.server_port}{path}"


@pytest.fixture
def served() -> Iterator[Callable[[bytes], str]]:
    s = Served()
    yield s  # pyright: ignore[reportReturnType]
    s.server.shutdown()
    s.server.server_close()
