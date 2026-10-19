# 메모리 평가 하네스

AI 에이전트용 메모리 엔진이 "낡았거나 거짓인 지식을 막고, 참인 지식은 올려 주는지" 평가하는 하네스입니다. 채점·에이전트·기준선에 LLM을 쓰지 않으며, 같은 입력이면 같은 출력이 나옵니다.

## 설치

Python 3.14 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
uv sync
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

평가할 엔진은 `--engine package.module:ClassName`으로 추가합니다(여러 번 지정 가능, 기준선은 항상 같이 돕니다). 실행 결과는 `runs/`에 쌓이며 레포에는 올리지 않습니다.

## 시나리오 세트

| 위치 | 내용 | 쓰임 |
|---|---|---|
| `scenarios/dev/` | 사람이 검토해 승인한(`review.status: approved`) 개발용 세트 | 엔진 개발과 튜닝 |
| `scenarios/examples/` | LLM이 쓴 초안 5개(계열마다 하나) | 하네스가 끝까지 도는지 보는 스모크 테스트 |
| test 세트 | 레포 밖에 보관 | `--sealed`로 실행: 리포트에서 시나리오·사실 id를 뺌 |

평가에는 `approved`만 들어갑니다. 초안을 넣으려면 `--include-drafts`를 줍니다(`rejected`는 항상 제외).

**예시(`scenarios/examples/`)는 스모크 테스트 전용이며 발표 수치에 쓰지 않습니다.**

## 시나리오 형식

YAML 파일 하나가 시나리오 하나입니다. 계열, 사실, 이벤트, 문항(`block` / `promote` / `leak`)의 형식과 계열별 틀은 설계 문서 [5장](docs/superpowers/specs/2026-10-19-memory-eval-harness-design.md#5-시나리오-형식)을 보세요. 시각은 모두 시간대를 포함해야 합니다(예: `2026-10-09T14:00+09:00`).

## 테스트

```bash
uv run pytest -q
```
