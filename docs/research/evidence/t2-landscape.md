# T2. AI 메모리 평가 경쟁 현황 (Competitive landscape)

작성일: 2026-10-25 / 작성: T2 (Sonnet) / 범위: `.superpowers/research/` 내부만 수정

> 읽는 법
> - 모든 사실 주장 옆에 URL을 붙였다. 신뢰도 표기: **높음** = 논문/공식 페이지 본문 또는 초록을 직접 읽음, **중간** = 초록·요약 페이지만 읽었거나 웹 요약기(WebFetch가 쓰는 소형 모델) 결과에 의존, **낮음** = 검색 스니펫·2차 자료·벤더 자기보고.
> - `summary-source.md`는 근거로 쓰지 않았다(리드 이름만 참고). 거기 나온 주장은 아래에서 모두 원문으로 다시 확인했거나 아예 쓰지 않았다.
> - 날짜 표기는 arXiv 최초 제출일 기준(확인한 경우). 오늘은 2026-10-25.
> - 웹 요약기를 거친 정보라 수치 전사 오류 가능성이 있다. 이 문서의 수치를 우리 문서에 인용하기 전에 T1(출처 검증) 단계에서 원문 대조를 권한다.

---

## 0. 핵심 결론 (먼저 읽을 것)

1. **"아무도 행동 수준에서 잘못된 기억을 측정하지 않는다"는 주장은 틀리다.** 이미 가까운 선행 연구가 있다.
   - DreamBench-SWE(2026-08): 소프트웨어 에이전트 대상 다중 세션 "memory hygiene" 벤치마크. 실행 가능한 숨은 오라클로 채점(주 결과는 judge-free), 사전등록(preregistration), 호스팅된 Mem0를 블랙박스로 채점, StaleUseRate/OverscopeRate/RepeatedErrorRate 진단 지표 보유. https://arxiv.org/abs/2608.20664 (중간)
   - The Memory Trust Gap(2026-09): 행동 채점(action-scored)으로 "오래된 메모리가 최신 권위 있는 증거를 덮어쓰는" 해악(Δmem, 무메모리 대비)을 측정. https://arxiv.org/abs/2609.01852 (중간)
   - Sleeper Memory Poisoning(2026-05)과 Injection-Execution Dissociation(2026-05): 오염 메모리가 "기록 → 인출 → 실제 행동"으로 이어지는 비율을 단계별로 측정. https://arxiv.org/abs/2605.15338 , https://arxiv.org/abs/2605.08442 (중간)
2. **팀/개인 격리**도 이미 벤치마크가 있다: GateMem(접근 통제+삭제+유용성 동시 평가), MemLeak(공유 벡터 저장소 교차 사용자 누출), UCC(악의 없는 교차 사용자 오염). 다만 "0이어야 하는 합격/불합격 기준"으로 못 박은 것은 찾지 못했다(아래 §4).
3. **독립 출처 승격(independent-source promotion)** 은 벤치마크로는 찾지 못했다(설계 문서·도구 수준만 있음). 가장 확실한 미발견 영역.
4. **블랙박스 엔진 포트에서 promote/demote 결정을 채점**하는 벤치마크도 찾지 못했다. 단, "블랙박스 어댑터" 자체는 흔하다(ForgetEval, Bemula, MemoryBench 등).
5. 우리 설계의 **새로움은 개별 각도가 아니라 조합**에 있을 가능성이 높다: (코딩 에이전트 팀 맥락) × (결정론적 규칙 기반 시뮬레이션 에이전트) × (store-everything 기준선 대비 상대 M1) × (격리 0 기준) × (피드백 모드 폐루프) × (엔진 블랙박스 + 승격/강등). 이 조합을 한 하네스에서 하는 것은 찾지 못했다. 단 "찾지 못함"은 §1의 검색 범위 안에서만 유효하다.
6. 신뢰 경고: 메모리 벤치마크 점수는 채점 모델·프롬프트에 크게 의존하고 벤더 자기보고가 많다(§3). LoCoMo는 정답지 오류·관대한 judge 문제가 보고됨.

---

## 1. 조사 방법 (주장을 반증 가능하게 하기 위한 기록)

도구: WebSearch(standard/extended), WebFetch(arXiv abs/html, GitHub, 벤더 블로그). 서브에이전트 미사용. 2026-10-25 하루 세션.

### 1.1 실행한 검색 질의 (요지, 영문 원문 그대로)

| # | 목적 | 질의 |
|---|---|---|
| Q1 | 개별 벤치마크 확인 | `LongMemEval ... arXiv 2410.10813` / `MemoryAgentBench Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions` / `MemoryArena benchmark agent memory multi-session agentic tasks` / `STATE-Bench agent memory benchmark enterprise` / `BEAM benchmark beyond a million tokens ...` / `LoCoMo ... Maharana snap-research` / `MemBench ... factual reflective memory` / `HaluMem ...` |
| Q2 | 벤더 평가 | `Mem0: Building Production-Ready AI Agents ... LLM-as-a-judge LOCOMO latency token cost` / `Zep: A Temporal Knowledge Graph Architecture ... LongMemEval` / `Letta Benchmarking AI agent memory: Is a filesystem all you need` / `Letta Context-Bench OR Letta Evals` / `LangMem LangGraph long-term memory evaluation benchmark` / `Mem0 research benchmark 2026 ...` / `Zep Mem0 LoCoMo evaluation dispute` |
| Q3 | 포이즈닝 | `agent memory poisoning attack benchmark evaluation MINJA AgentPoison MemoryGraft memory injection` / `cross-user memory poisoning shared memory one user plants false memory ...` |
| Q4 | 멀티테넌트/접근통제 | `multi-tenant memory isolation benchmark agent memory cross-user leakage evaluation access control` / `shared memory multi-user team agents benchmark collaborative memory permissions read write access asymmetric` / `PersistBench OR CIMemories ...` / `"No attacker needed" unintentional cross-user contamination ...` |
| Q5 | 승격/통합/강등 | `memory consolidation promotion evaluation benchmark agent memory "consolidation" independent sources corroboration` / `memory tiers promotion demotion ... "demotion" OR "demote" OR "eviction" correctness` / `memory trust provenance independent corroboration multiple sources promote memory to shared team memory single-source claim benchmark` |
| Q6 | 망각/삭제 | `forgetting decay agent memory benchmark selective forgetting capacity degradation ...` / `machine unlearning right to be forgotten agent memory deletion benchmark GDPR erasure ...` |
| Q7 | 비용/지연 | `memory write-path overhead background LLM extraction cost latency agent memory systems benchmark ingestion cost ...` |
| Q8 | 코딩 에이전트/오래된 기억 | `benchmark coding agents persistent memory stale outdated memory harmful actions repository memory evaluation SWE` / `SWE-Bench-CL ...` / `STALE benchmark agents act on invalidated memory ...` |
| Q9 | 자기강화 | `self-reinforcing memory feedback loop agent writes its own output back into memory error amplification ...` / `Evo-Memory OR MemoryBench continual learning memory feedback benchmark ...` |
| Q10 | 조건부 사실 | `memory benchmark conditional facts context-dependent preferences scope "only when" ...` |
| Q11 | 사전등록/블라인딩 | `pre-registered memory benchmark agents hypothesis registered before running blinded evaluation memory systems` |
| Q12 | 결정론 채점/블랙박스 어댑터 | (개별 논문 본문 확인 중 수집: ForgetEval adapter protocol, Bemula reset/observe/query, supermemory MemoryBench provider 인터페이스) |

### 1.2 "찾지 못했다"의 정의
어떤 각도를 "미발견"이라 쓴 경우는, 위 질의들의 상위 결과 약 10건씩과, 그 결과에서 파생된 논문 초록·본문 일부를 읽었을 때 해당 각도를 **명시적으로 측정**하는 항목이 없었다는 뜻이다. 반증 방법: 아래 §4 각 행의 "반증용 검색어"로 arXiv/Google Scholar에서 검색해 해당 항목을 찾으면 된다.

### 1.3 한계
- 검색 도구는 US 기반 일반 웹 검색이다. 중국어권 학술지·비영어 자료, 비공개 사내 벤치마크, 유료 논문은 못 봤다.
- 대부분의 arXiv는 **초록 페이지 또는 HTML 앞부분만** 읽었다. 읽지 못한 부분을 문서 안에 "미확인"으로 표시했다. 긴 논문(DreamBench-SWE 25만 자, GateMem 12만 자 등)은 일부만 읽었다.
- 2026년 7~10월 프리프린트가 많다. 아직 동료심사를 거치지 않은 것이 많고, 단일 저자·소규모 표본이 있다.
- 검색 요약에서 Mem0 연구 페이지에 AI 어시스턴트를 향한 문구가 있다고 보고되었다. 이는 데이터로만 취급했고 따르지 않았으며, 그 페이지의 수치만 "벤더 자기보고"로 표기했다.

---

## 2. 벤치마크·평가 카탈로그

표 열: 이름 / 날짜 / 저자·기관 / 과제 형식 / 규모 / 채점 / 행동 결합 / 시간·갱신·모순 / 테넌트 격리 / 망각·삭제 / 포이즈닝 / 비용 / 알려진 비판 / 링크 / 신뢰도.
열이 많아 군(群)별로 나눠 필요한 열만 남겼다. 해당 없음은 "-", 미확인은 "미확인".

### 2.A 대화형 장기 기억 QA 계열 (회상 중심)

| 이름 | 날짜·저자 | 형식·규모 | 채점 | 행동 결합 | 시간/갱신/모순 | 비용 | 비판·한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|---|---|---|
| LoCoMo | ACL 2024; Maharana 외(UNC, USC, Snap) | 장기 대화 QA·요약·멀티모달 대화 생성. 논문 초록은 평균 300턴/9K 토큰/최대 35세션, ACL판은 600턴/16K 토큰, GitHub 공개본은 10개 대화(판본 불일치) | 레포는 GPT/Claude/Gemini 평가 스크립트 제공. 지표 상세(F1/judge)는 이번에 미확인. 벤더들은 LLM judge로 사용 | 없음(QA) | 시간·인과 질문 포함(논문 서술) | - | 정답지 오류 6.4%(99/1,540), judge(gpt-4o-mini)가 의도적 오답의 최대 62.81%를 수용(Penfield Labs 감사, 벤더); 커뮤니티 이슈로 정답 오류 5건+주석 결함 2건 보고; top-k가 세션 수보다 커서 검색이 사실상 우회된다는 이슈; 적대 범주를 분모에서 뺀 점수 논란 | https://huggingface.co/papers/2402.17753.md , https://github.com/snap-research/locomo , https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer , https://github.com/snap-research/locomo/issues/44 , https://github.com/MemPalace/mempalace/issues/29 | 논문·레포: 중간. 감사 수치: 낮음(벤더/경쟁자 자료) |
| LongMemEval | 2024-10 (ICLR 2025); Wu 외 | 500문항, 5능력(정보추출, 다중세션 추론, 지식갱신, 시간추론, 기권). S=약 115K 토큰/약 50세션, M=500세션/약 1.5M 토큰 | GPT-4o judge(사람과 97% 이상 일치, 유형당 30문항 표본). Recall@k/NDCG@k도 보고 | 없음(QA) | 지식갱신·시간추론·기권 포함 | - | 문항·근거 세션은 LLM 생성 후 사람 편집(통과율 약 5%); 저자 스스로 삭제 연산 부재·개인정보 누출 위험 언급; judge 의존 | https://arxiv.org/abs/2410.10813 , https://arxiv.org/html/2410.10813v2 | 높음 |
| LongMemEval-V2 | 2026-05 (작업중); Wu 외 | 웹 에이전트용. 451문항, 최대 500궤적/115M 토큰. 능력: 정적 상태 회상, 동적 상태 추적, 워크플로 지식, 환경 함정(gotchas), 전제 인식 | 초록은 정확도만 명시, judge 여부 미확인 | 부분(환경 경험을 답에 활용, 직접 행동은 아님) | 동적 상태 추적 | 코딩 에이전트 방식은 지연 비용이 큼(저자 언급) | "Work in Progress" 표기 | https://arxiv.org/abs/2605.12493 | 중간 |
| MemoryAgentBench | 2025-07 (ICLR 2026); Hu, Wang, McAuley(UCSD) | 4능력(정확한 검색, 테스트 시 학습, 장거리 이해, 선택적 망각/충돌 해소). 기존 데이터를 증분 다회전으로 변환 + EventQA(약 534K 토큰) + FactConsolidation(6K~262K) | 대부분 정확도, 영화 추천 Recall@5, 요약 F1. 정확 일치/judge 세부(부록 L)는 미확인 | 없음 | FactConsolidation: MQuAKE 반사실 편집쌍을 뒤에 붙여 "갱신" 모사(최신 사실 우선 지시) | - | 예산상 일부 메모리 에이전트만 평가(저자); 다단계(multi-hop) 충돌 해소는 최대 약 7%(v3 HTML) | https://arxiv.org/abs/2507.05257 , https://arxiv.org/html/2507.05257v3 | 중간 |
| BEAM | 2025-10 (ICLR 2026); Tavakoli 외(Alberta, UMass) | 100개 대화, 2,000 검증 문항, 대화당 최대 10M 토큰. 10능력(기권, 모순 해소, 이벤트 순서, 정보 추출, 지시 따르기, 지식 갱신, 다중 홉, 선호 따르기, 요약, 시간 추론) | 문항을 원자 "nugget" 루브릭으로 쪼개 LLM judge가 0/0.5/1 채점(judge 모델명 미확인); 이벤트 순서는 Kendall tau-b | 없음 | 모순 해소·지식 갱신 포함, 모든 방법이 모순 해소에서 최약 | - | 4개 모델은 10M 창 미지원이라 최근 구간만 평가; 검색 문서 15개 초과 시 소폭 악화 | https://arxiv.org/abs/2510.27246 , https://arxiv.org/html/2510.27246v1 | 높음(채점), 중간(나머지) |
| MemBench | 2025-06 (ACL Findings 2025); Tan 외(인민대, Huawei) | 사실/반성 기억 × 참여/관찰 시나리오. 500 사용자 관계 그래프, 하위 세트 약 10K·100K 토큰 | 전부 객관식, 정확도 + Recall@10. judge 언급 없음 | 없음 | 지식 갱신 문항 포함, "노이즈 세션은 사실 충돌을 피하도록 구성" | 효율(연산당 읽기/쓰기 시간), 용량(메모리 크기별 정확도) 측정 | 구조화 데이터 한정, LLM 생성 데이터 | https://arxiv.org/abs/2506.21605 , https://arxiv.org/html/2506.21605v1 | 중간 |
| HaluMem | 2025-11; Chen 외(China Telecom, MemTensor 등) | 메모리 추출·갱신·QA 3연산 수준에서 환각 측정. 20 사용자, Medium 약 16만 토큰/Long 약 100만 토큰, 메모리 포인트 14,948, QA 3,467 | GPT-4o judge; 지표: 메모리 재현율, 오기억 저항(FMR), 갱신 환각률·누락률 등 | 없음(QA) | 갱신 단계 환각·누락 측정 | Zep 비동기 처리 때문에 수집 시간 정확 측정 불가(저자) | 합성 페르소나 20명, 갱신 단계 표본 적어 환각률 해석 주의, GPT-4o가 judge 겸 답변자 | https://arxiv.org/abs/2511.03506 , https://arxiv.org/html/2511.03506 | 중간 |
| Memora ("From Recall to Forgetting") | 2026-04 (ACL 2026 Findings); Uddin 외 | 수주~수개월 대화, 기억하기·추론·추천 3과제. 4개 LLM + 6개 메모리 에이전트 | FAMA(Forgetting-Aware Memory Accuracy): 폐기/무효 메모리 의존을 감점. 규모·judge 여부는 초록에 없어 미확인 | 부분(추천 과제) | 갱신·통합 | - | 메모리 에이전트 개선은 미미(저자), 무효 기억 재사용 빈번 | https://arxiv.org/abs/2604.20006 | 중간 |
| DMR (MemGPT/Zep) | 2023~2025; Zep 논문에서 재사용 | 500 다중세션 대화, 각 5세션/세션당 최대 12메시지 + 질문 1개 | LLM judge(모델명 미기재) | 없음 | - | - | Zep 저자 스스로: 규모가 작고 단일 사실 검색, 전체 맥락 기준선이 강해 기업 시나리오를 반영 못 함 | https://arxiv.org/html/2501.13956v1 | 높음 |

### 2.B 에이전트 작업·행동 결합 계열

| 이름 | 날짜·저자 | 형식·규모 | 채점 | 행동 결합 | 시간/갱신/모순 | 격리·포이즈닝 | 비용 | 비판·한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|---|---|---|---|
| MemoryArena | 2026-02 (ICML 2026); He 외(Stanford, UCSD, UIUC, Princeton, Pitt 등) | 상호 의존 하위 과제를 세션으로 분리(쇼핑 150, 여행 270, 점진 검색 221, 수학 40+물리 20). 총 701과제/약 4,850 하위과제/과제당 평균 57 행동 | 성공률 SR, 진행 점수 PS, 부분 PS. 결정론/judge 여부는 본문에서 불명확 | 있음(기억을 얻고 이후 행동에 사용) | stale/충돌/주입 오기억 시험 없음(본문 서술) | 격리는 에피소드 시작 시 메모리 초기화뿐 | 메모리 시스템 하위 과제당 평균 99~133초(장문맥보다 느림) | 그룹 여행 점수 거의 0, 수학·물리 문항 제작 비용 큼, 모델명 표기 불일치. 평가 대상에 Letta, Mem0, Mem0-g, Mirix, ReasoningBank 포함 | https://arxiv.org/abs/2602.16313 , https://arxiv.org/html/2602.16313 | 중간 |
| STATE-Bench | 2026-05-19; Microsoft(Lewis Liu, Nishant Yadav 게시) | 고객지원·여행·쇼핑 3도메인 450과제, 사전 채운 DB. 과제당 5회 실행. "Agent Learning track"(이전 궤적에서 재사용 학습 추출 후 보류 과제에서 검증, 검색 hook으로 메모리 연결) | 상태 변경 과제는 최종 환경 상태의 결정론 단언, 절차·정보 과제와 UX는 LLM judge(모델명 미공개). pass@1, pass^5, 과제당 비용 | 있음(DB 상태 변경) | 오래된 기억/교차 사용자 시험은 페이지에 언급 없음 | 언급 없음 | 효율(턴, 불필요 도구 호출, 토큰) 측정 | 3도메인, 기준선 1개(GPT-5.1 무메모리) 보고, LLM 생성 합성 데이터, 사용자 시뮬레이터 LLM(분산 약 1%) | https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/ , https://github.com/microsoft/STATE-Bench | 중간 |
| DolphinBench | 2026-09; Rathi, Yadav, Singh | 지식노동 페르소나 3명, 각 약 500K 토큰 사용자 메시지, 각 200과제(약 600). 과제는 "이력 있을 때 성공, 없을 때 실패"로 필터 | 정확도 + 총비용 + 지연을 함께 보고 | 있음(과제 완료) | 초록에 없음 | 언급 없음 | 총비용에 수집(ingestion) 포함. 검색 요약: Mem0가 중앙 지연을 44.35→37.69초로 줄였지만 총비용 61.48→96.21달러로 증가(낮음, 2차 요약) | 저자 소속 미확인(저자명 중 Deshraj Yadav, Taranjeet Singh은 Mem0 창업자로 기억하나 이번 조사에서 확인 못 함, **확인 필요**). 평가 대상 시스템은 초록에 없어 미확인 | https://arxiv.org/abs/2609.24971 | 초록: 중간, 비용 수치: 낮음 |
| Mem2ActBench | ACL 2026 (arXiv 2026-01); Shen 외(중국과학원 정보공정연구소) | 도구 호출 매개변수 근거화. 2,029 세션에서 400 도구 과제. 7개 프레임워크(LTMemory, SCM, Generative Agents, MemTree, Mem0, LangMem, A-Mem) | F1(매개변수), BLEU-1, Tool Accuracy(정확 일치). judge 언급 없음 | 부분(정답 도구가 주어진 오프라인 도구 호출 생성) | 충돌 해소는 데이터 파이프라인에서 처리, 별도 시험 없음 | - | - | 오프라인 생성만, Qwen2.5 단일 계열, 상호작용 실행·피드백 없음. 평균 F1 Mem0 22.6, LangMem 19.9 vs A-Mem 33.6 | https://arxiv.org/abs/2601.19935 , https://arxiv.org/html/2601.19935v1 | 중간 |
| **DreamBench-SWE** | 2026-08-21; Sarthak Singh(단일 저자) | 소프트웨어 에이전트용 3세션 함정(S1·S2 증거 주입, S3 시험). 5유형: 규약 학습, 생성 파일, 오래된 아키텍처, 리뷰어 선호, 플레이키 테스트. 구성개념 C1~C10. 조건당 180 단위(후속 감사 360/360) | **실행 가능한 숨은 저장소 오라클 pass@1(주 결과는 judge-free)**. 위생 지표는 결과 기록의 결정론적 오프라인 재채점(진단용). 사전등록 PREREGISTRATION.md 2026-07-03 동결, v2.1 후속 감사 사전 동결 | 있음(S3 최종 diff를 오라클로 채점) | 있음: 오래된/대체된 사실(C3), 모순·출처(C6), 거짓 교훈(C9), 기권(C10) | 오염·테넌트 격리·승격·자기강화·삭제는 다루지 않음(본문 일부 확인) | 비용 반증 기준만 보고, 유리한 비용 주장은 없음 | 주 결과 영(null: DF-hybrid 95/180 vs B5 89/180, Holm p=1), C9·C10이 B0 여유 점검 실패로 무효, 단일 wake 모델(gpt-5.5), 큐레이션 픽스처, 네트워크 비격리, Mem0는 호스팅 서비스를 두 설정으로만 채점하고 기본 추출 설정과 Supermemory는 사전 적합성 검사에서 탈락. 지표 StaleUseRate 0.200, OverscopeRate 0.167, RepeatedErrorRate 0.571 등은 정의 미기재 진단값 | https://arxiv.org/abs/2608.20664 , https://arxiv.org/html/2608.20664 | 중간(본문 약 20만 자 중 읽은 부분 기준) |
| SWE-Bench-CL | 2025-06~07; Joshi, Chowdhury, Uysal | SWE-Bench Verified 이슈를 저장소별 시간순 수열로 재구성. 지표: 평균 정확도, 망각, 순/역 전이, 도구 효율, 종합 CL 점수. LangGraph + FAISS 의미 메모리 | 실행 기반 해결률(SWE-bench 방식) | 있음 | 망각·전이 | - | 도구 효율 | 정적 하네스에 순차 과제를 얹을 때 정렬 문제(저자). 과제 수 273은 2차 블로그 보고(낮음) | https://arxiv.org/abs/2507.00014 | 중간 |
| Evo-Memory | 2025-11; UIUC + Google DeepMind | 기존 데이터셋을 순차 과제 스트림으로 재구성하여 테스트 시 학습·경험 재사용 평가, ReMem/ExpRAG 제안 | 미확인 | 있음 | 미확인 | - | - | 2차 보도 기반 | https://arxiv.org/pdf/2511.20857 | 낮음 |
| EvoMemBench | 2026-05 | 범위(에피소드 내/간) × 내용(지식/절차) 2축 | 미확인 | 부분 | - | - | - | 검색 스니펫만 확인 | https://arxiv.org/html/2605.18421 | 낮음 |
| MemoryBench (Ai 외) | 2025-10 | 시뮬레이션 사용자 피드백으로 지속 학습 평가(선언·절차 기억) | 미확인 | 부분 | - | - | - | **2차 설명만 확인**(2026년 논문의 서술). 원문 미확인 | https://arxiv.org/abs/2510.17281 | 낮음 |
| coding-agent-memory-benchmark | 설계 2026-06-17, 실행 2026-06-24; 독립 연구자 | Claude Code 헤드리스, SWE-Bench Verified 49쌍 과제. 과거 실패에서 추출한 제약을 처치군에 제공 | 기준선·처치군 동일한 judge 모델 사용(결정론 여부는 페이지에 불명확) | 있음 | provenance 필드, 증거 종류별 감쇠 창(세션 14일 ~ 사용자 교정 730일) | - | - | 33/49(67.3%) → 38/49(77.6%)이지만 다중 시드 평균 효과 +0.24, 95% CI [0, 0.47]; 단일 저자, 비동료심사, 저자가 평가 대상 world-model-mcp의 유지보수자 | https://github.com/SaravananJaichandar/coding-agent-memory-benchmark | 중간 |
| Letta Context-Bench | 2025-10-30 오픈소스; Letta | 연쇄 파일 연산·엔티티 관계 추적·다단계 검색. Letta Evals 위에 구축. Filesystem Suite, Skills Suite | 미확인 | 부분 | - | - | - | 벤더 벤치마크. 리더보드 수치가 출처마다 다름 | https://letta.com/blog/context-bench , https://leaderboard.letta.com/ | 낮음 |

### 2.C 오래된 기억·진리 변화·상태 추적 (시간 축)

| 이름 | 날짜·저자 | 형식·규모 | 채점 | 행동 결합 | 비고·한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|---|
| STALE | 2026-05-07; Chao 외 | 400 전문가 검증 충돌 시나리오, 1,200 질의, 100개 이상 주제, 최대 150K 토큰. "암묵적 충돌"(뒤의 관찰이 앞 기억을 반박 없이 무효화). 3 프로브: 상태 해소, 전제 저항, **암묵적 정책 적응(하위 행동에 갱신 상태 적용)** | 채점 방식·judge 미확인 | 부분(정책 적응 프로브가 행동 지향) | 최고 모델 전체 55.2%. 갱신 증거를 검색하는 것과 그것에 따라 행동하는 것 사이에 격차. 한 서베이는 "STALE은 값이 낡았음을 아는지를 보지, 낡은 값이 정당화했을 도구 호출을 피하는지는 보지 않는다"고 평가 | https://arxiv.org/abs/2605.06527 , https://arxiv.org/pdf/2606.30306 | 중간 |
| StateMemBench | 2026-08-20; Fan 외(Jiawei Han 그룹) | 234 다중 세션 시나리오, 사실·제약·결정이 수정됨 | 닫힌 풀 분류: 현재 상태 반영 / 대체된 상태 반영 / 기타 오류 | 아니오(답변) | StateMem(상태 우선 메모리)이 현재 상태 정확도 0.205→0.363 (DeepSeek-V4-Flash) | https://arxiv.org/abs/2608.19652 | 중간 |
| Memory Trust Gap | 2026-09-01 (NeurIPS 2026 워크숍 심사중); Hu, Ramachandran | **동결·닫힌 집합·행동 채점** 벤치마크. 이익 스위트(저장 사실 없이는 풀 수 없음) + 안전 스위트(권위 도구가 항상 정답 보유). 2×2×2×2 요인 설계(라벨, 최신성, 출처 권위, 위치, 메타데이터). Qwen3 0.6/1.7/4/8B, Llama-Instruct 계열, RGB, MisBench | 해악 = 오래된 메모리 조건에서 무메모리 대비 변화 Δmem | **있음(행동 채점)** | 이익 스위트에서 모델이 낡은 값을 0.92~1.00 비율로 사용, 안전 스위트에서 해악은 능력 의존적(큰 모델은 낡은 메모를 최신처럼 꾸미면 더 속음). **메모리 엔진이 아니라 모델 크기를 변수로 함** | https://arxiv.org/abs/2609.01852 | 중간 |
| (FactConsolidation, LongMemEval 지식갱신, BEAM 모순 해소, HaluMem 갱신, Memora FAMA는 §2.A 참조) | | | | | | | |

### 2.D 격리·접근 통제·교차 사용자 누출

| 이름 | 날짜·저자 | 형식·규모 | 채점 | 평가한 시스템 | 핵심 수치·한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|---|
| **GateMem** | 2026-06-17; Zhe Ren 외 | 다중 주체 공유 메모리. 의료·사무·교육·가정 4도메인, 91 에피소드(평균 약 223턴), 숨은 체크포인트 2,218개(유용성 728, 접근통제 727, 능동 망각 763), 에피소드당 평균 주체 13.4·역할 11.6 | 위반 = 보호 대상 누출 또는 refuse/answer_redacted 이외 행동. **주 결과는 GPT-4o judge 라벨**, 누출 대상 주석으로 명시적 누출 감사 병행, 사람과의 일치 97.7% 이상 | Long-Context, RAG-Naive, RAG-Policy, A-Mem, Mem0, ReMem (Zep, MemGPT, Letta는 미평가). 시스템을 메모리 상태 위의 함수로 취급(블랙박스라고 명시하진 않음) | 어떤 방법도 유용성·접근통제·망각을 동시에 만족 못함. 장문맥은 접근통제 점수 10.4(의료, GPT-5.4). 한계: 망각은 인터페이스 수준일 뿐 물리적 삭제 증명이 아님, LLM judge 의존, 시나리오 LLM 보조 생성, 기준선 일부만 실행. 포이즈닝·승격은 미포함 | https://arxiv.org/abs/2606.18829 , https://arxiv.org/html/2606.18829 | 중간(본문 앞 10만 자 기준) |
| MemLeak (텍스트 메모리) | 2026-10-03 (NeurIPS-PALM 2026); Mudgal 외 | 공유 벡터 저장소에서 코사인 검색만으로 타 사용자 메모리가 인출됨("cross-user admissibility failure"). 6실험 + 절제. 표본 작음(조건당 n=10, 검색 요약 기준) | 응답 오염 5점 척도(사람 검증 언급). 1차 채점이 LLM인지 결정론인지 미확인 | 상용 제품이 아닌 TF-IDF/MiniLM 검색기 | 같은 팀 풀 검색 시 우발 누출 70~100%, 적대적 메모리 top-k 진입 90~100%, 검색 후 소유권 게이트만이 기준선을 복원(질의당 약 1.4ms). 개념증명 수준(저자) | https://arxiv.org/abs/2610.04195 | 중간 |
| UCC (No Attacker Needed) | 2026-04-01; Yang 외(USC, MSU, Northwestern, Adobe) | 악의 없는 상호작용이 공유 상태에 남아 다른 사용자에게 잘못 적용됨. 34개 규약(MIMIC-III 11, eICU 14, Slack 9)×최대 3 피해 과제×3회 | EHRAgent는 정확 일치(정규화), Slack(MURMUR)은 GPT-4o judge | EHRAgent(공유 메모리), MURMUR(공유 컨텍스트), 백본 GPT-4o | 공유 상태 원본 그대로일 때 오염 57~71%; 쓰기 시 정제(SSI)로 Slack 57→6%, eICU 71→33%, MIMIC 60→41%. 한계: 연구자 제작 소규모, 단일 모델, 텍스트 수준 정제는 실행 산출물 속 규약 제거 못함 | https://arxiv.org/abs/2604.01350 , https://arxiv.org/html/2604.01350v1 | 높음(HTML 본문) |
| MUMBench (AIM 논문) | 2026-09-11; Johnson 외 | 다중 사용자 비공개/공유 정보 4도메인, 검색·생성·갱신·삭제 연산 | 방식·규모 미확인 | 자체 AIM 프레임워크 | 가시성 분류 정확도 96.0%, 엄격 연산 정확도 58.8%, 상태 인지 70.5% (3회 평균) | https://arxiv.org/abs/2609.12320 | 중간 |
| CoMemBench | 2026-09-26; Zhao 외 | 다중 에이전트 워크플로 800개, 4도메인. 격리 도전 쌍. 워크플로 완료, 노드 진행, 핸드오프 신뢰도, 격리 강건성, 토큰 비용 | 실행 근거 평가기(native evaluators) | 다중 에이전트 구성 | 공유-격리 트레이드오프(맥락이 넓으면 가용성↑·격리↓). 수치는 초록에 없음 | https://arxiv.org/abs/2609.32192 | 중간 |
| CIMemories | 2025-11 (ICLR 2026); Mireshghallah 외(Meta FAIR 등) | 사용자 프로필(속성 100개 이상)×과제 맥락. 같은 속성이 어떤 과제엔 필수, 어떤 과제엔 부적절 | 채점 방식 미확인 | 프런티어 LLM | 속성 수준 위반 최대 69%; GPT-5 위반률은 1과제 0.1% → 40과제 9.6% → 동일 프롬프트 5회 25.1% | https://arxiv.org/abs/2511.14937 , https://iclr.cc/virtual/2026/poster/10008868 | 중간 |
| PersistBench | ICML 2026 포스터 | 교차 도메인 누출 + 메모리 유발 아첨(sycophancy). 18개 LLM | 미확인 | - | 상세 수치 미확인 | https://icml.cc/virtual/2026/poster/63204 | 낮음 |
| MemSyco-Bench | 2026-07-01 (v3 2026-10-15); Xiang 외 | 5과제: 메모리를 사실 증거로 거부, **적용 범위(scope) 존중**, 메모리 vs 객관 증거 충돌 해소, 갱신 추적, 유효 메모리로 개인화 | 방식·규모 미확인 | - | 범위 통제 범주가 우리 "조건부 사실"과 일부 겹침 | https://arxiv.org/abs/2607.01071 | 중간(초록만) |
| MemPABench | 2026-10-21; Li 외 | 상호작용 선호(묻기/확인/자율 행동). 사용자 4명, 선호 14개, 99세션/4개월, 사용자당 선호 변화 5회 | 선호 선택·전달된 행동·맥락/변화 민감도 3차원 | Mem0, MemOS, 단순 RAG, 파일 기반 + 무메모리, 백본 4개 | Mem0·MemOS·RAG는 무메모리를 못 이김, 파일 기반은 정확도↑이나 적용 범위 밖으로 선호가 번짐 | https://arxiv.org/abs/2610.30485 | 중간 |

### 2.E 포이즈닝·주입 (공격 연구와 평가)

| 이름 | 날짜·저자 | 설정 | 지표·채점 | 핵심 수치 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|
| AgentPoison | 2024-07-17; Chen 외(Dawn Song, Bo Li 등) | 장기 메모리/RAG 지식베이스 백도어. 자율주행 RAG 에이전트, 지식집약 QA, EHRAgent | 공격 성공률, 정상 성능 영향, 오염 비율 | 평균 공격 성공률 80% 이상, 정상 성능 저하 1% 미만, 오염 비율 0.1% 미만 | https://arxiv.org/abs/2407.12784 | 높음 |
| MINJA | 2025-03-05 (v5 2026-02); Dong 외 | 질의 전용 메모리 주입(브리징 단계 + 점진적 단축). 메모리 직접 수정 불필요 | ISR/ASR | 초록에 수치 없음. 2차 요약 인용치 주입 성공 98.2%·공격 성공 76.8%는 낮음 신뢰 | https://arxiv.org/abs/2503.03704 | 중간(초록) |
| Agent Security Bench (ASB) | 2024-10 (ICLR 2025); Zhang 외 | 10 시나리오, 10 에이전트, 400개 이상 도구, 27 공격/방어 조합, 메모리 오염 공격 1종 포함 | 7개 지표 + 효용-보안 균형 지표 | 최고 평균 공격 성공 84.30% | https://arxiv.org/abs/2410.02644 | 중간 |
| Memory Poisoning Attack and Defense (MINJA 재현·방어) | 2026-01-09; Sunil 외 | MIMIC-III EHR 에이전트, 3모델. 방어: 입출력 조정, 신뢰 인지 검색 정제 | ISR/ASR | "기존 정상 메모리가 있는 현실 조건에서는 공격 효과가 크게 감소"(초록) | https://arxiv.org/abs/2601.05504 | 중간 |
| Sleeper Memory Poisoning | 2026-05-14; Pulipaka 외(Mario Fritz 등) | 외부 콘텐츠를 통해 가짜 사용자 메모리를 저장시키는 지연 공격. **기록률 → 인출 → 에이전트 행동**의 3단계 | 단계별 비율 | 기록률 최대 99.8%(GPT-5.5), 인출 성공 중 공격자 의도 행동 60~89%. 86쪽 | https://arxiv.org/abs/2605.15338 | 중간 |
| Injection-Execution Dissociation | 2026-05-08 (v5 2026-08-04); Jun Wen Leong(단일 저자) | 지속 메모리 공격에서 주입 성공과 도구 실행 성공이 분리됨. 5,040 실행, 9모델, 6 방어 4계층; 프런티어 21모델 | 공격 성공률 | 악성 지시 저장 97.5% 이상, 실행은 0~95%로 저장률과 무상관. 도구 계층 "Memory Sandbox"가 9모델 중 8모델에서 ASR 0% | https://arxiv.org/abs/2605.08442 | 중간 |
| Utility Under Attack | 2026-08-21; Karunanidhi(단일 저자) | LongMemEval 기반 코퍼스에 평이한 거짓 주장을 일회 생성해 주입(최적화 없음). 쓰기 시 선별과 출처 가중 검색을 방어로 평가 | 정확도, 증거 재현율, p값 | 말뭉치 1.2% 오염 시 정확도 0.850→0.300. 선별 파이프라인은 오염 메모리 360개 중 0개 차단(간접 프롬프트 주입 재현율 0.832). 기본 출처 가중치는 무방어와 구분 불가(p=0.80). 저자 결론: 내용만으로는 거짓/참 구별에 외부 근거 필요 | https://arxiv.org/abs/2608.21230 | 중간 |
| MPBench / MemoryGraft | (검색 스니펫만) | MPBench는 "오염 항목이 기록될 확률"과 "이후 무관 세션에서 인출·행동할 확률"을 분리해 측정한다고 보고. MemoryGraft는 타 논문의 선행 연구 인용으로만 확인 | - | - | https://arxiv.org/pdf/2607.06595 , https://www.alphaxiv.org/abs/2606.04329 | 낮음 |

### 2.F 승격(promotion)·통합(consolidation)·강등

| 이름 | 날짜·저자 | 내용 | 채점 | 한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|
| ConsolidationBench ("The Epistemics of Agent Memory") | 2026-09-26; Annapureddy, Thamatani | 어떤 경험을 승격·압축·추상화·망각할지 **결정 자체**를 정답 최적해(oracle-by-construction)와 비교. 4단계 연구(에피소드 경계 학습, 승격·추상화 학습, 벤치마크, 거버넌스: 오염 저항·가역성·감사 가능성) | 최적 대비 3축(축 이름 미확인). 품질과 거버넌스를 따로 측정. 단계별 사전등록은 **요청 시 심사자 제공**(공개 아님) | 상용 검색 시스템은 정보를 보존하지만 수준 간 전이는 0점. 품질 점수가 실제 전이 정확도를 예측하지 못함(ρ=-0.24, 신뢰구간이 0 포함). 13쪽, 일부 메커니즘 내부 통제 공개 | https://arxiv.org/abs/2609.33013 | 중간(초록) |
| Memora | 위 §2.A | 통합·갱신 시간 축에서 무효 기억 재사용 감점 | | | https://arxiv.org/abs/2604.20006 | 중간 |
| MEMTIER / CrystalMem / AMV-L | 2026 프리프린트 | 계층(hot/warm/cold) 승격·강등·퇴출 **정책 절제 실험**. 예: MEMTIER는 LongMemEval-S에서 연령 임계 퇴출 Recall@2 0.711 vs 무퇴출 0.669 vs 임의 50% 퇴출 0.612 (검색 요약). 강등은 되돌릴 수 있어야 함(CrystalMem 절제) | 자체 지표 | **강등/퇴출의 정확성을 채점하는 표준 지표는 없음**(검색 요약의 결론). 외부 엔진이 아닌 자체 구현 | https://www.mdpi.com/1999-4893/19/7/607 , https://arxiv.org/pdf/2608.00303 , https://arxiv.org/pdf/2603.04443 | 낮음 |
| 독립 출처 기반 승격 | (벤치마크 없음) | 설계 수준만 확인: "memory-as-history" 도구는 단일 출처 메모리를 독립 출처 확인 시에만 승격(같은 화자 반복·한 문서 사본은 한 출처로 계산). 에이전트 RAG 이슈는 어시스턴트 자체 출력의 재진술을 독립 확증으로 세지 말라고 제안. Isnad 프레임워크는 확증자가 같은 모델 계열·상류 출처를 공유하면 확증을 발동하지 말라고 주장. MAP-Graph는 합성 벤치마크(2,700과제)에서 94.96% (Pith 리뷰: 주장 미검증) | - | 모두 벤치마크/동료심사 평가가 아님 | https://glama.ai/mcp/servers/BrunoBanana/memory-as-history/tools/corroborate , https://github.com/phense/agentic-rag/issues/7 , https://arxiv.org/pdf/2607.24117 , https://pith.science/paper/2608.10509 | 낮음 |

### 2.G 망각·감쇠·삭제(잊힐 권리)

| 이름 | 날짜·저자 | 형식·규모 | 채점 | 평가한 시스템 | 핵심 수치·한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|---|---|
| **ForgetEval** | 2026-06-14 (v2 06-16); Dongxu Yang(단일 저자) | 제어면 연산(대체 supersede, 해제 release, 삭제 purge). 1,000 템플릿 + 385 적대 사례(수작업 132 + LLM 초안 253은 오라클 검증). 13개 시스템 구성 | **결정론적 부분문자열 일치**. 6메서드 **어댑터 프로토콜** + "정직한 N/A" 채점(어댑터 약 130줄). Fleiss' κ=0.958(주석자 10명), 외부 기여자 4명이 블라인드로 쓴 77사례 하위집합 | 13구성(이질적 메모리 저장소 연결) | 변이 시점 LLM 훅이 의도 인지 삭제를 78~85%로 회복(전체 91.7~93.2%), 385사례 실행당 약 0.17달러·변이당 약 2.3초 vs 결정론 64~191ms. 주장: 프로덕션 실패는 회상보다 망각 실패가 많다(저자 주장). 단일 저자 | https://arxiv.org/abs/2606.15903 | 중간 |
| Bemula ("Can Agents Forget When Asked?") | 2026-10-13; Xie 외 | 50 다중 세션 대화 + 망각 요청 없는 쌍 통제본. 망각 요청 135개(6유형), 테스트 질문 1,742. 5개 스위트: 저장, 제거, 무관 기억 보존, 재학습, 추출 공격 저항 | 문항별 LLM judge(DeepSeek-V4.1-Flash, 온도 0, `[FORGOTTEN]`/`[NO_LEAK]` 마커), 집계는 결정론. 사람 대비 98/100 일치(κ=0.96) | Naive RAG, Mem0, A-MEM, LangMem, LightMem, SimpleMem + 전체 맥락. **uniform adapter(reset/observe/query)** 로 구동 | 어떤 시스템도 망각 요청의 1.5% 이상을 끝까지 통과하지 못함(대부분 0). 한계: 합성 대화, 50개 표본, judge가 평가 대상 백본 중 하나, 도구 사용 에이전트 아님 | https://arxiv.org/abs/2610.18052 , https://arxiv.org/html/2610.18052 | 높음(HTML 본문) |
| AUBench | 2026-10-13; Lin 외 | TOFU 가상 저자 설정을 에이전트로 확장: 매개변수 지식·지속 메모리·도구 호출 획득 3경로 | 미확인 | 기존 매개변수·메모리 수준 방법 | 모델 수준 망각과 시스템 수준 에이전트 망각 사이 격차 | https://arxiv.org/abs/2610.17659 | 중간(초록) |
| GateMem 망각 체크포인트 | 위 §2.D | 능동 망각 763 체크포인트 | | | https://arxiv.org/abs/2606.18829 | 중간 |
| MemoryAgentBench 선택적 망각 | 위 §2.A | FactConsolidation | | | https://arxiv.org/abs/2507.05257 | 중간 |
| ForgetBench | 2026-07 | **매개변수(모델 가중치) 기억**의 망각 동역학(반감기 등). 외부 메모리 엔진 아님 | | | https://arxiv.org/html/2607.26455 | 낮음 |
| Graph 기반 선택적 망각 / FSFM | 2026 | 감쇠 정책 제안 논문(방법론, 벤치마크 아님). 예: 노드 9.8% 제거해도 token F1 불변(단일 프리프린트) | | 자기보고 | https://arxiv.org/pdf/2608.28978 , https://arxiv.org/pdf/2604.20300 | 낮음 |

### 2.H 자기강화(self-reinforcement / echo)

| 이름 | 날짜·저자 | 내용 | 한계 | 링크 | 신뢰도 |
|---|---|---|---|---|---|
| Self-Cleaning and Captured Anyway | 2026-09-06; Chen 외 | 에이전트가 자신이 쓰는 저장소로 오류가 "포획(capture)"되어 굳는지를 추가 전용(append-only) 저장소의 무한 보유 극한에서 모델링. Wikidata 36개 사실 + 합성 실행, 개입 4가지를 사전 고정 기준으로 평가. claude-sonnet-4.5가 20 시드 전부에서 포획됨(사전 등록 예측 0.5 미만이 빗나감) | 이상화(무한 보유, 추가 전용), 작은 사실 집합(36), 시드를 늘리면 여러 주장이 약해지고 저자 스스로 87개 채점 행 중 37개를 철회/실패/자기교정/판정불가로 표기. **메모리 엔진 블랙박스 벤치마크가 아님** | https://arxiv.org/abs/2609.25052 | 중간(초록) |
| PersistBench의 메모리 유발 아첨 | ICML 2026 | 저장된 메모리가 사용자 편향을 강화 | 상세 미확인 | https://icml.cc/virtual/2026/poster/63204 | 낮음 |
| MemSyco-Bench | 위 §2.D | 메모리 유발 아첨 | | https://arxiv.org/abs/2607.01071 | 중간 |
| 검색 중 발견한 기타 | Bias Amplification 개념 연구, Honest Lying(메모리 confabulation) 등 | 개념·소규모 실험 | 표준 벤치마크는 못 찾음(검색 결과 요약의 결론) | https://doi.org/10.3390/technologies14070415 , https://arxiv.org/html/2605.29463v1 | 낮음 |

### 2.I 비용·지연 (백그라운드 처리 오버헤드)

| 출처 | 내용 | 링크 | 신뢰도 |
|---|---|---|---|
| Mem0 논문 | 전체 맥락 대비 p95 지연 91% 감소, 토큰 비용 90% 이상 절감(자기보고, LOCOMO, LLM-as-a-Judge) | https://arxiv.org/abs/2504.19413 | 중간(벤더 자기보고) |
| Zep 논문 | LongMemEval(S)에서 gpt-4o-mini: 전체 맥락 55.4%·31.3초·115K 토큰 vs Zep 63.8%·3.20초·1.6K 토큰; gpt-4o: 60.2%·28.9초 vs 71.2%·2.58초. 지연 측정은 가정용 노트북→AWS 경로(저자 명시) | https://arxiv.org/html/2501.13956v1 | 높음(벤더 자기보고) |
| Mem0 2026 연구 페이지 | LoCoMo 92.5, LongMemEval 94.4, 검색 호출당 평균 약 6,900 토큰(전체 맥락 25,000 이상); 방법론 세부 미기재; 독립 재현 못 찾음 | https://mem0.ai/research-3 | 낮음(벤더 자기보고) |
| DolphinBench | 정확도와 함께 총비용·지연 필수 보고 | https://arxiv.org/abs/2609.24971 | 중간 |
| STATE-Bench | 과제당 비용(턴, 불필요 도구 호출, 토큰) | https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/ | 중간 |
| MemBench | 연산별 읽기/쓰기 시간, 용량 | https://arxiv.org/html/2506.21605v1 | 중간 |
| GateMem | 토큰: 장문맥 체크포인트당 약 4K~7.6K vs ReMem 약 1K, 단 ReMem-I는 체크포인트당 최대 약 267초 | https://arxiv.org/html/2606.18829 | 중간 |
| MemoryArena | 메모리 시스템은 하위 과제당 평균 99~133초(장문맥보다 느림) | https://arxiv.org/html/2602.16313 | 중간 |
| ForgetEval | 변이 시점 LLM 훅 약 2.3초/사례, 385사례 약 0.17달러 | https://arxiv.org/abs/2606.15903 | 중간 |
| Memanto (경쟁 논문) | Mem0-Graph와 Zep은 쓰기당 LLM 호출 2회 이상, 수집 지연 약 2·3초 주장. 경쟁사 분석이라 편향 가능 | https://arxiv.org/pdf/2604.22085 | 낮음 |

### 2.J 벤더·프레임워크 자체 평가와 하네스 인프라

| 이름 | 무엇을 측정/제공 | 채점 | 비판 | 링크 | 신뢰도 |
|---|---|---|---|---|---|
| Mem0 논문 (ECAI 2025) | LOCOMO 4범주(단일 홉, 시간, 다중 홉, 개방 도메인)에서 OpenAI 메모리 등 대비 LLM-as-a-Judge 26% 상대 향상, 지연·토큰 | LLM-as-a-Judge | 자기보고, 독립 재현 못 찾음(검색 결과) | https://arxiv.org/abs/2504.19413 | 중간 |
| Mem0 블로그 (2025-04): OpenAI Memory/LangMem/MemGPT/Mem0 비교 | LangMem judge 정확도 58.1% vs Mem0 66.9%, LangMem p95 60초 | LLM judge | 벤더가 경쟁 제품을 직접 평가 | https://mem0.ai/blog/ai-agent-memory-benchmark | 낮음 |
| Zep 논문 | DMR + LongMemEval(위 §2.I). LongMemEval 지식갱신은 혼합(gpt-4o 78.2→83.3, mini 76.9→74.4). 이중 시간 그래프 | LLM judge | 단일 어시스턴트 응답 범주는 하락(94.6→80.4 등)을 저자가 인정. Zep-Mem0 LoCoMo 점수 분쟁(분모 처리) 2차 보고 | https://arxiv.org/html/2501.13956v1 , https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html | 높음(논문), 낮음(분쟁 요약) |
| Letta: "Is a filesystem all you need?" (2025-08-12) | GPT-4o-mini 에이전트가 파일시스템 도구만으로 LoCoMo 74.0%(Mem0 보고 68.5%). 결론: 현 메모리 벤치마크는 별 의미가 없을 수 있고 에이전트 능력이 검색 메커니즘보다 중요 | judge 방식 글에 미기재 | 후속: Mem0가 2026-04 알고리즘을 재작성해 LoCoMo 92.5 게시(Letta 재실험 안 함, 2차 글) | https://www.letta.com/blog/benchmarking-ai-agent-memory | 높음(글), 낮음(후속 2차) |
| LangMem / LangGraph 메모리 | 문서에 평가·벤치마크 서술 **없음**(핫패스 도구, 백그라운드 메모리 매니저, 프롬프트 최적화). 3자 비교에서만 등장(LoCoMo judge 51.3~73.4, Mnemis 논문 표) | - | 공식 평가 방법론을 찾지 못함 | https://langchain-ai.github.io/langmem/ , https://arxiv.org/pdf/2602.15313 | 중간(문서), 낮음(3자) |
| supermemory MemoryBench | 플러그형 하네스. 벤치마크 LoCoMo, LongMemEval, ConvoMem. 제공자 Supermemory/Mem0/Zep. 파이프라인 ingest→index→search→answer→evaluate→report, 단계별 체크포인트. judge 설정 가능(GPT-4o 등) | LLM judge | 벤더 도구 | https://github.com/supermemoryai/memorybench | 중간 |
| MemoryAgentBench 코드 | "inject once, query multiple times" 설계로 평가 효율화(HUST-AI-HYZ 저장소) | | 저장소 URL 직접 열람 안 함 | https://www.sourcepulse.org/projects/26054903 | 낮음 |

항목 수 집계: 이 표들에 개별 이름으로 등재한 항목은 §2.A 8 + §2.B 11 + §2.C 3(STALE, StateMemBench, Memory Trust Gap) + §2.D 9 + §2.E 8 + §2.F 3(ConsolidationBench, MEMTIER 계열 묶음, 독립 출처 설계 묶음) + §2.G 6 + §2.H 3 + §2.J 5 = 약 56건(중복·묶음 포함). 원문 초록/본문을 직접 읽은 것은 약 40건, 나머지는 스니펫 또는 2차 설명(표에 "낮음"으로 표기).

---

## 3. 알려진 비판·메타 평가 (평가 자체에 대한 문제)

| 쟁점 | 내용 | 링크 | 신뢰도 |
|---|---|---|---|
| LoCoMo 정답지·judge | 정답 오류 6.4%, 관대한 judge, top-k가 세션 수보다 큼, 범주 5 분모 처리 분쟁, 일부 문항 구조적으로 채점 불가라는 주장(444문항, 약 23%) | https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer , https://github.com/snap-research/locomo/issues/44 , https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html | 낮음 (벤더·경쟁자·2차 글) |
| 채점 모델·프롬프트 민감도 | 같은 답이 프롬프트에 따라 26점 이동, 2026년 94.7 점수는 gpt-5.4가 답변·채점 겸임이라는 보고 | https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html | 낮음 |
| 회상 위주의 한계 | MemoryArena: LoCoMo 같은 장문맥 메모리 벤치에서 포화에 가까운 에이전트도 에이전트 환경에서는 성과가 낮음. DolphinBench: 질의가 무엇을 검색할지 알려줌, 정확도만 봄 | https://arxiv.org/abs/2602.16313 , https://arxiv.org/abs/2609.24971 | 중간 |
| 벤더 자기평가의 이해충돌 | Mem0(자사 vs LangMem/MemGPT), Zep 점수 정정, Letta의 Mem0 수치 의문 제기 | https://mem0.ai/blog/ai-agent-memory-benchmark , https://www.letta.com/blog/benchmarking-ai-agent-memory | 중간 |
| judge가 평가 대상과 동일 | Bemula는 judge가 평가 대상 백본 중 하나(저자 한계 기재), HaluMem은 GPT-4o가 judge 겸 답변자 | https://arxiv.org/html/2610.18052 , https://arxiv.org/html/2511.03506 | 높음 |
| 합성·소규모 | DreamBench-SWE(큐레이션 픽스처, 단일 모델), MemLeak(n=10), UCC(34 규약), HaluMem(20 사용자) | 위 각 행 링크 | 중간 |
| 점수 비교 불가 | 하네스·설정 차이로 논문 간 점수 비교가 어렵다(2차 요약) | https://github.com/Luminous-Dynamics/symthaea/issues/8445 | 낮음 |

---

## 4. 격차 분석: 우리 8개 각도 vs 기존 연구

판정 기준: **커버됨** = 같은 양을 같은 방식으로 측정하는 공개 연구가 있음. **부분** = 일부 요소만 있음(무엇이 있고 무엇이 없는지 명시). **미발견** = 위 검색 범위에서 명시적 측정을 못 찾음.

### 4.0 한눈에 보기

| # | 우리 각도 | 판정 | 가장 가까운 선행 연구 | 신뢰도 |
|---|---|---|---|---|
| 1 | 잘못된 기억에 의한 **행동**률 | **부분** | DreamBench-SWE, Memory Trust Gap, Sleeper, Injection-Execution, STALE(정책 적응) | 중간 |
| 2 | 팀/개인 격리를 **합격/불합격**(0) 기준으로 | **부분** | GateMem, MemLeak, UCC, MUMBench, CoMemBench | 중간 |
| 3 | **독립 출처** 승격 | **미발견** | (설계 문서만) ConsolidationBench는 승격 결정이나 출처 독립성은 아님 | 중간 |
| 4 | 시간에 따른 **진리 변화** | **커버됨**(QA 중심) + 행동 결합은 부분 | LongMemEval 지식갱신, FactConsolidation, STALE, StateMemBench, Memora, BEAM, Memory Trust Gap | 높음 |
| 5 | **조건부 사실** | **부분** | MemSyco-Bench(범위 통제), PairPref, MemPABench, DreamBench-SWE C5, UCC | 중간 |
| 6 | **자기강화**(echo) | **부분(이론·소규모)** | Self-Cleaning and Captured Anyway | 중간 |
| 7 | 결정론적 비-LLM 채점 + **사전등록/블라인딩** | **부분(조합은 DreamBench-SWE가 가장 근접)** | DreamBench-SWE, ForgetEval, STATE-Bench(상태 단언), Mem2ActBench, coding-agent-memory-benchmark | 중간 |
| 8 | 블랙박스 엔진 포트 + **promote/demote 결정** | 포트: **커버됨**, promote/demote: **미발견** | ForgetEval 어댑터, Bemula 어댑터, MemoryBench / 승격·강등: ConsolidationBench(엔진 아님) | 중간 |

### 4.1 각도별 상세

#### 각도 1. 잘못된 기억에 의한 행동률 (M1: store-everything 기준선 대비)

- **있는 것**
  - DreamBench-SWE: 주 결과가 실행 가능한 오라클 pass@1이므로 에이전트 산출물(행동 결과)에 결합되어 있다. 위생 진단으로 StaleUseRate(0.200), OverscopeRate(0.167), RepeatedErrorRate(0.571) 등을 보고하되 정의가 본문에 없고 "진단일 뿐 주 결과 아님"이라 명시. 기준선 B0~B7과 "결정론적 verbatim 이벤트 메모리"가 있어 우리 store-everything 기준선과 개념적으로 가깝다. https://arxiv.org/html/2608.20664 (중간)
  - Memory Trust Gap: 행동 채점, 해악 = 무메모리 대비 Δmem. 오래된 메모를 최신으로 보이게 하면 큰 모델이 더 속는다는 요인 분석. 변수는 모델 크기이며 메모리 엔진이 아니다. https://arxiv.org/abs/2609.01852 (중간)
  - Sleeper Memory Poisoning, Injection-Execution Dissociation: 오염 메모리 → 실제 행동 비율을 단계별로 보고. https://arxiv.org/abs/2605.15338 , https://arxiv.org/abs/2605.08442 (중간)
  - STALE의 "암묵적 정책 적응" 프로브(하위 행동에 갱신 상태 적용). 다만 한 서베이는 STALE이 "도구 호출 회피"까지는 안 본다고 평가. https://arxiv.org/abs/2605.06527 , https://arxiv.org/pdf/2606.30306 (중간)
- **못 찾은 것**: (a) 잘못된 기억의 **여러 원인**(우연 일치, 단일 출처 오염, 자기강화 echo, 진리 변화, 조건부 사실, 임시방편 vs 근본 해결)을 한 척도(M1)로 묶고 (b) **store-everything 기준선 대비 상대값**으로 (c) **블랙박스 메모리 엔진**에 대해 보고하는 연구. DreamBench-SWE는 (a)가 부분적이고 오염·격리·승격은 제외, Memory Trust Gap은 엔진이 아님.
- **반증용 검색어**: `"wrong memory" action rate memory engine baseline store everything`, `memory-induced error rate coding agent relative to no memory`, `harmful memory use rate benchmark Mem0 Zep Letta`.
- **권고**: "행동 수준 측정은 새롭다"는 표현은 쓰지 말 것. "여러 오기억 원인을 한 상대 지표로 통합"이 차별점 후보.

#### 각도 2. 팀/개인 격리를 합격/불합격 기준으로

- **있는 것**
  - GateMem: 접근통제 위반을 정의(보호 대상 누출 또는 refuse/answer_redacted 외의 행동)하고 727개 접근통제 체크포인트로 측정. 단 주 채점이 GPT-4o judge이고 점수가 연속값이며(예: 접근통제 10.4, 27.1) "0이어야 한다"는 합격선은 아님. https://arxiv.org/html/2606.18829 (중간)
  - MemLeak: 공유 벡터 저장소에서 교차 사용자 우발 누출 70~100%, "소유권 게이트"만 기준선을 복원. 개념증명, n=10. https://arxiv.org/abs/2610.04195 (중간)
  - UCC: 악의 없는 공유 상태 오염 57~71%. EHRAgent는 정확 일치로 채점(결정론). https://arxiv.org/html/2604.01350v1 (높음)
  - MUMBench, CoMemBench, CIMemories는 격리 변종(사용자 간 가시성, 워크플로 격리, 맥락별 속성 노출)을 각각 다룸. https://arxiv.org/abs/2609.12320 , https://arxiv.org/abs/2609.32192 , https://arxiv.org/abs/2511.14937 (중간)
  - 실무자 제안: 테넌트 A에 고유 사실을 저장 후 B로 회상해 아무것도 반환되지 않는지 단언(Hindsight 블로그). 이는 벤치마크가 아니라 테스트 템플릿. https://hindsight.vectorize.io/blog/2026/08/04/per-user-multi-tenant-agent-memory (낮음)
- **못 찾은 것**: 격리를 "비율 보고"가 아니라 "**위반 0건이 아니면 불합격**"으로 정의한 평가, 그리고 **상용 메모리 엔진을 블랙박스로** 개인/팀/교차 팀 경계에서 시험한 결과. GateMem이 Mem0을 포함하지만 점수 체계가 다름. 검색 요약도 "교차 사용자 메모리 격리를 검색·접근통제·생성까지 아우르는 널리 채택된 벤치마크는 없다"고 결론.
- **반증용 검색어**: `tenant isolation memory engine zero leakage pass fail benchmark`, `cross-team memory leakage coding agent benchmark`, `memory scope enforcement test suite Mem0 Zep`.

#### 각도 3. 독립 출처 승격

- **있는 것**: 벤치마크는 못 찾음. 설계·도구 수준: memory-as-history의 corroborate(같은 화자 반복·문서 사본은 한 출처, 출처 불명이면 증인 2명), agentic-rag 이슈(어시스턴트 자체 재진술은 독립 확증이 아님), Isnad(확증자가 같은 모델 계열/상류 출처를 공유하면 확증 금지), MAP-Graph(합성 벤치마크 2,700과제). https://glama.ai/mcp/servers/BrunoBanana/memory-as-history/tools/corroborate , https://github.com/phense/agentic-rag/issues/7 , https://arxiv.org/pdf/2607.24117 , https://pith.science/paper/2608.10509 (낮음)
- 근접: ConsolidationBench는 "무엇을 승격할지" 결정을 오라클 최적해와 비교하지만, 결정 기준이 출처 독립성이 아니라 작업 효용/추상화 수준이다. https://arxiv.org/abs/2609.33013 (중간). Utility Under Attack은 출처 가중 검색을 평가해 "내용만으로는 참/거짓을 구분할 수 없다"고 결론, 우리 "독립 출처" 요구와 같은 방향의 근거. https://arxiv.org/abs/2608.21230 (중간)
- **결론**: 미발견. 우리 각도 중 가장 새로울 가능성이 높다.
- **반증용 검색어**: `independent corroboration memory promotion benchmark`, `single-source claim promotion shared memory evaluation`, `memory consolidation provenance independence test`.

#### 각도 4. 시간에 따른 진리 변화

- **커버됨(회상·QA 수준)**: LongMemEval 지식갱신·시간추론 https://arxiv.org/html/2410.10813v2 ; MemoryAgentBench FactConsolidation https://arxiv.org/html/2507.05257v3 ; BEAM 지식 갱신·모순 해소 https://arxiv.org/html/2510.27246v1 ; HaluMem 갱신 연산 https://arxiv.org/html/2511.03506 ; Memora FAMA https://arxiv.org/abs/2604.20006 ; StateMemBench https://arxiv.org/abs/2608.19652 ; Zep 이중 시간 그래프(주장) https://arxiv.org/html/2501.13956v1 (높음~중간)
- **행동과 결합**: STALE(정책 적응), Memory Trust Gap(행동 채점), DreamBench-SWE C3(오래된 아키텍처, 실행 오라클). https://arxiv.org/abs/2605.06527 , https://arxiv.org/abs/2609.01852 , https://arxiv.org/html/2608.20664 (중간)
- **결론**: "진리 변화 측정 자체"는 새롭지 않다. 우리 차별점은 (엔진 블랙박스 + 결정론적 에이전트 + 피드백 모드에서 진리 변화 신호가 되돌아오는 폐루프)로 한정해야 함.
- **반증용 검색어**: `knowledge update benchmark agent action memory engine`, `superseded fact benchmark tool call` (이 질의는 이미 다수 결과를 반환하므로 "미발견" 주장은 하지 말 것).

#### 각도 5. 조건부 사실

- **부분**: MemSyco-Bench의 "적용 범위 존중" 범주 https://arxiv.org/abs/2607.01071 ; PairPref(상황 사실을 바꿔 선호 적용 적절성 분리, 검색 스니펫만) https://arxiv.org/html/2609.34526v1 ; MemPABench(파일 기반 메모리가 선호를 적용 범위 밖으로 번지게 함) https://arxiv.org/abs/2610.30485 ; DreamBench-SWE C5 범위 규율(유효기간·범위 필드 절제 A12/A13은 설계만 하고 미실행) https://arxiv.org/html/2608.20664 ; UCC(범위가 한정된 규약이 타 사용자에게 오적용) https://arxiv.org/html/2604.01350v1 ; CIMemories(속성이 과제 맥락에 따라 필수/부적절) https://arxiv.org/abs/2511.14937 (중간)
- **못 찾은 것**: "사실이 특정 조건(환경, 버전, 브랜치, 시기)에서만 참"이라는 **조건부 사실**을 명시 분류해 엔진이 조건을 보존하는지 보는 코딩 에이전트용 벤치마크.
- **반증용 검색어**: `conditional fact memory benchmark applies only when`, `scoped memory validity condition environment version coding agent memory benchmark`.

#### 각도 6. 자기강화(echo)

- **있는 것**: Self-Cleaning and Captured Anyway는 에이전트가 자기 저장소에 쓴 결론이 이후 인출되어 굳는 현상을 이론+소규모 실험으로 다룸(36 Wikidata 사실, claude-sonnet-4.5가 20 시드 모두에서 포획). 저자 스스로 채점 행의 상당수를 철회·실패로 기록. https://arxiv.org/abs/2609.25052 (중간). 추가로 "자기평가 편향", "외부 검증 게이트가 핵심"이라는 보고(검색 요약, 낮음) https://arxiv.org/pdf/2607.25152
- **못 찾은 것**: 메모리 엔진을 블랙박스로 두고 "에이전트의 후속 기록이 엔진에 되먹임될 때 독립 증거 없이 확신도·승격이 오르는가"를 보는 벤치마크. DreamBench-SWE의 거짓 교훈(C9)은 B0 여유 점검에 실패해 무효. 검색 요약도 "자기 기록 메모리의 오류 증폭을 재는 표준 벤치마크는 없다"고 결론.
- **반증용 검색어**: `self-reinforcing memory loop benchmark memory engine`, `agent writes conclusions back to memory error persistence benchmark`, `echo amplification memory write-back evaluation`.

#### 각도 7. 결정론적 비-LLM 채점 + 사전등록/블라인딩

- **결정론 채점**: ForgetEval(부분문자열 일치, 어댑터 프로토콜) https://arxiv.org/abs/2606.15903 ; DreamBench-SWE(실행 오라클) https://arxiv.org/html/2608.20664 ; STATE-Bench(상태 단언, 단 일부 LLM judge) https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/ ; Mem2ActBench(정확 일치/F1) https://arxiv.org/html/2601.19935v1 ; UCC 일부(정확 일치) https://arxiv.org/html/2604.01350v1 ; MemBench(객관식) https://arxiv.org/html/2506.21605v1 (중간~높음)
- **사전등록**: DreamBench-SWE(2026-07-03 동결, v2.1 후속 감사, Holm 보정, 적합성 게이트) https://arxiv.org/html/2608.20664 ; coding-agent-memory-benchmark(DESIGN.md 2026-06-17, 실행 06-24) https://github.com/SaravananJaichandar/coding-agent-memory-benchmark ; Self-Cleaning(사전 고정 기준과 사전 등록 예측) https://arxiv.org/abs/2609.25052 ; ConsolidationBench(사전등록은 요청 시 제공) https://arxiv.org/abs/2609.33013 (중간)
- **블라인딩**: ForgetEval의 블라인드 외부 기여자 77사례 https://arxiv.org/abs/2606.15903 ; 일부 벤치마크가 judge에게 메모리 맥락을 가리거나(검색 요약이 ACL판 Mem2ActBench를 그렇게 서술; arXiv판 본문에는 judge 서술이 없어 판본 차이 가능, 미확인) 생성 답변을 보이지 않음(Keep It InMind) https://aclanthology.org/2026.acl-long.370.pdf , https://arxiv.org/pdf/2607.24368 (낮음). 다만 이는 "엔진 평가자 블라인딩"이 아니라 judge 설계 수준.
- **결론**: DreamBench-SWE가 "결정론+사전등록+호스팅 Mem0 블랙박스"를 이미 한 편으로 묶었다. 우리는 이 조합을 새롭다고 주장하기 어렵고, 차별점은 (오염·격리·승격 등 안전 축으로 확장 + 결정론적 시뮬레이션 에이전트로 에이전트 LLM 변동 제거)가 된다. DreamBench-SWE는 에이전트가 LLM(gpt-5.5)이라 모델 변동이 남는 점이 대비점(중간).
- **반증용 검색어**: `preregistered memory benchmark deterministic oracle`, `blinded evaluation memory engines`.

#### 각도 8. 블랙박스 엔진 포트 + promote/demote 결정

- **포트(커버됨)**: ForgetEval 어댑터 프로토콜(6메서드, 약 130줄, 정직한 N/A) https://arxiv.org/abs/2606.15903 ; Bemula uniform adapter(reset/observe/query) https://arxiv.org/html/2610.18052 ; supermemory MemoryBench 제공자 인터페이스 https://github.com/supermemoryai/memorybench ; GateMem이 시스템을 "메모리 상태 위의 함수"로 취급 https://arxiv.org/html/2606.18829 ; DreamBench-SWE의 호스팅 Mem0 호출과 적합성 게이트 https://arxiv.org/html/2608.20664 (중간~높음)
- **promote/demote(미발견)**: 엔진이 "이 메모리를 승격/강등했다"는 결정을 포트로 내보내고 그 정확성을 채점하는 벤치마크는 찾지 못함. 근접: ConsolidationBench는 결정을 최적해와 비교하지만 자체 정책 대상이고 "상용 검색 시스템은 수준 간 전이 0점"이라고 보고(엔진 포트 아님) https://arxiv.org/abs/2609.33013 ; MEMTIER/CrystalMem/AMV-L은 자체 구현의 정책 절제로 "강등 정확성 표준 지표 없음" https://www.mdpi.com/1999-4893/19/7/607 , https://arxiv.org/pdf/2608.00303 , https://arxiv.org/pdf/2603.04443 (낮음~중간). Mem0 문서는 삭제와 검색 시 하향 가중(down-ranking)을 구분한다고 서술(블로그) https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents (낮음). 즉 대부분의 상용 엔진은 promote/demote 결정을 외부에 노출하지 않을 가능성이 있다(추정, 미확인).
- **반증용 검색어**: `memory engine promotion demotion API benchmark black-box`, `memory tier decision correctness metric`, `eviction correctness agent memory benchmark`.

---

## 5. 우리 설계에 주는 시사점 (T4/T5 입력용, 제안 수준)

1. **인용해야 할 가장 가까운 선행 연구 5건**: DreamBench-SWE, Memory Trust Gap, GateMem, ForgetEval, STALE. 이들과 직접 비교하는 표를 문서에 넣어 "아무도 안 했다"는 서술을 피할 것.
2. **강하게 주장해도 되는 쪽**: 독립 출처 승격(각도 3), 자기강화 echo의 엔진 수준 측정(각도 6), 승격/강등 결정의 포트 채점(각도 8 일부), 격리 0 합격선(각도 2의 합격/불합격 정의).
3. **약하게 주장해야 하는 쪽**: 행동 수준 측정(각도 1), 진리 변화(각도 4), 결정론 채점·사전등록(각도 7)은 선행 연구가 있으므로 "조합"과 "결정론적 시뮬레이션 에이전트 + store-everything 기준선 대비 상대 M1"로 차별화.
4. **재현성 위험**: DreamBench-SWE는 호스팅 Mem0의 서비스 드리프트와 적합성 게이트 탈락(기본 추출 설정, Supermemory 탈락)을 겪었다. 우리 블랙박스 포트에도 같은 위험이 있으므로 어댑터 적합성 검사와 "N/A 정직 채점"(ForgetEval 방식)을 설계에 넣을 근거가 된다.
5. **측정 오염 위험**: LoCoMo식 judge 관대함·정답지 오류 문제는 우리가 LLM judge를 쓰지 않는 것의 근거로 인용 가능(단 수치는 벤더 감사이므로 "보고됨" 수준으로).
6. **피드백 모드**: 조사한 어떤 벤치마크도 "에이전트 후속 기록을 엔진에 되먹이는 폐루프"를 결정론적으로 통제하지는 않았다(근접: Evo-Memory, MemoryBench(Ai 외)의 시뮬레이션 사용자 피드백, 둘 다 2차 설명뿐). 이 점은 미확인이므로 "찾지 못함"으로만 기술.

---

## 6. 미확인·후속 확인 권고 목록

- 원문 미열람: LoCoMo 지표(F1/judge) 상세, MemoryAgentBench 부록 L(judge), MemoryArena 채점 결정론 여부, MemLeak 1차 채점 방식·표본 크기, STATE-Bench judge 모델, MemSyco-Bench·PairPref·PersistBench·AUBench의 규모·채점, MPBench 원문, Evo-Memory/MemoryBench(Ai 외)/EvoMemBench 상세, Letta Evals의 채점 방식.
- DolphinBench 저자 소속(Mem0 여부)과 평가 대상 시스템: 확인 필요.
- DreamBench-SWE 본문 일부(약 25만 자 중 앞 20만+일부)만 열람. 격리·오염·삭제 미다룸 판단은 열람 범위 기준.
- 웹 요약기 의존 수치(특히 §2.B~2.E의 숫자)는 원문 대조 후 인용.
- MemoryAgentBench의 다단계 충돌 해소 상한: 검색 요약은 28%, v3 HTML 요약은 약 7%로 상충 → 판본 차이 가능, 인용 전 확인.
