# 메모리 평가 하네스

AI 에이전트용 메모리 엔진이 "낡았거나 거짓인 지식을 막고, 참인 지식은 올려 주는지" 평가하는 하네스입니다. 채점·에이전트·기준선에 LLM을 쓰지 않으며, 같은 입력이면 같은 출력이 나옵니다.

## 설치

Python 3.12 이상(3.12·3.13·3.14에서 테스트)과 [uv](https://docs.astral.sh/uv/)가 필요합니다. Python은 uv가 설치·관리합니다(pyenv 불필요). 레포의 `.python-version`이 개발 기본 버전(3.12)을 정합니다.

```bash
uv sync            # .venv 생성 + 의존성 설치 (Python이 없으면 uv가 내려받음)
uv run pytest      # 테스트
```

## 명령 세 가지

```bash
# 1. 시나리오 검사: 형식 검증 + 기준선 사이를 가르는지(변별력) 확인
uv run harness scenario check scenarios/examples

# 2. 자가 검증: 기준선 4개(none, all_store, repeat_n, oracle)가 설계대로 줄 서는지 확인
uv run harness selfcheck --scenarios scenarios/examples --include-drafts

# 3. 실행: 자가 검증 -> 평가 -> 판정·순위 -> 리포트(report.md) 작성. 마지막 줄이 리포트 경로
uv run harness run --scenarios scenarios/examples --include-drafts --out runs
```

평가할 엔진은 `--engine package.module:ClassName`으로 추가합니다(여러 번 지정 가능, 기준선은 항상 같이 돕니다). `all_store_noacl` 같은 내부 진단용 이름은 후보로 받지 않고(종료 코드 2), 엔진의 `info()`는 `EngineInfo`이며 JSON으로 쓸 수 있어야 합니다(pydantic JSON 모드로 바꿀 수 있는 값이면 됩니다. `Path` 등은 문자열로 기록). 실행 결과는 `runs/`에 쌓이며 레포에는 올리지 않습니다.

## 동작 확인과 결과 읽기

하네스가 제대로 도는지는 위 세 명령을 차례로 돌려 확인합니다. 각 단계에서 무엇이 보이면 정상인지 적습니다.

### 1. 시나리오 검사 (`scenario check`)

- 문제가 없으면 **아무것도 출력하지 않고** 종료 코드 0으로 끝납니다.
- 문제가 있으면 `<파일 경로>: error <코드>` 또는 `warning <코드>` 줄이 나오고, 오류가 하나라도 있으면 종료 코드 1입니다. 예: `unknown_fact`(없는 사실을 가리킴), `too_easy`(전부 저장 기준선도 틀리지 않는 쉬운 문항).
- `run`·`selfcheck`는 오류가 있는 세트를 돌리지 않습니다.

### 2. 자가 검증 (`selfcheck`)

기준선 4개가 설계대로 나오는지 봅니다. 하나라도 `FAIL`이면 하네스나 시나리오 세트를 믿을 수 없다는 뜻이고, `run`도 멈춥니다(종료 코드 1).

| 검사 | 정상일 때 |
|---|---|
| `no_errors` | 기준선 실행 중 오류가 없음 |
| `oracle_perfect` | 정답 기준선: M1 = 0, M2 = 1, M3 = 0 |
| `none_empty` | 아무것도 기억하지 않는 기준선: M1 = 0, M2 = 0, M3 = 0 |
| `all_store_max_m1` | 전부 저장 기준선의 M1이 0보다 크고 기준선 중 가장 큼 |
| `repeat_n_poison`, `repeat_n_echo` | 반복 N회 기준선이 한 사람의 오염·메아리 계열에서 정답보다 많이 틀림(그 계열이 세트에 있을 때만) |

### 3. 실행 (`run`)

마지막 줄이 리포트 경로입니다. `runs/<실행 id>/`에 네 파일이 생깁니다.

| 파일 | 내용 |
|---|---|
| `report.md` | 사람이 읽는 리포트 |
| `summary.json` | 같은 내용을 기계가 읽는 형식으로: `meta`(실행 정보와 해시), `engines.<id>`(`metrics`, `verdict`, `repeats`, `flip`), `ranking`, `selfcheck` |
| `results.jsonl` | 엔진 × 문항마다 한 줄: 어떤 결과를 받았고(`results`), 무엇을 골랐고(`choice`), 어떻게 채점됐는지(`outcome`), 누출(`leaks`) |
| `engine_ids.json` | 블라인드 id(E1, E2, ...)와 실제 엔진 이름의 대응표, 블라인드 시드(섞기와 엔진에 넘기는 이름공간 키를 함께 정함) |

`--sealed`로 돌리면 `results.jsonl`을 만들지 않고 리포트에서 시나리오·문항 id를 뺍니다.

블라인드(E1, E2…)는 `report.md`·`summary.json`에서 후보의 전체 이름, 클래스 이름, 모듈 점 경로, `info().name`으로 밝힌 이름을 가립니다. 엔진이 정한 다른 문자열(버전, 설정, 오류 문구)은 그대로이고, `results.jsonl`에는 엔진이 돌려준 결과 본문이 그대로 남아 블라인드가 아닙니다.

### report.md 읽는 법

| 절 | 볼 것 |
|---|---|
| 머리말 | 실행 id, 하네스 버전, **설정 해시**, **시나리오 세트 해시**, 초안 포함 여부. 초안이 들어가면 맨 위에 `⚠️ 초안 포함 — 발표 수치로 쓰지 않음`이 붙습니다 |
| 판정 | 엔진마다 M1, M2, M3, 통과/탈락과 근거. 값은 `0.500 (n=4) [0.150, 0.850]` 형식으로, 비율 (문항 수) [95% Wilson 신뢰구간]입니다. 부트스트랩은 순위의 차이 구간에만 씁니다 |
| 보조·관찰 | 판정에는 쓰지 않는 지표: 철회 지연, 오염 노출률, 출처 없는 결과 비율, promote FP(틀린 지식을 쓴 비율) 등 |
| 계열별 / 되먹임 / 출처별 | M1·M2·M3를 계열별로, 되먹임 계열은 따로, 시나리오 작성 출처(사람·LLM·생성기)별로 나눈 표 |
| 순위 | 판정을 통과한 후보 엔진의 순위와 차이의 신뢰구간 |
| 성능 | `record`·`search` 응답 시간과 질문당 건넨 글자 수(표시만 하고 판정에는 쓰지 않음) |
| 엔진 한계 | 가상 시계·출처·범위 지원 여부와 설정. 가상 시계를 지원하지 않으면 시간 계열 결과에 주석이 붙습니다 |
| 오류 시나리오 | 엔진이 예외를 낸 시나리오(반복 실행이면 회차와 함께). 하나라도 있으면 그 엔진은 판정을 통과할 수 없습니다 |
| 자가 검증 | 2단계와 같은 표 |

**판정 기준** (`config/eval-v0.1.yaml`의 `judgment`):

- **M1**(막아야 할 문항에서 틀린 기억으로 행동한 비율) ≤ 전부 저장 기준선 M1 × 0.5
- **M2**(승격돼야 할 문항에서 맞는 지식을 쓴 비율) ≥ 전부 저장 기준선 M2 − 0.10
- **M3**(볼 수 없는 기록의 누출 수) = 0
- 오류 시나리오 없음

기준선도 같은 기준으로 판정됩니다. 예시 시나리오로 돌리면 아래처럼 나오는 것이 정상이고, 이는 하네스가 기준선을 제대로 가른다는 뜻입니다.

| 기준선 | 결과 | 이유 |
|---|---|---|
| `oracle` | 통과 | 정답만 건넴 |
| `none` | 탈락 | 아무것도 안 건네서 M2 = 0 |
| `all_store` | 탈락 | 다 건네서 M1 = 1 (자기 M1의 절반을 넘음) |
| `repeat_n` | 탈락 | 여러 번 나온 것만 건네서 오염에 속고(M1) 한 번 나온 참 지식을 놓침(M2) |

### results.jsonl의 `outcome`

| 문항 종류 | 값 | 뜻 |
|---|---|---|
| `block` | `BLOCKED` | 틀린 지식으로 행동하지 않음(좋음) |
| `block` | `FP` | 틀린 지식으로 행동함(M1에 들어감) |
| `promote` | `TP` | 맞는 지식을 씀(M2에 들어감) |
| `promote` | `FN` | 쓸 만한 결과가 없어 행동하지 않음 |
| `promote` | `PROMOTE_FP` | 틀린 지식을 씀 |
| `leak` | `LEAK_ONLY` | 누출 검사 전용 문항. 누출은 `leaks`에 기록 |

특정 문항이 왜 그렇게 채점됐는지는 그 줄의 `results`(엔진이 건넨 것)와 `choice`(에이전트가 고른 결과와 근거 사실)를 보면 됩니다. 예: `jq 'select(.engine_id=="all_store" and .outcome=="FP")' runs/<실행 id>/results.jsonl`

### 같은 입력이면 같은 출력인지 확인

채점·에이전트·기준선은 결정적입니다. 같은 시나리오 세트와 설정으로 두 번 돌리면(후보 엔진이 둘 이상이면 같은 `--blind-seed`로) `results.jsonl`은 바이트까지 같고, `summary.json`·`report.md`는 아래 값만 다릅니다.

- 실행 id와 실행 일시
- 응답 시간(성능 표)
- 블라인드 시드(`engine_ids.json`에 기록). 주지 않으면 실행마다 무작위이고, 후보가 둘 이상이면 E1·E2 배정과 `results.jsonl`의 `engine_id`도 따라 달라집니다(후보가 하나면 항상 E1).

`--blind-seed N`으로 시드를 고정하면 후보 id 배정과 엔진에 넘기는 이름공간의 해시 부분(실행 id 뒤)이 같아져, 후보가 여럿이어도 `engine_ids.json`과 `results.jsonl`이 같습니다. 이 옵션은 기록된 실행을 재현하기 위한 것이므로 `N`은 추측하기 어려운 큰 값(예: 그 실행의 `engine_ids.json`에 적힌 시드)을 쓰세요. 작은 수는 경고가 나옵니다.

```bash
uv run harness run --scenarios scenarios/examples --include-drafts --blind-seed 482910376145820931 --out /tmp/r1
uv run harness run --scenarios scenarios/examples --include-drafts --blind-seed 482910376145820931 --out /tmp/r2
cmp /tmp/r1/*/results.jsonl /tmp/r2/*/results.jsonl && echo same
```

LLM을 쓰는 엔진은 같은 입력에도 답이 달라질 수 있습니다. 그런 엔진은 `--repeat`(기본 3회)로 여러 번 돌리고, 리포트의 재실행 뒤집힘률(`flip`)로 흔들림을 봅니다. M1·M2 값은 회차 평균이고, 그 신뢰구간과 순위 부트스트랩은 첫 회차 기준이며, 나머지 표는 회차를 합산합니다(리포트 머리말에 표시).

### 직접 만든 엔진 붙여 보기

```bash
# tests/dummy_engine.py의 예시 엔진. 레포 안의 모듈을 쓰려면 PYTHONPATH=.가 필요합니다
PYTHONPATH=. uv run harness run --scenarios scenarios/examples --include-drafts \
  --engine tests.dummy_engine:LatestWins --repeat 2 --out runs
```

엔진은 `harness.contract.BaseEngine`을 구현합니다(`info`, `reset`, `record`, `search`, 선택으로 `tick`·`flush`). 결과의 `sources`에 근거 이벤트 id를 채우면 출처로 정밀 채점되고, 비우면 사실의 판정 문자열(`markers`)로 채점됩니다.

### 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 성공 |
| 1 | 시나리오 오류(로드 실패, 검증 오류, id 중복, 승인 뒤 고친 파일 `approved_content_changed`, 새 도구가 만든 파일을 손으로 승인해 해시가 없는 `approved_hash_missing`) 또는 자가 검증 실패. `review set ... approved`가 막힌 경우(자동 검증 오류·`too_easy`, `labels_changed_outside_review`)와 id가 없거나 여럿인 `review` 명령도 1 |
| 2 | 사용법 문제: 경로 없음, 평가할(승인된) 시나리오 없음, 설정 파일 오류, 엔진을 만들 수 없음 |

## 시나리오 세트

| 위치 | 내용 | 쓰임 |
|---|---|---|
| `scenarios/dev/` | 사람이 검토해 승인한(`review.status: approved`) 개발용 세트 | 엔진 개발과 튜닝 |
| `scenarios/examples/` | LLM이 쓴 초안 5개(계열마다 하나) | 하네스가 끝까지 도는지 보는 스모크 테스트 |
| test 세트 | 레포 밖에 보관 | `--sealed`로 실행: 리포트에서 시나리오·사실 id를 뺌 |

평가에는 `approved`만 들어갑니다. 초안을 넣으려면 `--include-drafts`를 줍니다(`rejected`는 항상 제외).

**예시(`scenarios/examples/`)는 스모크 테스트 전용이며 발표 수치에 쓰지 않습니다.**

## 시나리오 초안 만들기

실제 에이전트 궤적(Open-SWE-Traces)에서 LLM으로 시나리오 초안을 만듭니다. 정답 라벨은 계열 틀(코드)이 정하고, LLM은 본문만 씁니다. 평가만 하는 사람은 이 절이 필요 없습니다.

```bash
uv sync --extra gen                      # anthropic, openai, pyarrow, huggingface_hub 설치
export ANTHROPIC_API_KEY=...             # config/gen-v0.1.yaml의 api_key_env를 주면 두 공급자 모두 그 변수를 읽음

# 1. 궤적 조각 받기: 크기(MB)를 보여 주고 y/N을 묻는다. --yes로 건너뜀. 조각 하나가 수백 MB
#    --timeout 초(기본 60): 서버가 답하지 않으면 그 시간 뒤 오류로 끝남
uv run harness traces fetch --config v1.2 --split minisweagent --shard 0
uv run harness traces list               # 받은 조각, 저장소별 궤적 수, 묶음 수

# 2. 초안 만들기: 계열마다 n개를 목표로 scenarios/dev/<계열>/<id>.yaml (review.status: draft)에 저장
uv run harness generate --family all --n 4 --seed 20261020
uv run harness generate --family truth_change --n 2 --repos owner/name --out /path/outside/repo
uv run harness generate --family echo --dry-run   # 첫 LLM 요청만 출력하고 부르지 않음
```

- `--family all`은 10개 계열 전부이고, 짝 문항이 없는 `normal_repeat`는 `n // 2`개(최소 1)만 만듭니다.
- 자동 검증(`check_draft`)은 먼저 `validate_scenario`를 돌립니다. 이 검증기는 비밀 문자열 배타성(`secret_marker_not_exclusive`), 다른 사실의 판정 문자열 섞임(`marker_bleed`), 모르는 팀(`unknown_team`)도 잡고, 오류가 있으면 거기서 멈추므로 검증기 코드가 먼저 나옵니다. 생성기의 `other_fact_marker`·`secret_in_public_event` 등은 검증기가 보지 않는 곳(문항이 묻지 않는 사실의 마커, 아무도 볼 수 없는 기록 속 비밀 문자열)을 맡습니다. 되먹임 틀의 본문(행동·기권·결과)도 이벤트 본문과 같은 검사(마커 포함, 다른 사실 마커 금지, 금지어)를 받고, 비밀 문자열은 사실 문장(`secret_in_fact_text`)과 되먹임 본문(`secret_in_feedback`)에도 없어야 합니다. 비밀 기록이 어느 이벤트인지는 생성 때 `provenance.secret_events`에 적어 두며(적혀 있으면 `secret_markers`가 있는 이벤트와 정확히 같아야 합니다. 다르면 오류 `secret_events_mismatch`), 이 목록이 없는 파일(`secret_events: null`이거나 키 없음. 하네스가 다시 저장해도 null로 남습니다)은 `secret_markers`에서 유도하고 경고 `secret_events_not_recorded`만 붙습니다. 누출 계열인데 비밀 기록이 하나도 없으면(목록이 비었거나 유도할 `secret_markers`가 없으면) 오류 `no_secret_events`입니다.
- 자동 검증(`check_draft`)에서 오류가 나거나 `too_easy` 경고가 뜬 초안은 저장하지 않고 거절 로그에 남깁니다. 같은 id의 파일은 손대지 않은 LLM 초안일 때만 새로 씁니다. 상태가 draft가 아니거나, 검토자가 고쳤거나(`provenance.reviewer_edited: true`), 사람이 쓴(`provenance.author`가 `llm`이 아닌) 파일, 읽을 수 없는 파일, 생성 뒤 편집기로 직접 고친 파일(지금 내용의 해시가 생성 때 적은 `provenance.generated_hash`와 다름)은 덮어쓰지 않고 요약에 "건너뜀"으로 보여 줍니다. `generated_hash`가 없는 이전 초안은 앞의 규칙만 적용합니다.
- 후보 뽑기에 보내는 궤적은 한 번 실행에 최대 `--max-traces`(기본 100)개입니다. 한도에 닿으면 요약에 표시됩니다.
- 본문 쓰기 시도는 계열마다 최대 `--max-attempts`(기본: 그 계열 목표 개수 × 3)번입니다. 한도에 닿으면 그 계열을 멈추고 부족 사유에 "stopped at --max-attempts N"이 나옵니다.
- 같은 입력·시드면 같은 id가 나오고, LLM 응답은 캐시되어 다시 돌려도 호출하지 않습니다. 캐시 키에는 `effort`·`temperature`·`max_output_tokens`·`base_url`·`token_param`이 들어가므로 이 설정을 바꾸면 새로 호출합니다.
- 요청 하나는 `timeout_s`(기본 300초) 안에 끝나야 하고 SDK가 `max_retries`(기본 2)번까지 다시 시도합니다(요청당 최악 15분. 검증에 실패하면 요청이 한 번 더 있습니다). OpenAI 호환 서버가 `max_completion_tokens`를 모르면 `token_param: max_tokens`(또는 `none`)로 바꿉니다.
- 키가 없거나 인증이 거부되거나, 모델 이름이 틀렸거나(NotFoundError), 요청이 거부되거나(BadRequestError), 서버에 닿지 않으면(APIConnectionError) 전체가 멈춥니다(종료 코드 2). 그 밖의 LLM 오류(시간 초과, 잘린 응답 등)는 해당 후보나 초안만 거절하지만, 연달아 3번 나면 마지막 오류와 함께 실행을 멈춥니다(종료 코드 2). 요약에는 실행 중 처음 난 LLM 오류가 `first LLM error:`로 나옵니다.

캐시 위치(`T5_HARNESS_CACHE`, 기본 `~/.cache/t5-harness`):

| 경로 | 내용 |
|---|---|
| `traces/<config>/<split>/` | 받은 parquet 조각 |
| `llm/` | LLM 응답 캐시 |
| `runs/<run_id>/` | `candidates.jsonl`(사실 후보), `rejected.jsonl`(거절 사유) |
| `review/` | `review edit`에서 되돌린 편집본(`<id>-<YYYYmmdd-HHMMSS>.yaml`; id의 영문자·숫자·`.`·`_`·`-` 밖의 글자는 `_`로 바꾸고, 경로가 이 폴더 안인지 확인함) |

**출처 표기**: 궤적은 [nvidia/Open-SWE-Traces](https://huggingface.co/datasets/nvidia/Open-SWE-Traces)(CC BY 4.0)에서 가져오며, 초안의 `provenance`에 원본 궤적 id, 저장소, 라이선스를 남깁니다. 원본 저장소 라이선스가 허용 목록에 있는 python 궤적만 씁니다.

## 초안 검토하기

초안(`review.status: draft`)은 사람이 보고 `approved`나 `rejected`로 바꿔야 평가에 들어갑니다. 검토는 시나리오 YAML만 있으면 되므로 각자 노트북에서 하면 되고, `uv sync --extra gen`도 필요 없습니다.

```bash
uv run harness review list                        # 기본 scenarios/dev. id, 계열, 상태, 생성 시각, 경고, 경로
uv run harness review show <id>                   # 보기
uv run harness review edit <id>                   # 고치기: $EDITOR(없으면 vi)로 연다
uv run harness review set <id> approved --reviewer 이름   # 승인
uv run harness review set <id> rejected --reviewer 이름 --note "정답이 틀림"   # 거절(이유 권장)
```

- `show`: 머리(id, 계열, 모드, 원본 저장소·궤적, 공급자·모델, 자동 검증 경고), 정답표(사실마다 참인 기간, 조건, `valid`, `fix`, 지어낸 사실 여부, 마커, 문장), 시각 순서의 이벤트·문항 흐름(근거의 `origin`과 인용, 문항 시점에 참인 사실), 기준선 미리보기(oracle, all_store, 누출 계열은 all_store_noacl의 문항별 결과와 누출 수)를 한 화면에 보여 줍니다.
- `edit`에서 **고칠 수 있는 것**: 사실의 `text`·`markers`, 이벤트의 `text`, 문항의 `query`, 되먹임 틀의 `text`, `review.notes`.
- **고칠 수 없는 것**: 사실의 id·기간·조건·`valid`·`fix`, 이벤트의 id·시각·사용자·세션·저장소·범위·팀·신호·종류·결과·연결 사실·`refs`·`meta`·`secret_markers`, 문항의 id·종류·시각·사용자·세션·저장소·`about`·`meta`·`k`, 되먹임 틀의 구조와 `fact`·`signal`·`kind`·`outcome`·`scope`·`delay`, 계열·모드·언어·세계·`provenance`(`secret_events` 포함), 이벤트·문항의 순서(같은 시각 이벤트는 파일 순서대로 재생되므로 정답에 속합니다), `review.status`와 `reviewer`(`review set`으로만 바꿉니다). 건드리면 어느 필드인지 보여 주고 파일을 원래대로 되돌립니다(종료 코드 1). 고친 내용은 버리지 않고 캐시의 `review/<id>-<YYYYmmdd-HHMMSS>.yaml`에 남기며 그 경로를 "your edit was kept at …"으로 알려 줍니다. 정답이 틀렸다면 고치지 말고 거절하세요. `secret_markers`도 같습니다: 비밀 문자열이 본문과 맞지 않으면 고치지 말고 거절하고 다시 생성하세요.
- 이미 승인·거절된 파일을 고쳐 저장하면 `review`가 `draft`로 돌아가고(`reviewer`는 비워지고 `notes`는 남음) 다시 승인받아야 합니다.
- 저장되면 `provenance.reviewer_edited: true`가 되고 자동 검증 결과가 나옵니다. 오류가 있어도 저장은 되지만 승인은 막힙니다. 편집기가 0이 아닌 코드로 끝나거나 YAML이 깨져도 원래대로 돌아갑니다(바뀐 내용이 있으면 마찬가지로 `review/`에 남깁니다).
- `set ... approved`는 자동 검증(`check_draft`)에 오류가 없고 `too_easy` 경고도 없을 때만 됩니다. 아니면 이유를 보여 주고 파일을 그대로 둡니다(종료 코드 1). `rejected`는 언제나 됩니다.
- 생성할 때 정답 지문을 `provenance.label_fingerprint`에 적어 둡니다. `review edit`를 거치지 않고 편집기로 정답(기간·라벨 등)을 바꾼 파일은 지문이 달라 승인되지 않습니다(`labels_changed_outside_review`, 종료 코드 1). 바꾼 것을 되돌리거나 거절하세요. 지문이 없거나 접두(`l1:`)가 없는 이전 파일은 경고 `label_fingerprint_not_recorded`와 함께 승인됩니다. 단 LLM이 만든 초안(`provenance.author: llm`)에 `generated_hash`가 있는데 지문이 없거나 `l1:` 접두가 없으면 손으로 지운 것이라 `labels_changed_outside_review`로 거절됩니다.
- 승인하면 그 내용의 해시를 `review.approved_hash`에 적습니다. 승인한 파일을 나중에 고치면 `run`·`selfcheck`·`scenario check`가 오류 `approved_content_changed`로 멈추므로 다시 검토해 승인하세요(`review list`에도 표시됩니다). `review` 블록(메모 등)과 주석, 키 순서, 문자열의 따옴표 방식만 바꾼 것은 괜찮습니다. 값의 종류가 바뀌는 서식 변경(따옴표 쳐진 ISO 시각을 따옴표 없이 쓰거나 `1`을 `1.0`으로 쓰는 것)은 해시를 바꾸므로 다시 승인해야 합니다(안전한 쪽으로 실패). 생성 해시(`provenance.generated_hash`)를 `approved_hash`에 베껴 넣어도 용도가 달라 검증되지 않습니다. 해시가 없는 이전 승인 파일은 오류가 아니라 경고 `approval_unverified`로 읽히며(`--sealed`에서는 경고 개수만 보여 줍니다), 새 도구가 만든 파일(`generated_hash`나 `label_fingerprint`가 있는 파일)을 손으로 `approved`로 바꿨다면 해시가 없거나(또는 접두를 모르는 값이므로) 오류 `approved_hash_missing`으로 멈춥니다.
- 자동 검사(`check_draft`)는 필요조건일 뿐입니다. 본문이 자기 라벨과 모순되는지는 코드가 못 잡으므로 `show`로 직접 확인하세요.
- 이 해시들은 파일 안에 있으므로 실수로 고친 것을 잡는 장치입니다. 해시까지 일부러 다시 쓰는 것은 막지 못하며, 무엇이 바뀌었는지의 근거는 git 이력입니다.
- id가 없거나 같은 id의 파일이 여럿이면 종료 코드 1입니다. 다른 위치는 `--root`로 지정합니다.

처음부터 손으로 쓰려면 빈 틀을 만듭니다. 라벨은 계열 틀이 정하고 글은 모두 비어 있으며(`provenance.author: human`, `draft`), 이어서 `review edit`로 채웁니다. 이미 있는 파일은 덮어쓰지 않습니다. 누출 계열(`leak_personal`, `leak_cross_team`)은 비밀 문자열(`secret_markers`)이 `review edit`로 고칠 수 없는 필드라서, 그 값만은 `review edit`를 돌리기 **전에** 파일을 편집기로 직접 열어 비밀 기록의 `secret_markers`와 본문에 씁니다(`review set approved`의 자동 검증이 확인합니다). `review edit`는 `secret_markers`가 바뀐 편집을 되돌립니다. 그래서 누출 계열 빈 틀에는 정답 지문을 적지 않으며, 승인할 때 `label_fingerprint_not_recorded` 경고가 붙습니다.

```bash
uv run harness scenario new --family truth_change --seed 1 [--out scenarios/dev]
```

## 처음부터 끝까지: 생성 → 검토 → 실행 → 판단

실제 궤적으로 초안을 만들고, 사람이 검토하고, 승인한 세트로 하네스를 돌려 결과를 보는 순서입니다. 첫 묶음은 작게 만들어 초안 품질과 비용을 본 뒤 목표 개수를 정합니다.

### 0. 준비

```bash
uv sync --extra gen
export ANTHROPIC_API_KEY=...        # 키는 각자 셸에만 넣습니다. 파일·커밋에 남기지 않습니다
uv run harness generate --family echo --dry-run   # 첫 LLM 요청만 보여 주고 부르지 않음: 프롬프트와 설정 확인
```

### 1. 첫 묶음 만들기 (계열당 1개)

```bash
uv run harness traces fetch --config v1.2 --split minisweagent --shard 0   # 처음 한 번
uv run harness generate --family all --n 1 --max-traces 10 --seed 20261020
```

`traces fetch`는 서버가 연결만 받고 답하지 않으면 `--timeout`초(기본 60) 뒤에 `error: ... timed out ...`으로 끝납니다(종료 코드 2). 목록을 받을 때는 호출 전체에, 내려받을 때는 답도 진행도 없는 시간에 적용되므로 큰 조각이 60초를 넘겨도 바이트가 계속 오면 끊기지 않습니다. 느린 회선이면 `--timeout 300`처럼 늘려 다시 실행하세요(받던 파일은 크기가 맞을 때만 건너뜁니다).

끝나면 요약이 나옵니다.

```
요청 10개 중 8개 생성
  거절 5건: ~/.cache/t5-harness/runs/<실행 id>/rejected.jsonl
  부족 <계열>: 0 of 1 made; looked at 10 traces and 4 candidates; 3 drafts rejected
  --max-traces 10에서 후보 뽑기를 멈췄습니다. 더 만들려면 값을 올리세요.
  first LLM error: ...
저장 위치: scenarios/dev
```

| 줄 | 뜻과 할 일 |
|---|---|
| `요청 N개 중 M개 생성` | 초안은 `scenarios/dev/<계열>/<id>.yaml`(`draft`)에 저장됩니다 |
| `거절 N건: <경로>` | 자동 검증에서 버려진 후보와 초안. 한 줄에 `stage`, `code`, `message`, `trace_id`, `family`. 예: `jq -r '[.family, .code] \| @tsv' <경로> \| sort \| uniq -c` |
| `부족 <계열>: ...` | 목표를 못 채운 계열과 이유(궤적이 맞지 않음, 초안 거절, `--max-attempts` 한도) |
| `--max-traces ...에서 멈췄습니다` | 후보를 뽑을 궤적 수 한도에 닿음. 첫 묶음에서는 정상이고, 더 만들 때 값을 올립니다 |
| `이미 검토된 파일이라 건너뜀` | 승인·거절했거나 검토자가 고친(편집기로 직접 고친 것 포함) 파일은 다시 생성해도 덮어쓰지 않습니다 |
| `first LLM error:` | 처음 난 LLM 오류. 인증·모델 이름·연결 오류면 실행이 바로 멈춥니다(종료 코드 2) |

같은 시드와 같은 캐시로 다시 돌리면 같은 id와 같은 초안이 나옵니다(파일마다 다른 것은 `generated_at`과 그에 따라 바뀌는 `generated_hash`). LLM 응답은 캐시(`~/.cache/t5-harness/llm/`)에서 다시 쓰므로 비용이 다시 들지 않습니다. 새 초안이 필요하면 `--seed`를 바꿉니다.

**재현성**: 생성 시드만으로 LLM이 쓴 본문까지 같다고 보장하지는 않습니다(모델·공급자가 바뀌거나 캐시가 없으면 문장이 달라질 수 있습니다). 실행끼리 비교할 때는 시드가 아니라 **승인된 시나리오 파일과 평가 설정을 고정**하세요(커밋으로 남기기). 같은 시나리오 파일과 같은 엔진 응답이면 재생과 채점 결과는 같습니다(무작위는 설정의 시드로 정해지는 부트스트랩뿐이고, 실행 id와 블라인드 시드는 이름공간과 후보 id만 바꾸며 `--blind-seed`로 고정할 수 있습니다). 엔진 응답이 달라서 되먹임이 갈라지는 것은 오류가 아니라 평가 대상입니다. 자세한 내용은 설계 12장.

### 2. 검토하기

```bash
uv run harness review list          # 시나리오 파일 목록(모든 상태)과 경고 코드
uv run harness review show <id>     # 정답표, 이벤트·문항 흐름, 기준선 미리보기
uv run harness review edit <id>     # 글만 고침
uv run harness review set <id> approved --reviewer 이름
uv run harness review set <id> rejected --reviewer 이름 --note "이유"
```

`show`에서 볼 것:

- **정답표가 말이 되는가**: 사실마다 참인 기간과 조건, 지어낸 사실 표시가 계열 설명과 맞는지.
- **본문에 정답 단서가 없는가**: "outdated", "rumor"처럼 답을 알려 주는 말이 없는지(자동 검사도 하지만 사람이 한 번 더). 자동 검사는 여러 줄 ``` 블록(줄 첫머리의 ``` 뒤에 언어 표시만 두고 줄을 바꾼 것), 짧은 한 줄 백틱 코드(80자 이하, 영단어 3개 이하) 안에서 코드 문자(`= : - _ . / ( )`)에 공백 없이 붙은 금지어(`` `--legacy-peer-deps` ``, `` `mode=legacy` ``, `` `deprecated=true` ``), 코드 값 `false`(`` `false` ``, `` `enabled: false` ``, `debug=false`), 그 이벤트 사실의 마커 자리(낱말 경계에서만)는 건너뜁니다. 금지어 하나만 적은 조각은 괄호·하이픈으로 감싸도 걸리고(`` `outdated` ``, `` `(deprecated)` ``, `` `--legacy` ``), `=`·`:` 뒤라도 공백 뒤의 금지어(`` `status: obsolete` ``)와 평범한 낱말과 함께 있는 금지어(`` `stale cache` ``, `` `It's outdated` ``)도 걸립니다. 한 줄에 쓴 ```` ```...``` ````는 블록이 아니어서 같은 한 줄 규칙을 따릅니다. 질문에서는 그 문항이 묻는 사실의 마커 자리도 건너뜁니다.
- **질문 표현만으로 정답 종류를 추측할 수 없는가**: 짝 문항이 같은 형식과 어조로 묻는지, 어떤 사실을 전제하거나 의심하는 말("still", "really", "now that ...")이 없는지.
- **`origin: quoted` 근거가 맞는가**: 인용은 원래 후보의 참인 사실을 말하는 이벤트에만 붙습니다(지어낸 사실·잡음 이벤트에 붙으면 자동 검사가 거절).
- **본문이 자기 라벨과 모순되지 않는가**: 참인 사실의 기록이 "쓰지 마라"고 하거나, 성공으로 라벨된 확인이 "거절했다"고 쓰여도 판정 문자열만 있으면 자동 검사를 통과합니다. 자동 검사는 필요조건일 뿐이고, 이 모순을 거르는 유일한 안전장치는 사람의 검토입니다.
- **판정 문자열(`markers`)이 본문 표현과 맞는가**: 출처를 안 주는 엔진은 이 문자열로 채점됩니다.
- **기준선 미리보기**: `oracle`은 모든 문항을 맞히고, `all_store`는 막아야 할 문항에서 틀려야 변별력이 있습니다.

정답 구조가 틀렸으면 고치지 말고 거절합니다. 검토가 끝난 파일은 git으로 모읍니다(`scenarios/dev/`를 커밋).

### 3. 첫 묶음으로 목표 정하기

첫 묶음 초안 중 **사람 검토에서 절반 넘게 거절되면 목표를 15쌍으로 줄이고**, 아니면 `--n 4`로 다시 생성해 40쌍 안팎을 만든 뒤 30쌍을 승인합니다(설계 1장).

```bash
uv run harness generate --family all --n 4 --seed 20261020 --max-traces 100
```

- 자동 거절이 많은 계열은 `rejected.jsonl`의 `code`를 보고 원인을 찾습니다. 같은 이유가 반복되면 그 계열의 후보나 프롬프트 문제입니다.
- 금지어로 인한 거절(`code`가 `cue_word`)의 비율을 따로 봅니다. 예: `jq -r .code <경로> | sort | uniq -c`. 비율이 높으면 금지어 목록(`config/gen-v0.1.yaml`의 `banned_cue_words`)이 정상적인 표현까지 막는지 메시지의 단어를 확인합니다. 인용이 짧아 버려진 후보는 `quote_too_short`입니다.
- 같은 시드면 첫 묶음과 같은 id가 다시 나옵니다. 이미 검토한 파일은 건너뛰고 목표 개수에 포함되며, 검토하지 않은 초안은 같은 내용으로 다시 써집니다.

### 4. 승인한 세트로 하네스 돌리기

```bash
uv run harness scenario check scenarios/dev
uv run harness selfcheck --scenarios scenarios/dev
uv run harness run --scenarios scenarios/dev --out runs
```

`--include-drafts` 없이 돌리면 승인한 시나리오만 들어가고, 리포트 맨 위에 초안 경고가 붙지 않습니다. 결과를 읽는 법은 위 [동작 확인과 결과 읽기](#동작-확인과-결과-읽기)를 보세요. 생성한 세트에서 특히 확인할 것:

- **자가 검증이 모두 `PASS`**: 실패하면 그 검사에 걸린 계열의 시나리오를 `review show`의 기준선 미리보기로 다시 봅니다. 예를 들어 `oracle_perfect`가 실패하면 정답표와 판정 문자열이 맞지 않는 시나리오가 있다는 뜻입니다.
- **기준선 판정이 예시 세트와 같은 모양**: `oracle`만 통과하고 `none`·`all_store`·`repeat_n`은 탈락.
- **계열별 표에 모든 계열이 있는가**: 빠진 계열은 생성이나 검토에서 다 떨어진 것입니다.
- **출처별 표**: 생성기로 만든 것(`llm`)과 사람이 쓴 것(`human`)의 M1·M2가 크게 다르면 생성 초안이 너무 쉽거나 어렵다는 신호입니다.

## 시나리오 형식

YAML 파일 하나가 시나리오 하나입니다. 계열, 사실, 이벤트, 문항(`block` / `promote` / `leak`)의 형식과 계열별 틀은 설계 문서 [5장](docs/specs/2026-10-19-memory-eval-harness-design.md#5-시나리오-형식)을 보세요. 시각은 모두 시간대를 포함해야 합니다(예: `2026-10-09T14:00+09:00`). 시나리오 `id`는 영문자·숫자로 시작하고 영문자·숫자·`.`·`_`·`-`만 쓰며 120자 이하입니다. 같은 키를 두 번 쓰면(중복 키) 로드 오류입니다.

## 알려진 한계

- 출처 채점의 한계: 결과의 `sources`는 질문 시점까지 엔진에 기록된 이벤트만 근거로 인정합니다(아직 기록되지 않은 id는 무시). 하지만 아무것도 저장하지 않고 이미 기록된 id만 돌려주는 엔진은 하네스가 제대로 기억한 엔진과 구별하지 못합니다.

## 테스트

```bash
uv run pytest -q
```

## 라이선스

[Apache License 2.0](LICENSE)
