# dev 시나리오 세트 생성 기록 (sunmin, 2026-10-29)

PR #13(Jh-jaehyuk 님)과 같은 방식으로 하니스를 독립 재현한 기록입니다. 목적은 시나리오를 더 모으는 것보다 **같은 절차를 다른 사람이 다른 시드로 돌렸을 때 하니스가 같은 모양의 결과와 같은 문제를 내는지** 보는 것입니다.

## 환경

| 항목 | 값 |
|---|---|
| 하니스 | `feat/scenario-generation` `0d89c54`, prompt `gen-v0.5` (PR #13과 동일) |
| 궤적 | `traces fetch --config v1.2 --split minisweagent --shard 0` (qwen38_27b, 2,500개, 묶음 0개) |
| LLM | `claude-opus-5-5`, effort medium. API 키 대신 `claude -p`로 호출. `provenance.provider: claude-code` |
| 래퍼 | `tools/gen_claude_p.py`. `make_client`만 바꿔 끼우고 프롬프트·스키마 검사·재시도·캐시·`check_draft`는 하니스 그대로. 하니스의 비공개 이름(`_ask_with_retry`, `_cache_settings`)을 쓰므로 유지 대상이 아니라 재현 기록용 |
| 검토자 | `sunmin` |

래퍼에서 주의할 점 하나: 하니스의 `AnthropicClient`가 쓰는 `anthropic.transform_schema`는 본문 쓰기 스키마(`BodySlots`)의 id→본문 맵(`additionalProperties`)을 빈 객체로 바꿉니다. `claude -p --json-schema`에 그대로 넘기면 모델이 "스키마가 빈 객체만 허용한다"며 제출을 거부해 3회 연속 오류로 멈춥니다. 래퍼는 pydantic 원본 스키마(`model_json_schema()`)를 넘깁니다.

## 생성 명령

```bash
uv sync --extra gen
uv run harness traces fetch --config v1.2 --split minisweagent --shard 0 --yes --timeout 300

# 묶음 1: 계열 전부, 시드 하나
uv run python tools/gen_claude_p.py generate --family all --n 1 --max-traces 10 --seed 20261101

# 묶음 2: 계열마다 시드 하나씩 (20261102부터 1씩 증가, TEMPLATES 순서)
#   normal_repeat --n 1, 나머지 --n 2; conditional만 --max-traces 40, 나머지 15
for fam in normal_repeat coincidence single_source_poison echo failure_feedback truth_change \
           conditional workaround_then_root leak_personal leak_cross_team; do
  uv run python tools/gen_claude_p.py generate --family $fam --n 2 --max-traces 15 --seed <20261102..20261111>
done
```

| 계열 | 시드 | 요청 | 생성 | 원본 저장소 |
|---|---|---|---|---|
| (all) | 20261101 | 10 | 8 | django-storages (8개 전부) |
| normal_repeat | 20261102 | 1 | 1 | snowflake-cli |
| coincidence | 20261103 | 2 | 2 | click |
| single_source_poison | 20261104 | 2 | 2 | cel-python |
| echo | 20261105 | 2 | 2 | more-itertools, operator |
| failure_feedback | 20261106 | 2 | 2 | sanic-routing, wechatpy |
| truth_change | 20261107 | 2 | 2 | bioregistry, morecantile |
| conditional | 20261108 | 2 | 2 | beets |
| workaround_then_root | 20261109 | 2 | 0 | 후보 30개 중 fix가 workaround/root인 것 없음 |
| leak_personal | 20261110 | 2 | 2 | duploctl |
| leak_cross_team | 20261111 | 2 | 2 | simple-ddl-parser |

초안 25개. 자동 거절(두 묶음 합계):

| 단계 | 코드 | 건수 |
|---|---|---|
| candidates | quote_not_found | 57 |
| candidates | quote_too_short | 12 |
| checks | too_easy | 8 |
| checks | cue_word | 4 |
| checks | quoted_evidence_mismatch | 2 |

## 검토 결과

| 상태 | 수 | 내용 |
|---|---|---|
| approved | 21 | coincidence 2, conditional 2, failure_feedback 3, leak_cross_team 3, leak_personal 3, normal_repeat 2, single_source_poison 3, truth_change 3 |
| rejected (결함) | 1 | `conditional-beets-66f45a`: 조건 "web plugin"과 "beta"가 같은 컴포넌트라 겹치고, 질문("어떻게 보내야 하는가")에 정답 사실("그 경로는 깨진다")이 답이 되지 않음 |
| rejected (제외) | 3 | echo 3개. 시나리오 결함이 아니라 아래 발견 1 때문. 각 파일 `review.notes`에 기록 |

PR #13과 달리 거절·제외한 4개도 `scenarios/dev`에 둡니다. `run`과 `selfcheck`는 승인된 것만 쓰므로 결과에는 영향이 없고, echo 3개는 하니스가 바뀌면 `review set approved`로 바로 되살리기 위해 남겼습니다.

검토자 수정 1건: `failure_feedback-django-storages-ade0aa`. 네 문항의 `on_false` 본문이 "`tox …`가 실패했다"고 적혀 있었는데, 그 앞의 잘못된 행동은 `python -m pytest …`를 실행한 것이라 행동과 결과가 이어지지 않았습니다. 자동 검사는 행동과 결과 본문의 연결을 보지 않으므로 통과했습니다. `review edit`로 본문만 "pytest 직접 실행이 site-packages를 가져와 실패했고 tox로 돌려야 한다"로 바꾸고 재승인했습니다(`provenance.reviewer_edited: true`, `review.notes`에 사유).

**소재 편중**: 승인 21개 중 7개가 django-storages이고 그중 6개는 같은 사실(pytest로 backend 테스트 실행)입니다. 묶음 1이 시드 하나로 모든 계열을 돌려 첫 궤적의 같은 후보를 썼기 때문입니다(발견 2). 세트 다양성이 필요하면 이 6개 중 일부를 빼는 것이 좋습니다.

## 실행 결과

```bash
uv run harness scenario check scenarios/dev      # 종료 코드 0
uv run harness selfcheck --scenarios scenarios/dev   # 6개 PASS (repeat_n_echo는 echo가 없어 건너뜀)
uv run harness run --scenarios scenarios/dev --out runs
uv run pytest -q                                 # 1035 passed
```

리포트: `2026-10-29-dev-scenarios-sunmin-report.md` (시나리오 21개, 세트 해시 `3ad0814d27…`).

| 엔진 | M1 (n=10) | M2 (n=18) | 판정 |
|---|---|---|---|
| none | 0.000 | 0.000 | 탈락 |
| all_store | 1.000 | 0.944 | 탈락 |
| repeat_n | 0.100 | 0.000 | 탈락 |
| oracle | 0.000 | 1.000 | 통과 |

되먹임(failure_feedback, n=8): all_store M1 0.500, none·repeat_n·oracle 0.000. PR #13은 0.800 (n=5).

기준선 모양은 PR #13과 같습니다(oracle만 통과). 승인 세트는 10계열 중 **8계열만** 검증합니다(echo 제외, workaround_then_root 생성 0).

## 발견 (PR #13의 확인 요청과 대조)

1. **echo와 `repeat_n`: 재현.** echo 초안 3개의 거짓 기록이 각각 1, 1, 2개라 `repeat_n`(n=3, tau=0.5)이 잡지 못하고 `repeat_n_echo`가 FAIL합니다. 이 상태로는 `run`이 리포트를 내지 않으므로 PR #13처럼 echo를 뺐습니다. 리포트의 `repeat_n_echo` PASS는 검사 생략입니다. 반면 **poison은 손대지 않고 통과**했습니다(cel-python 2개와 django-storages 1개, M1 0.333). PR #13은 poison 2개의 본문을 다듬어야 했으므로 Jaccard 문제는 "항상"이 아니라 후보에 따라 갈립니다.
2. **계열마다 같은 후보: 재현.** 묶음 1의 8개가 모두 django-storages 한 궤적의 같은 사실에서 나왔습니다. 계열별 시드(묶음 2)로는 레포가 분산됐습니다.
3. **`quote_not_found` 비율: 재현.** 후보 거절 69건 중 57건(83%). 원인은 발견 8.
4. **묶음 0개**: 같은 shard 0이라 확인하지 못했습니다.
5. **workaround_then_root 생성 0: 재현.** 상세는 발견 9.
6. **자동 검사가 놓친 본문 문제 (알려진 한계의 되먹임 사례).** PR #2 리뷰 6번("본문이 자기 라벨과 반대 뜻이어도 통과", README 검토 항목에 반영됨)이 되먹임 틀의 행동→결과 연결에서 나타난 구체 사례입니다. 되먹임 틀의 `on_false` 본문은 "잘못된 행동 뒤의 실패"여야 합니다. 검사(`_body_issues`)는 되먹임 본문에도 이벤트 본문과 같은 검사(자기 사실 마커 포함, 다른 사실 마커 없음, 금지어 없음)를 하지만, 행동 본문(`action.f_old`)과 결과 본문(`on_false`)이 이어지는지는 검사하지 않습니다. LLM이 f_new 마커를 넣으려고 "tox가 실패했다"고 써도 통과합니다. 이 연결은 사람 검토가 유일한 안전장치이며, 프롬프트에 "on_false는 action.f_old의 행동이 실패한 기록"임을 명시하면 줄일 수 있을 것 같습니다.
7. **이벤트 id 빈자리 (PR #2 후속 6번) 재현.** 되먹임 계열에서 마지막 시나리오 이벤트가 첫 block 문항 뒤에 오면 되먹임 기록이 e5 자리를 먼저 차지해 엔진이 e1에서 e4, e6, e7, e8, e5 순으로 받고 생성 기록이 드러납니다(`replay.py`의 `generated_event_id`). 승인한 failure_feedback 3개 모두 이 조건입니다(ade0aa e5 10-11 vs p_block1 10-06, sanic-routing e5 10-13 vs 10-08, wechatpy e5 10-12 vs 10-05). 기준선 4개는 id 형식을 보지 않아 이번 수치에는 영향이 없지만, 실제 엔진으로 되먹임을 평가할 때는 유의해야 합니다.
8. **`quote_not_found` 57건의 원인은 하니스 쪽에 있습니다.** 거절 로그의 궤적 32개를 LLM 캐시(추가 호출 없음)로 다시 불러 인용 57건을 원문과 대조했습니다. `traces.py`의 `_call_text`가 parquet의 `function.arguments`(JSON 문자열, 예: `'{"command": "ls -la /testbed"}'`)를 그대로 붙이므로 메시지 본문에는 `\n`, `\"`, `\u003e`(`>`), `\u0027`(`'`) 같은 이스케이프가 남습니다. 모델은 이를 풀어서 인용하므로 줄바꿈·따옴표·`->`가 든 인용은 맞을 수 없습니다. 이스케이프를 되돌린 뒤 다시 대조하면 55건이 인용한 메시지에 그대로 있고, 1건은 다른 메시지에 있으며, 1건만 실제로 없습니다(모델이 `\n`은 그대로 두고 `\u003e`만 푼 경우). 즉 57건 중 56건(98%)은 모델이 원문을 정확히 인용한 것이고, 거절은 대조 전처리 문제입니다. 분석 스크립트는 같은 폴더의 `2026-10-29-quote-not-found-analysis.py`입니다(캐시된 응답만 읽어 LLM 호출 없음). `_call_text`에서 arguments를 `json.loads`로 풀어 값만 붙이면 후보 뽑기 호출의 대부분을 살릴 수 있습니다. PR #13의 16/19·43/60도 같은 원인일 가능성이 높지만 그쪽 캐시는 확인하지 못했습니다.
9. **workaround_then_root는 후보 부족이 아니라 `root` 부재와 초안 거절입니다.** 인용 검사를 통과한 후보 87개(중복 제거) 중 `fix: workaround`가 4개(django-storages manage.py, click zsh, sanic-routing egg-link, w3lib sys.path), `fix: root`는 0개입니다. 두 번의 workaround_then_root 실행에서 각각 후보 1개가 틀에 맞았고 초안까지 만들어졌지만 둘 다 자동 검사에서 거절됐습니다. 묶음 1은 `too_easy`(all_store가 어떤 block 문항에서도 거짓 사실을 따르지 않음), 묶음 2는 `cue_word`(이벤트 e2에 'wrong'). 따라서 확인할 것은 두 가지입니다. 후보 프롬프트가 `root`를 한 번도 내지 않는 이유(궤적 특성인지 프롬프트 지시의 문제인지)와, 이 틀이 만드는 초안이 `too_easy`에 걸리는 구조인지입니다. 앞서 "후보 52개 중 맞는 것 없음"이라고 쓴 것은 실행 메시지(`29 did not fit`)를 그대로 옮긴 것이라 정확하지 않아 고칩니다.
10. **#13 인라인 지적(flask-dance) 대조.** HJinS 님이 PR #13 `truth_change-flask-dance-3ef268`에서 `f_old`가 상시 참 사실(테스트 파일 위치)을 함께 담고 있어 질문의 그 부분에 올바르게 답해도 오래된 지식으로 판정된다고 지적했습니다. 이 세트의 시간 계열 6개(truth_change 3, failure_feedback 3)의 `f_old`/`f_new`와 질문을 대조한 결과, 질문이 묻는 부분이 모두 `f_new`에서 바뀌므로 해당 없습니다. 자동 검사가 잡지 못하는 패턴이라 검토 체크리스트 항목으로 추가할 만합니다.
11. **생성 로그 공유.** HJinS 님이 #13에서 `rejected.jsonl`을 요청해, 이번 실행 13개의 `candidates.jsonl`·`rejected.jsonl`을 `2026-10-29-gen-logs/`에 넣었습니다.

## 정리

승인 세트의 평가 재현 성공, echo 실패 재현, 자동 검사가 놓친 되먹임 본문 문제 발견. 하니스 자체를 검증한다는 목적에는 의미 있는 결과입니다.
