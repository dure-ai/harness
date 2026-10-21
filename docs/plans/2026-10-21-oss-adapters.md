# 오픈소스 어댑터 (Hindsight, Mem0) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hindsight와 Mem0를 Docker로 띄우고 `harness run --engine hindsight --engine mem0`으로 기준선과 같은 시나리오로 채점하는 어댑터를 만든다.

**Architecture:** `src/harness/oss/` 패키지에 공통 부분(본문 형식, 이름공간 해시, 범위 단위, 표준 라이브러리 HTTP 클라이언트, 모델 확인)과 엔진별 어댑터 두 개를 둔다. 어댑터는 `BaseEngine`을 구현해 각 엔진의 REST API만 부른다. 테스트는 각 엔진 API를 흉내 내는 스레드 HTTP 가짜 서버로 하고, 실제 컨테이너 점검은 환경 변수로 켜는 별도 테스트로 둔다.

**Tech Stack:** Python ≥3.12, uv, 표준 라이브러리(`urllib.request`, `json`, `http.server`, `hashlib`, `threading`), pydantic v2, typer, pytest, Docker Compose.

**Spec:** `docs/specs/2026-10-21-oss-adapters-design.md` (이하 "설계 3"). 상위 문서는 `docs/specs/2026-10-19-memory-eval-harness-design.md`(이하 "설계")다. 설계 3과 설계가 다르면 설계 3을 따른다.

## Global Constraints

- 패키지 관리는 uv만 쓴다(pip 금지). 실행은 `uv run ...`. 하네스에 새 런타임 의존성을 넣지 않는다.
- 숨은 필드(`fact`, `secret_markers`, `review`, `provenance`, 질문의 `about`·`feedback`·`kind`)와 시나리오 id·계열 이름은 엔진에 보내는 어떤 값(본문, 태그, 메타데이터, URL, id)에도 들어가지 않는다.
- 어댑터는 호스트가 `localhost`, `127.0.0.1`, `[::1]`이 아니거나 포트가 37701, 37777인 주소를 거부한다.
- 결과를 다시 거르거나 순위를 바꾸지 않는다. 앞 `query.k`개로 자르는 것만 한다.
- 고정 값: Hindsight 이미지 `ghcr.io/vectorize-io/hindsight:1.0.0`, Mem0 `mem0ai==2.2.1`(서버 코드 `v2.2.1` 태그), `pgvector/pgvector:0.8.7-pg17`. 포트 `127.0.0.1:8888`(Hindsight), `127.0.0.1:8890`→8000(Mem0).
- 모델: `llm.provider: openai`, `llm.model: gpt-4.1-mini`, `llm.embedding_model: text-embedding-3-small`. 환경 변수 `OPENAI_API_KEY`, `HARNESS_LLM_MODEL`, `HARNESS_EMBEDDING_MODEL`, 선택 `OPENAI_BASE_URL`. 키 값은 코드, 로그, 리포트, 커밋 어디에도 남기지 않는다.
- 어댑터 주소 환경 변수: `HARNESS_HINDSIGHT_URL`(기본 `http://127.0.0.1:8888`), `HARNESS_MEM0_URL`(기본 `http://127.0.0.1:8890`).
- HTTP: 요청 시간 제한 120초. 429·502·503·504·연결 실패·시간 초과는 2, 8, 30초 간격으로 최대 세 번 다시 시도한다. 그 밖의 오류는 바로 `EngineHTTPError`.
- 모든 공개 함수·클래스는 Google 스타일 docstring(ruff `D` 규칙). 검사: `uv run ruff check`, `uv run basedpyright`, `uv run pytest -q`가 모두 통과해야 한다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 사용자의 실제 claude-mem이나 다른 실제 메모리에 연결하지 않는다. 테스트는 실제 네트워크를 쓰지 않는다(실제 연결 점검 테스트 제외).

## Review Focus

1. **엔진 컨테이너를 안 띄우고 돌림**: 첫 시나리오에서 연결이 거부되면 실행 전체가 종료 코드 2와 "엔진 주소와 compose 명령"을 알려 주는 메시지로 끝나야 한다(시나리오 수백 개를 오류로 채우지 않음). → Task 3, Task 4(생성자 상태 확인), Task 5(CLI)에 테스트.
2. **한글이 섞인 본문과 질문**: 시나리오에 한글이 섞일 수 있다. 요청 JSON은 UTF-8로 그대로 가고 가짜 서버에서 같은 문자열로 돌아와야 한다. → Task 2에 테스트.
3. **엔진이 빈 값이나 빠진 필드를 돌려줌**: `document_id: null`, `metadata` 없음, `source_facts: null`, `results` 빈 목록. 죽지 말고 `sources`만 비어야 한다. → Task 3, Task 4에 테스트.
4. **설정의 모델이 비어 있음**: `llm.model`이 `null`인 설정 파일로 돌리면 실행 전에 "설정의 llm.model을 채우라"는 오류로 멈춰야 한다. → Task 2에 테스트.
5. **엔진이 `k`보다 많이 돌려줌**: Hindsight recall은 개수를 정할 수 없어 많이 돌려준다. 앞 `k`개만 넘겨야 한다. → Task 3에 테스트.

---

## 파일 구조

```
src/harness/oss/__init__.py      # 패키지 설명만
src/harness/oss/text.py          # format_event, format_query, engine_ns, event_unit, query_units
src/harness/oss/http.py          # EngineHTTPError, check_local_url, JsonClient
src/harness/oss/models.py        # expected_models (설정·환경 변수 모델 확인)
src/harness/oss/hindsight.py     # HindsightEngine
src/harness/oss/mem0.py          # Mem0Engine
src/harness/engines.py           # 이름 hindsight, mem0 추가
config/eval-v0.1.yaml            # llm 채움, version 0.2
engines/docker/hindsight/compose.yaml
engines/docker/mem0/compose.yaml # 빌드 정의(dockerfile_inline) 포함
engines/docker/mem0/init-db.sh
tests/oss_fakes.py               # FakeServer, FakeHindsight, FakeMem0
tests/test_oss_text.py
tests/test_oss_http.py
tests/test_oss_hindsight.py
tests/test_oss_mem0.py
tests/test_oss_cli.py
tests/test_oss_live.py           # HARNESS_LIVE_OSS=1 일 때만
README.md, docs/ARCHITECTURE.md, docs/specs/2026-10-19-memory-eval-harness-design.md, docs/specs/2026-10-21-oss-adapters-design.md
```

설계 3의 4장은 Mem0용 `Dockerfile`을 따로 둔다고 적었지만, 원격 git 빌드 컨텍스트에서는 로컬 Dockerfile 경로를 쓸 수 없다. 그래서 compose의 `dockerfile_inline`에 둔다. Task 6에서 설계 3의 해당 줄을 고친다.

---

### Task 1: 본문 형식과 이름공간 (`oss/text.py`)

**Files:**
- Create: `src/harness/oss/__init__.py`, `src/harness/oss/text.py`
- Test: `tests/test_oss_text.py`

**Interfaces:**
- Consumes: `harness.contract.Event`, `Query`.
- Produces:
  - `format_event(event: Event) -> str`
  - `format_query(query: Query) -> str`
  - `engine_ns(namespace: str) -> str`: `"t5-" + sha256(namespace.encode()).hexdigest()[:24]`
  - `event_unit(event: Event) -> str`: personal이면 `f"user:{event.user}"`, team이면 `f"team:{event.team}"`
  - `query_units(query: Query) -> list[str]`: `[f"user:{query.user}", f"team:{query.team}"]`

형식(설계 3 5.1): `"[" + repo + "] "` + (meta가 있으면 `"{" + ", ".join(f"{k}={v}" for k, v in sorted(meta.items())) + "} "`) + `f"{user}: {text}"` + 접미어. 접미어는 kind가 `commit`이면 `"(commit)"`, 그 뒤에 신호 문구(설계 3의 표, `self`는 없음)를 공백으로 이어 붙인다. 접미어가 비면 뒤 공백도 없다. 질문은 같은 규칙에서 작성자와 접미어를 뺀 `[repo] {meta} text`다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_oss_text.py`, 이벤트는 `harness.contract.Event`를 직접 만든다)

```python
def test_plain_agent_record():
    assert format_event(ev(text="Use the replica", user="u2")) == "[shop-api] u2: Use the replica"

def test_meta_sorted_and_signal_phrase():
    e = ev(text="Fixed it", user="u3", signal="test", kind="ci_result", outcome="success",
           meta={"service": "billing", "env": "prod"})
    assert format_event(e) == "[shop-api] {env=prod, service=billing} u3: Fixed it (tests passed)"

@pytest.mark.parametrize("signal,outcome,phrase", [
    ("test", "failure", "(tests failed)"), ("test", "unknown", "(tests ran)"),
    ("review", "success", "(review approved)"), ("review", "failure", "(review rejected)"),
    ("review", "unknown", "(reviewed)"), ("human", "success", "(confirmed by a person)"),
    ("human", "failure", "(rejected by a person)"), ("human", "unknown", "(noted by a person)"),
    ("doc", "success", "(from docs)"), ("doc", "failure", "(from docs)"), ("doc", "unknown", "(from docs)"),
])
def test_every_signal_phrase(signal, outcome, phrase):
    assert format_event(ev(text="x", signal=signal, outcome=outcome)).endswith(" x " + phrase)

def test_commit_prefix_before_signal():
    assert format_event(ev(text="x", kind="commit", signal="self", outcome="success")).endswith("x (commit)")
    assert format_event(ev(text="x", kind="commit", signal="test", outcome="success")).endswith("x (commit) (tests passed)")

def test_refs_not_in_body():
    assert "e1" not in format_event(ev(text="CI ok", signal="test", kind="ci_result", refs=("e1",)))

def test_query_has_no_author():
    assert format_query(q(text="Can I fix it?", meta={"env": "prod"})) == "[shop-api] {env=prod} Can I fix it?"

def test_engine_ns_hides_scenario_id():
    ns = "20261021-120000-r1:leak_personal-widget-a1b2c3"
    out = engine_ns(ns)
    assert out.startswith("t5-") and len(out) == 27
    assert "leak" not in out and out == engine_ns(ns) and out != engine_ns(ns + "x")
    assert re.fullmatch(r"[a-z0-9-]+", out)

def test_units():
    assert event_unit(ev(scope="personal", user="u1")) == "user:u1"
    assert event_unit(ev(scope="team", team="team-a")) == "team:team-a"
    assert query_units(q(user="u4", team="team-a")) == ["user:u4", "team:team-a"]
```

`ev`·`q`는 테스트 파일 안의 작은 도우미로, 기본값 `repo="shop-api"`, `signal="self"`, `kind="agent_record"`, `outcome="unknown"`, `scope="team"`, `team="team-a"`, `user="u1"`, `session="s1"`, 시각 `2026-10-01T09:00:00+09:00`, `k=5`.

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_oss_text.py -q` / Expected: `ModuleNotFoundError: harness.oss`
- [ ] **Step 3: 구현** — 위 Interfaces의 다섯 함수. 신호 문구는 모듈 상수 dict `(signal, outcome) -> str`.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_oss_text.py -q && uv run ruff check src/harness/oss` / Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git add src/harness/oss tests/test_oss_text.py && git commit -m "feat: shared body format and namespace hash for OSS adapters"`

---

### Task 2: HTTP 클라이언트, 모델 확인, 가짜 서버 기반 (`oss/http.py`, `oss/models.py`, `tests/oss_fakes.py`)

**Files:**
- Create: `src/harness/oss/http.py`, `src/harness/oss/models.py`, `tests/oss_fakes.py`
- Test: `tests/test_oss_http.py`

**Interfaces:**
- Consumes: `harness.config.LLM`.
- Produces:
  - `class EngineHTTPError(RuntimeError)`: 속성 `method: str`, `path: str`, `status: int | None`(연결 실패면 None), `body: str`(응답 앞 500자). `str()`은 `"{method} {path} -> {status}: {body}"`.
  - `check_local_url(url: str) -> str`: 끝의 `/`를 뗀 주소를 돌려준다. 규칙 위반이면 `ValueError`.
  - `RETRY_STATUSES = frozenset({429, 502, 503, 504})`, `RETRY_DELAYS = (2.0, 8.0, 30.0)`
  - `class JsonClient(base_url: str, *, timeout: float = 120.0, retry_delays: Sequence[float] = RETRY_DELAYS, sleep: Callable[[float], None] = time.sleep)`; `base_url`은 `check_local_url`을 거친다.
  - `JsonClient.request(method: str, path: str, *, body: Any = None, query: Mapping[str, str] | None = None, ok_missing: bool = False) -> Any`: JSON 응답을 파싱해 돌려주고, 빈 본문은 `None`. `ok_missing=True`이면 404를 `None`으로 돌려준다. 요청 본문은 `json.dumps(body, ensure_ascii=False).encode("utf-8")`, 헤더 `Content-Type: application/json`. 다시 시도할 때마다 `logging.getLogger("harness.oss")`에 경고 한 줄(메서드, 경로, 상태, 대기 초).
  - `ENV_LLM = "HARNESS_LLM_MODEL"`, `ENV_EMBEDDING = "HARNESS_EMBEDDING_MODEL"`
  - `expected_models(llm: LLM, environ: Mapping[str, str] | None = None) -> tuple[str, str]`: `(llm_model, embedding_model)`. `llm.model`이나 `llm.embedding_model`이 None이면 `ValueError("set llm.model and llm.embedding_model in the eval config")`. 환경 변수가 없거나 설정과 다르면 두 값을 모두 적은 `ValueError`. `environ` 기본값은 `os.environ`.
  - `tests/oss_fakes.py`의 `class FakeServer`: 컨텍스트 매니저. `127.0.0.1:0`에 `ThreadingHTTPServer`를 띄우고 `.url`, `.requests: list[Recorded]`(`method`, `path`, `query: dict[str, str]`, `body: Any`)를 가진다. 하위 클래스는 `handle(method, path, query, body) -> tuple[int, Any]`를 구현한다. `fail_next(status: int, times: int)`는 다음 `times`개 요청에 `status`를 돌려준다.
  - `tests/oss_fakes.py`의 `free_port() -> int`: 잠깐 바인드했다 닫아 아무도 듣지 않는 로컬 포트를 돌려준다.
  - 이후 Task의 테스트 파일은 `fake`(가짜 서버)·`engine`(그 서버에 붙은 어댑터) 픽스처, `cfg()`(llm을 채운 `EvalConfig`), `MODELS_ENV`(두 모델 환경 변수 dict), `make(fake, **kw)`(옵션을 바꿔 어댑터를 만드는 도우미)를 파일 안에 둔다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_oss_http.py`; 테스트용 `Echo(FakeServer)`는 받은 본문을 그대로 돌려준다. 대기는 `sleep=waits.append`로 기록)

```python
@pytest.mark.parametrize("url", ["http://example.com:8888", "http://10.0.0.5:8888",
                                 "http://127.0.0.1:37777", "http://localhost:37701"])
def test_rejects_non_local_or_claude_mem(url):
    with pytest.raises(ValueError):
        check_local_url(url)

def test_accepts_local_and_strips_slash():
    assert check_local_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    assert check_local_url("http://localhost:8890") == "http://localhost:8890"

def test_korean_round_trip():
    with Echo() as s:
        assert JsonClient(s.url).request("POST", "/echo", body={"t": "벤더 모듈 수정"}) == {"t": "벤더 모듈 수정"}

def test_retries_429_then_succeeds():
    waits = []
    with Echo() as s:
        s.fail_next(429, 2)
        assert JsonClient(s.url, sleep=waits.append).request("POST", "/echo", body={"a": 1}) == {"a": 1}
    assert waits == [2.0, 8.0] and len(s.requests) == 3

def test_gives_up_after_three_retries():
    waits = []
    with Echo() as s:
        s.fail_next(503, 4)
        with pytest.raises(EngineHTTPError) as ei:
            JsonClient(s.url, sleep=waits.append).request("GET", "/x")
    assert ei.value.status == 503 and waits == [2.0, 8.0, 30.0]

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
    with pytest.raises(EngineHTTPError) as ei:
        JsonClient(f"http://127.0.0.1:{port}", sleep=lambda _: None).request("GET", "/x")
    assert ei.value.status is None

def test_models_null_in_config():
    with pytest.raises(ValueError, match="llm.model"):
        expected_models(LLM(provider="openai"), environ={})

def test_models_env_mismatch():
    llm = LLM(provider="openai", model="gpt-4.1-mini", embedding_model="text-embedding-3-small")
    with pytest.raises(ValueError, match="gpt-4o-mini"):
        expected_models(llm, environ={ENV_LLM: "gpt-4o-mini", ENV_EMBEDDING: "text-embedding-3-small"})
    assert expected_models(llm, environ={ENV_LLM: "gpt-4.1-mini", ENV_EMBEDDING: "text-embedding-3-small"}) \
        == ("gpt-4.1-mini", "text-embedding-3-small")
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_oss_http.py -q` / Expected: import 오류
- [ ] **Step 3: 구현** — `urllib.request.urlopen(req, timeout=...)`. `HTTPError`는 상태 코드로, `URLError`·`TimeoutError`·`ConnectionError`는 `status=None`으로 다룬다. 재시도 대상이면 `retry_delays`의 다음 값만큼 `sleep` 후 다시 보낸다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_oss_http.py -q && uv run ruff check src/harness/oss && uv run basedpyright src/harness/oss` / Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git add src/harness/oss tests/oss_fakes.py tests/test_oss_http.py && git commit -m "feat: local-only JSON client with bounded retries and model check"`

---

### Task 3: Hindsight 어댑터 (`oss/hindsight.py`)

**Files:**
- Create: `src/harness/oss/hindsight.py`
- Modify: `tests/oss_fakes.py` (`FakeHindsight` 추가)
- Test: `tests/test_oss_hindsight.py`

**Interfaces:**
- Consumes: Task 1의 다섯 함수, Task 2의 `JsonClient`, `expected_models`, `EvalConfig`.
- Produces:
  - `HINDSIGHT_VERSION = "1.0.0"`, `ENV_URL = "HARNESS_HINDSIGHT_URL"`, `DEFAULT_URL = "http://127.0.0.1:8888"`
  - `class HindsightEngine(BaseEngine)`: `__init__(self, cfg: EvalConfig, *, base_url: str | None = None, environ: Mapping[str, str] | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, poll_interval: float = 0.5, flush_timeout: float = 600.0)`
  - `FakeHindsight(FakeServer)`: `/version`, 은행 DELETE, memories POST(은행별 저장), operations GET(`status` 쿼리별로 `pending_rounds`만큼 `total=1`을 준 뒤 0, `failed_total` 설정 가능), recall POST(본문 단어 겹침 순, 저장한 `document_id` 포함). `observation` 결과를 섞어 돌려주는 옵션(`source_fact_ids`와 응답 `source_facts` 표).

동작(설계 3 6장):
- 생성자: 주소(`base_url` → `environ[ENV_URL]` → `DEFAULT_URL`)를 확인하고 `expected_models(cfg.llm, environ)`로 모델을 확인한다. `GET /version`이 실패하면 예외가 그대로 나간다(CLI가 종료 코드 2로 멈춤). 받은 `api_version`은 `info().config["api_version"]`에 남긴다(달라도 멈추지 않음).
- `reset(ns)`: `bank = engine_ns(ns)`. `DELETE /v1/default/banks/{bank}`(`ok_missing=True`). 이 시나리오의 이벤트 id 집합을 비운다.
- `record(e)`: `POST /v1/default/banks/{bank}/memories`, 본문 `{"items": [{"content": format_event(e), "timestamp": e.t.isoformat(), "document_id": e.id, "tags": [event_unit(e)]}]}`.
- `flush()`: `GET .../operations?status=pending&limit=1`과 `status=processing`의 `total`이 둘 다 0이 될 때까지 `poll_interval`마다 확인한다. `flush_timeout`을 넘으면 `TimeoutError`. 끝나면 `status=failed`의 `total`이 0보다 크면 `RuntimeError("hindsight: {n} background operations failed")`.
- `search(q)`: `POST .../memories/recall`, 본문 `{"query": format_query(q), "query_timestamp": q.t.isoformat(), "tags": query_units(q), "tags_match": "any_strict", "include": {"source_facts": {}}}`. 결과마다 `Result(text, sources, meta={"id", "type"})`. 사실은 `document_id`가 이 시나리오 이벤트 id일 때만 `sources`로 넣는다. `observation`은 `source_fact_ids`를 응답 `source_facts`에서 찾아 그 `document_id`들을 넣는다(순서 유지, 중복 제거, 모르는 id 버림). 앞 `q.k`개.
- `info()`: `EngineInfo(name="hindsight", version=HINDSIGHT_VERSION, config={"llm_model", "embedding_model", "reranker": "engine default (local cross-encoder)", "scope_mapping": "one bank per scenario; tags user:<u> / team:<t>; recall tags_match any_strict", "url", "api_version"}, virtual_clock=True, sources=True, scopes=True)`. `tick`은 기본(아무것도 안 함).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_oss_hindsight.py`; 각 테스트는 `FakeHindsight`와 `cfg`(llm 채운 `EvalConfig`), `environ={ENV_LLM: ..., ENV_EMBEDDING: ...}`, `sleep=lambda _: None`으로 엔진을 만든다)

```python
def test_reset_deletes_hashed_bank(fake, engine):
    engine.reset("run-r1:echo-widget-abc123")
    bank = engine_ns("run-r1:echo-widget-abc123")
    assert fake.requests[-1].method == "DELETE" and fake.requests[-1].path == f"/v1/default/banks/{bank}"
    assert "echo" not in fake.requests[-1].path

def test_record_sends_format_time_id_tag(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", scope="personal", user="u1", text="Use replica"))
    item = fake.requests[-1].body["items"][0]
    assert item == {"content": "[shop-api] u1: Use replica", "timestamp": "2026-10-01T09:00:00+09:00",
                    "document_id": "e1", "tags": ["user:u1"]}

def test_flush_waits_then_checks_failed(fake, engine):
    fake.pending_rounds = 2; engine.reset("ns"); engine.flush()
    assert sum(r.query.get("status") == "pending" for r in fake.requests) == 3

def test_flush_timeout(fake):
    fake.pending_rounds = 10**6
    eng = make(fake, clock=iter(range(0, 10**6, 100)).__next__, flush_timeout=600)
    eng.reset("ns")
    with pytest.raises(TimeoutError):
        eng.flush()

def test_failed_background_operation_errors(fake, engine):
    fake.failed_total = 1; engine.reset("ns")
    with pytest.raises(RuntimeError, match="1 background operations failed"):
        engine.flush()

def test_search_request_and_sources(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="Use the replica for reads"))
    res = engine.search(q(text="replica reads", user="u4", team="team-a", k=5))
    body = fake.requests[-1].body
    assert body["tags"] == ["user:u4", "team:team-a"] and body["tags_match"] == "any_strict"
    assert body["query_timestamp"] == "2026-10-01T09:00:00+09:00" and body["include"] == {"source_facts": {}}
    assert res[0].sources == ["e1"]

def test_observation_sources_from_source_facts(fake, engine):
    fake.observation_of = ["e1", "e2"]  # 가짜가 e1·e2 사실로 만든 관찰 하나를 맨 앞에 붙임
    engine.reset("ns"); engine.record(ev("e1", text="a b")); engine.record(ev("e2", text="a c"))
    assert engine.search(q(text="a"))[0].sources == ["e1", "e2"]

def test_unknown_or_null_document_ids_dropped(fake, engine):
    fake.extra_results = [{"id": "m9", "text": "x", "type": "world", "document_id": None},
                          {"id": "m8", "text": "y", "type": "world", "document_id": "other"},
                          {"id": "m7", "text": "z", "type": "observation", "source_fact_ids": ["nope"]}]
    engine.reset("ns")
    assert [r.sources for r in engine.search(q(text="x"))] == [[], [], []]

def test_null_source_facts_and_empty_results(fake, engine):
    fake.source_facts_null = True; engine.reset("ns")
    assert engine.search(q(text="nothing")) == []

def test_cut_to_k(fake, engine):
    engine.reset("ns")
    for i in range(8):
        engine.record(ev(f"e{i}", text=f"replica {i}"))
    assert len(engine.search(q(text="replica", k=3))) == 3

def test_constructor_fails_when_engine_down():
    with pytest.raises(EngineHTTPError):
        HindsightEngine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=lambda _: None)

def test_info(engine):
    i = engine.info()
    assert (i.name, i.version, i.virtual_clock, i.sources, i.scopes) == ("hindsight", "1.0.0", True, True, True)
    assert i.config["llm_model"] == "gpt-4.1-mini" and "OPENAI_API_KEY" not in str(i.config)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_oss_hindsight.py -q` / Expected: import 오류
- [ ] **Step 3: 구현** — 위 동작. 이벤트 id 집합은 `reset`에서 비우고 `record`에서 채운다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_oss_hindsight.py tests/test_oss_http.py -q && uv run ruff check && uv run basedpyright` / Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git add src/harness/oss/hindsight.py tests/oss_fakes.py tests/test_oss_hindsight.py && git commit -m "feat: Hindsight adapter with tag scopes and document-id sources"`

---

### Task 4: Mem0 어댑터 (`oss/mem0.py`)

**Files:**
- Create: `src/harness/oss/mem0.py`
- Modify: `tests/oss_fakes.py` (`FakeMem0` 추가)
- Test: `tests/test_oss_mem0.py`

**Interfaces:**
- Consumes: Task 1, Task 2와 같음.
- Produces:
  - `MEM0_VERSION = "2.2.1"`, `ENV_URL = "HARNESS_MEM0_URL"`, `DEFAULT_URL = "http://127.0.0.1:8890"`, `EVENT_KEY = "harness_event"`
  - `class Mem0Engine(BaseEngine)`: `__init__(self, cfg: EvalConfig, *, base_url: str | None = None, environ: Mapping[str, str] | None = None, sleep: Callable[[float], None] = time.sleep)`
  - `FakeMem0(FakeServer)`: `GET /configure`(`{"llm": {"config": {"model": ...}}, "embedder": {"config": {"model": ...}}}`, 모델 값 설정 가능), `POST /memories`(본문을 그대로 기억 하나로 저장, metadata 복사), `DELETE /memories?run_id=`, `POST /search`(필터 `run_id`와 `OR`의 `user_id`로 거른 뒤 단어 겹침 순, `top_k`, 결과 `{"results": [{"id", "memory", "score", "metadata"}]}`).

동작(설계 3 7장):
- 생성자: 주소 확인, `expected_models`, `GET /configure`의 `llm.config.model`과 `embedder.config.model`이 그 값과 다르면 `ValueError`(두 값 표시). 응답 전체는 저장하거나 출력하지 않는다(키가 가려져 있어도).
- `reset(ns)`: `self.run = engine_ns(ns)`, `DELETE /memories?run_id={run}`.
- `record(e)`: `POST /memories`, 본문 `{"messages": [{"role": "user", "content": format_event(e)}], "run_id": run, "user_id": event_unit(e), "metadata": {EVENT_KEY: e.id}}`.
- `flush()`: 기본(아무것도 안 함).
- `search(q)`: `POST /search`, 본문 `{"query": format_query(q), "filters": {"run_id": run, "OR": [{"user_id": u} for u in query_units(q)]}, "top_k": q.k}`. 결과마다 `Result(text=memory, sources=[metadata[EVENT_KEY]] if 이 시나리오 이벤트 id면 else [], meta={"id", "score"})`. 앞 `q.k`개.
- `info()`: `EngineInfo(name="mem0", version=MEM0_VERSION, config={"llm_model", "embedding_model", "reranker": "none (engine default)", "scope_mapping": "run_id per scenario; user_id user:<u> / team:<t>; search OR filter", "url", "note": "OSS add() ignores timestamps; memories carry wall-clock time"}, virtual_clock=False, sources=True, scopes=True)`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_oss_mem0.py`)

```python
def test_reset_deletes_hashed_run(fake, engine):
    engine.reset("run-r1:leak_personal-x"); r = fake.requests[-1]
    assert (r.method, r.path, r.query) == ("DELETE", "/memories", {"run_id": engine_ns("run-r1:leak_personal-x")})

def test_record_body(fake, engine):
    engine.reset("ns"); engine.record(ev("e3", scope="team", team="team-a", user="u2", text="Use replica"))
    assert fake.requests[-1].body == {"messages": [{"role": "user", "content": "[shop-api] u2: Use replica"}],
                                      "run_id": engine_ns("ns"), "user_id": "team:team-a",
                                      "metadata": {"harness_event": "e3"}}

def test_search_filter_and_sources(fake, engine):
    engine.reset("ns"); engine.record(ev("e1", text="replica reads"))
    res = engine.search(q(text="replica", user="u4", team="team-a", k=2))
    assert fake.requests[-1].body["filters"] == {"run_id": engine_ns("ns"),
                                                 "OR": [{"user_id": "user:u4"}, {"user_id": "team:team-a"}]}
    assert fake.requests[-1].body["top_k"] == 2 and res[0].sources == ["e1"]

def test_missing_or_foreign_metadata(fake, engine):
    fake.extra_results = [{"id": "m1", "memory": "x", "score": 0.9},
                          {"id": "m2", "memory": "y", "score": 0.8, "metadata": {"harness_event": "zz"}}]
    engine.reset("ns")
    assert [r.sources for r in engine.search(q(text="x"))] == [[], []]

def test_running_model_mismatch_stops(fake):
    fake.llm_model = "gpt-5-mini"
    with pytest.raises(ValueError, match="gpt-5-mini"):
        make(fake)

def test_constructor_fails_when_engine_down():
    with pytest.raises(EngineHTTPError):
        Mem0Engine(cfg(), base_url=f"http://127.0.0.1:{free_port()}", environ=MODELS_ENV, sleep=lambda _: None)

def test_info(engine):
    i = engine.info()
    assert (i.name, i.version, i.virtual_clock, i.sources, i.scopes) == ("mem0", "2.2.1", False, True, True)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_oss_mem0.py -q` / Expected: import 오류
- [ ] **Step 3: 구현** — 위 동작.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_oss_mem0.py -q && uv run ruff check && uv run basedpyright` / Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git add src/harness/oss/mem0.py tests/oss_fakes.py tests/test_oss_mem0.py && git commit -m "feat: Mem0 adapter with run_id scenarios and metadata sources"`

---

### Task 5: 엔진 등록, 설정, CLI 끝까지 (`engines.py`, `config/eval-v0.1.yaml`)

**Files:**
- Modify: `src/harness/engines.py`, `src/harness/cli.py`(`--engine` 도움말), `config/eval-v0.1.yaml`
- Test: `tests/test_oss_cli.py`, 그리고 기존 테스트 중 설정 값(`version: "0.1"`, `llm.provider: upstage`)을 확인하는 것이 있으면 새 값으로 고친다.

**Interfaces:**
- Consumes: `HindsightEngine`, `Mem0Engine`.
- Produces: `OSS_ENGINES = ("hindsight", "mem0")` in `harness/engines.py`. `make_engine("hindsight", s, cfg)` → `HindsightEngine(cfg)`, `"mem0"` → `Mem0Engine(cfg)`. import는 `make_engine` 안에서 한다(기준선만 쓰는 실행에 영향 없게). 알 수 없는 이름 오류 메시지에 두 이름을 넣는다.

설정 파일 변경: `version: "0.2"`, `llm: {provider: openai, model: gpt-4.1-mini, embedding_model: text-embedding-3-small}`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_oss_cli.py`; 기존 `tests/test_cli.py`의 `run_cli` 도우미 방식을 따른다. `monkeypatch.setenv`로 `HARNESS_HINDSIGHT_URL`, `HARNESS_MEM0_URL`을 가짜 서버 주소로, `HARNESS_LLM_MODEL=gpt-4.1-mini`, `HARNESS_EMBEDDING_MODEL=text-embedding-3-small`. 시나리오는 `scenarios/examples`)

```python
def test_run_with_both_fake_engines(tmp_path, fakes_env):
    result, out = run_cli(tmp_path, EXAMPLES, "--engine", "hindsight", "--engine", "mem0", "--repeat", "1")
    assert result.exit_code == 0, result.output
    summary = json.loads((next(out.iterdir()) / "summary.json").read_text())  # out/<run_id>/
    assert {"E1", "E2"} <= set(summary_engine_ids(summary))  # 두 후보가 한 리포트에 블라인드 id로
    assert not any_errors(summary)  # 가짜 서버로 오류 없이 끝남

def test_engine_down_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HINDSIGHT_URL", f"http://127.0.0.1:{free_port()}")
    set_model_env(monkeypatch)
    result, _ = run_cli(tmp_path, EXAMPLES, "--engine", "hindsight", "--repeat", "1")
    assert result.exit_code == 2 and "cannot build engine 'hindsight'" in result.output

def test_config_without_models_exits_2(tmp_path, fakes_env):
    cfg = tmp_path / "c.yaml"; cfg.write_text("llm: {provider: openai}\n")
    result, _ = run_cli(tmp_path, EXAMPLES, "--config", str(cfg), "--engine", "mem0", "--repeat", "1")
    assert result.exit_code == 2 and "llm.model" in result.output

def test_no_hidden_labels_reach_engines(tmp_path, fakes_env, hindsight_fake, mem0_fake):
    run_cli(tmp_path, EXAMPLES, "--engine", "hindsight", "--engine", "mem0", "--repeat", "1")
    sent = json.dumps([r.body for r in hindsight_fake.requests + mem0_fake.requests] +
                      [r.path for r in hindsight_fake.requests + mem0_fake.requests], ensure_ascii=False)
    for s in load_examples():
        assert s.id not in sent and s.family not in sent
        for m in all_secret_markers(s) + all_fact_markers_not_in_event_text(s):
            assert m not in sent
```

`summary_engine_ids`, `any_errors`, `all_secret_markers`, `all_fact_markers_not_in_event_text`는 이 테스트 파일 안의 작은 도우미로, summary.json과 시나리오 모델의 실제 필드 이름에 맞춰 쓴다. 마지막 테스트의 판정 문자열 검사는 이벤트 본문에 원래 들어 있는 문자열을 빼고 본다(본문은 엔진이 봐도 되는 관찰이기 때문).

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_oss_cli.py -q` / Expected: `unknown engine 'hindsight'`로 실패
- [ ] **Step 3: 구현** — `engines.py`의 `match`에 두 이름 추가, 설정 파일 변경, `--engine` 도움말을 `"Extra engine: 'hindsight', 'mem0' or 'pkg.module:Class' (repeatable). Baselines always run."`으로.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q && uv run ruff check && uv run basedpyright` / Expected: 전체 통과(기존 테스트 포함)
- [ ] **Step 5: 커밋** — `git add -A src config tests && git commit -m "feat: register hindsight and mem0 engines; eval config pins OpenAI models"`

---

### Task 6: Docker 설정, 실제 연결 점검, 문서

**Files:**
- Create: `engines/docker/hindsight/compose.yaml`, `engines/docker/mem0/compose.yaml`, `engines/docker/mem0/init-db.sh`, `tests/test_oss_live.py`
- Modify: `README.md`, `docs/ARCHITECTURE.md`, `docs/specs/2026-10-19-memory-eval-harness-design.md`, `docs/specs/2026-10-21-oss-adapters-design.md`

**Interfaces:**
- Consumes: Task 5의 엔진 이름, Global Constraints의 이미지·포트·환경 변수.
- Produces: 사람이 `docker compose ... up -d`로 띄우는 두 엔진.

compose 내용(값은 Global Constraints와 설계 3 4장):
- `hindsight/compose.yaml`: 서비스 `hindsight`, 이미지 고정, `ports: ["127.0.0.1:8888:8888"]`, 볼륨 `hindsight-data:/home/hindsight/.pg0`. 환경 변수 `HINDSIGHT_API_LLM_PROVIDER=openai`, `HINDSIGHT_API_LLM_API_KEY=${OPENAI_API_KEY:?set OPENAI_API_KEY}`, `HINDSIGHT_API_LLM_MODEL=${HARNESS_LLM_MODEL:?}`, `HINDSIGHT_API_LLM_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}`, `HINDSIGHT_API_EMBEDDINGS_PROVIDER=openai`, `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY=${OPENAI_API_KEY:?}`, `HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL=${HARNESS_EMBEDDING_MODEL:?}`, `HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}`. 헬스체크 `GET /health`.
- `mem0/compose.yaml`: 서비스 `mem0`(빌드 `context: https://github.com/mem0ai/mem0.git#v2.2.1:server`, `dockerfile_inline`: `FROM python:3.12-slim`, `WORKDIR /app`, `COPY . .`, `RUN pip install --no-cache-dir -r requirements.txt "mem0ai==2.2.1"`, `CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]`), `ports: ["127.0.0.1:8890:8000"]`, 환경 변수 `OPENAI_API_KEY=${OPENAI_API_KEY:?}`, `OPENAI_BASE_URL=${OPENAI_BASE_URL:-}`, `MEM0_DEFAULT_LLM_MODEL=${HARNESS_LLM_MODEL:?}`, `MEM0_DEFAULT_EMBEDDER_MODEL=${HARNESS_EMBEDDING_MODEL:?}`, `AUTH_DISABLED=true`, `MEM0_TELEMETRY=false`, `POSTGRES_HOST=postgres`, `POSTGRES_PASSWORD=postgres`, `APP_DB_NAME=mem0_app`, 볼륨 `mem0-history:/app/history`, `depends_on: postgres (service_healthy)`. 서비스 `postgres`: `pgvector/pgvector:0.8.7-pg17`, `POSTGRES_PASSWORD=postgres`, `./init-db.sh:/docker-entrypoint-initdb.d/init-db.sh:ro`, 볼륨 `mem0-pg`, 헬스체크 `pg_isready`, 호스트 포트는 열지 않는다.
- `mem0/init-db.sh`: `mem0_app` DB가 없으면 만든다(`v2.2.1`의 `server/init-db.sh`와 같은 SQL, 우리 파일로 작성).

- [ ] **Step 1: 실제 연결 점검 테스트 작성** (`tests/test_oss_live.py`)

```python
pytestmark = pytest.mark.skipif(os.environ.get("HARNESS_LIVE_OSS") != "1",
                                reason="set HARNESS_LIVE_OSS=1 with both containers up")

def test_live_examples_both_engines(tmp_path):
    result, out = run_cli(tmp_path, EXAMPLES, "--engine", "hindsight", "--engine", "mem0", "--repeat", "1")
    assert result.exit_code == 0, result.output
    assert not any_errors(json.loads((next(out.iterdir()) / "summary.json").read_text()))
```

- [ ] **Step 2: 건너뛰는지 확인** — Run: `uv run pytest tests/test_oss_live.py -q` / Expected: `1 skipped`
- [ ] **Step 3: compose 파일과 init-db.sh 작성**
- [ ] **Step 4: compose 문법 확인(이미지를 받지 않음)** — Run: `OPENAI_API_KEY=dummy HARNESS_LLM_MODEL=gpt-4.1-mini HARNESS_EMBEDDING_MODEL=text-embedding-3-small docker compose -f engines/docker/hindsight/compose.yaml config -q && OPENAI_API_KEY=dummy HARNESS_LLM_MODEL=gpt-4.1-mini HARNESS_EMBEDDING_MODEL=text-embedding-3-small docker compose -f engines/docker/mem0/compose.yaml config -q` / Expected: 출력 없이 종료 코드 0. 그리고 `HARNESS_LLM_MODEL` 없이 돌리면 오류가 나는지 한 번 확인.
- [ ] **Step 5: 문서**
  - `README.md`에 "오픈소스 엔진 측정" 절: 설계 3 8장의 측정 순서, 키는 직접 넣는다는 점, 두 엔진의 한계(재정렬 모델 차이, Mem0 가상 시계 미지원, Hindsight 모델은 환경 변수로만 확인), `HARNESS_LIVE_OSS=1` 점검 방법, 끝나면 `down -v`.
  - `docs/ARCHITECTURE.md`에 `harness.oss` 패키지 한 단락(공통 부분과 어댑터 두 개, 가짜 서버 테스트).
  - 설계 문서: 1장 6번과 10장 제목·첫 줄을 "Hindsight, Mem0(설계 3 참고)"로, 10장 본문 맨 위에 "구현 세부와 변경은 `2026-10-21-oss-adapters-design.md`를 따른다", 14장 6번을 "(Graphiti, Letta, Cognee, claude-mem)"으로, 17장 일정 행의 Graphiti를 Mem0로.
  - 설계 3 4장의 Mem0 위치 줄을 `engines/docker/mem0/compose.yaml`(빌드 정의는 `dockerfile_inline`), `engines/docker/mem0/init-db.sh`로 고친다.
- [ ] **Step 6: 전체 확인** — Run: `uv run pytest -q && uv run ruff check && uv run basedpyright` / Expected: 전체 통과, live 1개 skipped
- [ ] **Step 7: 커밋** — `git add engines tests/test_oss_live.py README.md docs && git commit -m "feat: docker setup, live check and docs for Hindsight and Mem0"`

---

## 실행 뒤 사람이 할 일 (계획 범위 밖)

1. 이미지 받기와 Mem0 빌드(내려받기이므로 진수 님 확인 후).
2. `OPENAI_API_KEY`를 넣고 두 컨테이너를 띄워 `HARNESS_LIVE_OSS=1 uv run pytest tests/test_oss_live.py`.
3. 점검 항목: Mem0 OR 필터, 두 엔진 동시 메모리 사용량, arm64 이미지, Hindsight 임베딩 차원 설정 필요 여부, 시나리오 1개 소요 시간.
