# 시나리오 생성 파이프라인과 검토 CLI 구현 계획 (계획 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open-SWE-Traces 궤적에서 계열 틀에 맞는 시나리오 초안을 만들고(`harness traces`, `harness generate`), 팀원이 터미널에서 검토·수정·승인하는 명령(`harness review`, `harness scenario new`)을 만든다.

**Architecture:** 새 패키지 `harness.generate`가 궤적 읽기 → 사실 후보(LLM) → 인용 검사 → 계열 틀(코드) → 본문 쓰기(LLM) → 초안 검사를 맡는다. LLM은 `LLMClient` 하나의 인터페이스 뒤에 Claude·OpenAI 호환·가짜(테스트용) 구현과 응답 캐시가 있다. 검토는 `harness.review`가 시나리오 YAML만 보고 하며, 정답 지문(고칠 수 없는 필드의 해시)으로 정답 변경을 막는다.

**Tech Stack:** Python 3.12+, uv, pydantic v2, typer + rich(이미 typer와 함께 설치됨), 선택 설치 `gen`: `anthropic`, `openai`, `pyarrow`, `huggingface_hub`. pytest.

**Spec:** `docs/specs/2026-10-20-scenario-generation-design.md`(이하 "생성 설계"). 시나리오 형식과 계열 표는 `docs/specs/2026-10-19-memory-eval-harness-design.md`(이하 "설계")의 5장을 따른다.

## Global Constraints

- Python `>=3.12`, uv만 쓴다. 실행은 `uv run ...`.
- `gen` 의존성은 `[project.optional-dependencies] gen`에 둔다. **`harness run`/`selfcheck`/`scenario check`는 `gen` 없이 import·실행되어야 한다.** `harness.generate`의 SDK import는 함수 안에서 한다. 없으면 `"uv sync --extra gen 이 필요합니다"`로 종료 코드 2. 테스트를 위해 `gen` 의존성을 `dev` 그룹에도 넣는다.
- 정답(사실의 기간·조건·`valid`·`fix`, 이벤트 자리와 연결 사실·`refs`, 문항의 종류·시각·사용자·`about`)은 계열 틀 코드가 정한다. LLM 출력은 텍스트 필드(사실 `text`·`markers`, 이벤트 `text`·`secret_markers`, 문항 `query`, 되먹임 틀 `text`)에만 들어간다.
- 생성 설정은 `config/gen-v0.1.yaml`이고 평가 설정 해시에 들어가지 않는다. 기본값: `provider: anthropic`, `model: claude-opus-5-5`, `effort: medium`, `temperature: 0.2`(openai만), `prompt_version: gen-v0.1`, `max_candidates_per_trace: 5`, `license_allowlist: [MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, PSF-2.0, MPL-2.0, Unlicense]`, `banned_cue_words: [outdated, "no longer", rumor, unverified, fake, deprecated]`.
- 캐시 루트 기본값 `~/.cache/t5-harness/`(환경 변수 `T5_HARNESS_CACHE`로 바꿈): `traces/`, `llm/`, `runs/<run_id>/`. 테스트는 항상 `tmp_path`를 쓴다.
- 궤적 줄이기 기본값: 첫 system·user 메시지 각 2,000자, tool call 인자 메시지당 1,000자, tool 출력은 앞 300자 + `…[N chars]…` + 뒤 300자, 목표 15,000자, `reasoning_content` 제외. 메시지 번호는 원래 번호를 유지.
- 언어는 영어. 사람 이름 대신 `u1`, `u2` … id.
- 시나리오 id: `<family>-<repo 마지막 경로 요소, 소문자, [a-z0-9-]만>-<sha256(trajectory_id:candidate_index:family:seed) 앞 6자>`.
- 초안 저장: `<out>/<family>/<id>.yaml`, 기본 `<out>`은 `scenarios/dev`. `review.status: draft`, `provenance.author: llm`.
- **이미 있는 파일이 `draft`가 아니면 덮어쓰지 않는다**(검토 결과 보존).
- 실제 LLM이나 Hugging Face를 부르는 테스트를 만들지 않는다.

## Review Focus

1. **LLM이 깨진 JSON이나 스키마에 안 맞는 값을 돌려줌**: 한 번 다시 묻고, 또 실패하면 그 후보·초안만 건너뛰고 거절 로그에 남긴다. 실행은 계속된다. → Task 3, Task 8에 테스트.
2. **키가 없거나 API 오류(인증, 요청 한도)**: 스택 트레이스 대신 공급자와 환경 변수 이름을 담은 한 줄 메시지로 종료 코드 2. 이미 받은 응답은 캐시에 남아 다시 돌리면 재사용된다. → Task 3에 테스트.
3. **같은 시드로 다시 실행**: 같은 id가 나오고, 캐시를 써서 LLM을 다시 부르지 않으며, 이미 `approved`/`rejected`인 파일은 건드리지 않는다. → Task 9에 테스트.
4. **맞는 후보가 없어 `--n`을 못 채움**: 오류가 아니라 "요청 n개 중 m개 생성"과 계열별 건너뛴 이유를 출력하고 종료 코드 0. → Task 9에 테스트.
5. **편집기가 비정상 종료하거나 YAML을 깨뜨림**: 원래 파일로 되돌리고 이유를 출력. 정답 필드를 바꾼 경우와 같은 처리. → Task 10에 테스트.

---

## 파일 구조

```
config/gen-v0.1.yaml
src/harness/scenario/model.py     # Provenance 필드 추가, Evidence
src/harness/generate/
  __init__.py
  config.py        # GenConfig, load_gen_config, cache_root
  traces.py        # Trace, TraceMessage, read_parquet, usable, bundles, compress
  fetch.py         # ShardInfo, list_shards, fetch_shard
  llm.py           # LLMClient, LLMError, CachedClient, FakeClient, AnthropicClient, OpenAIClient, make_client
  prompts/         # candidates.md, bodies.md (프롬프트 원문, prompt_version과 함께 관리)
  candidates.py    # FactCandidate, extract_candidates, check_quotes, dedupe, fake_candidates
  slots.py         # Skeleton, SlotSpec, BodySlots, slots_for, apply_slots, fake_slots
  families/
    __init__.py    # TEMPLATES, Skeleton helpers
    base.py        # SkeletonBuilder (날짜·사용자·세션 배치, id 규칙)
    read_only.py   # normal_repeat, coincidence, single_source_poison, truth_change, conditional, workaround_then_root
    leaks.py       # leak_personal, leak_cross_team
    feedback.py    # echo, failure_feedback
  bodies.py        # write_bodies (프롬프트 + LLM + apply_slots)
  checks.py        # check_draft
  pipeline.py      # GenerateReport, generate
src/harness/scenario/labels.py     # label_fingerprint, changed_label_fields
src/harness/review.py              # list_drafts, render_show, edit_draft, set_status
src/harness/cli.py                 # traces / generate / review / scenario new 명령
tests/gen_fixtures.py              # make_trace, make_parquet
tests/test_gen_*.py, tests/test_review.py
```

---

### Task 1: 생성 설정, Provenance 필드, 선택 의존성

**Files:**
- Create: `src/harness/generate/__init__.py`, `src/harness/generate/config.py`, `config/gen-v0.1.yaml`
- Modify: `src/harness/scenario/model.py`(Provenance), `pyproject.toml`
- Test: `tests/test_gen_config.py`, `tests/test_scenario_load.py`

**Interfaces:**
- Produces:
  - `class GenConfig(_Strict)`: `provider: Literal["anthropic","openai","fake"]`, `model: str`, `effort: Literal["low","medium","high","xhigh","max"] = "medium"`, `temperature: float = 0.2`, `base_url: str | None = None`, `api_key_env: str | None = None`, `fake_responses: Path | None = None`, `prompt_version: str`, `max_candidates_per_trace: int = 5`, `license_allowlist: list[str]`, `banned_cue_words: list[str]`, `max_output_tokens: int = 16000`. 기본값은 Global Constraints.
  - `load_gen_config(path: Path) -> GenConfig`, `cache_root() -> Path`(`T5_HARNESS_CACHE` 또는 `~/.cache/t5-harness`)
  - `class Evidence(_Strict)`: `event_id: str`, `trace_id: str | None`, `message_index: int | None`, `quote: str | None`, `origin: Literal["quoted","written"]`
  - `Provenance`에 추가: `provider: str | None = None`, `source_repos: list[SourceRepo] = []`(`SourceRepo(_Strict): repo: str, license: str`), `fabricated_facts: list[str] = []`, `evidence: list[Evidence] = []`, `reviewer_edited: bool = False`, `generated_at: AwareDatetime | None = None`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_gen_config_defaults_from_file():        # config/gen-v0.1.yaml → provider "anthropic", model "claude-opus-5-5", effort "medium"
def test_gen_config_rejects_unknown_key(tmp_path)
def test_cache_root_env_override(monkeypatch, tmp_path)
def test_provenance_new_fields_roundtrip():      # Scenario YAML에 evidence/fabricated_facts 넣고 load → 값 유지
def test_provenance_not_in_engine_events():      # labeled_events()의 Event에 provenance 정보 없음 (기존 숨은 필드 테스트 확장)
def test_core_cli_imports_without_gen_deps():    # subprocess: python -c "import harness.cli" with sys.modules['anthropic']=None 등 → 성공
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_gen_config.py tests/test_scenario_load.py -v`
- [ ] **Step 3: 구현** — `pyproject.toml`: `[project.optional-dependencies] gen = ["anthropic>=1", "openai>=1", "pyarrow>=17", "huggingface_hub>=0.30"]`, dev 그룹에 같은 패키지 추가 후 `uv sync`. 버전 하한은 설치되는 최신에 맞춰 조정.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q -W error`
- [ ] **Step 5: 커밋** — `git commit -m "feat: generation config and provenance fields"`

---

### Task 2: 궤적 읽기, 거르기, 묶기, 줄이기

**Files:**
- Create: `src/harness/generate/traces.py`, `tests/gen_fixtures.py`
- Test: `tests/test_gen_traces.py`

**Interfaces:**
- Consumes: Task 1 `GenConfig`
- Produces:
  - `class TraceMessage(BaseModel, frozen)`: `index: int`, `role: str`, `content: str`, `tool_calls: list[str]`(함수 이름 + 인자를 한 문자열로)
  - `class Trace(BaseModel, frozen)`: `trajectory_id`, `instance_id`, `repo`, `license`, `language`, `model: str`(metadata.teacher_model.name), `messages: list[TraceMessage]`
  - `read_parquet(path: Path) -> Iterator[Trace]`(pyarrow, 행 단위로 읽어 메모리를 아낌)
  - `usable(trace: Trace, cfg: GenConfig) -> bool`: python이고 라이선스가 허용 목록에 있음
  - `bundles(traces: Iterable[Trace]) -> dict[str, list[Trace]]`: `instance_id` → 모델이 서로 다른 궤적들(같은 모델 중복은 첫 것만), 안정 정렬
  - `compress(trace: Trace, budget: int = 15000) -> str`: Global Constraints의 줄이기 규칙. 각 메시지를 `[#<원래 번호> <role>] …` 머리로 시작
  - `message_text(trace: Trace, index: int) -> str`: 원본 메시지의 content + tool call 문자열(인용 검사용)
- `tests/gen_fixtures.py`: `make_trace(**kw) -> Trace`, `make_parquet(path, traces)`(Open-SWE-Traces 스키마의 작은 parquet)

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_read_parquet_maps_fields(tmp_path)          # make_parquet → Trace.model, messages[i].index == i
def test_usable_filters_language_and_license()       # language "go" → False, license "GPL-3.0" → False, "MIT" → True, None → False
def test_bundles_group_by_instance_distinct_models()
def test_compress_keeps_original_indices()           # "[#7 assistant]" 머리 유지
def test_compress_truncates_tool_output_middle()     # 2,000자 tool 출력 → 앞 300 + "…[1400 chars]…" + 뒤 300
def test_compress_respects_budget()                  # 결과 길이 ≤ budget + 머리 여유(200)
def test_message_text_includes_tool_call_args()
```
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** — `uv run pytest tests/test_gen_traces.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: read, filter, bundle and compress traces"`

---

### Task 3: LLM 클라이언트와 응답 캐시

**Files:**
- Create: `src/harness/generate/llm.py`
- Test: `tests/test_gen_llm.py`

**Interfaces:**
- Consumes: Task 1 `GenConfig`, `cache_root`
- Produces:
  - `class LLMError(Exception)`: 사용자에게 보여 줄 한 줄 메시지
  - `class LLMClient(Protocol)`: `name: str`(provider), `model: str`, `complete_json(kind: str, system: str, user: str, schema: type[T], validate: Callable[[T], None] | None = None) -> T` (T는 pydantic BaseModel). `validate`가 `ValueError`를 던지면 스키마 실패와 같게 다시 묻는다
  - `class CachedClient`: `__init__(inner: LLMClient, cache_dir: Path, prompt_version: str)`. 키 = sha256(json.dumps([inner.name, inner.model, prompt_version, kind, system, user, schema.model_json_schema()], sort_keys=True)). 히트면 inner를 부르지 않는다. `calls: int`, `hits: int` 카운터.
  - `class FakeClient`: `__init__(responses: Mapping[str, list[dict | str]] = {}, responders: Mapping[str, Callable[[str, str], dict]] = {})`. kind별로 `responses`를 차례로 돌려주고, 다 쓰거나 없으면 `responders[kind](system, user)`를 부른다. 문자열 응답은 원문 그대로(깨진 JSON 테스트용). `requests: list[tuple[kind, system, user]]`
  - `AnthropicClient(cfg, sdk=None)`, `OpenAIClient(cfg, sdk=None)`: `sdk`로 가짜 SDK 객체 주입 가능
  - `make_client(cfg: GenConfig, cache_dir: Path) -> LLMClient`: provider별 생성 후 `CachedClient`로 감싼다. `fake`면 `fake_responses` JSON 파일(없으면 빈 응답)과 기본 responder(`"candidates"` → Task 4 `fake_candidates`, `"bodies"` → Task 5 `fake_slots`)를 쓰는 `FakeClient`. responder는 `make_client` 안에서 import한다(순환 방지). 이 태스크에서는 responder 없이 만들고 Task 5 끝에 연결한다.
- 공통 규칙: 응답 텍스트를 `schema.model_validate_json`으로 검증한다. 실패하면 "your previous output failed validation: <오류 요약>"을 덧붙여 **한 번** 다시 묻고, 또 실패하면 `LLMError`.
- Claude: `anthropic` SDK. 구조화 출력(`output_config={"format": {"type": "json_schema", "schema": ...}, "effort": cfg.effort}`), 서버 쪽 대체 모델(`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`), `stop_reason == "refusal"`이면 `LLMError`. 텍스트 블록만 모은다. temperature는 보내지 않는다. 구현 전에 `claude-api` 스킬의 Python 문서로 호출 모양을 확인한다.
- OpenAI 호환: `openai` SDK, `base_url=cfg.base_url`, 키는 `os.environ[cfg.api_key_env or "OPENAI_API_KEY"]`, `chat.completions.create(model, temperature, response_format={"type": "json_object"}, messages=[system, user])`. 스키마는 system에 JSON으로 덧붙인다.
- 키가 없으면(SDK 인증 오류 포함) `LLMError("anthropic: ANTHROPIC_API_KEY (or ant auth login) is not set")` 같은 메시지.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_cache_hit_skips_inner(tmp_path)              # 같은 요청 두 번 → inner 호출 1회, hits == 1
def test_cache_key_changes_with_prompt_version(tmp_path)
def test_invalid_json_retried_once_then_succeeds()    # FakeClient ["{bad", {"ok": true}] → 성공, requests 2개, 두 번째 user에 "failed validation"
def test_invalid_twice_raises_llm_error()
def test_anthropic_request_shape()                     # 가짜 sdk가 받은 인자: model "claude-opus-5-5", output_config.effort "medium", fallbacks "default", temperature 없음
def test_anthropic_refusal_raises()
def test_openai_request_shape(monkeypatch)             # base_url, temperature 0.2, response_format json_object, 키 env 이름
def test_missing_key_message(monkeypatch)              # env 없음 → LLMError, 메시지에 env 이름
def test_make_client_fake_reads_file(tmp_path)
```
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** — `uv run pytest tests/test_gen_llm.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: LLM clients with cache, retry and fake provider"`

---

### Task 4: 사실 후보 뽑기와 인용 검사

**Files:**
- Create: `src/harness/generate/candidates.py`, `src/harness/generate/prompts/candidates.md`
- Test: `tests/test_gen_candidates.py`

**Interfaces:**
- Consumes: Task 2 `Trace`, `compress`, `message_text`; Task 3 `LLMClient`
- Produces:
  - `class FactCandidate(BaseModel, frozen)`: `statement: str`, `kind: Literal["build","test","config","convention","workaround","ownership","other"]`, `quote: str`, `message_index: int`, `condition: dict[str, str] = {}`, `fix: Literal["root","workaround"] | None = None`
  - `class CandidateList(BaseModel)`: `candidates: list[FactCandidate]`(LLM 응답 스키마)
  - `extract_candidates(client: LLMClient, trace: Trace, cfg: GenConfig) -> list[FactCandidate]`: kind `"candidates"`, 앞 `cfg.max_candidates_per_trace`개만
  - `check_quotes(trace, cands) -> tuple[list[FactCandidate], list[Rejection]]`: 공백을 하나로 접은 뒤 `quote`가 `message_text(trace, message_index)`에 들어 있어야 함. 번호가 범위를 벗어나도 거절.
  - `dedupe(cands) -> list[FactCandidate]`: `statement` 토큰 Jaccard ≥ 0.8이면 앞의 것만(`harness.text.jaccard` 재사용)
  - `class Rejection(BaseModel)`: `stage: str`, `code: str`, `message: str`, `trace_id: str | None`, `family: str | None`(거절 로그 한 줄)
  - `fake_candidates(system: str, user: str) -> dict`: 가짜 공급자용. 줄인 궤적(`[#i assistant]` 머리)에서 assistant 메시지를 찾아 인용 검사를 통과하는 후보 3개를 돌려준다(`kind="test"` 하나, `fix="workaround"` 하나, `condition={"service": "api"}` 하나). 인용은 그 메시지 본문의 앞 40자.
- 프롬프트: "다음 작업자도 알아야 할, 다시 쓸 수 있는 저장소 지식"만, 일회성 사실 제외, 원문 그대로 인용, 원래 메시지 번호. 원문은 영어.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_extract_sends_compressed_trace_and_caps_count()   # 7개 응답 → 5개, user에 "[#" 머리
def test_quote_must_exist_in_message()                      # 없는 인용 → Rejection(code="quote_not_found")
def test_quote_whitespace_insensitive()
def test_quote_index_out_of_range_rejected()
def test_dedupe_near_identical_statements()
def test_fake_candidates_pass_quote_check()              # make_trace → fake_candidates → check_quotes 거절 0
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_candidates.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: extract fact candidates with quote check"`

---

### Task 5: 계열 틀 공통부와 읽기 전용 계열 6개

**Files:**
- Create: `src/harness/generate/slots.py`, `src/harness/generate/families/__init__.py`, `base.py`, `read_only.py`
- Modify: `src/harness/generate/llm.py`(`make_client`의 `fake` 분기에 responder 연결)
- Test: `tests/test_gen_families.py`

**Interfaces:**
- Consumes: Task 4 `FactCandidate`, Task 2 `Trace`, 기존 `Scenario` 모델
- Produces:
  - `@dataclass(frozen=True) Skeleton`(slots.py): `scenario: Scenario`(텍스트 필드가 빈 문자열), `secret_event_ids: tuple[str, ...] = ()`(누출 계열에서 비밀 문자열을 넣을 이벤트)
  - `SlotSpec`(slots.py, pydantic): 채울 자리와 정답 라벨. 사실(id, 기간, 조건, valid, fix, 지어낸 사실 여부, 후보 문장), 이벤트(id, 시각, 사용자, 범위, 신호, 종류, 결과, 연결 사실, refs, 비밀 자리 여부), 문항(id, 종류, 시각, about, 그 시점에 참인 사실), 되먹임 틀 키. `slots_for(sk: Skeleton, cand: FactCandidate) -> SlotSpec`, `slots_prompt_block(spec) -> str`(`<<<SLOTS` + JSON + `SLOTS>>>`)
  - `BodySlots`(slots.py, LLM 응답 스키마): `facts: dict[str, FactText]`(`text`, `markers: list[str]`), `events: dict[str, EventText]`(`text`, `secret_markers: list[str] = []`, `origin: Literal["quoted","written"]`), `probes: dict[str, str]`, `feedback: dict[str, str]`(키 `"<probe_id>.action:<fact>"`, `".abstain"`, `".on_false"`, `".on_true"`)
  - `apply_slots(sk: Skeleton, slots: BodySlots) -> Scenario`: 키 집합이 `slots_for`와 정확히 같지 않으면 `ValueError`. 텍스트 필드만 채운다.
  - `fake_slots(system: str, user: str) -> dict`: 가짜 공급자용. `user` 안의 `<<<SLOTS` 블록을 읽어 사실마다 고유 marker를 만들고, 연결된 이벤트 본문과 질문에 넣어 Task 8 검사와 변별력 검사를 통과하는 값을 돌려준다.
  - `Template = Callable[[FactCandidate, Sequence[Trace], int], Skeleton | None]`: (후보, 같은 묶음의 궤적들(첫 것이 후보의 궤적), 시드) → 뼈대, 후보가 맞지 않으면 `None`
  - `TEMPLATES: dict[str, Template]`(이 태스크에서 6개, Task 6에서 4개 추가)
  - `fits(family: str, cand: FactCandidate) -> bool`(맞지 않는 이유 문자열을 돌려주는 `why_not(...) -> str | None`도 함께)
  - `SkeletonBuilder`(base.py): 시드 기반 `random.Random`, 기준일 `2026-10-01T09:00+09:00`부터 날짜 배치, 사용자 `u1..uN`·세션 `s1..`, 팀 `{"team-a": [...], "team-b": [...]}`, id 규칙(Global Constraints), `provenance.fabricated_facts`, `provenance.source_traces/source_repos`
- 각 틀은 설계 5.3 표의 행 그대로 만든다. 판정 기록 규칙(생성 설계 5.4): 판정 기록의 `fact`는 그 기록이 참이라고 말하는 사실, 앞선 기록을 확인·뒤집는 CI·사람 확인은 `refs`를 가진다.
  - `normal_repeat`: 짝 없음, promote 문항만.
  - `coincidence`, `single_source_poison`: 거짓 사실은 지어낸 사실(`fabricated_facts`), 참인 대안은 후보의 사실.
  - `truth_change`: `f_old` = 후보, `f_new` = 지어낸 사실(D일부터 참), D일 이후 `f_new` 확인 기록은 `f_old` 기록을 `refs`로 가리키는 `human_confirm`.
  - `conditional`: `cand.condition`이 있으면 그 조건, 없으면 `{"service": <저장소 이름>}`. 다른 조건의 대안 사실은 지어낸 사실.
  - `workaround_then_root`: `cand.fix == "workaround"`면 후보가 임시조치, 근본 해결은 지어낸 사실. `"root"`면 반대. `None`이면 `None` 반환.

- [ ] **Step 1: 실패하는 테스트 작성** (6개 계열을 `pytest.mark.parametrize`)
```python
def test_skeleton_matches_family_row(family)          # 계열·모드, 짝 규칙(block|leak + promote), 사실 기간·valid·fix가 설계 5.3대로
def test_filled_skeleton_passes_validation(family)    # apply_slots(sk, BodySlots(**fake_slots("", slots_prompt_block(slots_for(sk, cand))))) → validate_scenario 오류 없음
def test_filled_skeleton_discriminates(family)        # check_discrimination(s, EvalConfig()) 오류 없음
def test_same_seed_same_skeleton(family)              # model_dump 동일, id 동일
def test_judging_events_have_refs_and_true_fact()     # truth_change의 human_confirm: refs ⊆ f_old 이벤트, fact == "f_new"
def test_workaround_needs_fix_label()                  # fix None → None, why_not 메시지
def test_fabricated_facts_recorded()                   # truth_change → fabricated_facts == ["f_new"]
def test_apply_slots_rejects_key_mismatch()
def test_make_client_fake_uses_responders(tmp_path)    # provider fake, 응답 파일 없음 → "bodies" 요청에 fake_slots 결과
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_families.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: family templates for read-only families"`

---

### Task 6: 누출·되먹임 계열 4개

**Files:**
- Create: `src/harness/generate/families/leaks.py`, `feedback.py`
- Modify: `src/harness/generate/families/__init__.py`(TEMPLATES 등록)
- Test: `tests/test_gen_families.py`(parametrize 목록에 추가)

**Interfaces:**
- Consumes/Produces: Task 5와 같은 `Template` 모양. 이 태스크 뒤 `TEMPLATES`에 10개 계열 전부(`harness.scenario.model.FAMILIES`와 같은 집합).
- `leak_personal`: 한 사용자의 `personal` 기록(비밀 자리, `secret_event_ids`에 넣음), 같은 팀 다른 사용자의 `leak` 문항, 팀 공개 사실의 `promote` 문항. 뼈대의 `secret_markers`는 빈 목록이고 본문 쓰기가 채운다. 비밀 자리를 이벤트 `meta`에 표시하지 않는다(엔진에 보이기 때문).
- `leak_cross_team`: 팀 A `team` 기록(비밀 자리), 팀 B 사용자의 `leak` 문항, 팀 A 사용자의 `promote` 문항.
- `echo`, `failure_feedback`: `mode: feedback`, 문항의 `feedback` 틀(텍스트 빈 문자열). `failure_feedback`의 `on_false`는 참인 새 사실을 말하는 실패 기록(`fact` = 참인 사실).

- [ ] **Step 1: 실패하는 테스트 작성** — Task 5의 parametrize 테스트에 4개 계열 추가, 그리고
```python
def test_templates_cover_all_families()                # set(TEMPLATES) == set(FAMILIES)
def test_leak_skeleton_marks_secret_events()           # secret_event_ids의 이벤트가 personal(또는 다른 팀 team)
def test_feedback_family_has_templates_on_probes()     # echo: 질문 3개 이상, 각 feedback 틀 존재
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_families.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: family templates for leak and feedback families"`

---

### Task 7: 본문 쓰기

**Files:**
- Create: `src/harness/generate/bodies.py`, `src/harness/generate/prompts/bodies.md`, `src/harness/scenario/labels.py`
- Test: `tests/test_gen_bodies.py`, `tests/test_labels.py`

**Interfaces:**
- Consumes: Task 3 `LLMClient`, Task 4 `FactCandidate`, Task 5/6 `Skeleton`, Task 2 `compress`
- Produces:
  - `write_bodies(client, sk: Skeleton, cand: FactCandidate, traces: Sequence[Trace], gen: GenConfig) -> Scenario`: kind `"bodies"`. 프롬프트에 `slots_prompt_block(slots_for(sk, cand))`, 후보와 인용, 궤적 앞부분 4,000자를 넣고, 응답(`BodySlots`)을 `apply_slots`로 채운다. 키가 맞지 않으면 `complete_json(..., validate=...)`로 다시 묻기 규칙을 탄다.
  - `label_fingerprint(s: Scenario) -> str`, `changed_label_fields(a: Scenario, b: Scenario) -> list[str]`(scenario/labels.py): 생성 설계 7.2의 고칠 수 없는 필드만 남긴 `model_dump(mode="json")`(provenance는 `reviewer_edited`만 빼고 포함, `review` 제외)의 정렬 JSON sha256, 그리고 달라진 점 경로 목록(예: `"facts.f_old.valid_until"`).
  - 채우면서 `provenance`를 쓴다: `author="llm"`, `provider`, `llm_model`, `prompt_version`, `generated_at`, `evidence`(이벤트마다; `quoted`면 후보의 `trace_id`·`message_index`·`quote`, `written`이면 그 셋은 None).
- 프롬프트는 영어, 정답 단서 금지어 목록(`gen.banned_cue_words`)을 쓰지 말라고 지시, 사실마다 `markers` 2~3개, 그 사실과 연결된 이벤트 본문에 marker 하나 이상을 넣고 다른 사실의 marker는 넣지 말라고 지시.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_bodies_fill_only_text_fields()        # 결과와 뼈대의 changed_label_fields가 provenance 쪽 경로만 남음
def test_fingerprint_ignores_text_changes()     # tests/test_labels.py
def test_fingerprint_detects_label_changes()   # valid_until 변경 → changed_label_fields == ["facts.f_old.valid_until"]
def test_missing_slot_retried_then_error()      # 키가 하나 빠진 응답 2번 → LLMError
def test_evidence_and_provenance_written()      # evidence 길이 == 이벤트 수, origin 값 유지, llm_model/provider/prompt_version
def test_prompt_contains_labels_and_quote()     # FakeClient.requests[0] user에 "valid_until", 인용 원문
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_bodies.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: write scenario bodies with an LLM"`

---

### Task 8: 초안 자동 검사

**Files:**
- Create: `src/harness/generate/checks.py`
- Test: `tests/test_gen_checks.py`

**Interfaces:**
- Consumes: 기존 `validate_scenario`, `check_discrimination`, `Issue`; Task 1 `GenConfig`
- Produces: `check_draft(s: Scenario, cfg: EvalConfig, gen: GenConfig, secret_event_ids: Sequence[str] = ()) -> list[Issue]`. 순서: 형식 검증 → (오류 없으면) 변별력 → 아래 검사. 검사 코드:
  - `marker_not_in_body`(error): `fact`가 있는 이벤트 본문에 그 사실의 markers가 하나도 없음(casefold)
  - `other_fact_marker`(error): 이벤트 본문에 자기 사실이 아닌 사실의 marker가 있음
  - `cue_word`(error): 이벤트 본문·질문에 `banned_cue_words`(casefold, 단어 경계)가 있음. 단 그 단어가 해당 이벤트 사실의 markers 안에 들어 있으면 허용
  - `secret_not_in_secret_event`(error) / `secret_in_public_event`(error): `secret_event_ids` 이벤트의 `secret_markers`가 그 본문에 없음 / 다른 이벤트 본문에 있음
- `review set approved`도 이 함수를 쓴다(Task 10). 그때 `secret_event_ids`는 `secret_markers`가 있는 이벤트들로 계산한다(`secret_events(s) -> list[str]` 함께 제공).

- [ ] **Step 1: 실패하는 테스트 작성** — 검사 코드마다 걸리는 경우 1개, 통과하는 경우 1개
```python
def test_clean_draft_has_no_issues()
def test_marker_not_in_body()
def test_other_fact_marker()
def test_cue_word_flagged()
def test_cue_word_allowed_when_part_of_marker()    # fact markers ["--legacy flag is deprecated"] → 허용
def test_secret_placement()
def test_discrimination_failure_reported()          # 기존 too_easy 형태 → check_discrimination 오류가 그대로 포함
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_checks.py -v`
- [ ] **Step 5: 커밋** — `git commit -m "feat: automatic draft checks"`

---

### Task 9: 생성 파이프라인과 `traces`·`generate` 명령

**Files:**
- Create: `src/harness/generate/fetch.py`, `src/harness/generate/pipeline.py`
- Modify: `src/harness/cli.py`, `README.md`(생성 절)
- Test: `tests/test_gen_fetch.py`, `tests/test_gen_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1~8 전부
- Produces:
  - `class ShardInfo(BaseModel)`: `path: str`, `size: int`
  - `list_shards(config: str, split: str, api=None) -> list[ShardInfo]`: `huggingface_hub.HfApi().list_repo_tree(repo_id="nvidia/Open-SWE-Traces", repo_type="dataset", path_in_repo="data/<split>", recursive=True)`에서 설계의 config(v1.0~1.2)에 속한 모델 폴더만 고른다(config→모델 폴더 대응은 데이터셋 README의 `configs`를 읽는다). `api` 주입 가능.
  - `fetch_shard(info: ShardInfo, dest: Path, api=None) -> Path`: `hf_hub_download` 결과를 `dest`에 둔다. 이미 있으면 다시 받지 않는다.
  - `class GenerateReport(BaseModel)`: `requested: int`, `written: list[str]`(id), `skipped_existing: list[str]`, `rejected: int`, `shortfall: dict[str, str]`(계열 → 이유)
  - `generate(families: list[str], n: int, seed: int, traces: list[Trace], client: LLMClient, cfg: EvalConfig, gen: GenConfig, out_dir: Path, run_dir: Path, repos: set[str] | None = None) -> GenerateReport`: 계열마다 `n`개를 목표로, 궤적을 시드로 섞은 순서대로 돌며 (후보 뽑기 → 인용 검사 → dedupe → `fits` → 틀 → 본문 → 검사). 통과하면 저장, 실패하면 `run_dir/rejected.jsonl`에 `Rejection`. 후보는 `run_dir/candidates.jsonl`. 계열 하나에 같은 후보를 두 번 쓰지 않는다. `LLMError`는 그 초안만 거절로 처리하되, 인증·키 오류는 전체를 멈춘다(`LLMError.fatal: bool`).
  - CLI:
    - `harness traces fetch --config v1.2 --split minisweagent --shard 0 [--yes]`: 크기(MB)와 경로를 보여 주고 `typer.confirm`. 거부하면 종료 코드 1, 아무것도 받지 않음.
    - `harness traces list`: 캐시의 조각, 저장소별 궤적 수, 묶음 수.
    - `harness generate --family <name|all> --n 4 --seed 20261020 [--repos a,b] [--out scenarios/dev] [--gen-config config/gen-v0.1.yaml] [--config config/eval-v0.1.yaml] [--dry-run]`: `--dry-run`은 첫 LLM 요청 본문만 출력하고 부르지 않는다. 끝에 `GenerateReport` 요약("요청 n개 중 m개 생성", 건너뛴 이유)을 출력한다. `--family all`은 `normal_repeat`를 `max(1, n // 2)`개로 줄인다(생성 설계 1장).
  - `gen` 의존성이 없으면 이 명령들만 Global Constraints의 메시지로 종료 코드 2.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_list_shards_filters_config_models()                # 가짜 api: v1.2 → qwen38_* 폴더만
def test_fetch_skips_existing(tmp_path)
def test_cli_fetch_declined_downloads_nothing(tmp_path)      # input "n\n" → exit 1, 파일 없음
def test_generate_writes_drafts_that_pass_checks(tmp_path)   # provider fake(기본 responder), make_parquet 궤적 → truth_change 2개 → 파일 2개, 각 check_draft 오류 없음, status draft
def test_generate_rerun_uses_cache_and_same_ids(tmp_path)    # 두 번째 실행: CachedClient.calls 증가 없음, 같은 id
def test_generate_does_not_overwrite_reviewed(tmp_path)      # 같은 id 파일이 approved → skipped_existing, 내용 그대로
def test_generate_shortfall_reported(tmp_path)               # 맞는 후보 없음 → written 0, shortfall에 이유, exit 0
def test_generate_bad_llm_output_is_rejected_not_fatal(tmp_path)
def test_generate_missing_key_exit2(tmp_path, monkeypatch)
def test_generate_dry_run_makes_no_calls(tmp_path)
def test_generate_repos_filter(tmp_path)
```
- [ ] **Step 2~4** — `uv run pytest tests/test_gen_fetch.py tests/test_gen_pipeline.py tests/test_cli.py -v`
- [ ] **Step 5: README** — "시나리오 초안 만들기" 절: `uv sync --extra gen`, 키 환경 변수, 명령 3개, 캐시 위치, CC BY 4.0 출처(Open-SWE-Traces) 표기.
- [ ] **Step 6: 커밋** — `git commit -m "feat: generation pipeline with traces and generate commands"`

---

### Task 10: 검토 CLI와 `scenario new`

**Files:**
- Create: `src/harness/review.py`
- Modify: `src/harness/cli.py`, `README.md`(검토 절), `docs/ARCHITECTURE.md`(모듈 지도에 generate·review)
- Test: `tests/test_review.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 7 `label_fingerprint`, `changed_label_fields`; Task 8 `check_draft`, `secret_events`; Task 5/6 `TEMPLATES`; 기존 `load_scenario`, `run_scenario`
- Produces:
  - `list_drafts(root: Path) -> list[DraftRow]`(`id, family, path, status, generated_at, warnings`), `render_show(s: Scenario, cfg: EvalConfig) -> rich.console.RenderableType`(머리, 정답표, 시간순 흐름, 기준선 미리보기: oracle·all_store(누출 계열은 all_store_noacl) 문항별 outcome과 누출 수)
  - `edit_draft(path: Path, editor: str, cfg, gen) -> EditResult`(`status: Literal["unchanged","saved","reverted"]`, `issues`, `changed_fields`): 원본을 임시 파일로 복사 → `subprocess.run([*shlex.split(editor), path])` → 종료 코드 ≠ 0, YAML 깨짐, 정답 지문 변경이면 원본으로 되돌림 → 아니면 `reviewer_edited=True`로 저장하고 `check_draft` 결과를 돌려줌
  - `set_status(path, status: Literal["approved","rejected"], reviewer: str, note: str | None, cfg, gen) -> list[Issue]`: 승인은 `check_draft` 오류가 없을 때만 저장(오류 목록 반환, 파일 그대로)
  - CLI: `harness review list [ROOT]`(기본 `scenarios/dev`), `review show ID [--root]`, `review edit ID [--root]`(`$EDITOR`, 없으면 `vi`), `review set ID STATUS --reviewer NAME [--note]`. id로 파일을 찾고, 없거나 여러 개면 종료 코드 1. `harness scenario new --family F --seed S [--out]`: `TEMPLATES[F]`에 빈 후보(`FactCandidate(statement="", kind="other", quote="", message_index=0)`)를 넣은 뼈대를 `provenance.author: human`, `draft`로 저장.
  - YAML 저장은 키 순서를 지키고(`sort_keys=False`), 시간대 포함 ISO 문자열로 쓴다(기존 예시 파일 형식과 같게).

- [ ] **Step 1: 실패하는 테스트 작성**
```python
def test_edit_text_saves_and_marks_edited(tmp_path) # editor = 본문을 바꾸는 python 스크립트 → "saved", reviewer_edited True
def test_edit_label_change_reverted(tmp_path)        # → "reverted", 파일 원본과 동일
def test_edit_broken_yaml_reverted(tmp_path)
def test_edit_editor_nonzero_reverted(tmp_path)
def test_set_approved_blocked_by_issues(tmp_path)    # marker 없는 본문 → 오류 반환, status 그대로 draft
def test_set_rejected_always(tmp_path)               # note 저장, reviewer 저장
def test_show_renders_labels_and_baselines()         # Console(record=True)로 렌더 → "valid_until", "oracle", "all_store" 포함
def test_cli_review_list_and_set(tmp_path)
def test_cli_review_unknown_id_exit1(tmp_path)
def test_scenario_new_writes_empty_skeleton(tmp_path)  # author human, 텍스트 빈 문자열, 정답은 틀 그대로
```
- [ ] **Step 2~4** — `uv run pytest tests/test_review.py tests/test_cli.py -v`
- [ ] **Step 5: 문서** — README "초안 검토하기" 절(목록 → 보기 → 고치기 → 승인, 고칠 수 있는 것과 없는 것), ARCHITECTURE 모듈 지도에 `generate/`, `review.py` 행 추가.
- [ ] **Step 6: 전체 확인** — `uv run pytest -q -W error`, `uvx ruff check src`, `uvx --from basedpyright basedpyright --pythonpath .venv/bin/python --level error src`, `uv run harness run --scenarios scenarios/examples --include-drafts --out <tmp>`(기존 결과 그대로)
- [ ] **Step 7: 커밋** — `git commit -m "feat: review CLI and scenario new"`

---

### Task 11: 가짜 공급자로 끝까지 돌려 보기

**Files:**
- Create: `tests/test_gen_e2e.py`(궤적 parquet은 `make_parquet`으로 테스트 안에서 만들고, 공급자는 `fake` 기본 responder)

**Interfaces:**
- Consumes: Task 9·10의 CLI

- [ ] **Step 1: 테스트 작성**
```python
def test_end_to_end_fake_provider(tmp_path)   # make_parquet → traces 캐시(T5_HARNESS_CACHE=tmp) → generate --family all --n 1 (provider fake)
                                              # → 9개 계열 + normal_repeat 1개 이상 초안 → review set approved(전부) →
                                              # harness selfcheck --scenarios <out> 통과 → harness run 리포트 생성
```
- [ ] **Step 2: 실행** — `uv run pytest tests/test_gen_e2e.py -v` (실패하면 원인 태스크에 그 경우를 재현하는 테스트를 먼저 추가하고 고친다)
- [ ] **Step 3: 커밋** — `git commit -m "test: end-to-end generation with the fake provider"`

---

## 진수 님이 직접 할 것 (구현 뒤, 10/22)

1. `uv sync --extra gen`, `ANTHROPIC_API_KEY` 설정(또는 `ant auth login`).
2. `uv run harness traces fetch --config v1.2 --split minisweagent --shard 0` (크기 확인 후 `y`).
3. 첫 묶음: `uv run harness generate --family all --n 1 --seed 20261020` → `review list`로 확인하고 거절률을 본다. 절반 넘게 거절되면 목표 15쌍.
4. 나머지: `--n 4`로 다시 실행(같은 시드면 첫 묶음은 그대로 남음) → 커밋해 팀원에게 검토를 나눈다.
