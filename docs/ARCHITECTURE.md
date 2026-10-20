# 하네스 구조

코드를 처음 읽는 사람을 위한 지도입니다. 무엇을 왜 재는지는 설계 문서([specs/2026-10-19-memory-eval-harness-design.md](superpowers/specs/2026-10-19-memory-eval-harness-design.md))와 노션 [평가 하네스 설계](https://app.notion.com/p/3fd215ecaad881cea1f7e30c3ff39267)에 있습니다. 이 문서는 그 정의가 **코드의 어디에 있고 어떻게 흐르는지**만 다룹니다. 함수 단위 설명은 각 모듈의 docstring(Google 스타일)에 있습니다.

## 한 줄 요약

시나리오 하나를 가상 시간 순서대로 엔진에 흘려 넣고, 문항(probe)마다 엔진이 돌려준 결과를 **규칙 기반 에이전트**가 따라갔을 때 거짓 사실로 행동했는지(M1), 참 사실로 행동했는지(M2), 볼 수 없는 기록을 받았는지(M3)를 셉니다. 채점·에이전트·기준선 어디에도 LLM이 없으므로 같은 입력이면 같은 결과가 나옵니다.

## 전체 흐름

```
scenarios/*.yaml ──load_set──▶ Scenario ──validate_scenario──▶ (오류면 중단)
                                  │
                 ┌────────────────┴───────────────┐
                 ▼                                ▼
          selfcheck (기준선 4개)              run (추가 엔진 × 반복)
                 │                                │
                 └──────────▶ run_scenario ◀──────┘   시나리오마다 새 엔진
                                  │
            시간표(heapq): 이벤트 → record / 문항 → flush → search
                                  │
             문항마다: choose(에이전트) → judge · find_leaks
                                  │
                           ScenarioRun (ProbeRecord 목록)
                                  │
                          compute_metrics → Metrics
                                  │
                judge_engine(all_store 대비) → Verdict → rank
                                  │
     write_report → report.md · summary.json · results.jsonl · engine_ids.json
```

`harness run`은 항상 자가 검증부터 돌리고, 실패하면 엔진 평가로 넘어가지 않습니다. 기준선이 설계대로 줄 서지 않으면 시나리오나 하네스가 틀린 것이라 그 위의 엔진 점수도 믿을 수 없기 때문입니다.

## 모듈 지도

아래 층은 위 층을 모릅니다(의존은 위에서 아래로만).

| 층 | 모듈 | 역할 |
|---|---|---|
| 진입점 | `cli.py` | `scenario check`, `selfcheck`, `run` 세 명령. 단계들을 이어 붙이기만 하고 판단 로직은 없음 |
| 산출 | `report.py` | 네 개의 리포트 파일, 엔진 이름 가리기(blind id), sealed 모드 |
| 판정 | `selection.py` | 사전 등록 기준으로 통과/실패(`judge_engine`), 반복 평균(`averaged`), 순위(`rank`) |
| | `selfcheck.py` | 기준선 순서 검사(`run_selfcheck`), 시나리오 변별력 검사(`check_discrimination`) |
| 집계 | `metrics.py` | `ScenarioRun` 목록 → `Metrics`(M1·M2·M3와 진단 지표) |
| | `stats.py` | Wilson 구간, 시나리오 단위 bootstrap |
| 실행 | `replay.py` | 시나리오 한 개 재생(`run_scenario`), 피드백 모드에서 생성 이벤트 끼워 넣기 |
| 채점 | `agent.py` | 결과 → 근거 사실(`result_basis`), 따라갈 결과 고르기(`choose`) |
| | `scoring.py` | 문항 결과 판정(`judge`), 누출 찾기(`find_leaks`) |
| | `world.py` | 사실이 그 시각·맥락에서 참인지(`true_at`) |
| 데이터 | `scenario/model.py` | 시나리오 YAML 모델과 **숨은 라벨** 분리(`labeled_events`, `query_for`) |
| | `scenario/load.py`, `scenario/validate.py` | 파일 읽기·세트 해시, 구조·라벨 검사 |
| | `config.py` | 점수에 영향을 주는 모든 값(`config/eval-v0.1.yaml`)과 그 해시 |
| 엔진 | `contract.py` | 엔진이 보는 유일한 타입: `Event`, `Query`, `Result`, `EngineInfo`, `BaseEngine`, `can_see` |
| | `engines.py` | 이름 → 엔진 인스턴스(`make_engine`) |
| | `baselines/` | `none`, `all_store`(+`all_store_noacl`), `repeat_n`, `oracle` |
| | `text.py` | 기준선용 토큰화·Jaccard·BM25(결정적) |

추천 읽기 순서: `contract.py` → `scenario/model.py` → `world.py` → `agent.py` → `scoring.py` → `replay.py` → `metrics.py` → `selection.py` → `selfcheck.py` → `cli.py`. 예시 시나리오(`scenarios/examples/*.yaml`) 하나를 옆에 펴 두고 읽으면 빠릅니다.

## 엔진과의 경계

엔진은 `harness.contract`만 압니다. 시나리오 파일에는 엔진이 봐서는 안 되는 **숨은 라벨**이 같이 들어 있고, 이를 떼어 내는 곳은 `scenario/model.py` 한 곳뿐입니다.

| 숨은 라벨 | 어디에 | 엔진에 가는 것 |
|---|---|---|
| `ScenarioEvent.fact`, `secret_markers` | 이벤트 | `labeled_events()` → `LabeledEvent.event`만 |
| `facts`(참 구간, 조건, 마커, fix) | 시나리오 | 가지 않음 |
| `Probe.kind`, `about`, `feedback` | 문항 | `query_for()` → `Query`만 |

엔진 생명주기는 `reset(namespace)` 한 번 → 시간 순서대로 `record(event)` → 문항마다 `flush()` 후 `search(query)`입니다. `EngineInfo.virtual_clock`이 참이면 각 단계 전에 `tick(t)`이 불립니다. 엔진이 던진 예외는 그 시나리오만 오류로 끝내고(`ScenarioRun.error`), 하네스 코드의 예외는 잡지 않아 버그가 드러나게 합니다.

## 문항 하나가 채점되는 과정

1. **근거(`result_basis`)**: 결과의 `sources`가 가리키는 이벤트의 숨은 사실 중 문항의 `about`에 있는 것. `sources`가 없으면 사실의 마커가 본문에 나오는지로 대신 판단하고, 근거가 여러 개면 마커로 좁힙니다.
2. **선택(`choose`)**: 근거가 비지 않은 첫 결과를 따라갑니다. 없으면 기권. 에이전트는 엔진이 붙인 점수·라벨을 보지 않으므로 순서와 근거만이 차이를 만듭니다.
3. **판정(`judge`)**: 근거 중 하나라도 그 시각·맥락에서 거짓이면 틀린 근거입니다(`is_wrong` → `true_at`).
   - block 문항: 틀린 근거로 행동 → FP(M1), 아니면 BLOCKED
   - promote 문항: 맞는 근거 → TP(M2), 틀린 근거 → PROMOTE_FP, 기권 → FN
   - leak 문항: LEAK_ONLY(누출만 봄)
4. **누출(`find_leaks`)**: 모든 문항에서, 묻는 사람이 볼 수 없는 이벤트를 출처로 달았거나(source) 그 이벤트의 비밀 마커가 본문에 있으면(secret_marker) 누출입니다.

`true_at`은 `valid_from` 포함, `valid_until` 미포함이고, 조건(`conditions`)은 문항의 repo와 meta(`probe_ctx`)에 맞춰 봅니다.

## 피드백 모드

`mode: feedback` 시나리오에서는 에이전트가 행동한 뒤 남기는 기록(행동 기록, 결과 기록)을 하네스가 만들어 시간표에 다시 넣습니다(`feedback_events`). 생성 이벤트 id는 `"e" + sha256(시나리오:문항:슬롯)[:12]`로 문항이나 하네스를 드러내지 않습니다. 엔진은 생성 이벤트와 시나리오 이벤트를 구별할 수 없어야 합니다. 피드백 시나리오 점수는 헤드라인 M1·M2와 섞지 않고 `feedback_by_family`에 따로 둡니다.

## 판정과 순위

- **통과 기준**(`config.Judgment`, 사전 등록): M1 ≤ all_store M1 × 0.5, M2 ≥ all_store M2 − 0.10, M3 = 0. 비교에는 1e-9 허용 오차를 둡니다.
- 오류 난 시나리오가 하나라도 있으면 통과하지 못합니다("판정 불가: 오류 시나리오 N개"). 오류 시나리오가 조용히 분모에서 빠지는 것을 막기 위해서입니다.
- 반복 실행(`llm_repeats`): M1·M2는 평균, M3와 오류는 가장 나쁜 반복을 씁니다.
- **순위**(`rank`): 통과한 엔진만 M2 순서. promote TP 차이의 95% bootstrap 구간이 0을 포함하면 `TIEBREAKS`(m1 → shallow_fix → retraction → reach → context_chars) 순으로 가릅니다.

## 자가 검증

| 검사 | 기대 |
|---|---|
| `no_errors` | 기준선 어디서도 오류 없음 |
| `oracle_perfect` | oracle M1 = 0, M2 = 1, M3 = 0 |
| `none_empty` | none M1 = M2 = 0, M3 = 0 |
| `all_store_max_m1` | all_store M1 > 0이고 다른 기준선 이상 |
| `repeat_n_poison`, `repeat_n_echo` | repeat_n이 해당 계열에서 oracle보다 M1이 높음(반복에 속음) |

`scenario check`는 시나리오 하나마다 oracle과 all_store(누출 문항이 있으면 all_store_noacl도)를 돌려, 그 시나리오가 두 기준선을 실제로 가르는지 봅니다.

## 리포트

`runs/<run_id>/` 아래 네 파일이 생깁니다.

| 파일 | 내용 |
|---|---|
| `report.md` | 사람이 읽는 요약(판정, 순위, 계열별 표, 자가 검증) |
| `summary.json` | 같은 내용의 기계용 형태 |
| `results.jsonl` | 문항 단위 기록 |
| `engine_ids.json` | 가린 id ↔ 실제 엔진 이름과 섞기 seed. 이 파일에만 있음 |

기준선 외 엔진 이름은 E1, E2…로 가립니다. `--sealed`면 시나리오·문항 id와 검사 세부 값을 리포트와 터미널에서 뺍니다. `--include-drafts`로 돌린 리포트에는 "초안 포함 — 발표 수치로 쓰지 않음" 경고가 붙습니다.

## 확장하는 법

- **엔진 추가**: `BaseEngine`을 상속해 `info`, `reset`, `record`, `search`를 구현하고 `--engine package.module:ClassName`으로 넘깁니다(인자 없는 생성자). 정밀 채점을 위해 `Result.sources`에 이벤트 id를 채우고, 묻는 사람이 볼 수 없는 이벤트(`can_see`)는 돌려주지 않아야 합니다. 최소 예시는 `tests/dummy_engine.py`.
- **시나리오 추가**: `scenarios/examples/`에서 같은 계열 파일을 복사해 고친 뒤 `uv run harness scenario check <파일>`로 형식과 변별력을 확인합니다. 시각에는 시간대가 필요합니다. 평가에 들어가려면 검토 후 `review.status: approved`.
- **지표 추가**: `ProbeRecord`(replay)에 문항 단위 값을 남기고 `compute_metrics`에서 집계합니다. 점수에 영향을 주는 새 값은 `EvalConfig`에 넣어 설정 해시에 잡히게 합니다.

## 지켜야 할 것

- 엔진에 숨은 라벨을 넘기지 않습니다. 새 필드를 `Event`나 `Query`에 넣을 때는 그것이 정답을 흘리지 않는지 먼저 봅니다.
- 채점 경로에 무작위성이나 LLM을 넣지 않습니다. 해시 순서에 의존하지 않도록 정렬해서 순회합니다(BM25 질의 토큰, 사실 id 등).
- 점수에 영향을 주는 값은 코드에 박지 않고 `config/eval-v0.1.yaml`에 둡니다. 값을 바꾸면 `version`을 올리고 모든 엔진을 다시 잽니다.
- 시나리오마다 새 엔진을 만듭니다. 엔진 상태가 시나리오 사이에 새면 결과가 오염됩니다.

## 테스트

`uv run pytest -q`. 테스트 파일은 대체로 모듈과 짝을 이룹니다(`tests/test_<모듈>.py`, 피드백 모드는 `test_feedback.py`). `tests/factories.py`가 작은 시나리오를 코드로 만들어 주고, `tests/test_examples.py`는 예시 시나리오 전체가 끝까지 도는지 확인합니다. docstring 형식은 `uvx ruff check src`(pydocstyle, google 규약)로, 타입은 basedpyright로 검사합니다.
