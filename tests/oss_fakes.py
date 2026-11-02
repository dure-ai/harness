"""Fake local HTTP engine used to test the OSS adapters without a real network."""

import json
import re
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


class FakeHindsight(FakeServer):
    """A fake Hindsight API: stores memories per bank and recalls them by word overlap.

    Attributes:
        pending_rounds: How many times each of the pending and processing queries reports one operation before reporting none.
        failed_total: Count reported for the failed operations query.
        observation_of: Document ids; when set, recall puts one observation built from these facts first.
        extra_results: Raw result dicts appended to every recall.
        source_facts_null: Answer recall with `source_facts: null` and no stored results.
        operations_reply: When set, the operations route answers this payload as is.
        recall_reply: When set, the recall route answers this payload as is (once the bank holds memories).

    A bank that holds no memories (deleted, or never retained into) answers 404 on the
    operations and recall routes, as the real API does.
    """

    _UNSET: Any = object()

    def __init__(self) -> None:
        super().__init__()
        self.pending_rounds = 0
        self.failed_total = 0
        self.observation_of: list[str] | None = None
        self.extra_results: list[dict[str, Any]] = []
        self.source_facts_null = False
        self.operations_reply: Any = self._UNSET
        self.recall_reply: Any = self._UNSET
        self.banks: dict[str, list[dict[str, Any]]] = {}
        self._polls: dict[str, int] = {}

    @staticmethod
    def _words(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        """Answer the Hindsight routes the adapter uses."""
        if method == "GET" and path == "/version":
            return 200, {"api_version": "1.0.0"}
        match = re.fullmatch(r"/v1/default/banks/([^/]+)(/.*)?", path)
        if match is None:
            return 404, {"error": "not found"}
        bank, rest = match.group(1), match.group(2) or ""
        if method == "DELETE" and rest == "":
            if bank not in self.banks:
                return 404, {"error": "no such bank"}
            del self.banks[bank]
            return 200, {"success": True}
        if method == "POST" and rest == "/memories":
            stored = self.banks.setdefault(bank, [])
            for item in body["items"]:
                stored.append({
                    "id": f"m{len(stored)}",
                    "text": item["content"],
                    "type": "world",
                    "document_id": item.get("document_id"),
                    "tags": item.get("tags", []),
                })
            return 200, {"success": True, "items_count": len(body["items"])}
        if rest in ("/operations", "/memories/recall") and not self.banks.get(bank):
            return 404, {"detail": f"bank {bank} not found"}
        if method == "GET" and rest == "/operations":
            if self.operations_reply is not self._UNSET:
                return 200, self.operations_reply
            status = query["status"]
            if status == "failed":
                return 200, {"total": self.failed_total, "operations": []}
            seen = self._polls.get(status, 0)
            self._polls[status] = seen + 1
            return 200, {"total": 1 if seen < self.pending_rounds else 0, "operations": []}
        if method == "POST" and rest == "/memories/recall":
            if self.recall_reply is not self._UNSET:
                return 200, self.recall_reply
            return 200, self._recall(bank, body)
        return 404, {"error": "not found"}

    def _recall(self, bank: str, body: Any) -> dict[str, Any]:
        if self.source_facts_null:
            return {"results": [], "source_facts": None}
        want = self._words(body["query"])
        tags = set(body.get("tags") or [])
        facts = [m for m in self.banks.get(bank, []) if tags & set(m["tags"])]
        facts.sort(key=lambda m: -len(want & self._words(m["text"])))
        results: list[dict[str, Any]] = [dict(m) for m in facts]
        source_facts: dict[str, Any] = {m["id"]: m for m in self.banks.get(bank, [])}
        if self.observation_of is not None:
            ids = [m["id"] for m in self.banks.get(bank, []) if m["document_id"] in self.observation_of]
            results.insert(0, {"id": "obs0", "text": "an observation", "type": "observation",
                               "document_id": None, "source_fact_ids": ids})
        results.extend(self.extra_results)
        return {"results": results, "source_facts": source_facts}


class FakeMem0(FakeServer):
    """A fake Mem0 REST server: stores memories as sent and searches them by word overlap.

    Attributes:
        llm_model: Model name `GET /configure` reports for the LLM.
        embedding_model: Model name `GET /configure` reports for the embedder.
        extra_results: Raw result dicts appended to every search reply; like real Mem0 rows they
            carry `run_id` and `user_id` at the top level when a test means them to be well-formed.
        configure_reply: When set, `GET /configure` answers this payload as is.
        search_reply: When set, `POST /search` answers this payload as is.
        ignore_or_filter: Behave like a misbehaving server that drops the `OR` clause of the search
            filter: every memory of the run is returned, whoever may see it (the adapter must
            drop those rows itself).
    """

    _UNSET: Any = object()

    def __init__(self) -> None:
        super().__init__()
        self.llm_model = "gpt-4.1-mini"
        self.embedding_model = "text-embedding-3-small"
        self.extra_results: list[dict[str, Any]] = []
        self.configure_reply: Any = self._UNSET
        self.search_reply: Any = self._UNSET
        self.ignore_or_filter = False
        self.memories: list[dict[str, Any]] = []

    @staticmethod
    def _words(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        """Answer the Mem0 routes the adapter uses."""
        if method == "GET" and path == "/configure":
            if self.configure_reply is not self._UNSET:
                return 200, self.configure_reply
            return 200, {"llm": {"config": {"model": self.llm_model}},
                         "embedder": {"config": {"model": self.embedding_model}}}
        if method == "POST" and path == "/memories":
            memory = {
                "id": f"m{len(self.memories)}",
                "memory": body["messages"][0]["content"],
                "run_id": body.get("run_id"),
                "user_id": body.get("user_id"),
                "metadata": dict(body.get("metadata") or {}),
            }
            self.memories.append(memory)
            return 200, {"results": [{"id": memory["id"], "memory": memory["memory"], "event": "ADD"}]}
        if method == "DELETE" and path == "/memories":
            run = query.get("run_id")
            self.memories = [m for m in self.memories if m["run_id"] != run]
            return 200, {"message": "Memories deleted successfully"}
        if method == "POST" and path == "/search":
            if self.search_reply is not self._UNSET:
                return 200, self.search_reply
            return 200, {"results": self._search(body)}
        return 404, {"error": "not found"}

    def _search(self, body: Any) -> list[dict[str, Any]]:
        filters = body["filters"]
        units = {clause["user_id"] for clause in filters["OR"]}
        want = self._words(body["query"])
        rows = [
            m for m in self.memories
            if m["run_id"] == filters["run_id"] and (self.ignore_or_filter or m["user_id"] in units)
        ]
        rows.sort(key=lambda m: -len(want & self._words(m["memory"])))
        results: list[dict[str, Any]] = [
            {"id": m["id"], "memory": m["memory"], "score": 1.0 / (rank + 1), "metadata": m["metadata"],
             "run_id": m["run_id"], "user_id": m["user_id"]}
            for rank, m in enumerate(rows)
        ]
        results.extend(self.extra_results)
        return results[: body["top_k"]]
