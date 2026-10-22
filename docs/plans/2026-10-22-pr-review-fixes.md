# PR 리뷰 지적 수정 구현 계획 (harness PR #1·#2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 재혁 님(PR #1·#2)과 선민 님(PR #1·#2)의 리뷰 지적 중 재현이 확인된 것을 고쳐, 점수를 믿을 수 있게 하고, 정답이 엔진에 새지 않게 하고, 첫 실제 생성 전에 생성기의 검사와 LLM 계층을 바로잡는다.

**Architecture:** 코어 수정은 `feat/harness-core`, 생성기 수정은 `feat/scenario-generation`에서 하고, 오픈소스 브랜치(`feat/oss-adapters`)는 위로 다시 쌓는다. 순서는 코어 → 생성기 rebase → 생성기 → 오픈소스 rebase → 최종 정리다. 재현 스크립트가 있는 지적은 그 스크립트를 회귀 테스트로 옮긴다.

**Tech Stack:** Python ≥3.12, uv, pydantic v2, PyYAML, typer, pytest, ruff, basedpyright. 새 런타임 의존성은 없다.

**Spec:** `docs/specs/2026-10-19-memory-eval-harness-design.md`(이하 "설계"), `docs/specs/2026-10-20-scenario-generation-design.md`(이하 "생성 설계"). 재현 결과는 이 문서 끝의 "지적 → 작업 대응표"에 요약했다. 재현 스크립트(임시 폴더, 이 세션에서만 유지): `/private/tmp/claude-501/-Users-jin-git-harness/aaf67a0a-63fd-4ca4-b7b6-1f0e54fdad31/scratchpad/repro/out-core/`(C1~C10)와 `.../out-gen/`(D1~D14). 실행: 코어 `cd <core checkout> && PYTHONPATH=.:../out-core uv run python ../out-core/<script>.py`, 생성기 `PYTHONPATH=. uv run python <script>.py`.

**검증 이력:** 2026-10-22 처음 보는 에이전트가 GitHub 리뷰 원문(PR #1·#2·#3의 리뷰, 인라인 코멘트, 일반 코멘트 전부)과 이 계획을 대조해 17가지를 지적했고 모두 이 판에 반영했다. 리뷰 원문 이후 새 코멘트는 없다(PR #3에는 리뷰가 없음).

## 결정할 것 (작업 시작 전)

아래 여섯 가지는 코드가 아니라 규칙을 정하는 일이다. 추천을 기본값으로 계획을 썼으므로, 다르게 정하면 표의 "영향 작업"만 고친다.

| ID | 지적 | 무엇을 정하나 | 추천 | 영향 작업 |
|---|---|---|---|---|
| D-1 | C2 | `result_basis`의 `about` 필터를 설계 7.2대로 없앨지, 설계를 고칠지 | **코드를 설계에 맞춘다.** 근거 사실은 `sources`의 사실 전부이고, 그중 `about`에 하나라도 있으면 "관련 있음". 예시 5개의 기준선 M1·M2·M3는 시험해 보니 바뀌지 않음 | A1 |
| D-2 | C5 | 결과 기록의 시각을 어떻게 정할지 | **`max(질문 시각 + 지연, 행동 기록 시각)`** (같은 시각이면 행동 기록이 먼저). 행동 기록 시각 기준으로 지연을 다시 재는 안은 기각: `echo_stale_owner` 예시(문항 간격 1일, 실패 지연 1일)에서 실패 기록이 다음 문항 5분 뒤로 밀려 "앞 문항의 실패가 다음 문항 전에 들어간다"는 의도가 깨짐 | A5 |
| D-3 | C6b | PROMOTE_FP를 지표로 만들지 | **관찰 지표 `promote_fp`로 추가**(판정에는 안 씀). 설계 7.3과 8.1의 불일치도 해소 | A4 |
| D-4 | C10 | 블라인드가 어디까지 약속하는가 | **보통 수준**: 엔진이 이름공간·이벤트 id로 시나리오·계열·문항 종류를 알 수 없게 하고, 후보를 가리는 범위는 등록 이름, 클래스명, **전체 점 경로(`pkg.module`)**, 엔진이 `info().name`으로 밝힌 이름(4자 이상)까지. 모듈 이름 한 토막씩 가리지는 않는다(설계 12장의 "너무 흔한 단어일 수 있다"는 이유 유지, 그 문장은 점 경로 규칙에 맞게 고침). 엔진이 정한 문자열(버전, 설정, 오류 문구, 결과 본문)은 가리지 못한다고 명시. `results.jsonl`은 블라인드가 아니라고 문서화 | A6 |
| D-5 | D8 | 문항 순서·구조에서 정답 종류가 드러나는 것을 어디까지 고칠지 | **조건부 계열만 이번에 고친다**(역할 교환, 값 풀, 근거 수 맞춤). 시간 계열·되먹임·`leak_cross_team`은 "알려진 한계"로 문서화하고 가짜 문항 추가는 후속 | G2 |
| D-6 | C10b, D1 | 이름공간 해시의 비밀 값을 무엇에서 얻을지 | **실행의 블라인드 시드에서 유도**한다. 기본은 무작위, 새 옵션 `--blind-seed N`으로 고정. 이유: 후보가 둘 이상이면 E1·E2 배정이 실행마다 달라져 `results.jsonl`(`engine_id` 열)과 리포트가 달라지는데, 지금은 시드를 고를 방법이 없다. 시드 하나로 후보 배정과 이름공간 키를 함께 정해 개념과 옵션을 하나로 한다. (생성 이벤트 id는 키 없는 순번이라 `results.jsonl`은 키와 무관하다.) 같은 시드 → `engine_ids.json`, `results.jsonl`, 이름공간이 같다. `summary.json`·`report.md`는 실행 시각과 지연 때문에 달라진다 | A6 |

기본 처리(결정 불필요, 계획에 반영):
- 미기록 id는 누출 검사에서도 무시한다(A1).
- 새 승인 무결성 필드가 없는 기존 승인 파일은 로드는 되지만 `scenario check`가 `approval_unverified` 경고를 낸다(G4).
- 승인 무결성 해시는 모델 덤프가 아니라 **파싱한 YAML 원본**에서 계산한다(스키마에 기본값 필드가 추가돼도 승인이 깨지지 않게, G4).
- 부트스트랩 구간·순위 계산이 첫 회차 기준인 것은 설계 8.3대로 유지하고 리포트에 명시한다(A4).

## Global Constraints

- 패키지 관리는 uv만 쓴다(pip 금지). 검사: `uv run pytest -q`, `uvx ruff check`, `uvx basedpyright src/harness`(오류 0). 모든 공개 함수·클래스는 Google 스타일 docstring.
- 숨은 라벨(`fact`, `secret_markers`, `about`, `kind`, `feedback`, `review`, `provenance`)과 시나리오 id·계열·문항 id·문항 종류는 엔진에 보내는 어떤 값(본문, 메타데이터, id, 이름공간)에도 드러나지 않는다.
- 실제 LLM 호출, 외부 네트워크, API 키를 쓰지 않는다. 테스트는 가짜 공급자와 로컬 가짜 서버만 쓴다. `sleep`을 호출하지 않으며, 응답 없는 서버를 쓰는 시간 초과 테스트만 0.2초 제한으로 허용한다.
- 작업 하나는 커밋 하나 이상. 각 커밋에서 전체 테스트가 통과한다. 커밋 메시지 끝에 실제로 작성한 모델을 적은 `Co-Authored-By:` 줄을 붙인다.
- push는 사용자가 명시적으로 허락한 뒤에만 한다. 이미 올라간 브랜치는 `--force-with-lease=<브랜치>:<예상 커밋>`으로만 올린다.
- 동작이 달라지면 설계 문서, `docs/ARCHITECTURE.md`, README를 같은 커밋에서 고친다. `.superpowers/`는 건드리지 않는다.
- 예시 시나리오 5개(`scenarios/examples/`)의 M1·M2 값은 바뀌지 않아야 한다(바뀌면 이유를 보고).

## Review Focus

계획의 어느 작업도 직접 다루지 않지만 사용자가 겪기 쉬운 경우다. 각각 해당 작업에 테스트를 넣는다.

1. **새 필드가 없는 기존 시나리오 파일**: 승인 해시, 지문, `secret_events`가 없는 파일이 로드 오류 없이 열리고 경고만 나와야 한다. → G3·G4.
2. **재현성**: 같은 `--blind-seed`로 두 번 돌리면 `engine_ids.json`과 `results.jsonl`이 같고, 시드가 다르면 이름공간과 후보 ID만 다르고 라벨은 같아야 한다. → A6.
3. **생성 이벤트 id 충돌**: 저자가 `e1…e9`와 `tc_o1`, `e_new` 같은 임의 id를 섞어 써도 생성 id가 겹치지 않고 실행마다 같아야 한다. → A6.
4. **엔진이 이상한 id를 돌려줌**: 모르는 id, 중복, 미래 id, 비문자열이 섞여도 죽지 않고 근거로 인정하지 않아야 한다. → A1.
5. **반복 중 일부가 오류이거나 분모가 0인 지표 평균**: `averaged()`가 0으로 나누거나 값이 지표 구간 밖으로 나가지 않아야 한다. → A4.
6. **금지어가 코드 조각을 막음**: "enabled: false" 같은 코드 조각이 금지어 때문에 거절되면 쓸데없이 재시도 비용이 든다. → G2.

---

## 작업 순서

```
결정(D-1~D-6) → 코어 A0~A6 → B1 생성기 rebase → 생성기 G1~G5 → B2 오픈소스 rebase → 최종 정리(F1~F3)
```

코어 작업은 서로 파일이 겹치므로 A0부터 순서대로 한다. 생성기 작업 중 G1(LLM 계층)과 G2(프롬프트·골격)는 비용이 드는 첫 실제 생성 전에 끝내야 한다. 프롬프트, 골격 모양, 캐시 키가 바뀌면 이미 쓴 생성 비용을 다시 내기 때문이다.

---

# 파트 A. 코어 (`feat/harness-core`, PR #1)

### Task A0: 계약 모델의 `frozen` 설정

IDE 경고 "Frozen dataclasses can not inherit non-frozen one and vice versa"(`contract.py` L19). 리뷰 지적은 아니다. 사용자 요청.

**Files:**
- Modify: `src/harness/contract.py`(`Event` L19, `Query` L42)
- Test: `tests/test_contract.py`

**Interfaces:** `class Event(BaseModel, frozen=True)`와 `class Query(BaseModel, frozen=True)`를 `class Event(BaseModel):` 안의 `model_config = ConfigDict(frozen=True)`로 바꾼다. 동작(불변, 해시 가능)은 같다. 소스 전체에서 이 두 곳뿐이다(`_Strict` 계열은 이미 `ConfigDict`).

- [ ] **Step 1: 테스트** — `test_event_and_query_are_frozen`: 필드에 대입하면 `ValidationError`.
- [ ] **Step 2: 구현 후 전체 검사, 커밋** — `fix: declare frozen contract models through ConfigDict`. IDE 경고가 사라졌는지는 사용자가 확인한다.

### Task A1: 근거 인정 범위 (기록된 이벤트만, 설계 7.2대로)

지적 C1(미래 이벤트 id를 근거로 인정), C2(`about` 필터).

**Files:**
- Modify: `src/harness/replay.py`(`run_scenario`, `_score_probe`, 생성 이벤트 등록 지점 L326), `src/harness/agent.py`(`result_basis`)
- Modify: `docs/specs/2026-10-19-memory-eval-harness-design.md`(7.2), `docs/ARCHITECTURE.md`(L75 근거 규칙), `README.md`(알려진 한계)
- Test: `tests/test_agent.py`, `tests/test_replay.py`, `tests/test_scoring.py`

**Interfaces:**
- `run_scenario` 안에서 `recorded: dict[str, LabeledEvent]`를 유지한다. `engine.record()`가 성공한 이벤트만 넣는다. 생성 이벤트도 시간표에서 꺼내 기록할 때 넣는다(`events[generated.event.id] = generated`를 만들 때 바로 넣던 것을 없앤다). id 충돌 검사(`taken`)는 별도 집합으로 유지한다. `_score_probe(probe, results, scenario, recorded, search_ms)`와 `find_leaks`도 `recorded`를 받는다(결정: 아직 기록되지 않은 id는 누출 검사에서도 무시).
- `result_basis(result, probe, scenario, events)` 규칙(D-1): `sources`가 있으면 알려진 이벤트의 `fact`를 모두 모은다(None 제외). 그중 `probe.about`에 하나도 없으면 빈 집합(관련 없음). 있으면 모은 전체를 근거로 하고, 둘 이상이면 기존처럼 `markers`가 본문에 나오는 것으로 좁힌다. `sources`가 없을 때는 지금처럼 `about` 사실의 `markers`로 정한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_unrecorded_source_id_is_not_evidence`: 문항 시각 이후 이벤트의 id만 `sources`로 돌려주는 엔진이 promote 문항에서 `FN`, block 문항에서 `BLOCKED`가 된다. (재현: `c1_future_source.py`, 현재는 promote가 TP)
  - `test_generated_event_is_evidence_only_after_it_is_recorded`: 되먹임 시나리오에서 아직 기록 안 된 생성 이벤트 id를 인용하면 근거로 인정하지 않는다.
  - `test_source_fact_outside_about_still_counts_in_basis`: 참 사실(`about` 안)과 거짓 사실(`about` 밖)을 함께 가리키면 promote는 `PROMOTE_FP`, block은 `FP`. **결과 본문에 두 사실의 마커를 모두 넣거나 모두 넣지 않는다.** 설계 7.2 2번이 마커로 좁히므로, 한쪽 마커만 있으면 밖의 사실이 좁혀져 사라지고 테스트가 PROMOTE_FP를 만들지 못한다. (재현: `c2_basis_about.py`)
  - `test_result_with_no_source_fact_in_about_is_not_relevant`: 근거 사실이 모두 `about` 밖이면 기권.
  - `test_find_leaks_ignores_unrecorded_ids`.
  - 기존 `tests/test_agent.py::test_sourceless_ignores_facts_outside_about`는 그대로 통과해야 한다.
- [ ] **Step 2: 실패 확인** — `uv run pytest -q tests/test_agent.py tests/test_replay.py tests/test_scoring.py`
- [ ] **Step 3: 구현** — 위 Interfaces대로. `ScenarioRun`에 쌓는 `generated` 목록과 `generated_ids`는 그대로 둔다.
- [ ] **Step 4: 문서** — 설계 7.2 1번에 "기록된 이벤트만"을 적고, README 알려진 한계에 "저장 없이 이미 기록된 id만 돌려주는 엔진은 하네스가 구별하지 못한다(출처 채점의 한계)"를 적는다.
- [ ] **Step 5: 전체 검사 후 커밋** — `fix: only recorded events count as evidence; basis follows design 7.2`

### Task A2: 검증기 묶음 (비밀 문자열 배타성, 짝 규칙, 판정 문자열 섞임, 팀)

지적 C4(검증기 쪽), C7a·b·c.

**Files:**
- Modify: `src/harness/scenario/validate.py`(짝 규칙 L165-170, 새 검사)
- Modify: `docs/specs/2026-10-19-memory-eval-harness-design.md`(5.3 짝 규칙, 5.4 검증 코드 목록)
- Test: `tests/test_validate.py`

**Interfaces:**
- 새 오류 코드(기존 `Issue` 형식 그대로): `secret_marker_not_exclusive`, `marker_bleed`, `unknown_team`. 짝 규칙은 기존 코드 `pairing_missing`을 그대로 쓴다.
- 규칙:
  - 짝 규칙(설계 5.3, `validate.py` L165-170): 지금은 `normal_repeat`를 뺀 모든 계열에서 "`block` 또는 `leak` 중 하나"와 "`promote`"를 요구해서, `block` 없이 `leak`과 `promote`만 있는 `truth_change`가 통과한다. 계열별로 좁힌다: 누출 계열(`leak_personal`, `leak_cross_team`)은 `leak`과 `promote`, 그 밖의 계열(`normal_repeat` 제외)은 `block`과 `promote`가 모두 있어야 한다. `normal_repeat`는 지금처럼 제외한다.
  - `secret_marker_not_exclusive`: 각 문항에 대해, 묻는 사람이 볼 수 없고 문항 시각 전에 기록된 이벤트의 `secret_markers` 문자열이 그 문항의 `query` 또는 묻는 사람이 볼 수 있는 이벤트 본문에 (대소문자 무시) 나오면 오류. 공백뿐인 마커는 건너뜀(`empty_marker`가 이미 막음).
  - `marker_bleed`: 이벤트 본문에 같은 문항의 `about`에 속한 다른 사실(그 이벤트의 `fact`가 아닌 사실)의 `markers`가 들어 있으면 오류. `fact`가 없는 잡음 이벤트도 대상이다. 빈 마커는 건너뛴다.
  - `unknown_team`: 이벤트의 `team`이 `world.teams`에 없으면 오류.
- `find_leaks`(`scoring.py`)는 바꾸지 않는다. 설계 5.5의 문장 그대로 두고 저자 단계에서 막는다.
- **생성기와의 겹침(B1에서 정리):** 생성기에는 비슷한 검사(`secret_in_public_event`, `other_fact_marker`)가 이미 있고, `check_draft`는 검증기 오류가 있으면 거기서 멈춘다. 기준: 검증기는 "문항·가시성 기준"의 검사(`secret_marker_not_exclusive`, `marker_bleed`)를, 생성기는 "저자 단계에서 더 엄격한" 검사(모든 비밀 아닌 이벤트·모든 다른 사실 기준)를 맡는다. 두 층이 모두 걸리는 초안은 검증기 코드만 보인다.

- [ ] **Step 1: 실패하는 테스트 작성** — 각 코드마다 하나씩: `test_secret_marker_in_visible_event_is_an_error`, `test_secret_marker_in_probe_query_is_an_error`(재현: `c4_secret_marker_fp.py`), `test_truth_change_without_block_is_a_pairing_error`, `test_leak_family_without_leak_probe_is_a_pairing_error`, `test_leak_family_needs_no_block`, `test_normal_repeat_stays_exempt_from_pairing`, `test_marker_of_another_about_fact_in_event_body_is_an_error`, `test_marker_bleed_applies_to_noise_events`, `test_empty_marker_is_skipped_by_bleed_check`(기존 `test_empty_marker_in_fact[]`가 깨지지 않게), `test_event_team_not_in_world_is_an_error`.
- [ ] **Step 2: 구현 후 `tests/test_examples.py`로 예시 5개가 깨끗한지 확인**
- [ ] **Step 3: 전체 검사 후 커밋** — `feat: validator checks secret-marker exclusivity, pairing, marker bleed and teams`

### Task A3: 입력 경계 검증

지적 C7d, C8, C9a·b·c, C10d, D11(id).

**Files:**
- Modify: `src/harness/scenario/load.py`(중복 키), `src/harness/scenario/model.py`(id 패턴), `src/harness/config.py`, `src/harness/cli.py`(`_run_extra`, `--engine`), `src/harness/report.py`(`write_report`), `src/harness/replay.py`(`_as_result`)
- Test: `tests/test_scenario_load.py`, `tests/test_config.py`, `tests/test_cli.py`, `tests/test_report.py`, `tests/test_replay.py`, `tests/test_validate.py`

**Interfaces:**
- YAML 중복 키: `yaml.SafeLoader`를 상속해 `construct_mapping`에서 키가 반복되면 오류를 내는 로더. `ScenarioLoadError`로 올린다(파일 경로 포함, 봉인 모드에서는 지금처럼 경로를 숨김).
- 시나리오 id: `Scenario.id`에 패턴 `^[A-Za-z0-9][A-Za-z0-9._-]*$`(길이 1~120).
- 설정: `EvalConfig.k`, `llm_repeats`, `Stats.bootstrap_iters`는 `ge=1`. `Probe.k`는 지정했다면 `ge=1`(`k is None` 검사로 바꾼다, 지금은 `k or cfg.k`). `FeedbackDelays`의 세 지연은 0 이상(`field_validator`).
- `EngineInfo` 검증: `_run_extra`에서 `engine.info()`를 받은 직후 `isinstance(info, EngineInfo)`와 `json.dumps(info.model_dump(mode="json"))`를 확인한다. 둘 중 하나라도 실패하면 첫 빌드 실패와 같은 방식(종료 코드 2)으로 끝낸다. **dict를 모델로 바꿔 주지 않는다**: `replay.run_scenario`가 `engine.info().virtual_clock`을 직접 읽으므로 보고서 쪽만 고쳐서는 모든 시나리오가 오류가 된다.
- `write_report`는 `engine_ids.json`을 **가장 먼저** 쓴다.
- `_as_result`는 `Result`를 `model_copy(deep=True)`로 복사한다.
- `--engine all_store_noacl` 등 `BASELINES` 밖의 내부 이름은 후보로 받지 않는다(종료 코드 2). `selfcheck.py`가 내부에서 쓰는 것은 그대로.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_duplicate_yaml_key_is_a_load_error`(재현: `c7_validator_gaps.py`), `test_config_rejects_k_below_one`, `..._llm_repeats_zero`, `..._bootstrap_iters_zero`, `..._negative_feedback_delay`(재현: `c8_config_ranges.py`), `test_info_returning_dict_exits_2`, `test_non_json_config_exits_2_before_evaluation`, `test_engine_ids_written_even_if_report_fails`, `test_stored_result_does_not_change_when_engine_mutates_it`(재현: `c9b_result_mutation.py`), `test_all_store_noacl_is_not_a_candidate`, `test_scenario_id_with_path_characters_is_rejected`.
- [ ] **Step 2: 구현** — 위 Interfaces대로.
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: validate config ranges, duplicate keys, engine info, scenario ids at the input boundary`

### Task A4: 집계와 통계 (부트스트랩 표본, 반복 집계, PROMOTE_FP)

지적 C3, C6a, C6b. 결정 D-3.

**Files:**
- Modify: `src/harness/selection.py`(`_canonical_ci`, `averaged`), `src/harness/metrics.py`(`Metrics.promote_fp`), `src/harness/report.py`(보조·관찰 표 열, 반복 집계 표기)
- Modify: `docs/specs/2026-10-19-memory-eval-harness-design.md`(8.1 표에 `promote_fp` 행, 7.3과 일치, 8.3 반복 집계 설명)
- Test: `tests/test_selection.py`(`test_averaged_*`가 "첫 회차 유지"를 고정하고 있어 고친다, 헬퍼 `M()` L26), `tests/test_report.py`(~L28 `Metrics` 생성), `tests/test_stats.py`, `tests/test_metrics.py`

**Interfaces:**
- `_canonical_ci`: 공통 시나리오 중 두 엔진 모두 승격 문항 수(`n`)가 0보다 큰 것만 부트스트랩에 쓴다.
- `Metrics.promote_fp: Rate` = `rate(*_count(read_only, "promote", "PROMOTE_FP"))`. 판정에는 쓰지 않는다. 리포트의 보조·관찰 표에 열 추가.
- `averaged(repeats)` 규칙. 한 줄로: **M1·M2의 구간과 순위용 부트스트랩만 첫 회차 기준(설계 8.3), 나머지는 모두 회차를 합친다.**
  - M1·M2: 값은 평균, 구간은 첫 회차(지금과 같음). M3는 최악 회차, 오류는 합집합.
  - 비율 지표(`promote_fn`, `promote_fp`, `shallow_fix`, `reach`, `contamination`, `sourceless`)와 `by_family`·`feedback_by_family`·`by_author`의 각 칸: 회차별 `k`·`n`을 합친 합산 비율(`rate(Σk, Σn)`, 구간은 합산 기준의 참고용). `worst_family`는 합산한 `by_family`에서 다시 구한다.
  - `retraction`·`reflection`: 회차 목록을 이어 붙임. `context_chars_mean`과 지연 백분위: 회차 평균. 분모 합이 0이면 값 `None`.
  - 리포트 머리말에 "M1·M2 구간과 순위 부트스트랩은 첫 회차 기준, 나머지 표는 회차 합산"을 명시한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_canonical_ci_ignores_scenarios_without_promote_probes`(기대 (1.0, 1.0), 동률 아님, 재현: `c3c_rank_path.py`), `test_averaged_tiebreak_metrics_use_all_repeats`(재현: `c6_repeat_aggregation.py`: 도달률 평균이 같으면 동률 처리가 다음 기준으로 넘어감), `test_averaged_family_tables_pool_repeats`, `test_averaged_repeat_mistake_reduction_uses_mean_fn`, `test_averaged_handles_a_repeat_with_zero_denominator`, `test_promote_fp_counted_separately_from_fn`, `test_report_shows_promote_fp_column`.
- [ ] **Step 2: 구현 후 기존 `test_averaged_*` 수정** — "첫 회차 유지"를 고정하던 단언을 새 규칙으로 바꾼다. 이유를 테스트 이름에 남긴다.
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: bootstrap skips zero-promote scenarios; repeats aggregate every metric; add promote_fp`

### Task A5: 되먹임 기록 순서

지적 C5. 결정 D-2.

**Files:**
- Modify: `src/harness/replay.py`(`feedback_events`, `_generated`), `docs/specs/2026-10-19-memory-eval-harness-design.md`(6.2), `src/harness/generate/` 없음(코어 작업)
- Test: `tests/test_feedback.py`(`test_on_true_outcome_immediate_and_template_delay` L85)

**Interfaces:**
- 결과 기록 시각 = `max(질문 시각 + 지연, 행동 기록 시각)`. 행동 기록이 없으면 `질문 시각 + 지연`. 시각이 같으면 행동 기록이 먼저 재생된다. `Timeline`이 같은 시각에서 삽입 순서(`seq`)를 따르고 `feedback_events`가 행동 기록을 먼저 만들므로 이미 보장되며, 테스트로 고정한다.
- 기본 지연은 그대로(행동 5분, 실패 결과 1일, 성공 결과 0분). 지연이 행동 시각보다 이른 경우(성공 결과)만 달라지고, 실패 결과(1일)는 영향이 없다. `echo_stale_owner` 예시의 문항 간 흐름이 그대로다.
- 설계 6.2에 "결과 기록은 행동 기록보다 앞서지 않는다"를 적는다. `generate/feedback.py` 모듈 docstring의 "after the probe" 표현은 G 작업에서 확인한다(생성기 브랜치).

- [ ] **Step 1: 실패하는 테스트 작성** — `test_outcome_is_never_recorded_before_its_action`(성공 결과 기본값에서 `record` 호출 시각 순서, 재현: `c5_feedback_order.py`), `test_same_time_outcome_follows_action_in_insertion_order`, `test_failure_outcome_time_is_unchanged`, `test_outcome_counts_from_probe_time_when_no_action_record`. 기존 `test_on_true_outcome_immediate_and_template_delay`는 결과 시각이 행동 시각(12:05)이 되도록 고친다.
- [ ] **Step 2: 구현, 설계 6.2 문장 수정**
- [ ] **Step 3: 예시 5개 기준선 M1·M2·M3가 같은지 확인 후 커밋** — `fix: feedback outcome records never precede their action record`

### Task A6: 블라인딩 (이름공간, 이벤트 id, 후보 가리기)

지적 C10a·b·c·e, D1, D13. 결정 D-4, D-6.

**Files:**
- Modify: `src/harness/replay.py`(`run_scenario`, `generated_event_id`와 docstring), `src/harness/cli.py`(`run`, `selfcheck`, `_run_extra`, `_selfcheck`: 블라인드 시드 전달, `run --blind-seed`), `src/harness/report.py`(`write_report`가 받은 시드 사용, `_scrubber`·`scrub` 확장), `src/harness/selfcheck.py`(`_run_all`)
- Modify: `docs/specs/2026-10-19-memory-eval-harness-design.md`(6.1 이름공간, 6.2 생성 id, **L158** 되먹임 recall id 설명, 12장 블라인드 범위), `docs/ARCHITECTURE.md`(L87 id 규칙), `README.md`
- Test: `tests/test_replay.py`(reset 호출 단언 L101-104와 신규), `tests/test_feedback.py`(L206, L211 id 해시), `tests/dummy_engine.py`(`FailOnTc`만: 이름공간 `":tc"`에 기댐. `FailOnSecondRepeat`의 `"-r2:"`는 run_id 쪽이라 그대로 유지), `tests/test_report.py`, `tests/test_cli.py`

**Interfaces:**
- `blind_key(seed: int) -> bytes`: `sha256(f"t5-blind:{seed}")`. `run_scenario(..., blind_key: bytes = DEFAULT_BLIND_KEY)`. **기본값을 둔다**: 호출처가 많다(`test_replay.py`, `test_feedback.py` 약 29곳, `selfcheck._run_all`, `check_discrimination`, `cli._run_extra`·`_selfcheck`, 생성기의 `review.py` L454). 기본 키는 고정 상수로 테스트와 내부 검사용이고, `harness run`과 후보 평가는 항상 실행 시드에서 유도한 키를 넘긴다. `harness selfcheck` 명령은 기준선만 돌리므로 기본 키를 써도 된다.
- 이름공간: `f"{run_id}:{hmac(blind_key, 'ns:' + scenario.id)의 앞 16자리 16진}"`. 시나리오 id가 들어가지 않는다.
- 생성 이벤트 id: `e{n}` 형식. `n`은 (그 시나리오의 기존 이벤트 수 + 이미 만든 생성 이벤트 수 + 1)에서 시작하고, 이미 쓰는 id와 겹치면 다음 번호로 올린다. 저자가 `tc_o1`, `e_new` 같은 임의 id를 써도 겹치지 않는다. 문항 id·시나리오 id·slot을 id 계산에 쓰지 않는다. 생성 id와 문항의 대응은 하네스 쪽 `generated_ids`에만 둔다. 같은 엔진 응답이면 같은 id가 나오지만, 앞 문항에서 엔진이 행동했는지(기권했는지)에 따라 생성 기록 수가 달라지므로 엔진마다 같은 문항의 생성 id가 다를 수 있다(채점에는 영향 없음, 설계에 명시). `generated_event_id` docstring의 "엔진이 생성 이벤트를 구별할 수 없다" 문장은 id 형식이 같아졌다는 의미로 고친다.
- `harness run --blind-seed N`: 지정하면 이름공간 키와 후보 ID(E1…) 섞기가 모두 그 시드에서 나온다. 지정하지 않으면 무작위이고 `engine_ids.json`에 기록한다(기존과 같음). `write_report`에는 이미 `blind_seed` 인자가 있으므로(report.py L522) CLI에서 만든 값을 넘기기만 한다.
- `_scrubber`(D-4): 등록 이름, 클래스명에 더해 전체 점 경로(`pkg.module`)와 엔진이 `info().name`으로 밝힌 이름(4자 이상)을 가린다. 가리는 대상 목록에 `engine.info()` 결과가 필요하므로 `scrub`을 만들 때 엔진 정보를 함께 받는다. 설계 12장의 "모듈 이름은 바꾸지 않는다" 문장은 "점 경로 전체만 가린다"로 고친다.
- 설계 12장에 한계 명시: 엔진이 정한 문자열(버전, 설정, 오류 문구, 결과 본문)과 `results.jsonl`은 블라인드가 아니다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_namespace_does_not_contain_scenario_id`, `test_namespace_cannot_be_recovered_by_hashing_scenario_ids`(후보 id 공간을 해시해 비교, 일치 없음, 재현: `d1_namespace.py`), `test_generated_ids_look_like_scenario_ids_and_never_collide`(재현: `d13_event_ids.py`, 정규식 분류기가 구별 못 함. 저자 id가 `tc_o1`, `e_new`인 시나리오 포함), `test_generated_ids_are_independent_of_probe_ids`, `test_generated_event_refs_and_scoring_survive_the_id_change`(결과 기록의 `refs`가 따른 결과의 근거 id를 가리키고, `generated_ids` 대응과 채점이 보존됨), `test_same_blind_seed_gives_identical_engine_ids_and_results_jsonl`, `test_different_blind_seed_changes_namespaces_not_outcomes`, `test_scrubber_hides_dotted_module_path_and_declared_name`(재현: `c10_blinding.py`), `test_engine_ids_json_records_seed`.
- [ ] **Step 2: 구현** — `FailOnTc`는 이름공간 형식에 기대지 않고 호출 횟수나 이벤트 id로 실패하도록 바꾼다.
- [ ] **Step 3: 문서 수정, 전체 검사 후 커밋** — `fix: opaque keyed namespaces and sequential generated ids; scrub more engine identifiers`

---

# 파트 B. 다시 쌓기

### Task B1: 생성기 브랜치를 새 코어 위로 rebase하고 겹침 정리

**Files:** `feat/scenario-generation`의 충돌 해결, 관련 테스트와 문서.

- [ ] **Step 1: rebase** — `git fetch`, `git rebase feat/harness-core`. 이름공간 해시 커밋(`ff98573`, "R9")이 건드린 네 파일이 A6과 겹친다: `src/harness/replay.py`, `tests/dummy_engine.py`(`FailOnTc`가 R9에서 이미 해시 기반으로 바뀜, L73), `tests/test_replay.py`(reset 단언 + R9의 신규 테스트), 설계 6.1(L305). **코어의 새 불투명 이름공간을 택하고 R9 부분은 버린다.** 생성기에만 있는 호출처 `src/harness/review.py`(L454의 `run_scenario` 호출)도 확인한다. R9를 버리면 PR #2의 `replay.py:285` 인라인 스레드(이름공간 복원)는 코어 쪽 수정으로 해결되는 것이므로, 답글에서 이 대응을 알린다(F3).
- [ ] **Step 2: 겹침 정리** — A2의 검증기 코드와 생성기 검사가 겹친다. `check_draft`는 검증기 오류가 있으면 거기서 멈추므로 다음 테스트의 시나리오가 검증기 코드를 먼저 낸다: `tests/test_gen_checks.py::test_secret_in_public_event`(L161), `::test_other_fact_marker`(L63), `::test_other_fact_marker_in_noise_event`. 각 테스트가 자기 층의 코드만 내도록 시나리오를 고친다(예: 비밀을 문항 asker가 볼 수 없는 위치에 둔다). 두 층이 모두 걸리면 검증기 코드가 먼저 나온다는 테스트 `test_core_validator_code_wins_when_both_layers_fire`를 더한다.
- [ ] **Step 3: 확인** — 전체 테스트, ruff, basedpyright 통과. 생성기 e2e(`tests/test_gen_e2e.py`)와 예시 시나리오가 통과하는지 확인. rebase만 하므로 push는 하지 않는다.

---

# 파트 C. 생성기 (`feat/scenario-generation`, PR #2)

### Task G1: LLM 계층

지적 D10a·b·d, D12.

**Files:**
- Modify: `src/harness/generate/config.py`(`GenConfig`), `src/harness/generate/llm.py`(`CachedClient._key`, `AnthropicClient`, `OpenAIClient`, `make_client` L529·L533), `config/gen-v0.1.yaml`, `docs/specs/2026-10-20-scenario-generation-design.md`(8장), `README.md`
- Test: `tests/test_gen_llm.py`(`test_anthropic_request_shape`, `test_openai_request_shape`, `test_anthropic_missing_credentials_is_fatal`, `test_openai_length_is_truncation_without_retry`; `CachedClient` 호출 약 10곳), `tests/test_gen_config.py`

**Interfaces:**
- `GenConfig` 새 필드: `timeout_s: float = 300`(`gt=0`), `max_retries: int = 2`(`ge=0`), `token_param: Literal["max_completion_tokens", "max_tokens", "none"] = "max_completion_tokens"`. 기본 300초의 근거: 출력 한도 16,000토큰의 정상 응답이 스트리밍 없이 도는 최대 시간(약 5분). 최악은 요청당 `timeout_s × (max_retries + 1)` = 15분이고, 연속 3번 실패하면 멈추는 기존 규칙이 그대로 있다.
- 캐시 키: `CachedClient`가 생성 설정(`effort`, `temperature`, `max_output_tokens`, `base_url`, `token_param`)을 **선택 인자**(`settings: Mapping[str, object] | None = None`)로 받아 키에 넣는다. 인자를 안 주는 기존 호출은 그대로 동작한다. `make_client`는 항상 넘긴다.
- 두 SDK 생성자에 `timeout=cfg.timeout_s`, `max_retries=cfg.max_retries`를 넘긴다.
- `OpenAIClient._send`는 `token_param`이 `"none"`이 아니면 그 이름으로 `max_output_tokens`를 보낸다. 호환 서버마다 이름이 다르므로 하나로 강제하지 않는다.
- Claude 공급자도 `api_key_env`가 설정돼 있으면 그 환경 변수를 읽어 `api_key=`로 넘긴다. 변수가 비었으면 치명 오류(`LLMError`).

- [ ] **Step 1: 실패하는 테스트 작성** — `test_cache_key_differs_when_any_generation_setting_differs`(설정 다섯 개를 하나씩 바꿔 확인, 재현: `d10_d12_llm.py`), `test_cached_client_without_settings_still_works`, `test_sdk_clients_get_timeout_and_retries`, `test_openai_sends_configured_token_param`(세 값 모두), `test_anthropic_reads_api_key_env`, `test_anthropic_missing_api_key_env_is_fatal`.
- [ ] **Step 2: 구현, 기존 요청 모양 테스트의 기대 인자 갱신**(`test_openai_request_shape`는 호출 인자를 정확히 비교한다)
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: LLM cache key covers generation settings; set SDK timeouts; send output token limit`

### Task G2: 프롬프트와 골격 (첫 실제 생성 전)

지적 D14, D5, D8, D7 및 선민 님의 질문 표현 요청. 결정 D-5.

**Files:**
- Modify: `src/harness/generate/prompts/bodies.md`(규칙 9·10), `config/gen-v0.1.yaml`(`prompt_version: gen-v0.3`, `banned_cue_words`, `min_quote_chars`), `src/harness/generate/checks.py`(`_cue_words`, `_evidence_issues`), `src/harness/generate/candidates.py`(`check_quotes`), `src/harness/generate/bodies.py`(`write_bodies`의 검사), `src/harness/generate/slots.py`(`apply_slots`: 검사 콜백이 `origin`을 검증하는 곳), `src/harness/generate/families/read_only.py`(`conditional`, `_other_value`), `docs/specs/2026-10-20-scenario-generation-design.md`(5.4~5.6), `README.md`(검토 목록)
- Test: `tests/test_gen_bodies.py`, `tests/test_gen_checks.py`, `tests/test_gen_candidates.py`, `tests/test_gen_families.py`, `tests/test_gen_slots.py`

**Interfaces:**
- 프롬프트(D14): 규칙 9에서 문항 종류의 채점 의미를 빼고, 규칙 10에 "짝 문항은 같은 형식과 어조를 쓰고, 질문 표현만으로 정답 종류를 추측할 수 없어야 하며, 어떤 사실도 전제하거나 의심하지 않는다"를 넣는다. README "초안 검토하기"의 `show`에서 볼 것에도 같은 항목("질문 표현만으로 정답 종류를 추측할 수 없는가")을 넣는다. 계열마다 짝 문항의 문구를 실제로 재사용하게 만드는 템플릿 변경은 이번에 하지 않는다(선민 님도 일괄 강제는 필요 없다고 함, 후속).
- 금지어(D5): 목록에 `false`, `no longer true`, `legacy`, `obsolete`, `superseded`, `stale`, `incorrect`, `wrong`, `retired`, `replaced by`, `not valid anymore`를 더한다. `_cue_words`는 그 사실 자신의 `markers`를 **본문에서 지운 뒤** 금지어를 찾는다(마커에 금지어가 있어도 본문 전체에서 허용되던 것을 없앰). 백틱(`` ` ``) 안의 코드 조각은 검사에서 제외한다("enabled: false" 같은 코드가 막히지 않게). 문항 `query` 검사는 이미 있으므로 새 목록만 적용된다.
- `quoted`(D7): 거짓 사실(`valid=False` 또는 `fabricated_facts`에 있는 사실)이나 사실이 없는 이벤트에 `origin: quoted`를 붙이면 `ValueError`(`apply_slots`의 검사 콜백)와 `_evidence_issues` 오류. 인용은 공백을 뺀 8자 이상이면서 두 단어 이상이어야 `check_quotes`를 통과한다(`min_quote_chars: 8`, "uv run pytest"는 통과, "e"는 거절).
- `conditional`(D8, D-5 추천 A): ① 두 맥락 중 어느 쪽에서 block을 묻는지 시드로 정한다(역할 교환). ② 두 번째 맥락 값은 원래 값에 접미사를 붙이지 않고, 같은 종류의 이름 풀(서비스 이름 목록, 환경 이름 목록)에서 원래 값과 다른 것을 시드로 고른다. ③ 두 맥락이 같은 수·같은 종류의 근거 이벤트를 가진다.
- 시간 계열·되먹임·`leak_cross_team`의 문항 순서 단서는 생성 설계 5.4에 "알려진 한계"로 적고 이번에는 고치지 않는다. 첫 묶음 확인 때 금지어로 인한 거절 비율을 따로 본다(README 첫 묶음 절차에 한 줄).

- [ ] **Step 1: 실패하는 테스트 작성** — `test_prompt_rules_forbid_hinting_question_wording`, `test_cue_words_include_review_list`(재현: `d5_cuewords.py`의 통과 단어 목록), `test_cue_word_in_own_marker_is_masked_not_exempt`, `test_cue_word_inside_code_span_is_allowed`, `test_quoted_origin_on_fabricated_fact_is_rejected`, `test_one_character_quote_is_rejected`, `test_short_command_quote_is_accepted`(재현: `d7_quoted.py`), `test_conditional_block_context_is_not_predictable_from_the_suffix`(시드 300개에서 접미사 유무와 문항 종류가 독립, 재현: `d8_conditional_tell.py`의 규칙 두 가지가 정확도 약 50%), `test_conditional_contexts_have_equal_support`, `test_conditional_roles_swap_across_seeds`. 기존 `test_cue_word_allowed_when_part_of_marker`는 새 규칙에 맞게 고친다.
- [ ] **Step 2: 구현. `prompt_version`을 `gen-v0.3`으로 올린다**
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: stronger cue-word and quote checks; remove conditional-family tells; prompt v0.3`

### Task G3: 자동 검사 묶음

지적 D2, D6, D3.

**Files:**
- Modify: `src/harness/generate/checks.py`(`_body_issues`, `_secret_issues`, 새 공용 도우미), `src/harness/generate/families/base.py`(`build`), `src/harness/scenario/model.py`(`Provenance.secret_events`), `src/harness/scenario/labels.py`(편집 가능 필드 목록에서 `secret_markers` 제외), `src/harness/review.py`(`secret_events` 사용처 **L234(편집 경로)**, L283, L495), `docs/specs/2026-10-20-scenario-generation-design.md`(5.4, 5.6, 7장 편집 규칙), `README.md`(검토에서 고칠 수 있는 것 목록)
- Test: `tests/test_gen_checks.py`(비밀 관련 세 테스트), `tests/test_review.py`, `tests/test_labels.py`, `tests/test_gen_pipeline.py::test_generate_passes_secret_events_to_the_checks`(L311), `tests/test_gen_families.py::test_leak_skeleton_marks_secret_events`(L498)

**Interfaces:**
- 텍스트 검사 도우미 하나(`_text_issues(text, own_facts, other_facts, scenario)`)를 이벤트 본문, 질문, 되먹임 템플릿 본문(행동·기권·결과)에 모두 적용한다. 되먹임 템플릿: 행동 본문은 따른 사실의 마커를 포함하고 다른 사실의 마커와 금지어를 포함하지 않는다. 실패 결과 본문은 `fact`가 None이면 마커 포함 검사만 건너뛴다.
- 비밀 문자열 검사(D6): 질문 `query`는 A2의 검증기가 이미 본다. 여기서는 사실 본문과 되먹임 본문을 추가로 훑는다. 새 코드 `secret_in_fact_text`, `secret_in_feedback`.
- `Provenance.secret_events: list[str]`: `SkeletonBuilder.build(secret_event_ids)`가 이미 갖고 있는 `Skeleton.secret_event_ids`를 생성 시점에 기록한다. `_secret_issues`와 `review.py`(L234, L283, L495)는 파일에서 다시 계산하지 않고 이 목록을 쓴다. 목록의 이벤트가 `secret_markers`가 비어 있으면 오류 `secret_missing`. 누출 계열인데 목록이 비면 오류. 이 필드가 없는 파일(직접 쓴 초안 등)은 지금처럼 파일에서 유도하되 `secret_events_not_recorded` 경고.
- **`secret_markers`를 검토에서 편집할 수 없는 필드로 바꾼다**(재혁 님의 답글 요청). `review edit`에서 고치면 지금의 라벨 편집처럼 되돌리고 편집본을 캐시에 남긴다. 비밀 문자열이 본문에 안 맞으면 고치지 말고 거절하고 다시 생성한다. `secret_events`는 `Provenance` 안에 있어 지문에 포함된다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_feedback_action_body_with_other_fact_marker_is_blocking`, `test_feedback_abstain_body_without_its_marker_is_blocking`, `test_feedback_cue_word_is_blocking`(재현: `d2_feedback_checks.py`), `test_bare_failure_outcome_without_fact_is_allowed`, `test_secret_in_fact_text_is_blocking`, `test_secret_in_feedback_text_is_blocking`, `test_review_edit_reverts_secret_marker_changes`(재현: `d3_secret_markers.py`, 비운 뒤 `approved`가 종료 코드 1), `test_review_edit_cannot_swap_secret_markers_for_junk`, `test_secret_event_without_markers_is_an_error`, `test_old_file_without_secret_events_still_loads_with_warning`.
- [ ] **Step 2: 구현 후 영향받는 기존 테스트 수정, README의 "고칠 수 있는 것" 목록에서 `secret_markers`를 "고칠 수 없는 것"으로 옮김**
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: check feedback templates; record secret events at generation; secret markers are not editable`

### Task G4: 승인 무결성

지적 D4a·b·c, D11(검토 편집본 경로).

**Files:**
- Modify: `src/harness/scenario/model.py`(`Provenance.label_fingerprint`, `Provenance.generated_hash`, `Review.approved_hash`), `src/harness/scenario/labels.py`(새 `content_hash`), `src/harness/review.py`(`set_status`, `_keep_edit`), `src/harness/generate/pipeline.py`(`_is_reviewed` ~L306-312, 저장 시 필드 기록), `src/harness/scenario/load.py`(`load_set` 승인 검증, `ScenarioSet.warnings`), `src/harness/cli.py`(`scenario check`/`run`/`selfcheck` 경고 출력), 문서
- Test: `tests/test_labels.py`, `tests/test_review.py`, `tests/test_scenario_load.py`, `tests/test_gen_pipeline.py`(`test_generate_overwrites_an_older_draft` L169는 계속 통과해야 함)

**Interfaces:**
- `content_hash(raw: Mapping) -> str`: **파싱한 YAML 원본**의 정규화 JSON(키 정렬, `ensure_ascii=False`) 해시에서 `review` 블록 전체와 `provenance.generated_hash`, `provenance.label_fingerprint`를 뺀 값. 결과는 `"v1:<hex>"`로 저장한다(해시 방식 버전을 앞에 붙여, 방식이 바뀌면 모르는 버전은 검증 불가로 처리). 모델 덤프를 쓰지 않으므로 나중에 기본값이 있는 스키마 필드가 추가돼도 기존 승인이 깨지지 않는다.
- `label_fingerprint`(기존 `scenario.labels.label_fingerprint`): 생성(`generate`)과 `scenario new`가 파일에 `provenance.label_fingerprint`로 기록한다. 이 값 자신은 계산에서 제외된다.
- `review set <id> approved`: 지금 파일의 라벨 지문이 저장된 값과 다르면 거절한다("라벨이 검토 도구 밖에서 바뀌었다", 종료 코드 1). 통과하면 `review.approved_hash`에 `content_hash`를 기록한다.
- 승인 파일 검증: `load_set`, `harness run`, `scenario check`가 `approved` 시나리오의 현재 `content_hash`가 `approved_hash`와 다르면 오류 `approved_content_changed`(승인 후 고쳤으니 다시 승인). `approved_hash`가 없거나 모르는 버전이면 오류가 아니라 경고 `approval_unverified`. 경고를 전달하는 길로 `ScenarioSet`에 `warnings: list[str]`를 추가하고 CLI가 출력한다(봉인 모드에서는 개수만).
- 재생성 보호: 생성 시 `provenance.generated_hash`(생성 직후 `content_hash`)를 기록하고, 같은 id를 다시 만들 때 현재 내용 해시가 다르면 편집된 파일로 보고 덮어쓰지 않는다(`_is_reviewed`). `generated_hash`가 없는 기존 초안은 지금 규칙(상태, `reviewer_edited`, 작성자)을 그대로 쓴다.
- `_keep_edit`: 파일 이름에 쓰는 id를 `re.sub(r"[^A-Za-z0-9._-]", "_", id)`로 바꾸고, 최종 경로가 캐시 폴더 안인지 확인한다. (코어 A3의 id 패턴이 1차 방어)
- 한계 문서화: 파일 안의 해시는 실수로 고친 것을 잡는 장치이고 일부러 해시까지 고치는 것은 막지 못한다. 근거는 git 이력이다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_direct_valid_until_edit_blocks_approval`(재현: `d4_fingerprint.py` a), `test_body_edit_after_approval_is_an_error_at_load`, `test_adding_a_defaulted_schema_field_does_not_invalidate_approvals`(원본 기반 해시), `test_regenerate_does_not_overwrite_a_hand_edited_draft`(같은 시드 재실행), `test_untouched_draft_is_still_overwritten`, `test_draft_without_generated_hash_follows_the_old_rule`, `test_old_approved_file_without_hash_loads_with_warning`, `test_scenario_set_carries_approval_warnings`, `test_keep_edit_stays_inside_cache_for_odd_ids`(재현: `d11_path_traversal.py`).
- [ ] **Step 2: 구현. 새 필드 세 개를 `labels._label_view`에서 제외했는지 테스트로 고정**
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: approvals are bound to label fingerprint and content hash; regeneration keeps edited drafts`

### Task G5: 나머지와 문서

지적 D10c, D9, 선민 님의 재현성 문서 요청.

**Files:**
- Modify: `src/harness/generate/fetch.py`(`list_shards`, `fetch_shard`), `src/harness/cli.py`(`traces fetch --timeout`), `README.md`(검토 목록과 재현성), `docs/specs/2026-10-20-scenario-generation-design.md`(5.6 뒤 주의, **새 "재현성" 절**: 지금은 이 문서에 그런 절이 없다), `check_draft`·`set_status` docstring
- Test: `tests/test_gen_fetch.py`

- [ ] **Step 1:** `traces fetch --timeout 초`(기본 60). 로컬 가짜 서버가 응답하지 않으면 그 시간 안에 오류로 끝나는 테스트 `test_fetch_gives_up_after_timeout`(재현: `d10c_fetch_timeout.py`, 제한 0.2초로 줄여 실제 대기를 짧게). 구현 방법(deadline 래퍼 등)은 구현자가 정한다.
- [ ] **Step 2: 문서** — 생성 설계 5.6에 "자동 검사는 필요조건일 뿐 본문이 라벨과 모순되지 않는지는 사람 검토가 유일한 안전장치"를 쓰고(재현: `d9_contradicting_body.py`), README "초안 검토하기"의 `show`에서 볼 것에 "본문이 자기 라벨과 모순되지 않는가"를 더한다. 재현성 기준을 쓴다: 생성 시드만으로 LLM 본문까지 재현된다고 보장하지 않고, 승인된 시나리오 파일과 평가 설정을 고정해 비교한다. 같은 시나리오와 같은 엔진 응답이면 재생·채점은 같다. 엔진 응답이 달라서 되먹임이 갈라지는 것은 평가 대상이다.
- [ ] **Step 3: 전체 검사 후 커밋** — `fix: traces fetch timeout; document review limits and reproducibility`

---

# 파트 D. 오픈소스 브랜치

### Task B2: `feat/oss-adapters`를 새 생성기 위로 rebase하고 맞추기

- [ ] **Step 1:** `git rebase feat/scenario-generation`. 충돌이 나면 해결(주로 README, 설계 문서, `tests/oss_fakes.py` 주변).
- [ ] **Step 2: 맞추기**
  - `tests/test_oss_cli.py`의 숨은 라벨 검사가 새 이름공간·id 형식에서도 통과하는지 확인한다(엔진에 가는 값에 시나리오 id·계열 이름이 없음).
  - 이름공간 설명은 설계 **6.1과 12장**에 있다(4.5에는 없음). 오픈소스 쪽 낡은 부분은 `src/harness/oss/text.py` L71 docstring("`<run_id>-r<round>:<scenario hash>`")과 오픈소스 어댑터 설계 5.3이다. 코어의 불투명 이름공간 설명에 맞춰 고친다.
  - README R8의 "이벤트 id는 중립으로" 문구에 생성 id가 시나리오 번호 스타일(`e{n}`)이 된 것을 반영한다.
- [ ] **Step 3: 확인** — 전체 테스트(783개 이상), ruff, basedpyright 통과. 결정성 확인: `--blind-seed` 고정으로 `harness run --engine hindsight --engine mem0`(가짜 서버)를 두 번 돌려 `engine_ids.json`과 `results.jsonl`이 같다.

---

# 파트 E. 마무리

### Task F1: 새 세션의 설계 문서 ↔ 코드 일치 검토 (새 규칙)

- [ ] 대화 기록 없는 새 에이전트(Opus)에게 경로만 준다: 노션 설계 페이지 URL, 설계·생성 설계·오픈소스 설계 문서와 `docs/ARCHITECTURE.md`, 세 브랜치의 범위. 요청: "문서의 주장과 코드가 어긋나는 곳을 모두 찾아 문서 줄, 코드 `file:line`, 어느 쪽이 낡았는지를 적는다." 지금 알려진 후보: 설계 7.2·6.2·6.1·8.1·12장·5.6, 생성 설계 5.4·8장, ARCHITECTURE L75·L87.
- [ ] 결과를 보고 문서 또는 코드를 고친다.

### Task F2: 최종 검토와 노션 동기화

- [ ] 세 브랜치 각각 전체 테스트·ruff·basedpyright 통과 확인. 예시 5개 시나리오의 M1·M2 값이 바뀌지 않았는지 확인.
- [ ] 브랜치 전체 검토(Opus) 후 지적 정리.
- [ ] 노션 "평가 하네스 설계"를 레포 설계와 같게 고친다(7.2, 6.1·6.2, 8.1, 12장, 14장 진행 상황, 15장 결정 표시).

### Task F3: 리뷰 답글 초안과 push

- [ ] PR #1·#2 각 지적에 대한 답글 초안을 쓴다(고친 커밋, 고치지 않고 문서화한 한계와 이유 포함. R9 해시 커밋을 버리고 코어에서 고친 것도 알림). 사용자가 직접 올릴지 정한다.
- [ ] **사용자 허락 후에만** push: `feat/harness-core`는 일반 push(커밋이 추가됨), `feat/scenario-generation`과 `feat/oss-adapters`는 rebase했으므로 `--force-with-lease=<브랜치>:<예상 커밋>`.

---

## 지적 → 작업 대응표

| 지적 | 재현 | 작업 | 비고 |
|---|---|---|---|
| (사용자) `contract.py` frozen IDE 경고 | - | A0 | |
| C1 미래 이벤트 id | 재현 | A1 | 이미 기록된 id만 돌려주는 엔진은 못 막음(문서화) |
| C2 `about` 필터 | 재현 | A1 | D-1 |
| C3 부트스트랩 영 표본 | 작은 N만 재현 | A4 | 예시 5개는 영향 없음 |
| C4 비밀 문자열 오탐 | 재현(본문·질문) | A2 | 검증기에서 막음, `find_leaks` 유지 |
| C5 피드백 순서 | 재현(설계대로) | A5 | D-2: `max(...)` |
| C6a 순위용 보조 지표·반복 실수 감소율 | 재현 | A4 | 합산 |
| C6a 부트스트랩이 첫 회차 기준 | 설계 8.3대로 | A4 | 유지하고 리포트에 명시 |
| C6a `by_family` 등 표도 첫 회차 | 검증 중 발견 | A4 | 합산 |
| C6b PROMOTE_FP | 부분 | A4 | D-3 |
| C7a~c 검증기 | 재현 | A2 | |
| C7d 중복 YAML 키 | 재현 | A3 | |
| C8 설정 범위 | 재현(`k:0`은 부분) | A3 | |
| C9a·b·c EngineInfo, Result | 재현 | A3 | dict는 코어에서 거절(종료 코드 2) |
| C10a·b 이름공간·생성 id | 재현 | A6 | D-4, D-6 |
| C10c 가리기 | 재현(일부 설계) | A6 | D-4: 점 경로·선언 이름 |
| C10e `results.jsonl` 블라인드 아님 | 재현 | A6 | 알려진 한계로 문서화(결정 D-4) |
| C10d `all_store_noacl` | 재현 | A3 | |
| sunm2n PR #1 인라인 2개(oracle 키, 반복 오류) | - | 완료 | `73daa76`, `cf701b1`, 이미 세 브랜치에 있음 |
| D1 이름공간 복원, 재혁 답글(HMAC) | 재현(24.5초) | A6, B1 | 키 방식 D-6 |
| D1 재혁 답글 "문항 id에 종류를 넣지 않는다" | 재현 | 후속 | A6 이후 문항 id는 엔진에 가는 값(생성 id)에 쓰이지 않아 급하지 않음(`Query`에 문항 id 없음) |
| D2 되먹임 본문 검사 | 재현 | G3 | |
| D3 비밀 문자열 비우기, 재혁 답글(편집 불가 필드) | 재현 | G3 | 편집 불가로 변경 |
| D4a·b·c 승인 무결성 | 재현 | G4 | |
| D5 금지어 | 부분 | G2 | "no longer true"는 이미 "no longer"에 걸림(재혁 지적의 이 단어는 부정확) |
| D6 질문의 비밀 문자열 | 재현 | A2(질문), G3(사실·되먹임 본문) | |
| D7 `quoted`, 짧은 인용 | 재현 | G2 | |
| D8 조건부 단서 | 재현 | G2 | D-5, 다른 계열은 한계 문서화 |
| D9 사람 검토만 안전장치 | 확인 | G5 | 문서 |
| D10a 캐시 키 | 재현 | G1 | |
| D10b SDK 시간 제한 | 재현 | G1 | 최악 약 90분 → 요청당 15분 |
| D10c `traces fetch` 대기 | 재현 | G5 | |
| D10d `api_key_env` | 재현 | G1 | |
| D11 `../` id | 재현 | A3, G4 | |
| D12 출력 토큰 상한 | 재현 | G1 | |
| D13 이벤트 id 형식, 선민 테스트 요청 | 재현(65/65) | A6 | 두 제안 대신 번호 이어 붙이기. refs·채점 보존 테스트 포함 |
| D14·선민 질문 표현 규칙 | 확인 | G2 | 프롬프트와 검토 목록. 계열별 문구 재사용은 후속 |
| 선민: 재현성 문서 | - | G5 | 생성 설계에 새 절 |

## 알려진 한계 (이번 계획이 고치지 않는 것)

- 저장하지 않고 이미 기록된 id만 돌려주는 엔진이 출처 채점에서 TP를 받는 것(구조적으로 구별 불가).
- 시간 계열·되먹임 계열·`leak_cross_team`의 문항 순서에서 정답 종류를 추측할 수 있는 점(D-5). 후속: 가짜 문항 추가.
- 엔진이 정한 문자열(버전, 설정, 오류 문구, 결과 본문)과 `results.jsonl`로 후보를 알아낼 수 있는 점(D-4).
- 문항 id의 중립화(템플릿이 `p_block1` 같은 id를 씀). 엔진에 전달되지 않아 급하지 않음.
- 계열별로 짝 문항의 질문 문구를 재사용하게 만드는 템플릿 변경.
- 파일 안의 승인 해시는 일부러 고친 것을 막지 못한다(git 이력이 근거).
