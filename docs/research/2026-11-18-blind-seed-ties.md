# 블라인드 시드와 점수 동점: dev 측정 결과

기준일 2026-11-18. 하네스 `origin/main` `f827138`, core 고정 rev `a02369d`(`pyproject.toml`). dev 세트(`scenarios/dev`, 승인 40개)만 썼고 봉인된 test 세트는 건드리지 않았다. API 키가 필요한 엔진은 돌리지 않았다.

## 요약

- 로컬 엔진 6개(none, all_store, repeat_n, oracle, core `PromoteAllEngine`, core `NoMemoryEngine`)를 시드 12개로 돌렸다. **시드에 따라 결과가 바뀌는 문항은 PromoteAll의 2개뿐**이다. 하네스 기준선 4개와 NoMemory는 시드 12개에서 문항 단위까지 같다. 고정 rev의 core에는 `EvidenceEngine`이 없다(`engines/baseline.py`에 NoMemory·PromoteAll뿐).
- 두 문항 모두 최상위 점수가 **정확히 같은** 결과들 사이에서 순서가 갈린다(3개 동점, 4개 동점). core가 동점을 id 순으로 두고, 그 id가 시드로 정해지는 불투명 id라서다.
- 원인은 하네스 버그도 시나리오 오류도 아니다. 질문이 두 답을 모두 이름으로 부르게 쓰여 있어(편향 없는 질문) 단어 겹침만 보는 엔진은 동점이 되는 것이 정상이고, 하네스의 불투명 id는 그 동점을 시드마다 고르게 섞는다. 고정 id였다면 시나리오 작성 순서(e1이 먼저)가 늘 이겼을 것이다.
- core를 `daaba07`(PR #29의 고정 커밋, core#3 BM25 저장소 + core#4 EvidenceEngine)로 올리면 같은 시드 12개에서 **뒤집히는 문항이 없다**(EvidenceEngine 포함, 6절). 정수 점수가 BM25 실수 점수로 바뀌어 정확한 동점이 사라졌기 때문이다. 동점을 id 순으로 두는 규칙은 그대로이므로, 동점이 다시 생기면(같은 본문, 점수가 같은 다른 엔진) 같은 현상이 돌아온다.
- 그래서 한 시드의 PromoteAll 수치는 동점 추첨 한 번이다. dev M1은 12/17(0.706)과 13/17(0.765) 사이를 오가고, 기댓값은 12.67/17(0.745)이다. **비교는 `harness seeds`로 시드 5개 이상의 평균과 [최소, 최대]를 보고, 차이가 그 범위 안이면 효과로 읽지 않는다.**

## 1. 측정

시드 12개: `1, 2, 3, 7, 42, 7391846205173948, 482910376145820931, 1181772346203451187, 9007199254740993, 15485863278812345, 2718281828459045235, 3141592653589793238`. 엔진마다 시드당 한 번, 시나리오마다 새 엔진(`harness run`과 같은 `run_scenario`·`blind_key` 경로).

| 엔진 | M1 (read_only) | M2 | 바뀐 문항 |
|---|---|---|---|
| none | 0/17 | 0/31 | 0 |
| all_store | 17/17 | 29/31 | 0 |
| repeat_n | 5/17 | 0/31 | 0 |
| oracle | 0/17 | 31/31 | 0 |
| core NoMemoryEngine | 0/17 | 0/31 | 0 |
| core PromoteAllEngine | 12–13/17 | 25/31 | 2 |

PromoteAll에서 바뀐 문항:

| 문항 | 모드 | 시드 12개 결과 | 동점 | 기대 P(FP) |
|---|---|---|---|---|
| `conditional-geoarrow-python-b93126/p_block` | read_only | FP 8, BLOCKED 4 | 점수 9로 3개 동점(f_a 2, f_b 1) | 2/3 |
| `echo-meson-python-128a90/p_block3` | feedback | FP 10, BLOCKED 2 | 점수 10으로 4개 동점(f_false 3, f_true 1) | 3/4 |

첫 문항은 헤드라인 M1과 conditional 계열 M1(1–2/3)을, 둘째는 되먹임 echo 계열 M1(11–12/15)을 흔든다. 관측 빈도는 기댓값과 맞는다(8/12 ≈ 2/3, 10/12 ≈ 3/4).

`fix/confirm-option-b`(승인 30개, 재승인 대기 10개 제외)에서도 같은 두 문항만 바뀐다(FP 8·BLOCKED 4, FP 10·BLOCKED 2). PromoteAll M1은 8–9/12.

**잠재 동점 확인.** 시드 1에서 PromoteAll의 판정 문항 85개마다, 에이전트가 따른 첫 관련 결과가 다른 사실을 말하는 결과와 같은 점수인지 봤다. 해당하는 문항은 위 2개뿐이다. 지금은 숨은 동점 문항이 없다.

## 2. 동점 근거

PromoteAll은 record마다 지식 하나를 `k-{이벤트 id}`로 올리고, 검색 점수는 `|질문 단어 ∩ 지식 단어|`(정수)다. core 메모리 저장소는 id 순으로 훑고 점수로 안정 정렬하므로 동점은 id 순이다. 이벤트 id는 `HMAC(blind_key(seed), ...)`라 시드마다 동점 안의 순서가 무작위로 바뀐다.

### conditional-geoarrow-python-b93126 / p_block

질문(조건 `service: reports`): "For reports, which file does as_wkb go through and how are kernel options packed (Kernel.as_wkb in _kernel.py, or encode_options_json in reports/geo_export.py)?"

| 점수 | 이벤트 | 사실 | 이 문항에서 |
|---|---|---|---|
| 9 | e1 | f_b (reports 경로) | 맞음 |
| 9 | e3 | f_a (geoarrow-python 경로) | 조건이 다름 → FP |
| 9 | e4 | f_a | 조건이 다름 → FP |
| 5 | e6 | f_b | 맞음 |

시드 1·1181772346203451187은 e3이나 e4가 맨 위(FP), 시드 2는 e1이 맨 위(BLOCKED). 세 결과를 가르는 신호는 질문의 조건(`service`)과 사건의 조건뿐인데 PromoteAll은 조건을 보지 않는다.

### echo-meson-python-128a90 / p_block3

질문: "Which part of mesonpy.py decides the Python tag in the wheel filename, `Project.python_tag` or `_Tag.python`, and how is `abi_tag` related to it?"

| 점수 | 이벤트 | 사실 |
|---|---|---|
| 10 | e1 | f_false (원래 기록) |
| 10 | e4 | f_false (되먹임 메아리: "Following the earlier note: " + e1 본문) |
| 10 | e6 | f_false (메아리) |
| 10 | e2 | f_true |

메아리는 원문에 문구를 덧붙인 것이라 단어 집합이 원문을 포함하고, 질문과 겹치는 단어 수는 원문과 같다. 참 기록도 같은 수가 겹친다. 시드 1181772346203451187에서만 e2가 맨 위(BLOCKED)다.

## 3. 원인 분류

| 문항 | 시나리오 설계 | 엔진 | 하네스 |
|---|---|---|---|
| conditional p_block | 질문이 두 답을 다 부른다(의도: 질문이 답을 흘리지 않게). 거의 같은 본문은 아님. 조건만 답을 가른다 | PromoteAll은 조건을 무시하고 점수가 굵은 정수라 동점이 난다. 엔진이 모르는 문항이고, 결과는 동점 추첨 | 정상. 불투명 id가 동점을 고르게 섞음 |
| echo p_block3 | 메아리 계열은 설계상 거의 같은 본문을 쌓는다. 질문도 두 답을 다 부른다 | 단어 집합 점수로는 메아리가 점수를 올리지 못하고 추첨권만 늘린다(4장 중 3장). 기댓값으로는 "메아리가 희석한다"가 제대로 측정됨 | 정상 |

둘 다 "문항이 동점에 기대는" 경우지만, 그 동점은 단어만 보는 엔진에게 의도된 것이다. 시나리오를 고쳐 동점을 없애면 질문이 한쪽 답의 단어를 더 갖게 되어 단어 겹침이 정답을 흘린다. 고칠 대상이 아니다.

## 4. 고르지 않은 대안

- **core의 동점 규칙 변경(옵션 A)**: 저장소 세 곳의 계약이 바뀌고, 임의성은 다른 키(시각, 삽입 순서)로 옮겨 갈 뿐이다. 삽입 순서는 시나리오 작성 순서라 오히려 체계적 편향이 된다.
- **시드 하나 고정(옵션 C)**: 재현은 되지만 그 시드의 추첨에 묶인다. 예: PR 측정에 쓰던 시드 7391846205173948은 PromoteAll M1이 12/17로, 12개 중 낮은 쪽이다.
- **하네스가 동점을 풀기**: 하네스가 엔진 결과의 순서를 바꾸면 엔진 구조에 개입하게 된다. 하지 않는다.

## 5. 권고와 바꾼 것

1. **`harness seeds` 명령 추가.** 기준선과 후보를 시드마다 한 번씩 돌려 M1·M2·M3을 전체·계열별·되먹임 계열별로 `평균 [최소, 최대] (k/n)`으로 쓰고, 시드마다 결과가 바뀐 문항을 나열한다(`--sealed`면 개수만). 판정·순위는 하지 않는다. dev에서 로컬 엔진 6개 × 시드 12개가 1초 남짓이다.

   ```bash
   uv run harness seeds --scenarios scenarios/dev --engine cortex_mem.engines.baseline:PromoteAllEngine --n 5
   ```

2. **비교 절차.** 엔진이나 core를 바꾼 전후 비교(예: PR 본문 표)는 같은 시드 목록(`--blind-seed`를 반복, 이전 리포트의 `engine_ids.json`에서)으로 전후를 각각 돌려, 평균과 [최소, 최대]를 함께 적는다. 차이가 두 범위 안에 있으면 동점 추첨 차이일 수 있다. 시드별 값이 모두 같은 방향으로 움직였는지도 본다(같은 시드 목록이면 짝지어 비교할 수 있다).
3. **11-28 검증 일정.** 시드 5개 이상을 기본으로 한다(옵션 B). 단어 기반·임베딩 기반처럼 동점이 날 수 있는 엔진은 모두 해당한다. LLM 엔진은 시드마다 API 비용이 들고, `seeds`는 후보를 시드당 한 번만 돌리므로 그 분산에는 표본 잡음도 섞인다. 표본 잡음 자체는 `run --repeat`의 뒤집힘률로 따로 본다.
4. **한 시드 수치 읽기.** README "한 시드의 수치 읽기" 절에 정리했다.

## 6. core `daaba07`(PR #29)에서 다시 잰 결과

`origin/chore/bump-core-pin`(core `daaba07`)에서 같은 시드 12개로 다시 쟀다.

| 엔진 | M1 | M2 | 바뀐 문항 |
|---|---|---|---|
| none / all_store / repeat_n / oracle / NoMemory | 1절과 같음 | 1절과 같음 | 0 |
| core PromoteAllEngine | 13/17 | 29/31 | 0 |
| core EvidenceEngine | 5/17 | 31/31 | 0 |

core#3이 메모리 저장소의 점수를 `|질문 단어 ∩ 본문 단어|`(정수)에서 단어·바이그램 BM25(문서 길이로 정규화한 실수)로 바꿨다. 위 두 문항의 동점 결과들은 본문 길이가 달라 이제 점수가 갈린다. 따라서:

- 지금 고정(`a02369d`)에서 나온 PromoteAll 수치의 흔들림은 #29가 머지되면 사라진다. 다만 PromoteAll의 M1·M2 자체가 바뀌므로(12–13/17 → 13/17, 25/31 → 29/31) 고정 전후 비교에도 `seeds`를 쓰는 것이 안전하다.
- 동점 규칙은 바뀌지 않았다. 본문이 똑같은 기록이 둘이거나, 점수가 굵게 나뉘는 엔진·저장소(예: 임베딩 거리를 반올림하는 저장소)가 들어오면 다시 시드에 따라 뒤집힌다. `seeds`의 "시드마다 바뀐 문항" 목록이 그 감지기다.

## 재현

측정 스크립트는 레포 밖(세션 scratchpad)에서 돌렸고, 같은 결과는 다음으로 다시 얻을 수 있다.

```bash
uv sync --extra core
uv run harness seeds --scenarios scenarios/dev \
  --engine cortex_mem.engines.baseline:PromoteAllEngine \
  --engine cortex_mem.engines.baseline:NoMemoryEngine \
  --blind-seed 1 --blind-seed 2 --blind-seed 3 --blind-seed 7 --blind-seed 42 \
  --blind-seed 7391846205173948 --blind-seed 482910376145820931 --blind-seed 1181772346203451187 \
  --blind-seed 9007199254740993 --blind-seed 15485863278812345 \
  --blind-seed 2718281828459045235 --blind-seed 3141592653589793238 --out runs
```

(작은 시드는 경고가 나오지만 dev 측정에는 상관없다.)
