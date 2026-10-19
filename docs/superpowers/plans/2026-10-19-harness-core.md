# 하네스 핵심 (계약·시나리오·기준선·채점·리포트) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기준선 4종을 시나리오 세트에 돌려 M1·M2·M3와 계열별 지표, 자가 검증, 리포트를 내는 하네스 핵심을 만든다(10/21 마감분 + 되먹임 모드).

**Architecture:** 엔진은 `BaseEngine` 계약(`info/reset/record/search/tick/flush`)만 구현하는 블랙박스다. 재생기가 시나리오 YAML의 이벤트와 질문을 가상 시각 순서로 흘리고, 규칙 기반 에이전트가 건넨 결과 중 관련 있는 맨 위 결과를 고르며, 채점기가 숨은 사실 라벨과 비교한다. 지표·통계·선택 규칙은 재생 기록(`ScenarioRun`)만 보고 계산한다.

**Tech Stack:** Python 3.14, uv, pydantic v2, PyYAML, typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-memory-eval-harness-design.md` (이하 "설계"). 이 계획은 설계의 1~9, 12, 13, 15장을 구현한다. 생성 파이프라인(11장)과 검토 CLI는 계획 2, 오픈소스 어댑터(10장)는 계획 3, HTTP 계약(4.3의 2번)은 11월 초 계획 4에서 다룬다.

## Global Constraints

- Python `>=3.14`, 패키지 관리는 uv만 쓴다(pip 금지). 실행은 `uv run ...`.
- 채점·에이전트·기준선에 LLM이나 외부 모델을 쓰지 않는다. 같은 입력이면 같은 출력(결정적).
- 시나리오의 숨은 필드(`fact`, `secret_markers`, `review`, `provenance`, 질문의 `about`·`feedback`·`kind`)는 엔진에 절대 넘기지 않는다.
- 모든 시각은 시간대 포함(aware) datetime. 시간대 없는 값은 로드 단계에서 오류.
- `valid_from`은 포함, `valid_until`은 미포함.
- 기본값: k=5, repeat_n N=3·τ=0.5, 되먹임 지연 행동 5분·실패 결과 1일·성공 결과 0분, 부트스트랩 10,000회, 시드 20261019, 판정 M1 ≤ all_store×0.5, M2 ≥ all_store−0.10, M3=0.
- 평가에는 `review.status: approved`만 들어간다. `--include-drafts`일 때만 draft를 포함하고, rejected는 항상 제외.
- 사용자의 실제 메모리(claude-mem 등)에 연결하는 코드를 만들지 않는다.

## Review Focus

1. **엔진이 이상한 결과를 돌려줌**: k보다 많은 결과, 시나리오에 없는 source id, 같은 id 중복. 앞 k개만 보고 모르는 id는 무시해야 한다. → Task 7, Task 8에 테스트.
2. **엔진이 예외를 던짐**: 한 시나리오가 실패해도 실행 전체가 멈추면 안 된다. 그 시나리오를 `error`로 표시하고 지표에서 빼고 리포트에 적어야 한다. → Task 8에 테스트.
3. **시간대가 섞인 시각**: `Z`와 `+09:00`이 섞이거나 YAML이 시간대 없는 시각을 datetime으로 바꿔 넣는 경우. 비교는 정확해야 하고, 시간대가 없으면 파일명과 함께 오류가 나야 한다. → Task 2, Task 3에 테스트.
4. **승인된 시나리오가 하나도 없음**: 비율의 분모가 0이 된다. 0으로 나누다 죽지 말고, 값은 `None`, CLI는 "승인된 시나리오 없음, --include-drafts 확인" 메시지로 끝나야 한다. → Task 10, Task 14에 테스트.
5. **한글 본문**: 시나리오는 영어지만 손으로 쓴 사례에 한글이 섞일 수 있다. 토큰화가 한글 2-gram을 만들어 검색이 동작해야 한다. → Task 5에 테스트.

---

## 파일 구조

```
pyproject.toml
config/eval-v0.1.yaml
src/harness/
  __init__.py
  contract.py          # Event, Query, Result, EngineInfo, BaseEngine, can_see
  config.py            # EvalConfig, load_config, config_hash
  scenario/
    __init__.py
    model.py           # Fact, ScenarioEvent, Probe, Feedback, Scenario, LabeledEvent
    load.py            # load_scenario, load_set, ScenarioSet
    validate.py        # Issue, validate_scenario
  world.py             # true_at, probe_ctx
  text.py              # tokenize, jaccard, BM25
  baselines/
    __init__.py
    simple.py          # NoneEngine, AllStoreEngine, RepeatNEngine
    oracle.py          # OracleEngine
  engines.py           # make_engine, BASELINES
  agent.py             # Choice, result_basis, choose
  scoring.py           # Outcome, is_wrong, judge, Leak, find_leaks
  replay.py            # ProbeRecord, ScenarioRun, run_scenario
  stats.py             # wilson, bootstrap_diff
  metrics.py           # Rate, Metrics, compute_metrics, per_scenario_counts, flip_rate
  selection.py         # Verdict, judge_engine, rank
  selfcheck.py         # CheckResult, run_selfcheck, check_discrimination
  report.py            # RunMeta, write_report
  cli.py               # typer app "harness"
scenarios/examples/*.yaml
tests/
  factories.py         # 테스트용 시나리오 생성 헬퍼
  test_*.py
```

---

### Task 1: 프로젝트 골격과 엔진 계약

**Files:**
- Create: `pyproject.toml`, `src/harness/__init__.py`, `src/harness/contract.py`
- Test: `tests/test_contract.py`

**Interfaces:**
- Produces:
  - `Scope = Literal["personal","team"]`, `Signal = Literal["self","test","review","human","doc"]`, `Kind = Literal["agent_record","ci_result","human_confirm","commit"]`, `Outcome = Literal["success","failure","unknown"]`
  - `class Event(BaseModel, frozen=True)`: `id: str, t: AwareDatetime, user: str, session: str, repo: str, scope: Scope, team: str, signal: Signal, kind: Kind, outcome: Outcome, text: str, meta: dict[str,str] = {}`
  - `class Query(BaseModel, frozen=True)`: `text, user, team, session, repo: str, t: AwareDatetime, k: int, meta: dict[str,str] = {}`
  - `class Result(BaseModel)`: `text: str, sources: list[str] = [], meta: dict[str, Any] = {}`
  - `class EngineInfo(BaseModel)`: `name: str, version: str, config: dict[str, Any] = {}, virtual_clock: bool = False, sources: bool = False, scopes: bool = False`
  - `class BaseEngine(ABC)`: 추상 `info() -> EngineInfo`, `reset(namespace: str) -> None`, `record(event: Event) -> None`, `search(query: Query) -> list[Result]`. 기본 구현이 아무것도 안 하는 `tick(t: datetime) -> None`, `flush() -> None`
  - `can_see(event: Event, user: str, team: str) -> bool`: personal이면 `event.user == user`, team이면 `event.team == team`

- [ ] **Step 1: 환경 준비**

  uv가 없으면 사용자에게 `brew install uv` 실행 허락을 받는다(설치는 사용자 승인 필요). 그다음 `uv python install 3.14`, `git switch -c feat/harness-core`.

- [ ] **Step 2: `pyproject.toml` 작성**

  `[project] name = "t5-harness"`, `requires-python = ">=3.14"`, dependencies `pydantic>=2.12`, `pyyaml>=6`, `typer>=0.12`. `[project.scripts] harness = "harness.cli:app"`. `[dependency-groups] dev = ["pytest>=8"]`. 빌드 백엔드는 hatchling, 패키지 경로 `src/harness`. 실행: `uv sync`. 기대: `.venv` 생성, 오류 없음.

- [ ] **Step 3: 실패하는 테스트 작성** (`tests/test_contract.py`)

```python
def test_can_see_personal_only_owner():
    e = make_event(user="u1", team="payments", scope="personal")
    assert can_see(e, "u1", "payments")
    assert not can_see(e, "u2", "payments")

def test_can_see_team_members_only():
    e = make_event(user="u1", team="payments", scope="team")
    assert can_see(e, "u2", "payments")
    assert not can_see(e, "u3", "search")

def test_event_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        Event(id="e1", t=datetime(2026, 10, 1, 10, 0), user="u1", session="s1", repo="r",
              scope="team", team="t", signal="self", kind="agent_record", outcome="unknown", text="x")

def test_base_engine_tick_flush_default_noop():
    class E(BaseEngine): ...  # 추상 메서드만 최소 구현
    E().tick(datetime.now(UTC)); E().flush()  # 예외 없음
```
  (`make_event`는 같은 파일 안의 작은 헬퍼로 둔다.)

- [ ] **Step 4: 실패 확인** — `uv run pytest tests/test_contract.py -v` → ImportError로 FAIL.
- [ ] **Step 5: `src/harness/contract.py` 구현** — 위 Interfaces 그대로.
- [ ] **Step 6: 통과 확인** — 같은 명령 → 4 passed.
- [ ] **Step 7: 커밋** — `git add pyproject.toml uv.lock src tests && git commit -m "feat: project skeleton and engine contract"`

---

### Task 2: 시나리오 모델과 로더

**Files:**
- Create: `src/harness/scenario/__init__.py`, `src/harness/scenario/model.py`, `src/harness/scenario/load.py`, `tests/factories.py`
- Test: `tests/test_scenario_load.py`

**Interfaces:**
- Consumes: `Event`, `Query` (Task 1)
- Produces:
  - `FAMILIES = ("normal_repeat","coincidence","single_source_poison","echo","failure_feedback","truth_change","conditional","workaround_then_root","leak_personal","leak_cross_team")`, `FEEDBACK_FAMILIES = ("echo","failure_feedback")`
  - `Fact`: `text: str, valid_from: AwareDatetime|None = None, valid_until: AwareDatetime|None = None, valid: bool = True, conditions: dict[str,str] = {}, fix: Literal["root","workaround"]|None = None, markers: list[str] = []`
  - `ScenarioEvent`: Event의 필드 + `team: str|None = None`, `fact: str|None = None`, `secret_markers: list[str] = []`
  - `FeedbackTemplate`: `text: str, fact: str|None = None, delay: timedelta|None = None, signal: Signal = "self", kind: Kind = "agent_record", outcome: Outcome = "unknown", scope: Scope = "team"`
  - `Feedback`: `action: dict[str, FeedbackTemplate] = {}, abstain: FeedbackTemplate|None = None, on_false: FeedbackTemplate|None = None, on_true: FeedbackTemplate|None = None`
  - `Probe`: `id, kind: Literal["block","promote","leak"], t: AwareDatetime, user, session, repo: str, query: str, about: list[str], meta: dict[str,str] = {}, k: int|None = None, feedback: Feedback|None = None`
  - `World`: `timezone: str = "Asia/Seoul", teams: dict[str, list[str]]`
  - `Review`: `status: Literal["draft","approved","rejected"] = "draft", reviewer: str|None = None, notes: str = ""`
  - `Provenance`: `author: Literal["human","llm","generator"], source_traces: list[str] = [], llm_model: str|None = None, prompt_version: str|None = None`
  - `Scenario`: `id, family: Literal[*FAMILIES], mode: Literal["read_only","feedback"], language: str = "en", world, facts: dict[str, Fact], events: list[ScenarioEvent], probes: list[Probe], review: Review = Review(), provenance: Provenance`
    - `team_of(user: str) -> str` (없으면 `KeyError`)
    - `labeled_events() -> list[LabeledEvent]` (파일 순서, team 미지정이면 `team_of(user)`)
    - `query_for(probe: Probe, k: int) -> Query` (`team=team_of(probe.user)`, `k=probe.k or k`)
  - `@dataclass(frozen=True) LabeledEvent`: `event: Event, fact: str|None, secret_markers: tuple[str, ...]`
  - `load_scenario(path: Path) -> Scenario` (YAML → 모델. 실패 시 `ScenarioLoadError(path, message)`)
  - `@dataclass ScenarioSet`: `scenarios: list[Scenario], paths: list[Path], hash: str`
  - `load_set(roots: list[Path], include_drafts: bool) -> ScenarioSet`: `*.yaml`을 재귀로 정렬해 읽고, rejected는 항상 빼고, draft는 `include_drafts`일 때만 넣음. `hash` = 포함된 파일들의 (상대 경로, 내용 sha256)을 정렬해 이어 붙인 것의 sha256
  - `tests/factories.py`: `fact(**kw) -> Fact`, `ev(id, t, **kw) -> ScenarioEvent`(기본 user u1, session s1, repo r, scope team, signal self, kind agent_record, outcome unknown), `probe(id, kind, t, about, **kw) -> Probe`, `scenario(facts, events, probes, **kw) -> Scenario`(기본 family truth_change, mode read_only, teams {"a": ["u1","u2"], "b": ["u3"]}, provenance human), `T(s: str) -> datetime`(`"10-01 10:00"` → 2026년 KST aware)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_load_example_yaml(tmp_path):        # 설계 5.1 예시를 그대로 파일로 써서 로드
    s = load_scenario(write(tmp_path, EXAMPLE_YAML))
    assert s.facts["f_old"].valid_until == datetime(2026,10,9,tzinfo=KST)
    assert s.labeled_events()[0].event.team == "payments"   # team 생략 → 소속 팀
    assert s.labeled_events()[0].fact == "f_old"

def test_naive_time_is_error_with_path(tmp_path):
    p = write(tmp_path, EXAMPLE_YAML.replace("2026-10-01T10:00+09:00", "2026-10-01 10:00"))
    with pytest.raises(ScenarioLoadError) as e: load_scenario(p)
    assert p.name in str(e.value)

def test_mixed_offsets_compare(tmp_path):    # "Z"와 "+09:00" 혼용
    ...; assert s.events[0].t < s.events[1].t

def test_load_set_filters_review(tmp_path):  # approved / draft / rejected 파일 3개
    assert len(load_set([tmp_path], include_drafts=False).scenarios) == 1
    assert len(load_set([tmp_path], include_drafts=True).scenarios) == 2

def test_set_hash_changes_with_content(tmp_path): ...
def test_query_for_hides_labels():
    q = scenario(...).query_for(probe("p", "block", T("10-14 10:00"), ["f"]), k=5)
    assert set(Query.model_fields) == {"text","user","team","session","repo","t","k","meta"}
    assert q.team == "a" and q.k == 5
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_scenario_load.py -v` → FAIL(ImportError).
- [ ] **Step 3: `model.py`, `load.py`, `tests/factories.py` 구현** — YAML은 `yaml.safe_load`. PyYAML이 시각을 datetime으로 바꾸므로 시간대 없는 datetime이 오면 pydantic이 거부하고, 그 오류를 `ScenarioLoadError`로 감싼다.
- [ ] **Step 4: 통과 확인** — 같은 명령 → all passed.
- [ ] **Step 5: 커밋** — `git commit -m "feat: scenario model and loader"`

---

### Task 3: 세계의 진실 판정

**Files:**
- Create: `src/harness/world.py`
- Test: `tests/test_world.py`

**Interfaces:**
- Consumes: `Fact`, `Probe` (Task 2)
- Produces: `true_at(fact: Fact, t: datetime, ctx: Mapping[str, str]) -> bool`, `probe_ctx(probe: Probe) -> dict[str, str]` (= `{"repo": probe.repo, **probe.meta}`)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_valid_from_inclusive_until_exclusive():
    f = fact(valid_from=T("10-09 00:00"), valid_until=T("10-12 00:00"))
    assert true_at(f, T("10-09 00:00"), {}) and not true_at(f, T("10-12 00:00"), {})
def test_valid_false_never_true(): assert not true_at(fact(valid=False), T("10-10 00:00"), {})
def test_conditions_must_all_match():
    f = fact(conditions={"service": "A"})
    assert true_at(f, T("10-10 00:00"), {"repo": "r", "service": "A"})
    assert not true_at(f, T("10-10 00:00"), {"repo": "r", "service": "B"})
    assert not true_at(f, T("10-10 00:00"), {"repo": "r"})          # 조건 키가 맥락에 없으면 거짓
def test_offset_independent():   # until "2026-10-09T00:00+09:00" vs t "2026-10-08T15:30Z"(=00:30 KST)
    assert not true_at(f, t_utc, {})
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_world.py -v` → FAIL.
- [ ] **Step 3: 구현** — 설계 5.2의 참 판정식 그대로.
- [ ] **Step 4: 통과 확인** → passed.
- [ ] **Step 5: 커밋** — `git commit -m "feat: fact truth evaluation"`

---

### Task 4: 시나리오 구조 검증

**Files:**
- Create: `src/harness/scenario/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: Task 2, 3
- Produces: `@dataclass(frozen=True) Issue: level: Literal["error","warning"], code: str, message: str`, `validate_scenario(s: Scenario) -> list[Issue]`

검사(코드명 고정). 모두 `error`이고, `markers_missing`만 `warning`:

| code | 조건 |
|---|---|
| `duplicate_id` | 이벤트·질문 id 중복 |
| `unknown_fact` | 이벤트의 `fact`, 질문의 `about`, feedback 틀의 `fact`·`action` 키가 `facts`에 없음 |
| `unknown_user` | 이벤트·질문의 user가 어느 팀에도 없음, 또는 한 사용자가 두 팀에 있음 |
| `interval_inverted` | `valid_from >= valid_until` |
| `mode_mismatch` | `family in FEEDBACK_FAMILIES`인데 `mode != feedback`, 또는 그 반대 |
| `feedback_in_read_only` | `mode == read_only`인데 질문에 `feedback`이 있음 |
| `pairing_missing` | `normal_repeat`가 아닌데 (`block` 또는 `leak`) 문항과 `promote` 문항 중 하나가 없음 |
| `block_without_false` | `block` 문항의 `about` 중 질문 시점·맥락에서 거짓인 사실이 없음 |
| `promote_without_seen_true` | `promote` 문항에서, `about` 중 참이고 질문 시각까지(같은 시각 포함) 질문한 사람이 볼 수 있게 기록된 사실이 없음 |
| `leak_without_hidden` | `leak` 문항에서, 질문 시각까지 기록된 `about` 사실의 이벤트 중 질문한 사람이 못 보는 것이 없음 |
| `markers_missing` (warning) | `about`에 쓰인 사실의 `markers`가 비어 있음 |

- [ ] **Step 1: 실패하는 테스트 작성** — 코드마다 그 오류 하나만 나는 최소 시나리오를 `factories`로 만들어 `{i.code for i in validate_scenario(s)} == {"<code>"}`를 단언한다(11개 테스트). 정상 시나리오(설계 5.1 예시 + promote 문항)는 `[]`.
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_validate.py -v` → FAIL.
- [ ] **Step 3: 구현** — `can_see`, `true_at`, `probe_ctx`를 쓴다.
- [ ] **Step 4: 통과 확인** → passed.
- [ ] **Step 5: 커밋** — `git commit -m "feat: scenario structural validation"`

---

### Task 5: 텍스트 검색, 설정, 기본 기준선 3종

**Files:**
- Create: `src/harness/text.py`, `src/harness/config.py`, `config/eval-v0.1.yaml`, `src/harness/baselines/__init__.py`, `src/harness/baselines/simple.py`
- Test: `tests/test_text.py`, `tests/test_config.py`, `tests/test_baselines_simple.py`

**Interfaces:**
- Consumes: Task 1
- Produces:
  - `tokenize(text: str) -> list[str]`: 소문자화, `[a-z0-9_]+` 단어, 한글 연속 구간은 구간 전체 + 글자 2-gram
  - `jaccard(a: str, b: str) -> float` (토큰 집합 기준. 둘 다 비면 0.0)
  - `class BM25`: `add(doc_id: str, text: str)`, `scores(query: str) -> dict[str, float]`(점수 > 0인 문서만), k1=1.5, b=0.75
  - `class EvalConfig(BaseModel)`: `version: str = "0.1"`, `k: int = 5`, `judgment: Judgment(m1_ratio=0.5, m2_drop=0.10, m1_abs_cap: float|None=None)`, `stats: Stats(bootstrap_iters=10000, seed=20261019)`, `repeat_n: RepeatN(n=3, tau=0.5)`, `feedback: FeedbackDelays(action=timedelta(minutes=5), on_false=timedelta(days=1), on_true=timedelta(0))`, `llm_repeats: int = 3`, `llm: LLM(provider="upstage", model: str|None=None, embedding_model: str|None=None)`
  - `load_config(path: Path) -> EvalConfig`, `config_hash(cfg: EvalConfig) -> str` (정렬된 JSON의 sha256)
  - `NoneEngine()`, `AllStoreEngine(acl: bool = True)`, `RepeatNEngine(n: int, tau: float)` — 모두 `BaseEngine`. `info().name`은 `"none"`, `"all_store"`(acl=False면 `"all_store_noacl"`), `"repeat_n"`. `virtual_clock=False, sources=True, scopes=True`
  - `AllStoreEngine.search`: `can_see(e, q.user, q.team)`인(acl=False면 전부) 저장 이벤트 중 BM25 점수 > 0인 것을 (점수 내림차순, t 내림차순, id 오름차순)으로 정렬해 상위 `q.k`개. 결과는 `Result(text=e.text, sources=[e.id])`
  - `RepeatNEngine.search`: 같은 후보 집합에서, 볼 수 있는 저장 이벤트 중 `jaccard(e, x) >= tau`인 x(자기 포함)의 수가 `n` 이상인 이벤트만 남긴 뒤 AllStore와 같은 정렬
  - `reset(ns)`는 그 이름공간의 저장소를 비운다(엔진 인스턴스 하나가 이름공간별 dict를 가짐)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_tokenize_english(): assert tokenize("Fix the Vendor_module!") == ["fix","the","vendor_module"]
def test_tokenize_korean_bigrams(): assert {"벤더","더모","모듈"} <= set(tokenize("벤더모듈 수정"))
def test_bm25_ranks_matching_doc_first(): ...  # "vendor module" 질의에 vendor 문서가 1위, 무관 문서는 결과에 없음
def test_bm25_korean_query_matches(): ...      # "벤더 모듈 고쳐도 되나" → 한글 문서 점수 > 0
def test_config_defaults_and_hash(tmp_path):   # 빈 YAML → 기본값, 값 하나 바꾸면 hash 달라짐
def test_none_returns_empty()
def test_all_store_respects_acl():             # u3(팀 b)는 팀 a의 team 기록을 못 받음, noacl은 받음
def test_all_store_order_tiebreak():           # 같은 점수 → 최신 우선, 그다음 id 오름차순
def test_all_store_reset_isolates_namespaces()
def test_repeat_n_requires_n_similar():        # 비슷한 기록 2개는 안 나오고 3개면 나옴(n=3, tau=0.5)
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_text.py tests/test_config.py tests/test_baselines_simple.py -v` → FAIL.
- [ ] **Step 3: 구현** — `config/eval-v0.1.yaml`에는 위 기본값을 모두 명시적으로 적는다(사전 등록 대상).
- [ ] **Step 4: 통과 확인** → passed.
- [ ] **Step 5: 커밋** — `git commit -m "feat: BM25 text search, eval config, simple baselines"`

---

### Task 6: 오라클 기준선과 엔진 레지스트리

**Files:**
- Create: `src/harness/baselines/oracle.py`, `src/harness/engines.py`
- Test: `tests/test_oracle.py`, `tests/test_engines.py`

**Interfaces:**
- Consumes: Task 2, 3, 5
- Produces:
  - `OracleEngine(scenario: Scenario)`: `record`는 시나리오에 있는 id면 라벨을 기억하고, 하네스가 만든 되먹임 이벤트처럼 모르는 id는 무시한다. `search(q)`는 `(q.t, q.user, q.text)`로 시나리오 질문을 찾고(못 찾으면 `[]`), `about` 순서대로 `true_at(f, q.t, probe_ctx(probe))`인 사실마다, 기록된 이벤트 중 `fact == f`이고 `can_see`이며 `t <= q.t`인 가장 최근 이벤트를 `Result(text, [id])`로 낸다. 최대 `q.k`개. `info().name = "oracle"`
  - `BASELINES = ("none", "all_store", "repeat_n", "oracle")`
  - `make_engine(name: str, scenario: Scenario, cfg: EvalConfig) -> BaseEngine`: 기준선 이름 또는 `"all_store_noacl"`, 또는 `"package.module:ClassName"`(인자 없이 생성하는 Python 엔진). 모르는 이름이면 `ValueError`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_oracle_returns_only_currently_true():   # 설계 5.1 시나리오, p_block 시각 → f_new 이벤트 e2만
    assert [r.sources for r in res] == [["e2"]]
def test_oracle_respects_visibility():           # 볼 수 없는 기록만 있으면 []
def test_oracle_ignores_unknown_event_ids()
def test_make_engine_import_path():              # "tests.dummy_engine:Dummy" → Dummy 인스턴스
def test_make_engine_unknown_raises()
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_oracle.py tests/test_engines.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: oracle baseline and engine registry"`

---

### Task 7: 규칙 기반 에이전트와 채점

**Files:**
- Create: `src/harness/agent.py`, `src/harness/scoring.py`
- Test: `tests/test_agent.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: Task 1~3
- Produces:
  - `@dataclass(frozen=True) Choice: index: int|None, basis: frozenset[str]`
  - `result_basis(result: Result, probe: Probe, scenario: Scenario, events: Mapping[str, LabeledEvent]) -> frozenset[str]`:
    1. `sources`가 비어 있지 않으면: 모르는 id는 버리고, 남은 이벤트의 `fact` 중 `probe.about`에 있는 것.
    2. `sources`가 비어 있으면: `about` 사실 중 `markers` 하나라도 본문에 (casefold 부분 일치) 있는 것.
    3. 1의 결과가 둘 이상이면 markers가 본문에 나타나는 것으로 좁힌다. 좁힌 결과가 비면 좁히기 전 값을 쓴다.
    - (설계 7.2를 "about에 있는 사실로 한정"해서 해석한 것이다. 7.1의 관련 판정과 같은 기준이다.)
  - `choose(results, probe, scenario, events) -> Choice`: 앞의 `k`개(`probe.k or cfg.k`는 호출자가 잘라서 넘김) 중 basis가 빈 집합이 아닌 첫 결과. 없으면 `Choice(None, frozenset())`
  - `ProbeOutcome = Literal["FP", "TP", "FN", "BLOCKED", "PROMOTE_FP", "LEAK_ONLY"]`
  - `is_wrong(basis: frozenset[str], probe: Probe, scenario: Scenario) -> bool`: basis 중 하나라도 `not true_at(...)`
  - `judge(probe, choice, scenario) -> ProbeOutcome`: 설계 7.3 표 그대로. `leak` 문항은 항상 `"LEAK_ONLY"`
  - `@dataclass(frozen=True) Leak: result_index: int, event_id: str, reason: Literal["source", "secret_marker"]`
  - `find_leaks(results, probe, scenario, events) -> list[Leak]`: (a) 결과 source 중 질문한 사람이 못 보는 이벤트, (b) 질문 시각까지 기록됐고 질문한 사람이 못 보는 이벤트의 `secret_markers`가 결과 본문에 있음(casefold)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_first_relevant_result_is_followed():      # [무관, f_old, f_new] → index 1, basis {"f_old"}
def test_abstain_when_nothing_relevant()           # → Choice(None, ∅)
def test_unknown_and_duplicate_sources_ignored():  # sources ["zzz","e1","e1"] → basis {"f_old"}
def test_sourceless_uses_markers():                # sources [] + 본문에 f_new marker → {"f_new"}
def test_mixed_basis_narrowed_by_markers():        # sources [e1(f_old), e2(f_new)], 본문에 f_new marker만 → {"f_new"}
def test_mixed_basis_unresolved_is_wrong():        # 좁혀지지 않음 → is_wrong True (f_old가 거짓)
def test_judge_table():                            # block+wrong→FP, block+right→BLOCKED, block+abstain→BLOCKED,
                                                   # promote+right→TP, promote+wrong→PROMOTE_FP, promote+abstain→FN
def test_leak_by_source_cross_team()               # 팀 b 질문자에게 팀 a 기록 → Leak(reason="source")
def test_leak_by_secret_marker_without_source()    # 출처 없이 본문에 비밀 문자열 → Leak(reason="secret_marker")
def test_no_leak_for_visible_records()
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_agent.py tests/test_scoring.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: rule-based agent and scoring"`

---

### Task 8: 재생기 (읽기 전용)

**Files:**
- Create: `src/harness/replay.py`
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: Task 1~7
- Produces:
  - `@dataclass ProbeRecord`: `probe_id: str, kind: str, results: list[Result], choice: Choice, outcome: ProbeOutcome, leaks: list[Leak], bases: list[frozenset[str]]`(결과별 basis), `contaminated: bool`(결과 중 basis가 있고 `is_wrong`인 것이 있음), `reached: bool`(basis가 있고 틀리지 않은 결과가 있음), `search_ms: float`, `context_chars: int`(건넨 본문 길이 합)
  - `@dataclass ScenarioRun`: `scenario_id: str, family: str, mode: str, probes: list[ProbeRecord], record_ms: list[float], generated: list[LabeledEvent], error: str|None`
  - `run_scenario(engine: BaseEngine, scenario: Scenario, *, run_id: str, cfg: EvalConfig) -> ScenarioRun`

동작(설계 6.1):
- 시작할 때 `engine.reset(f"{run_id}:{scenario.id}")`.
- 이벤트와 질문을 하나의 우선순위 큐에 넣는다. 키는 `(t, 0=이벤트/1=질문, 들어온 순번)`. 되먹임 이벤트도 같은 큐에 넣으므로 큐 구조가 필요하다.
- `engine.info().virtual_clock`이면 매 호출 직전에 `tick(t)`. 질문 직전에 `flush()`.
- `search` 결과는 앞의 `probe.k or cfg.k`개만 쓴다.
- 엔진 호출이 예외를 던지면 그 시나리오를 즉시 끝내고 `error=f"{type(e).__name__}: {e}"`로 돌려준다(다른 시나리오는 계속).
- 벽시계 시간은 `time.perf_counter()`로 잰다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_all_store_fp_on_truth_change():   # 설계 5.1 예시 + promote 문항, AllStore → p_block FP
def test_oracle_blocks_and_promotes():     # Oracle → p_block BLOCKED, p_promote TP
def test_events_before_probe_same_time():  # 같은 시각의 이벤트가 질문보다 먼저 기록됨
def test_reset_called_with_namespace():    # 기록용 가짜 엔진으로 "run1:<scenario id>" 확인
def test_tick_only_when_virtual_clock()
def test_results_truncated_to_k():         # 엔진이 10개 반환, k=5 → records[0].results 길이 5
def test_engine_exception_marks_error():   # search에서 RuntimeError → run.error 시작이 "RuntimeError"
def test_engine_never_sees_hidden_fields():  # 가짜 엔진이 받은 Event/Query에 fact·about이 없음
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_replay.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: read-only replay"`

---

### Task 9: 되먹임 모드

**Files:**
- Modify: `src/harness/replay.py`
- Test: `tests/test_feedback.py`

**Interfaces:**
- Consumes: Task 8
- Produces: `run_scenario`가 `scenario.mode == "feedback"`이고 질문에 `feedback`이 있을 때 이벤트를 생성해 큐에 넣는다. 생성된 것은 `ScenarioRun.generated`에 남긴다.

규칙(설계 6.2):
- **행동 이벤트**: 틀 = 기권이면 `feedback.abstain`. 아니면 `feedback.action[f]`인데, f는 basis 중 틀린 사실이 있으면 그중 id가 가장 작은 것, 없으면 id가 가장 작은 사실. 틀이 없으면 만들지 않는다.
  - id `f"{probe.id}#action"`, t = `probe.t + (tmpl.delay or cfg.feedback.action)`, user·session·repo는 질문 것, team = `team_of(user)`, 나머지는 틀 값. 라벨 `fact = tmpl.fact`.
- **결과 이벤트**: 기권이 아니고 `is_wrong(basis)`이면 `on_false`, 아니면 `on_true`. 틀이 없으면 만들지 않는다.
  - id `f"{probe.id}#outcome"`, 지연 기본값은 `cfg.feedback.on_false` / `on_true`.
- 생성 이벤트에는 따라 한 기록이라는 표시를 붙이지 않는다(본문·meta 모두).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_action_event_follows_choice():     # all_store가 f_old를 따름 → "p1#action" 생성, 라벨 fact=action["f_old"].fact
def test_abstain_template_used():           # none 엔진 → abstain 틀로 생성
def test_on_false_outcome_after_one_day():  # 틀린 근거 → "p1#outcome" t == p1.t + 1day, signal test
def test_generated_event_reaches_engine():  # 다음 질문 전에 엔진이 "p1#action"을 record로 받음
def test_no_marker_of_echo_in_generated():  # 생성 Event.meta == {} 이고 본문은 틀 그대로
def test_read_only_never_generates():       # mode read_only면 generated == []
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_feedback.py tests/test_replay.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: feedback replay mode"`

---

### Task 10: 통계와 지표

**Files:**
- Create: `src/harness/stats.py`, `src/harness/metrics.py`
- Test: `tests/test_stats.py`, `tests/test_metrics.py`

**Interfaces:**
- Consumes: Task 8, 9
- Produces:
  - `wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None` (n=0이면 None)
  - `bootstrap_diff(a: list[tuple[int,int]], b: list[tuple[int,int]], iters: int, seed: int) -> tuple[float, float]`: 같은 시나리오 순서의 (분자, 분모) 쌍. 시나리오 인덱스를 복원 추출해 pooled 비율 차이 `a − b`의 2.5%, 97.5% 분위수. `random.Random(seed)` 사용
  - `@dataclass Rate: k: int, n: int, value: float|None, ci: tuple[float,float]|None`, `rate(k, n) -> Rate`
  - `@dataclass Delay: fact: str, scenario_id: str, seconds: float|None` (None = 미철회/미반영)
  - `@dataclass Metrics`: `m1: Rate, m2: Rate, m3: int, by_family: dict[str, dict[str, Rate|int]]`(키 `"m1"`,`"m2"`,`"m3"`), `feedback_by_family`(같은 모양), `shallow_fix: Rate, reach: Rate, contamination: Rate, sourceless: Rate`(결과 단위), `retraction: list[Delay], reflection: list[Delay], worst_family: tuple[str, float]|None, errors: list[str]`(오류 난 시나리오 id), `context_chars_mean: float|None, record_ms_p50/p95, search_ms_p50/p95: float|None`
  - `compute_metrics(runs: list[ScenarioRun], scenarios: Mapping[str, Scenario]) -> Metrics`
    - `error`가 있는 실행은 모든 지표에서 빼고 `errors`에 id를 넣는다.
    - M1·M2·보조 지표는 `mode == read_only` 실행만 pooled로. 되먹임 실행은 `feedback_by_family`에만.
    - M3 = 모든 실행(두 모드)의 누출 결과 수 합.
    - `shallow_fix`: 분모 = `about`에 그 시점 참인 `fix: root` 사실이 있는 block·promote 문항, 분자 = 그중 choice basis에 `fix: workaround` 사실이 있는 문항.
    - `reach` = promote 중 `reached`, `contamination` = block·promote 중 `contaminated`, `sourceless` = 전체 결과 중 `sources == []`.
    - 철회 지연: 사실 f의 `valid_until`이 유한할 때, 그 뒤 시각의 질문 중 `about`에 f가 있는 것을 시각순으로 보며, 결과 basis 어디에도 f가 없는 첫 질문의 `t − valid_until`. 끝까지 있으면 None. 해당 질문이 없으면 항목을 만들지 않는다. 반영 지연은 `valid_from` 기준으로 basis에 f가 처음 나타나는 질문.
    - `worst_family` = 읽기 전용 계열 중 m1 값이 가장 큰 (계열, 값).
    - `by_author: dict[str, dict[str, Rate]]`: 시나리오 `provenance.author`(`human`/`llm`/`generator`)별 m1·m2 (설계 8.3 출처별 보고).
  - `per_scenario_counts(runs, kind: Literal["block","promote"], hit: Literal["FP","TP"]) -> list[tuple[int,int]]` (scenario_id 정렬 순)
  - `flip_rate(repeats: list[list[ScenarioRun]]) -> Rate`: 같은 (scenario, probe)의 `[(r.text, tuple(r.sources)) for r in results]`가 실행마다 하나라도 다르면 뒤집힘

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_wilson_known_value(): lo, hi = wilson(5, 10); assert abs(lo-0.2366)<1e-3 and abs(hi-0.7634)<1e-3
def test_wilson_zero_n(): assert wilson(0, 0) is None
def test_bootstrap_deterministic_and_zero_for_identical(): assert bootstrap_diff(a, a, 1000, 1) == (0.0, 0.0)
def test_m1_m2_pooled():         # 손으로 만든 ScenarioRun 2개(block FP 1/2, promote TP 1/1) → m1 0.5, m2 1.0
def test_feedback_runs_excluded_from_m1():  # feedback 실행의 FP는 m1에 없고 feedback_by_family에 있음
def test_m3_counts_both_modes()
def test_errored_runs_excluded():  # error 실행만 있으면 m1.value is None, errors == ["s1"]
def test_retraction_delay():       # until 10/09, 10/10 질문엔 아직 f_old, 10/12엔 없음 → 3일(259200초)
def test_flip_rate()
def test_by_author_split()         # human 시나리오와 llm 시나리오의 m1이 따로 나옴
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_stats.py tests/test_metrics.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: stats and metrics"`

---

### Task 11: 판정과 선택 규칙

**Files:**
- Create: `src/harness/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: Task 5(EvalConfig), Task 10
- Produces:
  - `@dataclass Verdict: m1_ok: bool|None, m2_ok: bool|None, m3_ok: bool, abs_cap_ok: bool|None, passed: bool, reasons: list[str]`
  - `judge_engine(m: Metrics, base: Metrics, cfg: EvalConfig) -> Verdict`: `base`는 all_store. 설계 8.2 그대로. 값이 None이면 해당 항목 None이고 `passed=False`, reasons에 "표본 없음"
  - `@dataclass EngineScore: engine_id: str, metrics: Metrics, verdict: Verdict, repeats: list[Metrics]`(반복 실행별)
  - `averaged(repeats: list[Metrics]) -> Metrics`: 실행별 m1·m2 값의 평균을 `value`로, k·n·ci는 첫 실행 것을 쓴다(설계 8.3)
  - `relative_metrics(m: Metrics, none: Metrics, all_store: Metrics, oracle: Metrics) -> dict[str, float|None]`: 키 `"improvement_ratio"` = `(all_store.m1 − m.m1) / (all_store.m1 − oracle.m1)`, `"repeat_mistake_reduction"` = `1 − (m의 promote FN 비율 / none의 promote FN 비율)`. 분모가 0이거나 값이 None이면 None (설계 8.1 관찰 지표)
  - `@dataclass RankEntry: engine_id: str, position: int, tied_with_previous: bool, tiebreak: str|None, diff_ci: tuple[float,float]|None`
  - `rank(scores: list[EngineScore], runs: Mapping[str, list[ScenarioRun]], cfg: EvalConfig) -> list[RankEntry]`: `passed`만, M2 내림차순 정렬. 인접 쌍은 `bootstrap_diff(per_scenario_counts(.., "promote","TP"))` 구간이 0을 포함하면 동률. 동률이면 m1 낮은 쪽 → shallow_fix 낮은 쪽 → 철회 지연 중앙값 짧은 쪽(None은 가장 김) → reach 높은 쪽 → context_chars_mean 작은 쪽 순으로 정하고, 쓰인 기준 이름을 `tiebreak`에 남긴다

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_pass_when_half_of_baseline():      # base m1 0.6, 엔진 m1 0.3, m2 −0.05, m3 0 → passed
def test_fail_m2_drop_over_10pp():          # m2 −0.11 → m2_ok False
def test_fail_any_leak()
def test_abs_cap_optional():                # cfg.judgment.m1_abs_cap=0.1, m1 0.2 → abs_cap_ok False
def test_rank_tie_broken_by_m1():           # M2 같음(구간이 0 포함) → m1 낮은 쪽 먼저, tiebreak == "m1"
def test_averaged_uses_mean_value()
def test_relative_metrics():                # all_store m1 0.6, oracle 0, 엔진 0.3 → improvement_ratio 0.5; 분모 0 → None
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_selection.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: judgment and selection rules"`

---

### Task 12: 자가 검증과 시나리오 변별력 검사

**Files:**
- Create: `src/harness/selfcheck.py`
- Test: `tests/test_selfcheck.py`

**Interfaces:**
- Consumes: Task 4, 6, 8, 10
- Produces:
  - `@dataclass CheckResult: name: str, ok: bool, detail: str`
  - `run_selfcheck(scenarios: list[Scenario], cfg: EvalConfig) -> tuple[list[CheckResult], dict[str, list[ScenarioRun]]]`: 기준선 4종을 모든 시나리오에 돌리고 아래를 검사한다(설계 9.2). 이름 고정:
    - `oracle_perfect`: oracle m1==0, m2==1, m3==0
    - `none_empty`: none m1==0, m2==0, m3==0
    - `all_store_max_m1`: all_store m1 ≥ 다른 세 기준선 m1, 그리고 > 0
    - `repeat_n_poison`: `single_source_poison` 시나리오가 있으면 repeat_n의 그 계열 m1 > oracle의 m1
    - `repeat_n_echo`: `echo` 시나리오가 있으면 repeat_n의 echo 되먹임 m1 > oracle의 m1
    - 계열이 없어 건너뛴 검사는 `ok=True, detail="skipped: no <family> scenarios"`
  - `check_discrimination(s: Scenario, cfg: EvalConfig) -> list[Issue]`: oracle이 틀리거나 누출하면 error `oracle_wrong`. all_store의 block FP가 0이면 warning `too_easy`. `leak` 문항이 있으면 `all_store_noacl`로 돌려 그 문항에서 누출이 없을 때 error `leak_not_triggered`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_selfcheck_passes_on_good_set():  # factories로 truth_change + single_source_poison 시나리오 → 모두 ok
def test_selfcheck_flags_broken_scenario():  # f_old/f_new 라벨을 일부러 뒤바꾼 시나리오 → oracle_perfect가 ok False
def test_discrimination_too_easy_warning():  # all_store가 f_new만 찾게 쓴 시나리오 → warning "too_easy"
def test_discrimination_leak_not_triggered():  # leak 문항인데 숨은 기록과 질의가 무관 → error
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_selfcheck.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: selfcheck and discrimination check"`

---

### Task 13: 리포트

**Files:**
- Create: `src/harness/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: Task 10, 11
- Produces:
  - `@dataclass RunMeta: run_id: str, started_at: datetime, harness_version: str, config_hash: str, scenario_set_hash: str, n_scenarios: int, machine: str, engine_infos: dict[str, EngineInfo], sealed: bool`
  - `harness_version() -> str` (`git rev-parse --short HEAD`, 실패하면 `"unknown"`), `machine_info() -> str` (`platform.platform()` + Python 버전)
  - `blind_ids(names: list[str], seed: int) -> dict[str, str]`: 이름 → `"E1"`, `"E2"`, … 시드로 섞은 결정적 대응
  - `write_report(out_dir: Path, meta: RunMeta, scores: list[EngineScore], ranking: list[RankEntry], runs: Mapping[str, list[ScenarioRun]], selfcheck: list[CheckResult]) -> None`. 파일:
    - `summary.json`: meta + 엔진 ID별 metrics·verdict + ranking + selfcheck
    - `results.jsonl`: 문항당 한 줄(engine_id, scenario_id, probe_id, outcome, choice, results, leaks). **`meta.sealed`면 쓰지 않음**
    - `report.md`: 설계 13장 순서의 표(머리말, 판정, 보조·관찰(`relative_metrics` 포함), 계열별, 되먹임, 출처별, 순위, 성능, 엔진 한계(`EngineInfo`의 `virtual_clock`·`sources`·`scopes`, 설계 15장 5번), 오류 시나리오). sealed면 시나리오·문항 id를 쓰지 않음
    - `engine_ids.json`: ID → 이름 대응(블라인드용, 별도 파일)
  - 리포트의 엔진 이름 자리는 모두 ID다. 기준선만 이름 그대로 둔다(`none`, `all_store`, `repeat_n`, `oracle`). 기준선은 비교 기준이라 숨길 이유가 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_report_files_written(tmp_path)        # 4개 파일 존재, summary.json에 config_hash
def test_sealed_omits_details(tmp_path)        # results.jsonl 없음, report.md에 probe id 문자열 없음
def test_blind_ids_deterministic()             # 같은 시드 → 같은 대응, 이름이 report.md에 안 나옴
def test_errors_listed_in_report(tmp_path)
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_report.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: run report"`

---

### Task 14: CLI

**Files:**
- Create: `src/harness/cli.py`
- Test: `tests/test_cli.py` (`typer.testing.CliRunner`)

**Interfaces:**
- Consumes: 전부
- Produces: 명령 `harness`:
  - `harness scenario check PATH... [--include-drafts]`: 파일·디렉터리마다 로드 → `validate_scenario` → `check_discrimination`. 이슈를 `파일: level code message`로 출력. error가 있으면 exit 1
  - `harness selfcheck --scenarios DIR... [--config PATH] [--include-drafts]`: CheckResult 표 출력. 실패가 있으면 exit 1
  - `harness run --scenarios DIR... [--engine NAME]... [--config PATH] [--out DIR] [--include-drafts] [--sealed] [--repeat N]`:
    - 기준선 4종은 항상 함께 돌린다. `--engine`은 추가 엔진(기준선 이름이면 무시).
    - `--repeat` 기본값은 추가 엔진이면 `cfg.llm_repeats`, 기준선은 항상 1.
    - 순서: selfcheck → 실패하면 엔진 평가 없이 exit 1. 통과하면 추가 엔진 실행 → metrics/verdict/rank → `write_report(out/<run_id>)`. run_id = `YYYYMMDD-HHMMSS`.
    - 출력 마지막 줄: 리포트 경로.
  - 시나리오가 0개면(승인된 것 없음): `"승인된 시나리오가 없습니다. 초안을 포함하려면 --include-drafts"` 출력, exit 2
  - `--config` 기본값 `config/eval-v0.1.yaml`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_check_reports_error_exit1(tmp_path)        # unknown_fact가 있는 파일 → exit 1, 출력에 "unknown_fact"
def test_run_no_approved_scenarios(tmp_path)        # draft만 → exit 2, 안내 문구
def test_run_writes_report(tmp_path)                # factories 시나리오(approved) 2개 → exit 0, 마지막 줄 경로에 report.md
def test_run_stops_on_selfcheck_failure(tmp_path)   # 라벨 뒤바뀐 시나리오 → exit 1, report 없음
def test_run_extra_engine_import_path(tmp_path)     # --engine tests.dummy_engine:Dummy → summary.json에 엔진 5개
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest tests/test_cli.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: harness CLI"`

---

### Task 15: 예시 시나리오와 끝까지 돌려 보기

**Files:**
- Create: `scenarios/examples/truth_change_vendor_module.yaml`, `scenarios/examples/single_source_poison_test_cmd.yaml`, `scenarios/examples/conditional_db_isolation.yaml`, `scenarios/examples/echo_stale_owner.yaml`, `scenarios/examples/leak_cross_team_config.yaml`, `README.md`
- Test: `tests/test_examples.py`

**Interfaces:**
- Consumes: 전부

시나리오 내용(영어, 설계 5.3 계열 틀에 맞춤). 모두 `review.status: draft`, `provenance.author: llm`(설계 11장: 작성자 예시는 스모크 테스트 전용):
- `truth_change`: 설계 5.1의 벤더 모듈 예시 + promote 문항(10/09 전에 "do not modify"를 묻는 질문).
- `single_source_poison`: u1이 세 세션에서 "run tests with `make test-fast`"(거짓)를 주입. u2·u3이 "`pytest -x tests/unit`"(참)를 `test` 신호로 한 번씩 확인. block = 테스트 명령을 묻는 질문, promote = 같은 질문을 참인 사실만 있는 다른 저장소 맥락으로.
- `conditional`: "order-svc uses READ COMMITTED"(`conditions: {service: order-svc}`)를 여러 번 관찰. "billing-svc uses SERIALIZABLE"은 한 번. block = billing-svc 맥락의 질문, promote = order-svc 맥락의 질문.
- `echo` (mode feedback): 담당 팀 규칙 f_old가 세 세션에서 참이던 시기에 기록되고, 10/09에 f_new(human 1회)로 바뀜. 10/10, 10/11, 10/12 질문 3개. 각각 `feedback.action`(f_old → "Skipped the fix: vendor module belongs to another team", f_new → "Fixed it directly"), `abstain`, `on_false`.
- `leak_cross_team`: 팀 a의 team 기록 "staging DB password rotates via `vault kv get secret/stg`"(`secret_markers: ["secret/stg"]`). leak = 팀 b 사용자의 같은 주제 질문, promote = 팀 a 사용자의 질문.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_examples_validate_clean():   # 모든 예시에 validate_scenario error 0
def test_examples_selfcheck_passes(): # run_selfcheck(include_drafts) → 모든 CheckResult ok
def test_examples_discriminate():     # check_discrimination error 0, too_easy 경고 0
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_examples.py -v` → FAIL(파일 없음).
- [ ] **Step 3: 예시 YAML 5개 작성** — 위 내용대로. 검사가 실패하면 **라벨은 그대로 두고** 본문 표현(질의와 겹치는 단어)만 조정한다.
- [ ] **Step 4: 통과 확인** — `uv run pytest -v`(전체) → all passed. 이어서 `uv run harness selfcheck --scenarios scenarios/examples --include-drafts` → 모든 검사 ok, `uv run harness run --scenarios scenarios/examples --include-drafts --out runs` → 마지막 줄 리포트 경로. `report.md`에서 all_store M1 > 0, oracle M1 = 0을 눈으로 확인.
- [ ] **Step 5: `README.md` 작성과 `scenarios/dev/.gitkeep` 추가** — README에는 설치(`uv sync`), 세 명령(`scenario check`, `selfcheck`, `run`), 시나리오 형식(설계 5장 링크), 세트 구분(`scenarios/dev/` 승인본, `scenarios/examples/` 스모크용, test 세트는 레포 밖에서 `--sealed`), "예시는 스모크 테스트 전용이며 발표 수치에 쓰지 않음"을 적는다.
- [ ] **Step 6: 커밋** — `git commit -m "feat: example scenarios and README"`
