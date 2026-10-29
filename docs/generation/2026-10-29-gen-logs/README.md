# 생성 실행 로그 (2026-10-29, sunmin)

`~/.cache/t5-harness/runs/<실행 id>/`의 `candidates.jsonl`(인용 검사를 통과한 후보)과 `rejected.jsonl`(거절 사유)을 그대로 복사한 것입니다. PR #13 코멘트에서 HJinS 님이 요청한 자료입니다. 요약은 `batch1-summary.log`, `batch2-summary.log`.

| 실행 id | 묶음 | 계열 | 시드 | 비고 |
|---|---|---|---|---|
| 20261029-104012 | 1 | all | 20261101 | 래퍼 버그(`transform_schema`가 맵을 지움)로 본문 쓰기 단계에서 `llm_error` 3회, 종료 코드 2 |
| 20261029-104338 | 1 | normal_repeat | 20261101 | 같은 버그 재현용 실행(`--max-traces 1`) |
| 20261029-104649 | 1 | all | 20261101 | 래퍼 수정 후. 8/10 생성 |
| 20261029-105932 | 2 | normal_repeat | 20261102 | |
| 20261029-110000 | 2 | coincidence | 20261103 | |
| 20261029-110059 | 2 | single_source_poison | 20261104 | |
| 20261029-110136 | 2 | echo | 20261105 | |
| 20261029-110335 | 2 | failure_feedback | 20261106 | |
| 20261029-110716 | 2 | truth_change | 20261107 | |
| 20261029-110846 | 2 | conditional | 20261108 | `--max-traces 40` |
| 20261029-110930 | 2 | workaround_then_root | 20261109 | 0 생성 |
| 20261029-111229 | 2 | leak_personal | 20261110 | |
| 20261029-111319 | 2 | leak_cross_team | 20261111 | |

`quote_not_found` 57건의 원인 분석은 `../2026-10-29-quote-not-found-analysis.py`와 생성 기록 문서의 발견 8을 보세요.
