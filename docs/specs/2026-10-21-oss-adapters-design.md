# 오픈소스 어댑터 설계: Hindsight, Mem0 (계획 3)

- 작성일: 2026-10-21
- 기준 문서: [하네스 v0.1 구현 설계](2026-10-19-memory-eval-harness-design.md)(이하 "설계")의 4.4(본문 형식), 10장(오픈소스 어댑터), 15장(공정성 규칙). 이 문서는 10장을 구현 수준으로 구체화한 것이고, 둘이 다르면 이 문서를 따른다.
- 표기: **[결정]** 허진수가 정한 것 · **[설계]** 설계 문서에 이미 있는 것 · **[확인]** 2026-10-21에 각 프로젝트의 소스 코드에서 확인한 것 · **[판단]** 작성자(Claude)가 정한 기본값

---

## 1. 의도

10/18 회의에서 "구축한 하네스로 상용 오픈소스 도구를 테스트하고, 결과를 발표 자료에 간단한 표로 넣는다"고 정했다. 재혁 님은 도구를 1인당 1~2개씩 나눠 중복을 피하자고 제안했다. 회의록에는 도구 이름이 없다. 후보 6종(Mem0, Graphiti, Letta, Hindsight, claude-mem, Cognee)은 설계 1장의 제안이다.

이 계획은 두 엔진을 Docker로 띄우고 하네스 계약(`BaseEngine`)에 붙이는 어댑터를 만든다. 10/25 측정에서 기준선 4종과 같은 시나리오로 돌려 10/29 발표의 비교표에 쓴다.

- **Hindsight**: 방식이 T5와 가장 가깝다(기억을 쌓고 합쳐 관찰로 올림). 10/14 회의에서 한계로 지적한 출처 독립성 부재와 사람 검토 부재를 수치로 보여 줄 대상이다. 기록 시각과 질문 시각을 받으므로 시간 계열 비교도 맡는다.
- **Mem0**: 가장 대표적인 비교 대상이다. 노션 "AI 메모리 엔진 사용하면서 불편했던 점"에 "검색한 기억이 다시 저장돼 부풀려짐"(Mem0 이슈 #4573)이 있는데, 이것이 `echo` 계열이 재는 현상이다.

**Graphiti를 뺀 이유** [결정, 10/21]: 설계 10장은 Hindsight와 Graphiti였다. Graphiti는 Neo4j가 따로 필요하다. REST 서버의 처리 큐는 에피소드 하나가 실패하면 멈추고, 처리 완료 확인 API는 실제 현재 시각 이전 기록만 보여 줘서 가상 시각과 맞지 않는다. 임베딩과 재정렬 모델도 OpenAI로 고정되어 있다. 10/25까지의 위험이 커서 Mem0로 바꾼다. 사실별 유효 기간을 가진 유일한 후보라는 장점은 잃는다. 나중에 팀원 분담으로 다시 붙일 수 있다.

## 2. 목표 · 비목표 · 반목표

**목표**
1. 두 엔진의 Docker 실행 설정. 버전을 고정하고 `127.0.0.1`에만 연다.
2. `harness run --engine hindsight` / `--engine mem0`으로 돌리는 어댑터. 하네스에 새 의존성을 넣지 않는다(표준 라이브러리 HTTP).
3. 두 엔진 모두 **출처 채점**: 결과마다 근거 이벤트 id를 `Result.sources`로 돌려준다. 설계 10장은 Hindsight가, 4.5는 Mem0가 출처를 주지 않을 수 있다고 했다. 실제로는 Hindsight는 `document_id`로, Mem0는 기록 메타데이터로 받을 수 있다. [확인]
4. 컨테이너 없이 끝까지 테스트하는 가짜 HTTP 서버.
5. 키가 있으면 한 번에 돌려 보는 실제 연결 점검(선택 실행).

**비목표**
- Graphiti, Letta, Cognee, claude-mem 어댑터. 팀원 분담으로 넘긴다. [설계 14장 6번] 대신 공통 부분(본문 형식, HTTP 도우미, 가짜 서버, 이 문서의 순서)을 그대로 쓸 수 있게 만든다.
- 엔진 설정 튜닝. 각 도구의 기본 설정으로 평가한다. [설계 15장 2번]
- Upstage나 로컬 LLM으로 측정하는 것. 모델 주소와 이름은 환경 변수로 열어 두기만 한다. [결정]
- 컨테이너를 하네스가 자동으로 띄우고 내리는 것. 사람이 `docker compose`로 띄운다. [판단]

**반목표 (돌아가도 실패인 것)**
1. **숨은 라벨이 엔진에 새는 것.** 본문, 태그, 메타데이터, 문서 id 어디에도 `fact`, `secret_markers`, 문항 종류, 정답이 들어가지 않는다.
2. **엔진의 판단을 하네스가 대신하는 것.** 결과를 다시 거르거나 순위를 바꾸지 않는다. 결과를 `k`개로 자르는 것만 한다.
3. **사용자의 실제 메모리에 연결되는 것.** 어댑터는 `localhost`가 아닌 주소와 포트 37701, 37777을 거부한다. [설계 10장]
4. **엔진마다 다른 입력.** 두 엔진과 이후 오픈소스 어댑터는 같은 형식 함수로 만든 본문을 받는다. [설계 15장 1번]

## 3. 모델 [결정, 10/21]

- **OpenAI로 측정한다.** 두 엔진이 원래 맞춰진 환경이라 10/25까지 가장 안전하다.
- **같은 모델로 맞춘다** [설계 15장 3번]. 엔진 기본값이 서로 다르므로(Hindsight LLM `gpt-4o-mini`, Mem0 LLM `gpt-5-mini`) 하나로 맞춘다. [확인]

  | 용도 | Hindsight | Mem0 | 비고 |
  |---|---|---|---|
  | LLM | `gpt-4.1-mini`로 설정 | `gpt-4.1-mini`로 설정 | [판단] 추론 모델이 아니라서 두 엔진이 각자 정한 온도 설정(Mem0는 0.2)을 그대로 받는다 |
  | 임베딩 | `text-embedding-3-small`로 설정(기본은 로컬 `bge-small`) | `text-embedding-3-small`(기본값) | |
  | 재정렬 | 기본값(컨테이너 안 로컬 cross-encoder) | 없음(기본값) | 맞출 수 없음. 리포트에 적는다 |

- 첫 실행에서 `gpt-4.1-mini`를 쓸 수 없으면 두 엔진 모두 같은 다른 모델로 바꾸고 설정 버전을 올린다.
- 하네스 설정 `config/eval-v0.1.yaml`의 `llm`을 `provider: openai`, `model: gpt-4.1-mini`, `embedding_model: text-embedding-3-small`로 채우고 `version`을 `0.2`로 올린다. 사전 등록 규칙(설계 15장 7번)에 따라 측정 전에 커밋한다.
- 컨테이너는 `OPENAI_API_KEY`, `HARNESS_LLM_MODEL`, `HARNESS_EMBEDDING_MODEL` 환경 변수로 모델을 받는다(compose 파일에서 값이 없으면 시작하지 않음). 어댑터는 같은 환경 변수를 읽어 하네스 설정의 `llm`과 다르면 실행 전에 멈춘다. 키 값은 어디에도 기록하지 않는다. [판단]
- 모델 주소도 `OPENAI_BASE_URL` 하나로 열어 둔다(비우면 OpenAI 기본 주소). Mem0는 이 변수를 LLM과 임베딩 모두에서 직접 읽고, Hindsight에는 compose 파일이 `HINDSIGHT_API_LLM_BASE_URL`과 `HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL`로 넘긴다. [확인] Upstage나 로컬 LLM으로 다시 돌릴 때 코드를 바꾸지 않기 위해서다.

## 4. 실행 환경

| | Hindsight | Mem0 |
|---|---|---|
| 이미지 | `ghcr.io/vectorize-io/hindsight:1.0.0` (DB 내장) | 우리 compose의 `dockerfile_inline`으로 빌드(`mem0ai==2.2.1`, 서버 코드는 `v2.2.1` 태그) + `pgvector/pgvector:0.8.7-pg17` |
| 포트 | `127.0.0.1:8888` | `127.0.0.1:8890` → 컨테이너 8000 (Hindsight와 함께 띄울 수 있게) |
| 위치 | `engines/docker/hindsight/compose.yaml` | `engines/docker/mem0/compose.yaml`(빌드 정의는 `dockerfile_inline`), `engines/docker/mem0/init-db.sh` |
| 데이터 | 이름 붙은 볼륨. 측정이 끝나면 `docker compose down -v` | 같음 |

- **두 엔진을 함께 띄운다** [판단, 설계 표의 "한 번에 하나씩" 변경]. 한 번의 `harness run`에 두 엔진을 넣어야 리포트 하나에 블라인드 id(E1, E2)와 엔진 간 차이의 신뢰구간이 같이 나온다. 따로 돌리면 리포트가 둘로 갈라지고 둘 다 E1이 되며, 합치는 명령이 없다. "하나씩"은 Neo4j가 필요한 Graphiti를 전제로 한 판단이었고, Mem0는 Postgres 하나라 가볍다. 실제 연결 점검에서 메모리 사용량을 재고, 16GB에 안 들어가면 따로 돌린 뒤 각 엔진을 기준선하고만 비교한다.
- **Mem0를 직접 빌드하는 이유** [확인]: Docker Hub의 `mem0/mem0-api-server`는 2025-09 이후 갱신되지 않았다. 저장소의 `server/Dockerfile`은 `mem0ai>=0.1.48`을 설치해서 빌드할 때마다 버전이 달라진다. 우리 빌드 정의(`dockerfile_inline`)는 `v2.2.1` 태그의 `server/` 코드를 받아 `mem0ai==2.2.1`로 고정한다. Mem0 코드는 고치지 않는다.
  - 시작 명령은 `alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000`이다. 개발용 compose의 명령은 가져오지 않는다. 그 명령은 버전 없이 `mem0ai`를 다시 설치해 고정을 무너뜨리고, `--reload`도 켠다.
  - 인증·요청 기록용 DB가 따로 필요하다. Postgres 컨테이너에 `v2.2.1`의 `server/init-db.sh`(`mem0_app` DB 생성)를 넣고, 서버에 `APP_DB_NAME=mem0_app`을 준다. 기억은 기본 `postgres` DB의 pgvector에 저장된다.
- **Mem0 로컬 설정**: `AUTH_DISABLED=true`(기본은 인증이 켜져 있어 401이 난다. `127.0.0.1`에만 열므로 괜찮다), `MEM0_TELEMETRY=false`(기본은 사용 통계를 외부로 보낸다), `MEM0_DEFAULT_LLM_MODEL=${HARNESS_LLM_MODEL}`, `MEM0_DEFAULT_EMBEDDER_MODEL=${HARNESS_EMBEDDING_MODEL}`. [확인]
- **Hindsight 설정**: `HINDSIGHT_API_LLM_PROVIDER=openai`, `HINDSIGHT_API_LLM_MODEL`, `HINDSIGHT_API_LLM_API_KEY`, `HINDSIGHT_API_EMBEDDINGS_PROVIDER=openai`, `HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL`, `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY`. 사용 통계 설정은 없다. [확인]
- arm64 이미지가 있는지는 첫 실행에서 확인한다. 없으면 `platform: linux/amd64`로 돌리고 속도 저하를 리포트에 적는다.
- 어댑터가 쓰는 주소: `HARNESS_HINDSIGHT_URL`(기본 `http://127.0.0.1:8888`), `HARNESS_MEM0_URL`(기본 `http://127.0.0.1:8890`).

## 5. 공통 부분

### 5.1 본문 형식 (`harness/oss/text.py`) [설계 4.4 확장]

모든 오픈소스 어댑터가 이 함수로 이벤트 본문과 질문을 만든다.

```
[shop-api] u3: Fixed the vendor module bug (tests passed)
[shop-api] {env=prod, service=billing} u2: Use the replica for reads
```

- 앞에 `[repo]`, 이벤트 `meta`가 있으면 `{k=v, ...}`(키 순서대로), 작성자 `user:`, 본문, 뒤에 신호·결과 문구.
- 설계 4.4는 신호·결과·저장소만 적었다. **`meta`와 작성자를 더한다** [판단]. `meta`가 없으면 `conditional` 계열의 조건을 오픈소스가 알 길이 없어 불공정하고, 작성자가 없으면 "서로 다른 사람이 확인했다"는 정보가 사라진다. 둘 다 엔진에게 보이는 관찰 정보이고 숨은 라벨이 아니다.
- 신호·결과 문구(영어, 고정):

  | signal | success | failure | unknown |
  |---|---|---|---|
  | `test` | (tests passed) | (tests failed) | (tests ran) |
  | `review` | (review approved) | (review rejected) | (reviewed) |
  | `human` | (confirmed by a person) | (rejected by a person) | (noted by a person) |
  | `doc` | (from docs) | (from docs) | (from docs) |
  | `self` | 없음 | 없음 | 없음 |

  kind가 `commit`이면 위 문구 앞에 `(commit)`을 붙인다.
- `refs`는 본문에 넣지 않는다 [판단]. 이벤트 id는 오픈소스에게 의미가 없고, 판정 기록 본문 자체가 무엇을 판정하는지 문장으로 말한다.
- 질문은 `[repo] {meta} 질문` 형식이다(작성자 없음). 기준선은 구조화된 값을 그대로 쓰므로 영향이 없다.

### 5.2 HTTP 도우미 (`harness/oss/http.py`)

- 표준 라이브러리(`urllib.request`, `json`)만 쓴다.
- 요청마다 시간 제한 120초.
- **일시적 오류는 제한적으로 다시 시도한다** [판단]. 429, 502, 503, 504, 연결 실패, 시간 초과는 2초, 8초, 30초를 기다리며 최대 세 번 다시 보낸다. 오류가 난 시나리오가 하나라도 있으면 그 엔진은 판정을 통과할 수 없는데(`BaseEngine` 규칙), 기록 약 1,000건 × 3회 중 OpenAI 쪽 일시 오류 한 번으로 판정이 무너지면 엔진의 능력이 아니라 운을 재게 된다. 다시 시도한 횟수는 엔진 `info().config`에 남기지 않고 실행 로그에 적는다. 같은 기록을 다시 보내도 두 엔진 모두 중복으로 쌓이지 않는다(Hindsight는 같은 `document_id`를 교체, Mem0는 같은 문장을 해시로 건너뜀). [확인]
- 그 밖의 4xx·5xx, 또는 다시 시도해도 실패하면 `EngineHTTPError`(메서드, 경로, 상태 코드, 응답 앞 500자)를 올린다. 시나리오는 오류로 끝나고 실행 전체는 계속된다(기존 동작).
- 주소 검사: 호스트가 `localhost`, `127.0.0.1`, `[::1]`이 아니거나 포트가 37701, 37777이면 어댑터를 만들 때 `ValueError`.

### 5.3 이름공간

재생기는 `<run_id>-r<회차>:<sha256(시나리오 id) 앞 12자리>`를 준다(계획 2에서 바꿈). 시나리오 id에는 계열 이름이 들어 있어(`echo-...`, `leak_personal-...`) 그대로 쓰면 무엇을 시험하는지가 엔진에 보이기 때문이다(반목표 1).

어댑터는 이 이름공간을 **한 번 더 해시해** 엔진 쪽 id로 쓴다 [판단]: `t5-` + `sha256(이름공간)`의 앞 24자리. 목적은 문자 제한이다. 이름공간의 `:`를 피하고 영문 소문자, 숫자, `-`만 남겨 두 엔진의 URL 경로와 필터 값에 그대로 넣을 수 있게 한다. 재생기가 다른 형식의 이름공간을 주게 되어도 엔진 쪽 id에 시나리오 정보가 드러나지 않는다.

### 5.4 가짜 서버 (`tests/oss_fakes.py`)

- 각 엔진의 엔드포인트를 흉내 내는 `http.server` 기반 서버를 스레드로 띄운다. 받은 요청 본문을 모두 기록하고, 검색은 저장한 본문을 단순 단어 겹침으로 돌려준다.
- 테스트는 요청 내용(본문 형식, 태그·필터, 시각, id)과 결과 변환(`sources`, `k` 자르기)을 확인한다. 실제 엔진 품질은 재지 않는다.

## 6. Hindsight 어댑터

| 하네스 | Hindsight API [확인, v1.0.0 `api/http.py`] |
|---|---|
| `reset(ns)` | `DELETE /v1/default/banks/{bank}` (없으면 404를 무시). 은행은 첫 저장 때 생긴다 |
| `record(e)` | `POST /v1/default/banks/{bank}/memories`, `items=[{content: 형식 본문, timestamp: e.t, document_id: e.id, tags: [범위 태그]}]`, 동기 처리(`async: false`, 기본값) |
| `flush()` | `GET .../operations?status=pending`과 `status=processing`이 둘 다 비면 끝. 0.5초 간격, 최대 600초. 끝난 뒤 `status=failed`가 이 시나리오에서 하나라도 생겼으면 오류로 올린다 |
| `search(q)` | `POST .../memories/recall`, `query: 형식 질문`, `query_timestamp: q.t`, `tags: [user:<q.user>, team:<q.team>]`, `tags_match: any_strict`, `include.source_facts: {}` |

- **범위 대응** [판단, 설계 10장 변경]: 설계는 개인·팀 단위마다 은행을 따로 두고 검색 결과를 합치자고 했다. Hindsight는 태그 필터를 기본 기능으로 가지므로, 시나리오당 은행 하나에 개인 기록은 `user:<u>`, 팀 기록은 `team:<t>` 태그를 붙이고 한 번의 recall로 찾는다. 두 은행의 순위를 하네스가 합치는 어색함이 없어진다. 엔진이 원래 가진 가시성 기능이고, 리포트의 엔진 한계 표에 대응 방식을 적는다.
- **결과 변환**
  - `text` → `Result.text`. `meta`에 `{id, type}`.
  - 사실(`world`, `experience`)은 `document_id`가 이 시나리오의 이벤트 id이면 그것을 `sources`로.
  - 관찰(`observation`)은 `source_fact_ids`를 응답의 `source_facts` 표(id → 사실)에서 찾아 그 사실들의 `document_id`를 모은다. 표에 없는 id는 건너뛴다. 하나도 못 찾으면 그 결과의 `sources`는 비고 판정 문자열로 채점된다. [확인: `RecallResponse.source_facts`]
  - 반환 순서를 그대로 두고 앞 `q.k`개만 쓴다.
- **실패한 백그라운드 작업** [판단]: Hindsight는 관찰을 만드는 백그라운드 작업이 실패해도 저장 응답은 성공으로 준다. 그 실패는 `failed` 작업으로만 남는다. [확인] 그대로 두면 관찰이 조용히 빠진 채 채점되므로, flush에서 확인해 시나리오 오류로 만든다.
- 기본 설정(`observation_scopes` 없음 = combined)에서 관찰은 태그 묶음이 같은 사실끼리만 만들어진다. 그래서 `user:` 사실과 `team:` 사실이 한 관찰로 섞이지 않는다. [확인]
- **가상 시각**: 저장 시각(`timestamp`)과 질문 시각(`query_timestamp`)을 요청에 직접 싣는다. 그래서 `info().virtual_clock=True`로 두고 `tick()`은 아무것도 하지 않는다 [판단]. 리포트는 `virtual_clock=False`인 엔진에 "가상 시계 미지원" 주석을 다는데, 가상 시각을 받는 Hindsight에 그 주석이 붙으면 틀린 표시가 된다.
- **모델 확인**: 실행 중인 엔진의 모델 설정을 돌려주는 API가 있으면 그 값을, 없으면 환경 변수를 하네스 설정과 비교한다. 구현할 때 확인한다.
- `info()`: `name="hindsight"`, `version="1.0.0"`, `config`에 모델 이름, 범위 대응 방식, 주소. `sources=True`, `scopes=True`, `virtual_clock=True`.

## 7. Mem0 어댑터

| 하네스 | Mem0 REST API [확인, v2.2.1 `server/main.py`] |
|---|---|
| `reset(ns)` | `DELETE /memories?run_id=<ns>` |
| `record(e)` | `POST /memories`, `messages=[{role: "user", content: 형식 본문}]`, `run_id: <ns>`, `user_id: <범위 단위>`, `metadata: {harness_event: e.id}`. 동기 처리. 기억 추출은 기본값(`infer` 기본 true) |
| `flush()` | 없음(저장이 응답 전에 끝남) |
| `search(q)` | `POST /search`, `query: 형식 질문`, `filters: {run_id: <ns>, OR: [{user_id: <질문자 개인 단위>}, {user_id: <질문자 팀 단위>}]}`, `top_k: q.k` |

- **범위 대응** [판단]: Mem0의 격리 단위는 `user_id`, `agent_id`, `run_id`다. 시나리오는 `run_id`(5.3의 이름공간)로 나누고, 개인 기록은 `user_id=user:<u>`, 팀 기록은 `user_id=team:<t>`로 넣는다. 검색은 OR 필터로 두 단위를 한 번에 찾는다. 필터의 OR는 pgvector에서 지원되고, 검색 필터에는 세 id 중 하나가 꼭 있어야 하므로 `run_id`를 같이 넣는다. [확인: `Memory.search`, `pgvector._build_filter_conditions`]
  - 실제 연결 점검에서 OR 필터가 기대대로 동작하지 않으면, 단위별로 두 번 검색해 Mem0가 준 점수 순으로 합친다. 이 경우 리포트에 적는다.
  - 기억을 뽑을 때 Mem0는 같은 단위(`run_id`와 `user_id` 조합)의 최근 메시지 10개를 맥락으로 쓴다. 단위를 위처럼 나누면 개인 기록이 팀 기억의 맥락으로 섞이지 않는다. [확인: `_build_session_scope`]
- **출처** [확인]: v2의 추출은 추가만 한다. 기존 기억을 고치거나 지우지 않고, 같은 문장은 해시로 건너뛴다. 기록에 준 `metadata`가 뽑힌 기억마다 복사되므로, 검색 결과의 `metadata.harness_event`가 이 시나리오의 이벤트 id이면 `sources`에 넣는다. 같은 문장이 다시 들어와 건너뛰어지면 그 이벤트는 출처에 나타나지 않는다(엔진의 동작).
- **결과 변환**: `memory` → `text`, `meta`에 `{id, score}`. 반환 순서 그대로.
- **가상 시각 미지원** [확인]: 오픈소스판 `add`의 `timestamp`는 "유료 플랫폼 전용"이고 검색에도 기준 시각이 없다. 기억은 실제 시각으로 찍힌다. 기록은 가상 시각 순서대로 들어가므로 순서는 지켜지고, 질문 시점에는 그때까지의 기록만 있다. 잃는 것은 기록 사이의 간격(최근성)이다.
  - **채점은 바꾸지 않는다** [결정, 10/21]. 시간 계열(진실 변경, 우회책에서 근본 수정)에서도 다른 엔진과 똑같이 채점하고, 틀린 만큼 점수를 잃는다. 계열에서 빼거나 보정하지 않는다. 리포트의 주석은 해석을 돕는 표시일 뿐이다. [설계 15장 5번] 메타데이터로 생성 시각을 덮어쓰는 우회 방법이 있지만, 문서화된 기능이 아니라 쓰지 않는다. [판단]
- **모델 확인** [판단]: 어댑터를 만들 때 `GET /configure`로 실행 중인 Mem0의 LLM·임베딩 모델을 읽어 하네스 설정의 `llm`과 비교한다. 다르면 실행 전에 멈춘다. 환경 변수만 보면, 다른 값으로 띄운 컨테이너를 놓친다. [확인: `server/main.py` `/configure`]
- `info()`: `name="mem0"`, `version="2.2.1"`, `config`에 모델 이름, 범위 대응 방식, 주소. `sources=True`, `scopes=True`, `virtual_clock=False`(리포트가 "가상 시계 미지원" 주석을 단다).

## 8. 연결과 실행

- `make_engine`에 이름 `hindsight`, `mem0`을 더한다. 기준선이 아니므로 `--engine`으로만 돌고, 리포트에서는 블라인드 id(E1, E2)로 나온다. [설계 15장 8번]
- 반복: 기본 `llm_repeats: 3`. [설계 15장 6번]
- 측정 순서(README에 적음):
  1. `OPENAI_API_KEY`, `HARNESS_LLM_MODEL`, `HARNESS_EMBEDDING_MODEL`을 환경 변수로 넣는다(키는 본인이 직접).
  2. `docker compose -f engines/docker/hindsight/compose.yaml up -d`, `docker compose -f engines/docker/mem0/compose.yaml up -d --build`
  3. `uv run harness run --scenarios scenarios/dev --engine hindsight --engine mem0`
  4. 끝나면 두 compose 모두 `down -v`.
- 소요 예상 [판단]: 시나리오 40개 × 이벤트 약 8개 × 3회 = 기록 약 1,000건. 기록마다 LLM 호출이 몇 번씩 일어나고 동기 처리라 엔진당 1~3시간으로 본다. 첫 실제 점검에서 시나리오 1개 시간을 재고 README에 적는다.

## 9. 테스트

- 본문 형식: 표의 모든 칸, commit 접두어, meta 정렬, 작성자, 질문 형식, 숨은 라벨 미포함.
- HTTP 도우미: 주소 거부(외부 호스트, 37701, 37777), 일시적 오류 재시도(429 뒤 성공, 세 번 모두 실패), 재시도하지 않는 오류(400, 404), 시간 제한.
- 이름공간 해시: 시나리오 id(계열 이름)가 엔진 쪽 id에 나타나지 않음, 같은 입력은 같은 id.
- Hindsight 어댑터(가짜 서버): reset 대상, record 요청(태그, 시각, document_id), flush 대기·시간 초과·`failed` 작업 오류, search 요청(태그 필터, 질문 시각)과 결과 변환(document_id, 모르는 id 버림, 관찰의 원본 사실 표, `k` 자르기), `virtual_clock=True`.
- Mem0 어댑터(가짜 서버): reset 대상, record 요청(run_id, 범위 단위, 메타데이터), search 필터(OR, run_id)와 결과 변환(메타데이터 → sources, 모르는 id 버림, `k`), `/configure` 모델 불일치 시 멈춤.
- 모델 설정 검사: 환경 변수와 하네스 설정의 `llm`이 다르면 실행 전에 멈춤.
- 하네스 경로: 가짜 서버를 띄우고 `harness run --engine hindsight`와 `--engine mem0`이 예시 시나리오로 끝까지 돌아 리포트가 나오는지.
- 실제 연결 점검: `HARNESS_LIVE_OSS=1`일 때만 도는 테스트가 떠 있는 컨테이너에 예시 시나리오 하나를 돌려 오류 없이 결과가 나오는지 본다. 평소 테스트에서는 건너뛴다.

## 10. 일정

| 날짜 | 할 일 |
|---|---|
| 10/21 | 이 문서와 구현 계획 |
| 10/22 | 구현(가짜 서버까지). 같은 날 시나리오 첫 실제 생성 |
| 10/23~24 | 이미지 받기·빌드, 키로 실제 연결 점검(Mem0 OR 필터, 두 엔진 동시 메모리 사용량, Hindsight 모델 설정 API 포함), 시나리오 1개 시간 측정 |
| 10/25 | 기준선 4종 + 오픈소스 2종 측정 |

## 11. 열린 문제 (팀)

- 10/25 측정 담당이 문서마다 다르다. 10/18 회의록은 오픈소스 평가를 이선민 님 몫으로, 설계 17장은 재혁 님 측정으로 적었다. 컨셉 회의 페이지의 재혁 님 할 일은 "지금 방식 기준 점수 측정"이다.
- OpenAI 키 주인과 예산 상한(설계 14장 1번이 Upstage 기준으로 열려 있음).
- 나머지 4종(Graphiti, Letta, Cognee, claude-mem)의 분담(설계 14장 6번).

## 12. 구현자에게 맡기는 것

- 가짜 서버의 검색 방식(결정적이기만 하면 됨).
- 대기 간격과 로그 문구.
- compose 파일의 헬스체크 세부.
